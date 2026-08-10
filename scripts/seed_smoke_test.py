"""Seed the test container with a collection + doc + FTS so smoke tests
have data to exercise (login → library → collection → doc → pdf → ask)."""
import sqlite3
import uuid
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from app.auth.passwords import hash_password

# Resolve DB paths: /app/db inside the container, db-test on the host.
BASE = Path("/app")
if not BASE.exists():
    BASE = Path("/home/jason/Repos/rpg-master")
DB = BASE / "db" / "shared.sqlite" if (BASE / "db").exists() else BASE / "db-test" / "shared.sqlite"
DATA = BASE / "data" if (BASE / "data").exists() else BASE / "data-test"
USER_DB_DIR = BASE / "db" if (BASE / "db").exists() else BASE / "db-test"

# Shared DB: ensure admin exists
sconn = sqlite3.connect(DB)
sconn.row_factory = sqlite3.Row
admin = sconn.execute("SELECT user_id FROM users WHERE username = 'admin'").fetchone()
if not admin:
    uid = uuid.uuid4().hex
    sconn.execute(
        "INSERT INTO users (user_id, username, password_hash, is_admin, status) VALUES (?, ?, ?, 1, 'active')",
        (uid, "admin", hash_password("SmokeTestPass!123")),
    )
    sconn.commit()
    admin = {"user_id": uid}
uid = admin["user_id"]
sconn.close()

# User DB: collection + doc
uconn = sqlite3.connect(USER_DB_DIR / f"{uid}.sqlite")
uconn.row_factory = sqlite3.Row
cid = uuid.uuid4().hex
did = uuid.uuid4().hex
uconn.execute("INSERT INTO collections (collection_id, name) VALUES (?, ?)", (cid, "Smoke Test Shelf"))
uconn.execute(
    "INSERT INTO docs (doc_id, collection_id, title, sha256, status, page_count, enrich_progress, enrich_total) "
    "VALUES (?, ?, ?, ?, 'done', 2, 2, 2)",
    (did, cid, "Smoke Test Manual", "abcd"),
)
uconn.execute(
    "INSERT INTO documents_fts (path, title, summary, keywords, content) VALUES (?, ?, ?, ?, ?)",
    (f"{did}/01_combat.md", "Combat", "Combat rules.", "combat,armor", "Armor Class (AC) determines how hard a character is to hit."),
)
uconn.execute(
    "INSERT INTO documents_fts (path, title, summary, keywords, content) VALUES (?, ?, ?, ?, ?)",
    (f"{did}/02_magic.md", "Magic", "Spell rules.", "magic,fireball", "Fireball deals 8d6 fire damage."),
)
uconn.commit()
uconn.close()

# Data tree: markdown leaves + fake PDF + cover
doc_dir = DATA / uid / did
doc_dir.mkdir(parents=True, exist_ok=True)
(doc_dir / "original.pdf").write_bytes(b"%PDF-1.4 fake pdf for smoke tests")
(doc_dir / "cover.jpg").write_bytes(b"jpegdata")
(doc_dir / "01_combat.md").write_text(
    "---\nsummary: \"Combat rules.\"\nkeywords: [combat, armor]\npage: 1\n---\n\n# Combat\n\nArmor Class (AC) determines how hard a character is to hit."
)
(doc_dir / "02_magic.md").write_text(
    "---\nsummary: \"Spell rules.\"\nkeywords: [magic, fireball]\npage: 2\n---\n\n# Magic\n\nFireball deals 8d6 fire damage."
)

print(f"seeded uid={uid} cid={cid} did={did}")
