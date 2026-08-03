import json
import re
from app.agent.tools_schema import TOOL_DEFINITIONS
from app.agent.history import build_messages, trim_history

SYSTEM_PROMPT = """You are an expert RPG rules assistant. You answer questions about RPG manuals by searching the user's document collection.

SEARCH STRATEGY:
1. Start with fts_search using key terms from the question. Try the EXACT words the user used first.
2. If no good results, try synonyms or related terms (e.g. "clearance" instead of "level", "armor class" instead of "AC")
3. fts_search returns snippets — read the snippet to see if it's relevant BEFORE reading the full file
4. After finding a relevant path, use read_file to read the full content of that section
5. You may search up to 4 times with different terms if the first search doesn't find what you need

WHEN TO ANSWER:
- After reading 1-2 relevant sections, call done IMMEDIATELY with your answer
- Do NOT search again after you've read a section that answers the question
- If you searched 4 times and found nothing relevant, call done and say you couldn't find it
- Do NOT give up after one search — try different terms at least 2-3 times

CITATIONS:
- Do NOT include citation details (page, path, quotes) in your answer text
- Do NOT include a "Citations" section in your answer text
- Do NOT include phrases like "Based on the search results" in your answer
- Put citations ONLY in the "cites" field of the done tool
- Each citation: path (from fts_search results), page (if known), quote (exact sentence from the manual)
- The UI renders citations as clickable links automatically

ANSWER STYLE:
- Answer directly — start with the answer, not with "Based on the search results..."
- Be concise — give the player what they need at the table
- Quote exact rules text when relevant
- If multiple sections are relevant, synthesize from all of them
- If you cannot find the answer, call done and say so honestly"""


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