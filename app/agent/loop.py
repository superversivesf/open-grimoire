import json
import re
import time
from app.agent.tools_schema import TOOL_DEFINITIONS
from app.agent.history import build_messages, trim_history
from app.logging_utils import get_logger

log = get_logger("agent")

SYSTEM_PROMPT = """You are an RPG rules assistant. You search the user's RPG manual collection to answer questions.

Rules:
1. ALWAYS use fts_search first to find relevant sections
2. ALWAYS use read_file to read the full content before answering — never answer from snippets alone
3. Use done to give your final answer with citations and 3 suggested follow-up questions
4. If you can't find it after 3 searches, use done and say so

Never answer without first reading a file. The fts_search snippets are too short.

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


class AgentLoop:
    def __init__(self, gateway, toolbox, max_iterations: int = 12):
        self.gateway = gateway
        self.toolbox = toolbox
        self.max_iterations = max_iterations

    async def run(self, history: list[dict], new_question: str) -> dict:
        start_time = time.time()
        trimmed = trim_history(history, keep_last=6)
        messages = build_messages(trimmed, SYSTEM_PROMPT)
        messages.append({"role": "user", "content": new_question})

        log.info(f"QUERY START: \"{new_question}\" (history={len(history)} turns)")

        last_content = ""
        for iteration in range(1, self.max_iterations + 1):
            t0 = time.time()
            resp = await self.gateway.call("query", "", tools=TOOL_DEFINITIONS, messages=messages)
            llm_time = time.time() - t0
            msg = resp.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content", "")
            if content:
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
                    return {"answer": answer, "cites": [], "suggestions": [], "iterations": iteration}

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
                    for c in cites:
                        cite_path = c.get("path", "").split("/")[-1]
                        log.info(f"  cite: {cite_path} p.{c.get('page','?')} \"{c.get('quote','')[:60]}\"")
                    for s in suggestions:
                        log.info(f"  suggest: {s}")
                    return {
                        "answer": clean_answer(answer),
                        "cites": cites,
                        "suggestions": suggestions,
                        "iterations": iteration,
                    }

                result = self.toolbox.execute(name, args)
                result_preview = result[:120].replace("\n", " ")
                log.info(f"  iter {iteration}: {name}({json.dumps(args)[:100]}) -> {result_preview}... ({llm_time:.1f}s)")
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "tool", "name": name, "content": result})

                if iteration >= 6 and name != "done":
                    log.info(f"  iter {iteration}: nudging model to call done")
                    messages.append({"role": "user", "content": "You have searched enough. Please call the done tool now with your answer based on what you've found. If you didn't find the answer, say so."})

        elapsed = time.time() - start_time
        log.warning(f"QUERY BUDGET EXHAUSTED: \"{new_question}\" (iters={self.max_iterations}, {elapsed:.1f}s)")
        return {
            "answer": clean_answer(last_content) or "I couldn't find a complete answer within my tool-call budget.",
            "cites": [],
            "suggestions": [],
            "iterations": self.max_iterations,
        }