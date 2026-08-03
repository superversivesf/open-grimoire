import json
import re
from app.agent.tools_schema import TOOL_DEFINITIONS
from app.agent.history import build_messages, trim_history

SYSTEM_PROMPT = """You are an expert RPG rules assistant. You answer questions about RPG manuals by searching the user's document collection.

SEARCH STRATEGY:
1. Start with fts_search using key terms from the question (e.g. "initiative roll", "wound levels")
2. If no results, try broader or different terms (e.g. "combat" instead of "initiative")
3. Read the top results with read_file to get the actual content
4. You may search up to 3-4 times with different terms if needed

WHEN TO ANSWER:
- After reading 1-2 relevant sections, call done immediately
- Do NOT search more than 4 times — if you haven't found it by then, answer with what you have
- If you found the answer, call done RIGHT AWAY — do not search again "just to be sure"

CITATIONS:
- Do NOT include citation details (page, path, quotes) in your answer text
- Put citations ONLY in the "cites" field of the done tool
- Each citation: path (from fts_search results), page (if known), quote (exact sentence from the manual)
- The UI renders citations as clickable links automatically

ANSWER STYLE:
- Be concise — give the player what they need at the table
- Quote exact rules text when relevant
- If multiple sections are relevant, synthesize from all of them
- If you cannot find the answer, call done and say so honestly"""


def clean_answer(text: str) -> str:
    """Remove common citation/markdown artifacts that LLMs leak into answers."""
    if not text:
        return text
    text = re.sub(r'###\s*Citations?:?\s*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*Page\*\*:?\s*[^\n]+', '', text)
    text = re.sub(r'\*\*Path\*\*:?\s*[^\n]+', '', text)
    text = re.sub(r'\*\*Quote\*\*:?\s*[^\n]+', '', text)
    text = re.sub(r'\*\*Source\*\*:?\s*[^\n]+', '', text)
    text = re.sub(r'\*\*Reference\*\*:?\s*[^\n]+', '', text)
    text = re.sub(r'`[a-f0-9]{32}/[^`]+\.md`', '', text)
    text = re.sub(r'-\s*\*\*(Page|Path|Quote|Source|Reference)\*\*:?\s*[^\n]+\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'Would you like more (detailed )?information[^\?]*\??', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


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
                if content and len(content) > 10:
                    return {"answer": clean_answer(content), "cites": [], "iterations": iteration}
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