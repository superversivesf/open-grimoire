import json
import re
import yaml
from pathlib import Path
from string import Template
from typing import Any, cast

from app.logging_utils import get_logger
from app.constants import ENRICH_OPTIMAL_KEYWORDS

log = get_logger("enrich")

# Max chars of section content to send to the enrich model. Large sections
# (85KB+) overwhelm small models and produce unparsable responses.
ENRICH_MAX_CONTENT_CHARS = 8000


# Template for enrichment prompt. Uses $content placeholder to avoid
# JSON brace escaping confusion with str.format().
ENRICH_PROMPT = Template(
    "You are enriching an RPG rulebook section for search indexing. "
    "Read the section and return ONLY valid JSON, no prose, no markdown fences. "
    'Example of the exact format:\n'
    '{"summary": "Fireball is a 3rd-level evocation spell dealing 8d6 fire damage on a failed save.", '
    '"keywords": ["fireball", "evocation", "8d6", "saving throw", "fire damage"]}\n\n'
    "Rules:\n"
    '- "summary": 2-3 sentences covering what this section describes, including key game numbers '
    '(AC, HP, DC, damage dice, costs, levels) when present.\n'
    '- "keywords": 5-' + str(ENRICH_OPTIMAL_KEYWORDS) + ' lowercase keywords or short phrases (e.g. "spell slot", "saving throw"): '
    'proper nouns, rule names, spell/monster/class names, stat abbreviations (ac, hp), and distinctive jargon.\n'
    '- Exclude common words (the, and, of, a, or) from keywords.\n'
    '- Use canonical lowercase forms.\n\n'
    "$content"
)


class Enricher:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    async def enrich_leaf(self, path: Path, page: int | None = None) -> dict[str, Any]:
        content = path.read_text()
        # Truncate to keep the prompt within the model's context window.
        # Large sections (85KB+) overwhelm small enrich models.
        truncated = content[:ENRICH_MAX_CONTENT_CHARS]
        if len(content) > ENRICH_MAX_CONTENT_CHARS:
            truncated += "\n\n[... section truncated for length ...]"
        result: dict[str, Any] = {}
        for attempt in range(3):
            prompt = ENRICH_PROMPT.substitute(content=truncated)
            if attempt == 1:
                prompt += "\n\nIMPORTANT: Your previous response could not be parsed. Return ONLY the JSON object with a summary and at least 5 keywords."
            elif attempt == 2:
                # Last-ditch: accept just a summary, no keywords required.
                prompt += "\n\nIMPORTANT: Return ONLY a JSON object: {\"summary\": \"2-3 sentence summary\", \"keywords\": []}. Even if you cannot identify keywords, provide the summary."
            resp = await self.gateway.call("enrich", prompt)
            raw = resp.get("message", {}).get("content", "")
            result = self._parse_json(raw)
            if result.get("keywords") or (attempt == 2 and result.get("summary")):
                break
        if result.get("summary") or result.get("keywords"):
            self._write_frontmatter(path, content, result, page)
        else:
            log.warning(f"enrich FAILED for {path.name}: unparsable response (raw[:200]={raw[:200]!r}), no frontmatter written")
        return result

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return cast(dict[str, Any], parsed)
        except json.JSONDecodeError:
            pass
        start = raw.find("{")
        if start == -1:
            return {"summary": "", "keywords": []}
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(raw[start:], start=start):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(raw[start:i + 1])
                        if isinstance(parsed, dict):
                            return cast(dict[str, Any], parsed)
                    except json.JSONDecodeError:
                        break
        return {"summary": "", "keywords": []}

    @staticmethod
    def _write_frontmatter(path: Path, content: str, result: dict[str, Any], page: int | None) -> None:
        summary = result.get("summary", "")
        keywords = result.get("keywords", [])
        fm: dict[str, Any] = {
            "summary": str(summary),
            "keywords": keywords if isinstance(keywords, list) else [],
        }
        if page is not None:
            fm["page"] = page
        block = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(f"---\n{block}\n---\n\n{content}")
        tmp.replace(path)