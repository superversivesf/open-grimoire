import re


class Structurer:
    HEADING_PATTERNS = [
        re.compile(r"^(Chapter\s+\d+.*)$", re.IGNORECASE),
        re.compile(r"^(\d+\.\d+\s+.+)$"),
        re.compile(r"^(\d+\.\s+.+)$"),
        re.compile(r"^([A-Z][A-Z\s]{4,})$"),
    ]

    def __init__(self, gateway=None):
        self.gateway = gateway

    def detect(self, blocks: list[dict]) -> list[dict]:
        flat = self._scan_headings(blocks)
        if not flat:
            return [self._fallback_chapter(blocks)]
        return self._build_tree(flat, blocks)

    def _scan_headings(self, blocks: list[dict]) -> list[dict]:
        headings = []
        for b in blocks:
            for line in b["text"].splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                level = self._heading_level(stripped)
                if level:
                    headings.append({"title": stripped, "level": level, "page": b["page"]})
        return headings

    def _heading_level(self, line: str) -> int | None:
        if self.HEADING_PATTERNS[0].match(line):
            return 1
        if self.HEADING_PATTERNS[1].match(line):
            return 2
        if self.HEADING_PATTERNS[2].match(line):
            return 1
        if self.HEADING_PATTERNS[3].match(line):
            return 1
        return None

    def _build_tree(self, headings: list[dict], blocks: list[dict]) -> list[dict]:
        root = []
        stack: list[dict] = []
        for i, h in enumerate(headings):
            node = {"title": h["title"], "level": h["level"], "page_start": h["page"], "page_end": h["page"], "text": "", "children": []}
            next_page = headings[i + 1]["page"] if i + 1 < len(headings) else blocks[-1]["page"]
            node["page_end"] = next_page
            node["text"] = self._collect_text(blocks, h["page"], next_page, h["title"])
            while stack and stack[-1]["level"] >= node["level"]:
                stack.pop()
            if stack:
                stack[-1]["children"].append(node)
            else:
                root.append(node)
            stack.append(node)
        return root

    def _collect_text(self, blocks: list[dict], start_page: int, end_page: int, heading: str) -> str:
        chunks = []
        for b in blocks:
            if start_page <= b["page"] <= end_page:
                chunks.append(b["text"])
        return "\n".join(chunks)

    def _fallback_chapter(self, blocks: list[dict]) -> dict:
        return {
            "title": "Full Document",
            "level": 1,
            "page_start": blocks[0]["page"] if blocks else 1,
            "page_end": blocks[-1]["page"] if blocks else 1,
            "text": "\n".join(b["text"] for b in blocks),
            "children": [],
        }