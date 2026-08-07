from pathlib import Path
import re
from typing import Any


def slugify(title: str, order: int = 0) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s]", "", title).strip().lower().replace(" ", "_")
    s = re.sub(r"_+", "_", s).strip("_")
    prefix = f"{order:02d}_"
    return prefix + s if order else s


def tier_document(tree: list[dict[str, Any]], data_dir: Path, doc_id: str, doc_title: str) -> list[str]:
    doc_dir = data_dir / doc_id
    doc_dir.mkdir(parents=True, exist_ok=True)
    leaves = []

    def write_node(node: dict[str, Any], parent_dir: Path, order: int) -> None:
        slug = slugify(node["title"], order)
        if node["children"]:
            chap_dir = parent_dir / slug
            chap_dir.mkdir(exist_ok=True)
            chap_index_lines = [f"# {node['title']}\n"]
            for i, child in enumerate(node["children"], start=1):
                child_slug = slugify(child["title"], i)
                if child["children"]:
                    write_node(child, chap_dir, i)
                else:
                    leaf_path = chap_dir / f"{child_slug}.md"
                    leaf_path.write_text(f"# {child['title']}\n\n{child['text']}\n")
                    leaves.append(str(leaf_path.relative_to(data_dir)))
                chap_index_lines.append(f"- [{child['title']}]({child_slug}.md)\n")
            (chap_dir / "index.md").write_text("".join(chap_index_lines))
        else:
            leaf_path = parent_dir / f"{slug}.md"
            leaf_path.write_text(f"# {node['title']}\n\n{node['text']}\n")
            leaves.append(str(leaf_path.relative_to(data_dir)))

    doc_index_lines = [f"# {doc_title}\n\n"]
    for i, node in enumerate(tree, start=1):
        write_node(node, doc_dir, i)
        slug = slugify(node["title"], i)
        doc_index_lines.append(f"- [{node['title']}]({slug}/index.md)\n")
    (doc_dir / "index.md").write_text("".join(doc_index_lines))
    return leaves