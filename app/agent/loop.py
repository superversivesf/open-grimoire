import json
from app.agent.tools_schema import TOOL_DEFINITIONS
from app.agent.history import build_messages, trim_history

SYSTEM_PROMPT = (
    "You are a helpful RPG rules assistant. You answer questions about RPG manuals "
    "by searching the user's document collection. Use fts_search first to find relevant "
    "sections, then read_file to get details. Use grep for cross-references, table_extract "
    "for stat blocks, and calc for dice math. When you have enough information, call the "
    "done tool with your answer and citations. If you cannot find the answer, call done "
    "with an honest message saying so."
)


class AgentLoop:
    def __init__(self, gateway, toolbox, max_iterations: int = 8):
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
                if content and len(content) > 10:
                    return {"answer": content, "cites": [], "iterations": iteration}
                return {"answer": last_content or "I could not find an answer.", "cites": [], "iterations": iteration}

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
                        "answer": answer,
                        "cites": args.get("cites", []),
                        "iterations": iteration,
                    }

                result = self.toolbox.execute(name, args)
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "tool", "name": name, "content": result})

        return {
            "answer": last_content or "I couldn't find a complete answer within my tool-call budget.",
            "cites": [],
            "iterations": self.max_iterations,
        }