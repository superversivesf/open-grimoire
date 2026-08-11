"""Re-enrich all leaves of all docs for all users with the current ENRICH_PROMPT.

Preserves existing frontmatter `page` values, writes fresh summary/keywords,
then reindexes FTS for each doc (delete-before-insert).

Usage:
    DEV_MODE=1 .venv/bin/python scripts/re_enrich.py [config.yaml]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import load_config
from app.gateway.ollama import OllamaGateway
from app.logging_utils import get_logger
from app.pipeline.enrich import Enricher
from app.pipeline.index import index_document
from app.storage.paths import user_data_dir
from app.storage.shared_db import init_shared_db, list_users
from app.storage.user_db import add_enrich_completed_path, clear_enrich_completed_paths, init_user_db, list_collections, list_docs

log = get_logger("re-enrich")


async def reenrich_doc(enricher: Enricher, uconn, udata: Path, doc_id: str, page_map: dict[Path, int]) -> None:
    doc_dir = udata / doc_id
    if not doc_dir.exists():
        return
    leaf_files = sorted(f for f in doc_dir.rglob("*.md") if f.name != "index.md")
    if not leaf_files:
        return
    sem = asyncio.Semaphore(5)
    ok = 0

    async def _one(p: Path) -> None:
        nonlocal ok
        async with sem:
            try:
                r = await enricher.enrich_leaf(p, page_map.get(p))
            except Exception as e:  # noqa: BLE001
                log.warning(f"enrich failed {p.name}: {e}")
                return
            if r.get("keywords"):
                ok += 1
            else:
                log.warning(f"enrich no keywords (skipped): {p.name}")

    await asyncio.gather(*[_one(p) for p in leaf_files])
    for p in leaf_files:
        add_enrich_completed_path(uconn, doc_id, str(p.relative_to(udata)))
    index_document(uconn, [str(p.relative_to(udata)) for p in leaf_files], udata, doc_id)
    uconn.commit()
    log.info(f"doc {doc_id[:8]}: {ok}/{len(leaf_files)} enriched + reindexed")


def build_page_map(doc_dir: Path, leaves: list[Path]) -> dict[Path, int]:
    page_map: dict[Path, int] = {}
    for p in leaves:
        text = p.read_text()
        if not text.startswith("---\n"):
            continue
        end = text.find("\n---\n", 4)
        if end == -1:
            continue
        for line in text[4:end].splitlines():
            if line.startswith("page:"):
                try:
                    page_map[p] = int(line[5:].strip())
                except ValueError:
                    pass
                break
    return page_map


async def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent.parent / "config.yaml")
    cfg = load_config(cfg_path)
    gateway = OllamaGateway(cfg.ollama_host, cfg.models, num_ctx=cfg.num_ctx)
    enricher = Enricher(gateway)
    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    sconn.close()
    total = 0
    for user in users:
        uid = user["user_id"]
        uconn = init_user_db(cfg.db_dir, uid)
        udata = user_data_dir(cfg.data_dir, uid)
        for col in list_collections(uconn):
            for doc in list_docs(uconn, col["collection_id"]):
                doc_id = doc["doc_id"]
                doc_dir = udata / doc_id
                if not doc_dir.exists():
                    continue
                leaves = [f for f in doc_dir.rglob("*.md") if f.name != "index.md"]
                if not leaves:
                    continue
                clear_enrich_completed_paths(uconn, doc_id)
                page_map = build_page_map(doc_dir, leaves)
                await reenrich_doc(enricher, uconn, udata, doc_id, page_map)
                total += len(leaves)
        uconn.close()
    await gateway.close()
    print(f"done: {total} leaves re-enriched")


if __name__ == "__main__":
    asyncio.run(main())
