"""Re-run FTS5 indexing for all docs of all users.

Usage: .venv/bin/python scripts/reindex.py [config.yaml]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import load_config
from app.storage.shared_db import init_shared_db, list_users
from app.storage.user_db import init_user_db, list_collections, list_docs
from app.storage.paths import user_data_dir
from app.pipeline.index import index_document


def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent.parent / "config.yaml")
    cfg = load_config(cfg_path)
    sconn = init_shared_db(cfg.db_dir)
    users = list_users(sconn)
    total_docs = 0
    for user in users:
        uid = user["user_id"]
        uconn = init_user_db(cfg.db_dir, uid)
        for col in list_collections(uconn):
            for doc in list_docs(uconn, col["collection_id"]):
                doc_id = doc["doc_id"]
                udata = user_data_dir(cfg.data_dir, uid)
                doc_dir = udata / doc_id
                if not doc_dir.exists():
                    continue
                leaf_files = sorted(f for f in doc_dir.rglob("*.md") if f.name != "index.md")
                leaf_paths = [str(f.relative_to(udata)) for f in leaf_files]
                if not leaf_paths:
                    continue
                index_document(uconn, leaf_paths, udata, doc_id)
                total_docs += 1
                print(f"reindexed {uid[:8]}/{doc_id[:8]}: {len(leaf_paths)} sections")
        uconn.close()
    sconn.close()
    print(f"done: {total_docs} docs reindexed")


if __name__ == "__main__":
    main()
