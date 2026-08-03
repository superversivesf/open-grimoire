import json
import re
from app.agent.tools_schema import TOOL_DEFINITIONS
from app.agent.history import build_messages, trim_history

SYSTEM_PROMPT = """You are an RPG rules assistant. You search the user's RPG manual collection to answer questions.

Rules:
1. ALWAYS use fts_search first to find relevant sections
2. ALWAYS use read_file to read the full content before answering — never answer from snippets alone
3. Use done to give your final answer with citations
4. If you can't find it after 3 searches, use done and say so

Never answer without first reading a file. The fts_search snippets are too short."""


def clean_answer(text: str) -> str:
    """Remove common citation/markdown artifacts that LLMs leak into answers."""
    if not text:
        return text
    # Remove "Based on the search results..." opener
    text = re.sub(r'^Based on the search results,?\s*', '', text, flags=re.IGNORECASE)
    # Remove citation sections
    text = re.sub(r'###?\s*Citations?:?\s*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n###?\s*Citations?:?\s*\n.*', '', text, flags=re.IGNORECASE | re.DOTALL)
    # Remove individual citation lines
    text = re.sub(r'\n-?\s*\*\*(Page|Path|Quote|Source|Reference)\*\*:?\s*[^\n]+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n-?\s*(Page|Path|Quote|Source|Reference):?\s*[^\n]+', '', text, flags=re.IGNORECASE)
    # Remove bare path references like [Path: abc123/...]
    text = re.sub(r'\[Path:\s*[a-f0-9]+/[^\]]+\]', '', text)
    text = re.sub(r'`[a-f0-9]{32}/[^`]+\.md`', '', text)
    # Remove "If you need more..." filler
    text = re.sub(r'If you need more (detailed )?information[^\?]*\??', '', text, flags=re.IGNORECASE)
    text = re.sub(r'If you.*?like more.*?ask.*?!', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'feel free to ask!?', '', text, flags=re.IGNORECASE)
    # Clean up extra whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _parse_text_tool_call(content: str) -> dict | None:
    """Detect when a model writes a tool call as text instead of using the API.

    e.g. content = 'fts_search {"query": "security clearance"}'
    """
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
        trimmed = trim_history(history, keep_last=6)
        messages = build_messages(trimmed, SYSTEM_PROMPT)
        messages.append({"role": "user", "content": new_question})

        last_content = ""
        for iteration in range(1, self.max_iterations + 1):
            resp = await self.gateway.call("query", "", tools=TOOL_DEFINITIONS, messages=messages)
            msg = resp.get("message", {})
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content", "")
            if content:
                last_content = content

            if not tool_calls:
                # Check if the model put a tool call in the content text instead of the API
                # (common with Qwen 2.5 7B — it writes "fts_search {"query": "..."}" as text)
                if content:
                    parsed_tc = _parse_text_tool_call(content)
                    if parsed_tc:
                        tool_calls = [parsed_tc]
                        content = ""
                    elif len(content) > 10:
                        # Model returned an answer without calling done.
                        # Re-prompt it to call done with this answer + citations.
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": "Good answer. Now call the done tool with that answer in the 'answer' field, and add any source citations you have in the 'cites' field. Use the exact paths from the fts_search results."})
                        continue
                if not tool_calls:
                    return {"answer": clean_answer(last_content) or "I could not find an answer.", "cites": [], "iterations": iteration}

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
                    return {
                        "answer": clean_answer(answer),
                        "cites": args.get("cites", []),
                        "iterations": iteration,
                    }

                result = self.toolbox.execute(name, args)
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "tool", "name": name, "content": result})

                if iteration >= 6 and name != "done":
                    messages.append({"role": "user", "content": "You have searched enough. Please call the done tool now with your answer based on what you've found. If you didn't find the answer, say so."})

        return {
            "answer": clean_answer(last_content) or "I couldn't find a complete answer within my tool-call budget.",
            "cites": [],
            "iterations": self.max_iterations,
        }