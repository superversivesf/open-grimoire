import re
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
    fm = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            elif val.startswith("[") and val.endswith("]"):
                val = [x.strip().strip('"') for x in val[1:-1].split(",") if x.strip()]
            elif val.isdigit():
                val = int(val)
            fm[key.strip()] = val
    return fm, body


def _flatten_table_line(line: str) -> str:
    cells = [c.strip() for c in line.strip("|").split("|")]
    return " ".join(cells)


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