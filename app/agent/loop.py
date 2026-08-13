import json
import re
import time
import typing
from enum import Enum
from typing import Any
from app.agent.tools_schema import TOOL_DEFINITIONS, FORCED_DONE_TOOLS
from app.agent.history import build_messages, trim_history
from app.usage.tokens import estimate_messages_tokens, estimate_response_tokens
from app.logging_utils import get_logger
from app.constants import DEFAULT_MAX_ITERATIONS
log = get_logger("agent")

SYSTEM_PROMPT = """You are an RPG rules assistant. You search the user's RPG manual collection (one or more books) to answer questions.

Search strategy:
1. ALWAYS start with fts_search — never browse with ls or list_index.
2. Query with ONE distinctive keyword first — the most specific noun (e.g. "goblin", "sorcerer", "grapple"). If results are too broad or irrelevant, try a more specific keyword (e.g. "goblin ac").
3. Read the top result with read_file before answering — snippets are too short to answer from.
4. Each fts_search result reports match_mode: "and" (tight), "or" or "prefix" (loose). If results are loose, prefer the top-ranked hit but verify it with read_file; if nothing looks relevant, try a different keyword (2-3 attempts max) or use grep with a regex (e.g. grep "advantage" for every mention).
5. NEVER read the same file twice.
6. NEVER read index.md files — they are navigation only.

When calling done, always include 3 "suggestions" — short follow-up questions a player might ask next based on what they just learned."""


class AgentState(Enum):
    SEARCHING = "searching"
    READING = "reading"
    SYNTHESIZING = "synthesizing"
    DONE = "done"


# Max iterations per state
STATE_MAX_ITERATIONS = {
    AgentState.SEARCHING: 5,
    AgentState.READING: 5,
    AgentState.SYNTHESIZING: 3,
}

# Tools allowed in each state
STATE_TOOLS = {
    AgentState.SEARCHING: TOOL_DEFINITIONS,  # all tools
    AgentState.READING: [t for t in TOOL_DEFINITIONS if t["function"]["name"] in ("fts_search", "read_file", "grep", "table_extract", "calc", "ls", "done")],
    AgentState.SYNTHESIZING: FORCED_DONE_TOOLS,
    AgentState.DONE: FORCED_DONE_TOOLS,
}


def clean_answer(text: str) -> str:
    """Strip citation-format boilerplate from answers.

    Only matches the project's citation formats — bracket-wrapped paths,
    backtick-wrapped paths, bold metadata lines, and citation section
    headers.  Does NOT strip conversational text that could be part of a
    legitimate answer.
    """
    if not text:
        return text
    # Citation section headers: "### Citations:" / "## Citations"
    text = re.sub(r'###?\s*Citations?:?\s*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n###?\s*Citations?:?\s*\n.*', '', text, flags=re.IGNORECASE | re.DOTALL)
    # Bold citation metadata lines: "**Page**: 42", "**Path**: ...", etc.
    text = re.sub(r'\n-?\s*\*\*(Page|Path|Quote|Source|Reference)\*\*:\s*[^\n]+', '', text, flags=re.IGNORECASE)
    # Plain citation metadata lines: "Page: 42", "Path: ...", etc.
    text = re.sub(r'\n-?\s*(Page|Path|Quote|Source|Reference):\s*[^\n]+', '', text, flags=re.IGNORECASE)
    # Bracket-wrapped path citations: [Path: abc123/some_file.md]
    text = re.sub(r'\[Path:\s*[a-f0-9]+/[^\]]+\]', '', text)
    # Backtick-wrapped path citations: `abc123.../some_file.md`
    text = re.sub(r'`[a-f0-9]{32}/[^`]+\.md`', '', text)
    # Collapse runs of blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _parse_text_tool_call(content: str) -> dict[str, Any] | None:
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


def _extract_cites_from_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan tool results in message history for file paths that could serve as citations."""
    cites = []
    seen_paths = set()
    pending_read_path = None
    for msg in messages:
        if msg.get("role") == "assistant":
            parsed = _parse_text_tool_call(msg.get("content", ""))
            pending_read_path = None
            if parsed and parsed["function"]["name"] == "read_file":
                pending_read_path = parsed["function"].get("arguments", {}).get("path")
            for tc in msg.get("tool_calls") or []:
                if tc.get("function", {}).get("name") == "read_file":
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        pending_read_path = args.get("path")
                    except (json.JSONDecodeError, TypeError):
                        pass
        elif msg.get("role") == "tool":
            tool_name = msg.get("name", "")
            content = msg.get("content", "")
            # fts_search results
            if tool_name == "fts_search":
                try:
                    results = json.loads(content)
                    for r in results[:3]:
                        path = r.get("path", "")
                        if path and path not in seen_paths:
                            seen_paths.add(path)
                            cites.append({"path": path, "quote": r.get("snippet", "")[:200]})
                except (json.JSONDecodeError, TypeError):
                    pass
            # grep results - scan for file paths in the output
            elif tool_name == "grep":
                # grep returns lines like "path/to/file.md:line_num:matching_text"
                for line in content.split("\n"):
                    if ":" in line:
                        parts = line.split(":", 1)
                        if parts:
                            potential_path = parts[0]
                            if potential_path.endswith(".md") and potential_path not in seen_paths:
                                seen_paths.add(potential_path)
                                cites.append({"path": potential_path, "quote": line[:200]})
            # read_file results - pair with the path from the preceding assistant call
            elif tool_name == "read_file":
                if pending_read_path and pending_read_path not in seen_paths:
                    seen_paths.add(pending_read_path)
                    cites.append({"path": pending_read_path, "quote": content[:200]})
                pending_read_path = None
    return cites[:5]


def _synthesize_answer(messages: list[dict[str, Any]], question: str) -> str:
    """Build a fallback answer from tool results when the model didn't call done."""
    # Collect FTS search results
    fts_results = []
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("name") == "fts_search":
            try:
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
        question_words = set(w.lower() for w in question.split() if len(w) >= 2)
        best_snippet = ""
        best_score = 0
        for content in file_contents:
            if content.startswith("---"):
                end = content.find("\n---\n", 3)
                if end != -1:
                    content = content[end + 5:].strip()
            for para in content.split("\n\n"):
                para_clean = para.strip()
                if len(para_clean) < 20:
                    continue
                if para_clean.startswith("#"):
                    continue
                if para_clean.startswith("```"):
                    para_clean = para_clean.strip("`").strip()
                    if len(para_clean) < 20:
                        continue
                if para_clean.startswith("|") and "|" in para_clean[1:]:
                    cells = [c.strip() for c in para_clean.strip("|").split("|")]
                    if all(re.match(r"^[-:]+$", c) for c in cells):
                        continue
                    para_clean = " | ".join(cells)
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
    def __init__(self, gateway: Any, toolbox: Any, max_iterations: int = DEFAULT_MAX_ITERATIONS) -> None:
        self.gateway = gateway
        self.toolbox = toolbox
        self.max_iterations = max_iterations

    async def run(self, history: list[dict[str, Any]], new_question: str) -> dict[str, Any]:
        """Non-streaming run — returns the full result at once.

        Uses stream_prose=False so prose answers don't incur a redundant
        streamed gateway call — the non-streaming path has no live UI.
        """
        result = None
        async for event in self.run_stream(history, new_question, stream_prose=False):
            if event["type"] == "done":
                result = event
                break
            elif event["type"] == "budget_exhausted":
                # Non-streaming caller has no UI to offer a continue — return
                # the synthesized fallback answer, as before.
                result = {
                    "type": "done",
                    "answer": event.get("fallback_answer") or "I could not find an answer.",
                    "cites": event.get("fallback_cites", []),
                    "suggestions": [],
                    "iterations": event.get("iterations", self.max_iterations),
                    "done_called": False,
                    "est_input_tokens": event.get("est_input_tokens", 0),
                    "est_output_tokens": event.get("est_output_tokens", 0),
                }
                break
            elif event["type"] == "error":
                result = event
                break
        if result is None:
            result = {"type": "done", "answer": "I could not find an answer.", "cites": [], "suggestions": [], "iterations": self.max_iterations, "done_called": False}
        elif result.get("type") == "error":
            # run_stream surfaced a mid-run failure (LLM/tool error) as an
            # error event — convert it into a graceful fallback so the
            # non-streaming caller gets an answer instead of a crash.
            result = {
                "type": "done",
                "answer": f"Sorry, the AI service is unavailable right now. ({result.get('message', 'unknown error')})",
                "cites": [],
                "suggestions": [],
                "iterations": 0,
                "done_called": False,
            }
        return {
            "answer": result.get("answer", ""),
            "cites": result.get("cites", []),
            "suggestions": result.get("suggestions", []),
            "iterations": result.get("iterations", 0),
            "done_called": result.get("done_called", False),
            "est_input_tokens": result.get("est_input_tokens", 0),
            "est_output_tokens": result.get("est_output_tokens", 0),
        }

    async def run_stream(self, history: list[dict[str, Any]], new_question: str,
                         stream_prose: bool = True,
                         nudge: str | None = None) -> typing.AsyncGenerator[dict[str, Any], None]:
        """Streaming run — yields events as they happen.

        Event types:
          {"type": "thinking", "message": "..."} — status update (searching, reading)
          {"type": "token", "content": "..."} — answer token (streamed)
          {"type": "done", "answer": "...", "cites": [...], "suggestions": [...], "iterations": N}
          {"type": "budget_exhausted", "steps": [...], "iterations": N,
           "fallback_answer": "...", "fallback_cites": [...]}
          {"type": "error", "message": "..."}

        stream_prose=False keeps the legacy non-streaming path (no redundant
        streamed gateway call for the final prose turn).
        """
        start_time = time.time()
        trimmed = trim_history(history, keep_last=6)
        messages = build_messages(trimmed, SYSTEM_PROMPT)
        messages.append({"role": "user", "content": new_question})
        if nudge:
            messages.append({"role": "user", "content": nudge})

        log.info(f"QUERY START: \"{new_question}\" (history={len(history)} turns)")

        last_content = ""
        files_read: set[str] = set()
        file_cache: dict[str, str] = {}
        searches_done: set[str] = set()
        total_input_tokens = 0
        total_output_tokens = 0

        state = AgentState.SEARCHING
        state_iterations = 0
        searches_in_state = 0
        reads_in_state = 0
        total_iterations = 0
        nudge_given = False
        consecutive_dedups = 0
        step_log: list[dict[str, Any]] = []

        while total_iterations < self.max_iterations:
            total_iterations += 1
            state_iterations += 1
            t0 = time.time()

            tools = STATE_TOOLS[state]
            total_input_tokens += estimate_messages_tokens(messages)
            try:
                resp = await self.gateway.call("query", "", tools=tools, messages=messages)
            except Exception as e:
                log.error(f"QUERY ERROR: \"{new_question}\": {e}", exc_info=True)
                yield {"type": "error", "message": str(e)}
                return
            llm_time = time.time() - t0
            msg = resp.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content", "")

            if content:
                total_output_tokens += estimate_response_tokens(content)
                last_content = content

            if not tool_calls:
                if content:
                    parsed_tc = _parse_text_tool_call(content)
                    if parsed_tc:
                        tool_calls = [parsed_tc]
                        content = ""
                        log.info(f"  iter {total_iterations}: parsed text tool call -> {parsed_tc['function']['name']} ({llm_time:.1f}s)")
                    elif len(content) > 10:
                        if not stream_prose:
                            # Non-streaming path: legacy behavior — keep the
                            # prose and re-prompt for done without an extra call.
                            log.info(f"  iter {total_iterations}: content without tool calls, re-prompting for done ({llm_time:.1f}s)")
                            messages.append({"role": "assistant", "content": content})
                            messages.append({"role": "user", "content": "Good answer. Now call the done tool with that answer in the 'answer' field, add any source citations you have in the 'cites' field, and include 3 follow-up questions in the 'suggestions' field. Use the exact paths from the fts_search results."})
                            continue
                        # The model is writing prose: stream the answer tokens
                        # live, then re-prompt for the done tool call.
                        log.info(f"  iter {total_iterations}: streaming prose answer ({llm_time:.1f}s)")
                        stream_messages = messages + [{"role": "assistant", "content": content}]
                        streamed = ""
                        stream_tool_calls: list[dict[str, Any]] = []
                        try:
                            async for ev in self.gateway.stream("query", "", tools=tools, messages=stream_messages):
                                if ev["type"] == "content":
                                    streamed += ev["text"]
                                    yield {"type": "token", "content": ev["text"]}
                                elif ev["type"] == "tool_calls":
                                    stream_tool_calls = ev["calls"]
                        except Exception as e:
                            log.error(f"QUERY STREAM ERROR: {e}", exc_info=True)
                            yield {"type": "error", "message": str(e)}
                            return
                        if stream_tool_calls:
                            # The streamed response produced tool calls instead
                            # of prose — keep the assistant turn in context so
                            # the model doesn't lose what it just wrote.
                            messages.append({"role": "assistant", "content": streamed or content})
                            tool_calls = stream_tool_calls
                            content = ""
                        else:
                            messages.append({"role": "assistant", "content": streamed or content})
                            messages.append({"role": "user", "content": "Good answer. Now call the done tool with that answer in the 'answer' field, add any source citations you have in the 'cites' field, and include 3 follow-up questions in the 'suggestions' field. Use the exact paths from the fts_search results."})
                            last_content = streamed or content
                            continue
                if not tool_calls:
                    elapsed = time.time() - start_time
                    answer = clean_answer(last_content) or "I could not find an answer."
                    log.info(f"QUERY DONE: \"{new_question}\" -> \"{answer[:100]}\" (no done call, iters={total_iterations}, {elapsed:.1f}s)")
                    yield {"type": "done", "answer": answer, "cites": [], "suggestions": [], "iterations": total_iterations, "done_called": False}
                    return

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if isinstance(args_str, str) else args_str
                except json.JSONDecodeError:
                    args = {}
                step_log.append({"tool": name, "args": {k: v for k, v in args.items() if k != "password"}})
                if len(step_log) > 8:
                    step_log.pop(0)

                # Server-side state enforcement: in SYNTHESIZING/DONE only the
                # done tool may run. The model may ignore the tool list we send,
                # so reject anything else outright instead of executing it.
                if state in (AgentState.SYNTHESIZING, AgentState.DONE) and name != "done":
                    log.info(f"  iter {total_iterations}: BLOCKED {name} in {state.value} state — only done allowed")
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "tool", "name": name, "content": "Tool not allowed in this state. Call the done tool now with your answer and citations."})
                    continue

                if name == "done":
                    answer = args.get("answer", content or last_content or "No answer provided.")
                    cites = args.get("cites", [])
                    suggestions = args.get("suggestions", [])
                    elapsed = time.time() - start_time
                    log.info(f"  iter {total_iterations}: DONE called ({llm_time:.1f}s)")
                    log.info(f"QUERY DONE: \"{new_question}\" -> \"{answer[:100]}\" (iters={total_iterations}, cites={len(cites)}, suggestions={len(suggestions)}, {elapsed:.1f}s)")

                    cleaned = clean_answer(answer)
                    yield {"type": "done", "answer": cleaned, "cites": cites, "suggestions": suggestions,
                           "iterations": total_iterations, "done_called": True,
                           "est_input_tokens": total_input_tokens,
                           "est_output_tokens": total_output_tokens}
                    return

                # Dedup checks
                if name == "read_file":
                    fpath = args.get("path", "")
                    if fpath in files_read:
                        consecutive_dedups += 1
                        # Replay the cached content so the model can't claim it
                        # lacks the file — it already has this in context.
                        cached = file_cache.get(fpath, "")
                        if cached:
                            log.info(f"  iter {total_iterations}: REPLAY read_file({fpath}) from cache ({len(cached)} chars)")
                            messages.append({"role": "assistant", "content": content})
                            messages.append({"role": "tool", "name": name, "content": cached})
                        else:
                            log.info(f"  iter {total_iterations}: DEDUP skip read_file({fpath}) — already read ({llm_time:.1f}s)")
                            messages.append({"role": "assistant", "content": content})
                            messages.append({"role": "tool", "name": name, "content": f"Already read this file. You have its content above. If you need more information, try reading a DIFFERENT file from your fts_search results, or call done with what you know."})
                        if consecutive_dedups >= 2:
                            log.info(f"  iter {total_iterations}: repeated dedup skips ({consecutive_dedups}), forcing SYNTHESIZING")
                            state = AgentState.SYNTHESIZING
                            state_iterations = 0
                            searches_in_state = 0
                            reads_in_state = 0
                        continue
                    consecutive_dedups = 0
                    files_read.add(fpath)

                if name == "fts_search":
                    q = args.get("query", "").strip().lower()
                    if q in searches_done:
                        consecutive_dedups += 1
                        log.info(f"  iter {total_iterations}: DEDUP skip fts_search({q}) — already searched ({llm_time:.1f}s)")
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "tool", "name": name, "content": f"Already searched for \"{q}\". Try a different query or call done."})
                        if consecutive_dedups >= 2:
                            log.info(f"  iter {total_iterations}: repeated dedup skips ({consecutive_dedups}), forcing SYNTHESIZING")
                            state = AgentState.SYNTHESIZING
                            state_iterations = 0
                            searches_in_state = 0
                            reads_in_state = 0
                        continue
                    consecutive_dedups = 0
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

                try:
                    result = self.toolbox.execute(name, args)
                except Exception as e:
                    log.error(f"QUERY ERROR (tool {name}): {e}", exc_info=True)
                    yield {"type": "error", "message": str(e)}
                    return
                if name == "read_file":
                    file_cache[args.get("path", "")] = result
                result_preview = result[:120].replace("\n", " ")
                log.info(f"  iter {total_iterations}: {name}({json.dumps(args)[:100]}) -> {result_preview}... ({llm_time:.1f}s)")
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "tool", "name": name, "content": result})

                # State transitions based on tool used
                if name in ("fts_search", "grep", "list_index", "ls"):
                    if state != AgentState.SEARCHING:
                        state = AgentState.SEARCHING
                        state_iterations = 0
                        searches_in_state = 0
                        reads_in_state = 0
                    searches_in_state += 1
                elif name == "read_file":
                    if state != AgentState.READING:
                        state = AgentState.READING
                        state_iterations = 0
                        searches_in_state = 0
                        reads_in_state = 0
                    reads_in_state += 1
                elif name == "table_extract":
                    if state != AgentState.READING:
                        state = AgentState.READING
                        state_iterations = 0
                        searches_in_state = 0
                        reads_in_state = 0
                    reads_in_state += 1

            # Check state iteration limits and transition
            max_state_iter = STATE_MAX_ITERATIONS.get(state, 3)
            if state_iterations >= max_state_iter or (
                state == AgentState.SEARCHING and searches_in_state >= STATE_MAX_ITERATIONS[AgentState.SEARCHING]
            ) or (
                state == AgentState.READING and reads_in_state >= STATE_MAX_ITERATIONS[AgentState.READING]
            ):
                if state == AgentState.SEARCHING:
                    state = AgentState.READING
                    state_iterations = 0
                    searches_in_state = 0
                    reads_in_state = 0
                    if not nudge_given:
                        messages.append({"role": "user", "content": "You have searched enough. Please read the most relevant file from your search results, then call done with your answer."})
                        nudge_given = True
                        log.info(f"  iter {total_iterations}: transitioning to READING state")
                elif state == AgentState.READING:
                    state = AgentState.SYNTHESIZING
                    state_iterations = 0
                    searches_in_state = 0
                    reads_in_state = 0
                    messages.append({"role": "user", "content": "You have read enough. Call the done tool now with your answer and citations. Cite the exact paths from your fts_search results."})
                    log.info(f"  iter {total_iterations}: transitioning to SYNTHESIZING state")
                elif state == AgentState.SYNTHESIZING:
                    state = AgentState.DONE
                    # Force fallback answer
                    break

        # Budget exhausted — hand control back to the user with the recent steps
        elapsed = time.time() - start_time
        log.warning(f"QUERY BUDGET EXHAUSTED: \"{new_question}\" (iters={total_iterations}, {elapsed:.1f}s)")
        fallback = _synthesize_answer(messages, new_question)
        cites = _extract_cites_from_history(messages)
        yield {"type": "budget_exhausted", "steps": step_log, "iterations": total_iterations,
               "fallback_answer": fallback, "fallback_cites": cites,
               "est_input_tokens": total_input_tokens, "est_output_tokens": total_output_tokens}