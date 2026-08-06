import json
import re
import time
import typing
from app.agent.tools_schema import TOOL_DEFINITIONS, DONE_ONLY_TOOLS
from app.agent.history import build_messages, trim_history
from app.usage.tokens import estimate_messages_tokens, estimate_response_tokens
from app.logging_utils import get_logger

log = get_logger("agent")

SYSTEM_PROMPT = """You are an RPG rules assistant. You search the user's RPG manual collection to answer questions.

Rules:
1. ALWAYS use fts_search FIRST. Never use ls or list_index to browse — they waste turns.
2. Use SIMPLE search terms — one or two words, not full sentences. Example: fts_search "goblin" not fts_search "what is a goblin's armor class". Avoid hyphens.
3. After fts_search, use read_file to read the full content of the most relevant result.
4. Use done to give your final answer with citations and 3 suggested follow-up questions.
5. If fts_search returns no results, try a different simpler query (2-3 attempts max), then call done.
6. NEVER read the same file twice.
7. NEVER use ls — the file structure is flat .md files, not directories. Use fts_search instead.
8. NEVER try to read index.md files — they don't exist in this collection.

The fts_search snippets are too short to answer from. Always read_file before answering.

When calling done, always include 3 "suggestions" — short follow-up questions a player might ask next based on what they just learned."""


def clean_answer(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'^Based on the search results,?\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'###?\s*Citations?:?\s*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n###?\s*Citations?:?\s*\n.*', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'\n-?\s*\*\*(Page|Path|Quote|Source|Reference)\*\*:?\s*[^\n]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n-?\s*(Page|Path|Quote|Source|Reference):?\s*[^\n]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[Path:\s*[a-f0-9]+/[^\]]+\]', '', text)
    text = re.sub(r'`[a-f0-9]{32}/[^`]+\.md`', '', text)
    text = re.sub(r'If you need more (detailed )?information[^\?]*\??', '', text, flags=re.IGNORECASE)
    text = re.sub(r'If you.*?like more.*?ask.*?!', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'feel free to ask!?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _parse_text_tool_call(content: str) -> dict | None:
    content = content.strip()
    tool_names = {"fts_search", "read_file", "list_index", "grep", "table_extract", "calc", "ls", "done"}
    for name in tool_names:
        if content.startswith(name + " ") or content.startswith(name + "{"):
            json_part = content[len(name):].strip()
            try:
                args = json.loads(json_part)
                return {"function": {"name": name, "arguments": args}}
            except json.JSONDecodeError:
                pass
    return None


def _extract_cites_from_history(messages: list[dict]) -> list[dict]:
    """Scan tool results in message history for file paths that could serve as citations."""
    cites = []
    seen_paths = set()
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("name") == "fts_search":
            try:
                results = json.loads(msg["content"])
                for r in results[:3]:
                    path = r.get("path", "")
                    if path and path not in seen_paths:
                        seen_paths.add(path)
                        cites.append({"path": path, "quote": r.get("snippet", "")[:200]})
            except (json.JSONDecodeError, TypeError):
                pass
    return cites[:5]


def _synthesize_answer(messages: list[dict], question: str) -> str:
    """Build a fallback answer from tool results when the model didn't call done.

    Tries to extract useful content from FTS search results and read files
    to give the user *something* useful instead of a generic error message.
    """
    # Collect FTS search results
    fts_results = []
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("name") == "fts_search":
            try:
                import json
                results = json.loads(msg.get("content", "[]"))
                for r in results[:5]:
                    title = r.get("title", "")
                    snippet = r.get("snippet", "")
                    path = r.get("path", "")
                    if title or snippet:
                        fts_results.append({"title": title, "snippet": snippet, "path": path})
            except Exception:
                pass

    # Collect file content from read_file results
    file_contents = []
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("name") == "read_file":
            content = msg.get("content", "")
            if content and "not found" not in content.lower() and "invalid" not in content.lower():
                file_contents.append(content)

    # If we have file content, extract the most relevant paragraph
    if file_contents:
        # Find paragraphs mentioning keywords from the question
        question_words = set(w.lower() for w in question.split() if len(w) > 4)
        best_snippet = ""
        best_score = 0
        for content in file_contents:
            # Strip front-matter
            if content.startswith("---"):
                parts = content.split("---", 2)
                content = parts[2].strip() if len(parts) > 2 else content
            for para in content.split("\n\n"):
                para_clean = para.strip()
                if len(para_clean) < 20 or para_clean.startswith("#"):
                    continue
                para_lower = para_clean.lower()
                score = sum(1 for w in question_words if w in para_lower)
                if score > best_score:
                    best_score = score
                    best_snippet = para_clean

        if best_snippet and best_score > 0:
            return best_snippet[:500] + "\n\n*This answer was extracted from the manual but the AI ran out of turns to fully synthesize it. Try asking a more specific question for a complete answer.*"

    # If we have FTS results but no file content
    if fts_results:
        result_list = "\n".join(
            f"- **{r['title']}**: {r['snippet'][:100]}..." for r in fts_results[:3]
        )
        return f"I found these relevant sections but couldn't fully read them:\n\n{result_list}\n\n*Try asking a more specific question about one of these topics.*"

    # Last resort — no results at all
    return f"I searched the manual but could not find information about: \"{question}\". Try rephrasing with different keywords (e.g. specific rule names, character classes, or monster names)."


class AgentLoop:
    def __init__(self, gateway, toolbox, max_iterations: int = 15):
        self.gateway = gateway
        self.toolbox = toolbox
        self.max_iterations = max_iterations

    async def run(self, history: list[dict], new_question: str) -> dict:
        """Non-streaming run — returns the full result at once."""
        result = None
        async for event in self.run_stream(history, new_question):
            if event["type"] == "done":
                result = event
                break
            elif event["type"] == "error":
                result = event
                break
        if result is None:
            result = {"type": "done", "answer": "I could not find an answer.", "cites": [], "suggestions": [], "iterations": self.max_iterations}
        return {
            "answer": result.get("answer", ""),
            "cites": result.get("cites", []),
            "suggestions": result.get("suggestions", []),
            "iterations": result.get("iterations", 0),
            "est_input_tokens": result.get("est_input_tokens", 0),
            "est_output_tokens": result.get("est_output_tokens", 0),
        }

    async def run_stream(self, history: list[dict], new_question: str) -> typing.AsyncGenerator[dict, None]:
        """Streaming run — yields events as they happen.

        Event types:
          {"type": "thinking", "message": "..."} — status update (searching, reading)
          {"type": "token", "content": "..."} — answer token (streamed)
          {"type": "done", "answer": "...", "cites": [...], "suggestions": [...], "iterations": N}
          {"type": "error", "message": "..."}
        """
        start_time = time.time()
        trimmed = trim_history(history, keep_last=6)
        messages = build_messages(trimmed, SYSTEM_PROMPT)
        messages.append({"role": "user", "content": new_question})

        log.info(f"QUERY START: \"{new_question}\" (history={len(history)} turns)")

        last_content = ""
        files_read: set[str] = set()
        searches_done: set[str] = set()
        forced_done = False  # when True, only done tool is offered
        total_input_tokens = 0
        total_output_tokens = 0
        dedup_read_count = 0  # track repeated read attempts

        for iteration in range(1, self.max_iterations + 1):
            t0 = time.time()
            tools = DONE_ONLY_TOOLS if forced_done else TOOL_DEFINITIONS
            # Track input tokens
            total_input_tokens += estimate_messages_tokens(messages)
            resp = await self.gateway.call("query", "", tools=tools, messages=messages)
            llm_time = time.time() - t0
            msg = resp.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content", "")
            # Track output tokens
            if content:
                total_output_tokens += estimate_response_tokens(content)
                last_content = content

            if not tool_calls:
                if content:
                    parsed_tc = _parse_text_tool_call(content)
                    if parsed_tc:
                        tool_calls = [parsed_tc]
                        content = ""
                        log.info(f"  iter {iteration}: parsed text tool call -> {parsed_tc['function']['name']} ({llm_time:.1f}s)")
                    elif len(content) > 10:
                        log.info(f"  iter {iteration}: content without tool calls, re-prompting for done ({llm_time:.1f}s)")
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": "Good answer. Now call the done tool with that answer in the 'answer' field, and add any source citations you have in the 'cites' field. Use the exact paths from the fts_search results."})
                        continue
                if not tool_calls:
                    elapsed = time.time() - start_time
                    answer = clean_answer(last_content) or "I could not find an answer."
                    log.info(f"QUERY DONE: \"{new_question}\" -> \"{answer[:100]}\" (no done call, iters={iteration}, {elapsed:.1f}s)")
                    yield {"type": "done", "answer": answer, "cites": [], "suggestions": [], "iterations": iteration}
                    return

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}

                if name == "done":
                    answer = args.get("answer", content or last_content or "No answer provided.")
                    cites = args.get("cites", [])
                    suggestions = args.get("suggestions", [])
                    elapsed = time.time() - start_time
                    log.info(f"  iter {iteration}: DONE called ({llm_time:.1f}s)")
                    log.info(f"QUERY DONE: \"{new_question}\" -> \"{answer[:100]}\" (iters={iteration}, cites={len(cites)}, suggestions={len(suggestions)}, {elapsed:.1f}s)")

                    cleaned = clean_answer(answer)
                    yield {"type": "done", "answer": cleaned, "cites": cites, "suggestions": suggestions,
                           "iterations": iteration, "est_input_tokens": total_input_tokens,
                           "est_output_tokens": total_output_tokens}
                    return

                # --- If forced_done, reject any tool that isn't 'done' ---
                if forced_done and name != "done":
                    log.info(f"  iter {iteration}: rejecting {name} — forced_done mode ({llm_time:.1f}s)")
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": "You must call done now. The only available tool is done. Call it with your answer."})
                    continue

                # --- Redirect browsing tools to fts_search early on ---
                if name in ("ls", "list_index") and iteration <= 3:
                    log.info(f"  iter {iteration}: redirect {name} -> fts_search ({llm_time:.1f}s)")
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "tool", "name": name, "content": f"Don't browse. Use fts_search to find relevant sections directly. For example: fts_search with a query about the user's question."})
                    continue

                # --- Dedup checks ---
                if name == "read_file":
                    fpath = args.get("path", "")
                    if fpath in files_read:
                        dedup_read_count += 1
                        log.info(f"  iter {iteration}: DEDUP skip read_file({fpath}) — already read ({llm_time:.1f}s)")
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "tool", "name": name, "content": f"Already read. You have this file's content above — use it to answer. Do not read it again."})
                        # After 2 dedup blocks on read_file, force done — model is stuck
                        if dedup_read_count >= 2 and not forced_done:
                            forced_done = True
                            messages.append({"role": "user", "content": "You already have the information from your files. Call done NOW with your answer based on what you've already read. Do not try to read any more files."})
                            log.info(f"  iter {iteration}: forcing done-only tools (repeated read attempts)")
                        elif not forced_done and iteration >= 6:
                            forced_done = True
                            messages.append({"role": "user", "content": "You already have the information. Call done now with your answer."})
                        continue
                    files_read.add(fpath)

                if name == "fts_search":
                    q = args.get("query", "").strip().lower()
                    if q in searches_done:
                        log.info(f"  iter {iteration}: DEDUP skip fts_search({q}) — already searched ({llm_time:.1f}s)")
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "tool", "name": name, "content": f"Already searched for \"{q}\". Try a different query or call done."})
                        if not forced_done and iteration >= 6:
                            forced_done = True
                            messages.append({"role": "user", "content": "You have searched enough. Call done now with your answer."})
                        continue
                    searches_done.add(q)

                # Emit thinking event
                if name == "fts_search":
                    yield {"type": "thinking", "message": f"Searching for: {args.get('query', '')}"}
                elif name == "read_file":
                    fname = args.get("path", "").split("/")[-1].replace("_", " ").replace(".md", "")
                    yield {"type": "thinking", "message": f"Reading: {fname}"}
                elif name == "grep":
                    yield {"type": "thinking", "message": f"Searching for: {args.get('pattern', '')}"}
                elif name == "table_extract":
                    yield {"type": "thinking", "message": "Extracting table data..."}
                elif name == "list_index":
                    yield {"type": "thinking", "message": "Browsing document index..."}

                result = self.toolbox.execute(name, args)
                result_preview = result[:120].replace("\n", " ")
                log.info(f"  iter {iteration}: {name}({json.dumps(args)[:100]}) -> {result_preview}... ({llm_time:.1f}s)")
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "tool", "name": name, "content": result})

                # After iteration 6, nudge; after 8, force done
                if iteration >= 8 and not forced_done:
                    forced_done = True
                    messages.append({"role": "user", "content": "You have enough information. Call done now with your answer and citations."})
                    log.info(f"  iter {iteration}: forcing done-only tools")
                elif iteration >= 6 and not forced_done:
                    messages.append({"role": "user", "content": "You have searched enough. Please call the done tool now with your answer based on what you've found. If you didn't find the answer, say so."})
                    log.info(f"  iter {iteration}: nudging model to call done")

        elapsed = time.time() - start_time
        log.warning(f"QUERY BUDGET EXHAUSTED: \"{new_question}\" (iters={self.max_iterations}, {elapsed:.1f}s)")
        fallback = _synthesize_answer(messages, new_question)
        cites = _extract_cites_from_history(messages)
        yield {"type": "done", "answer": fallback, "cites": cites, "suggestions": [],
               "iterations": self.max_iterations,
               "est_input_tokens": total_input_tokens,
               "est_output_tokens": total_output_tokens}