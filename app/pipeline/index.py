import re
import yaml
from pathlib import Path
from app.storage.user_db import insert_fts_row, delete_fts_rows_for_doc


def parse_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + 5:]
    fm = yaml.safe_load(fm_text) or {}
    return fm, body


def _clean_content(body: str) -> str:
    out = []
    for line in body.splitlines():
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue  # separator row
            out.append(" ".join(cells))
        else:
            out.append(line)
    return "\n".join(out)


def index_document(conn, leaf_paths: list[str], data_dir: Path, doc_id: str) -> None:
    delete_fts_rows_for_doc(conn, doc_id)
    for rel in leaf_paths:
        full = data_dir / rel
        fm, body = parse_frontmatter(full)
        title_match = re.search(r"^# (.+)$", body, re.MULTILINE)
        title = title_match.group(1) if title_match else full.stem
        summary = fm.get("summary", "")
        keywords = fm.get("keywords", "")
        if isinstance(keywords, list):
            keywords = ", ".join(keywords)
        insert_fts_row(conn, rel, title, str(summary), str(keywords), _clean_content(body))