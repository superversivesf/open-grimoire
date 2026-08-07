import json
import re
from pathlib import Path
from string import Template
from typing import Any, cast


# Template for enrichment prompt. Uses $content placeholder to avoid
# JSON brace escaping confusion with str.format().
ENRICH_PROMPT = Template(
    "You are enriching an RPG rulebook section for search indexing. "
    "Read the section and return ONLY valid JSON, no prose:\n"
    '{"summary": "2-3 sentences covering what this section describes, including key game numbers '
    '(AC, HP, DC, damage dice, costs, levels) when present.", '
    '"keywords": ["5-10 lowercase keywords: proper nouns, rule names, spell/monster/class names, '
    'stat abbreviations (ac, hp), and distinctive jargon"]}\n\n'
    "$content"
)


class Enricher:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    async def enrich_leaf(self, path: Path, page: int | None = None) -> dict[str, Any]:
        content = path.read_text()
        prompt = ENRICH_PROMPT.substitute(content=content)
        resp = await self.gateway.call("enrich", prompt)
        raw = resp.get("message", {}).get("content", "")
        result = self._parse_json(raw)
        self._write_frontmatter(path, content, result, page)
        return result

    async def enrich_all(self, leaf_paths: list[Path], page_map: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for p in leaf_paths:
            page = page_map.get(str(p))
            r = await self.enrich_leaf(p, page)
            results.append(r)
        return results

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        try:
            return cast(dict[str, Any], json.loads(raw))
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return cast(dict[str, Any], json.loads(match.group(0)))
                except json.JSONDecodeError:
                    pass
        return {"summary": "", "keywords": []}

    @staticmethod
    def _write_frontmatter(path: Path, content: str, result: dict[str, Any], page: int | None) -> None:
        summary = result.get("summary", "")
        keywords = result.get("keywords", [])
        kw_yaml = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)
        fm = f"---\nsummary: \"{summary}\"\nkeywords: [{kw_yaml}]\n"
        if page is not None:
            fm += f"page: {page}\n"
        fm += f"---\n\n{content}"
        path.write_text(fm)