from fastapi import APIRouter, Request, Form, UploadFile, File, Depends
from fastapi.responses import RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Any
from urllib.parse import quote
import hashlib
import time
import uuid
import shutil
import markdown as md_lib
from app.auth.middleware import current_user_id
from app.auth.csrf import require_csrf
from app.storage.user_db import (
    init_user_db, list_collections, create_collection, rename_collection,
    delete_collection, list_docs, get_doc as _get_doc, delete_doc as _delete_doc,
    update_doc_status, create_doc,
)
from app.storage.shared_db import init_shared_db, unlink_user_book, enqueue_job, get_user_by_username, add_collection_member, remove_collection_member, list_collection_members, list_shared_collections_for_user
from app.storage.resolver import resolve_collection
from app.storage.paths import user_data_dir, validate_user_path
from app.web.template_utils import create_templates

router = APIRouter()
_templates = create_templates(str(Path(__file__).parent / "templates"))
# Late-init module config: set by init_web_routes() from create_app() before any
# request is served. Typed as Path (not Optional) to reflect that invariant.
_db_dir: Path = Path()
_data_dir: Path = Path()


def init_web_routes(db_dir: Path, data_dir: Path) -> None:
    global _db_dir, _data_dir
    _db_dir = db_dir
    _data_dir = data_dir


def _resolve_owner(request: Request, collection_id: str) -> tuple[str | None, str | None, bool]:
    """Return (owner_uid, role, is_authenticated).

    The authorization seam: every collection-scoped route must use this
    before touching the DB or filesystem. Unauthenticated callers get
    (None, None, False) — routes redirect to /login. No-access callers
    get (None, None, True) — routes redirect to /.
    """
    uid = current_user_id(request)
    if not uid:
        return None, None, False
    r = resolve_collection(_db_dir, collection_id, uid)
    if not r:
        return None, None, True
    return r["owner_uid"], r["role"], True


def _resolve_doc_owner(request: Request, doc_id: str) -> tuple[str | None, dict | None, bool]:
    """Return (owner_uid, doc, is_authenticated).

    Finds the collection containing the doc (checking the user's own DB
    first, then any shared collection they belong to whose owner has it).
    """
    uid = current_user_id(request)
    if not uid:
        return None, None, False
    # Private: the doc exists in the user's own DB
    uconn = init_user_db(_db_dir, uid)
    try:
        d = _get_doc(uconn, doc_id)
        if d:
            return uid, d, True
    finally:
        uconn.close()
    # Shared: any membership whose owner has this doc
    sconn = init_shared_db(_db_dir)
    try:
        memberships = list_shared_collections_for_user(sconn, uid)
        for m in memberships:
            cid = m["collection_id"]
            if m["role"] != "member":
                continue
            resolved = resolve_collection(_db_dir, cid, uid)
            if not resolved:
                continue
            owner_uid = resolved["owner_uid"]
            owner_uconn = init_user_db(_db_dir, owner_uid)
            try:
                d = _get_doc(owner_uconn, doc_id)
                if d and d["collection_id"] == cid:
                    return owner_uid, d, True
            finally:
                owner_uconn.close()
    finally:
        sconn.close()
    return None, None, True


@router.post("/collections/{collection_id}/share")
async def share_collection_route(request: Request, collection_id: str, username: str = Form(...), role: str = Form("member"), _: None = Depends(require_csrf)) -> Response:
    """Share a collection with another user (owner only)."""
    owner, current_role, auth = _resolve_owner(request, collection_id)
    if not owner or current_role != "owner":
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    if role not in ("owner", "member"):
        role = "member"
    sconn = init_shared_db(_db_dir)
    try:
        target = get_user_by_username(sconn, username.strip())
        if not target:
            return RedirectResponse(f"/collections/{collection_id}", status_code=303)
        # Self-share would create a member row for the owner and demote them
        if target["user_id"] == owner:
            return RedirectResponse(f"/collections/{collection_id}", status_code=303)
        add_collection_member(sconn, collection_id, target["user_id"], role)
    finally:
        sconn.close()
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@router.post("/collections/{collection_id}/unshare")
async def unshare_collection_route(request: Request, collection_id: str, username: str = Form(...), _: None = Depends(require_csrf)) -> Response:
    """Remove a user from a shared collection (owner only)."""
    owner, current_role, auth = _resolve_owner(request, collection_id)
    if not owner or current_role != "owner":
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    sconn = init_shared_db(_db_dir)
    try:
        target = get_user_by_username(sconn, username.strip())
        if not target:
            return RedirectResponse(f"/collections/{collection_id}", status_code=303)
        # Owner cannot remove themselves (would orphan the collection)
        if target["user_id"] != owner:
            remove_collection_member(sconn, collection_id, target["user_id"])
    finally:
        sconn.close()
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@router.get("/")
async def library(request: Request) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    cols = list_collections(conn)
    conn.close()
    # Merge shared collections (from shared DB) — owner's name resolved
    # via the resolver (membership rows don't carry the owner id).
    shared = []
    sconn = init_shared_db(_db_dir)
    try:
        memberships = list_shared_collections_for_user(sconn, uid)
        for m in memberships:
            cid = m["collection_id"]
            resolved = resolve_collection(_db_dir, cid, uid)
            if not resolved:
                continue
            owner_uid = resolved["owner_uid"]
            owner_uconn = init_user_db(_db_dir, owner_uid)
            try:
                row = owner_uconn.execute(
                    "SELECT name FROM collections WHERE collection_id = ?", (cid,)
                ).fetchone()
            finally:
                owner_uconn.close()
            if row:
                shared.append({
                    "collection_id": cid,
                    "name": row["name"],
                    "created_at": m.get("added_at", ""),
                    "shared": True,
                    "role": m["role"],
                })
    finally:
        sconn.close()
    all_cols = cols + shared
    return _templates.TemplateResponse(
        request,
        "library.html",
        {"user_id": uid, "collections": all_cols, "storage": _storage_info(_data_dir, uid)},
    )


@router.post("/collections")
async def create_collection_route(request: Request, name: str = Form(...), _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    create_collection(conn, name)
    conn.close()
    return RedirectResponse("/", status_code=303)


@router.post("/collections/{collection_id}/rename")
async def rename_collection_route(request: Request, collection_id: str, name: str = Form(...), _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    owner, role, auth = _resolve_owner(request, collection_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    # Rename is owner-only for shared collections
    if role != "owner":
        return RedirectResponse("/", status_code=303)
    conn = init_user_db(_db_dir, owner)
    rename_collection(conn, collection_id, name)
    conn.close()
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@router.post("/collections/{collection_id}/delete")
async def delete_collection_route(request: Request, collection_id: str, _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    owner, role, auth = _resolve_owner(request, collection_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    # Delete is owner-only for shared collections
    if role != "owner":
        return RedirectResponse("/", status_code=303)
    conn = init_user_db(_db_dir, owner)
    # Delete all doc files from disk
    docs = list_docs(conn, collection_id)
    for d in docs:
        doc_dir = _data_dir / owner / d["doc_id"]
        if doc_dir.exists():
            shutil.rmtree(doc_dir)
    delete_collection(conn, collection_id)
    conn.close()
    # Clean up membership rows
    sconn = init_shared_db(_db_dir)
    sconn.execute("DELETE FROM collection_members WHERE collection_id = ?", (collection_id,))
    sconn.commit()
    sconn.close()
    _invalidate_storage(owner)
    return RedirectResponse("/", status_code=303)


@router.get("/collections/{collection_id}")
async def collection_view(request: Request, collection_id: str) -> Response:
    owner, role, auth = _resolve_owner(request, collection_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    uid = current_user_id(request)
    conn = init_user_db(_db_dir, owner)
    cols = list_collections(conn)
    col = next((c for c in cols if c["collection_id"] == collection_id), None)
    docs = list_docs(conn, collection_id)
    conn.close()
    if not col:
        return RedirectResponse("/", status_code=303)
    col = {**col, "shared": role != "owner" or col.get("shared", False)}
    # Members list (owner only) with usernames for the share UI
    members = []
    if role == "owner":
        sconn = init_shared_db(_db_dir)
        try:
            rows = list_collection_members(sconn, collection_id)
            for m in rows:
                if m["user_id"] == owner:
                    continue  # owner shown implicitly
                uname = sconn.execute(
                    "SELECT username FROM users WHERE user_id = ?", (m["user_id"],)
                ).fetchone()
                members.append({"username": uname["username"] if uname else m["user_id"], "role": m["role"]})
        finally:
            sconn.close()
    return _templates.TemplateResponse(
        request,
        "collection.html",
        {"user_id": uid, "collection": col, "docs": docs, "role": role, "members": members},
    )


@router.get("/collections/{collection_id}/table")
async def collection_table(request: Request, collection_id: str) -> Response:
    owner, role, auth = _resolve_owner(request, collection_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    uid = current_user_id(request)
    conn = init_user_db(_db_dir, owner)
    docs = list_docs(conn, collection_id)
    conn.close()
    return _templates.TemplateResponse(
        request,
        "_table.html",
        {"user_id": uid, "collection_id": collection_id, "docs": docs, "role": role},
    )


@router.get("/collections/{collection_id}/upload")
async def upload_form(request: Request, collection_id: str) -> Response:
    owner, role, auth = _resolve_owner(request, collection_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    uid = current_user_id(request)
    conn = init_user_db(_db_dir, owner)
    cols = list_collections(conn)
    col = next((c for c in cols if c["collection_id"] == collection_id), None)
    conn.close()
    if not col:
        return RedirectResponse("/", status_code=303)
    return _templates.TemplateResponse(
        request,
        "upload.html",
        {"user_id": uid, "collection": col, "storage": _storage_info(_data_dir, uid)},
    )


from app.constants import MAX_UPLOAD_BYTES, USER_STORAGE_LIMIT

# TTL cache for storage usage — rglob over the whole user tree is O(files)
# per call; the hot paths (page load, upload loop) must not pay it.
_storage_cache: dict[str, tuple[float, int]] = {}
_STORAGE_TTL = 10.0


def _invalidate_storage(uid: str) -> None:
    if uid == "__all__":
        _storage_cache.clear()
    else:
        _storage_cache.pop(uid, None)


def _user_storage_used(data_dir: Path, uid: str) -> int:
    now = time.monotonic()
    hit = _storage_cache.get(uid)
    if hit and now - hit[0] < _STORAGE_TTL:
        return hit[1]
    user_dir = data_dir / uid
    if not user_dir.exists():
        _storage_cache[uid] = (now, 0)
        return 0
    total = 0
    for f in user_dir.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    _storage_cache[uid] = (now, total)
    return total


def _storage_info(data_dir: Path, uid: str) -> dict[str, Any]:
    used = _user_storage_used(data_dir, uid)
    return {
        "used_bytes": used,
        "limit_bytes": USER_STORAGE_LIMIT,
        "used_mb": used / (1024 * 1024),
        "limit_mb": USER_STORAGE_LIMIT / (1024 * 1024),
        "percent": round((used / USER_STORAGE_LIMIT) * 100) if USER_STORAGE_LIMIT else 0,
        "remaining_mb": (USER_STORAGE_LIMIT - used) / (1024 * 1024),
    }


@router.post("/upload")
async def upload(request: Request, collection_id: str = Form(...), files: list[UploadFile] = File(...), _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    # Shared collections: files/docs/jobs belong to the owner.
    owner, role, auth = _resolve_owner(request, collection_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    udata = user_data_dir(_data_dir, owner)
    sconn = init_shared_db(_db_dir)
    uconn = init_user_db(_db_dir, owner)
    try:
        used = _user_storage_used(_data_dir, owner)
        for f in files:
            if not f.filename or not f.filename.lower().endswith(".pdf"):
                continue
            # Stream-read with a hard size cap — never buffer the whole body.
            data = await f.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                continue
            # Magic-byte check: a .pdf name is not a PDF.
            if not data.startswith(b"%PDF-"):
                continue
            if used + len(data) > USER_STORAGE_LIMIT:
                continue
            sha = hashlib.sha256(data).hexdigest()
            doc_id = uuid.uuid4().hex
            doc_dir = udata / doc_id
            doc_dir.mkdir(parents=True, exist_ok=True)
            tmp = doc_dir / "original.pdf.tmp"
            tmp.write_bytes(data)
            tmp.replace(doc_dir / "original.pdf")
            used += len(data)
            create_doc(uconn, doc_id, collection_id, f.filename.rsplit(".", 1)[0], sha)
            enqueue_job(sconn, owner, doc_id, str(doc_dir / "original.pdf"))
        _invalidate_storage(owner)
    finally:
        uconn.close()
        sconn.close()
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@router.post("/docs/{doc_id}/reprocess")
async def reprocess_doc(request: Request, doc_id: str, _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    owner, d, auth = _resolve_doc_owner(request, doc_id)
    if not owner or not d:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    # Owner-only reprocess for shared collections
    if owner != uid:
        return RedirectResponse("/", status_code=303)
    uconn = init_user_db(_db_dir, owner)
    sconn = init_shared_db(_db_dir)
    try:
        pdf_path = _data_dir / owner / doc_id / "original.pdf"
        update_doc_status(uconn, doc_id, "queued")
        enqueue_job(sconn, owner, doc_id, str(pdf_path))
    finally:
        uconn.close()
        sconn.close()
    return RedirectResponse(f"/collections/{d['collection_id']}", status_code=303)


@router.post("/docs/{doc_id}/delete")
async def delete_doc_route(request: Request, doc_id: str, _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    owner, d, auth = _resolve_doc_owner(request, doc_id)
    if not owner or not d:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    # Owner-only delete: members cannot delete docs in a shared collection.
    if owner != uid:
        return RedirectResponse("/", status_code=303)
    uconn = init_user_db(_db_dir, owner)
    try:
        _delete_doc(uconn, doc_id)
    finally:
        uconn.close()
    sconn = init_shared_db(_db_dir)
    unlink_user_book(sconn, doc_id)
    sconn.close()
    doc_dir = _data_dir / owner / doc_id
    if doc_dir.exists():
        shutil.rmtree(doc_dir)
    _invalidate_storage(owner)
    return RedirectResponse("/", status_code=303)


@router.get("/docs/search")
async def doc_search_path(request: Request, path: str) -> Response:
    """Find which doc contains a given file path and redirect to it.
    Must be registered BEFORE /docs/{doc_id} to avoid matching 'search' as doc_id.
    Handles various path formats the LLM might return:
    - doc_id/filename (correct FTS format)
    - filename (bare filename)
    - section_name/index.md (hallucinated directory)
    - partial_filename.md (partial match)
    """
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    # Search roots: the user's own tree plus every shared collection's
    # owner tree (members can cite shared docs).
    roots: list[tuple[str, Path]] = [(uid, _data_dir / uid)]
    sconn = init_shared_db(_db_dir)
    try:
        memberships = list_shared_collections_for_user(sconn, uid)
        for m in memberships:
            if m["role"] != "member":
                continue
            resolved = resolve_collection(_db_dir, m["collection_id"], uid)
            if resolved:
                owner_root = _data_dir / resolved["owner_uid"]
                if owner_root.exists():
                    roots.append((resolved["owner_uid"], owner_root))
    finally:
        sconn.close()
    if not any(r.exists() for _, r in roots):
        return RedirectResponse("/", status_code=303)

    parts = path.split("/")
    filename = parts[-1]

    # 1. If path starts with a 32-char hex, try that as the doc_id directly
    if len(parts) > 1 and len(parts[0]) == 32:
        direct_doc_id = parts[0]
        direct_file = "/".join(parts[1:])
        for owner_uid, root in roots:
            direct_path = root / direct_doc_id / direct_file
            if direct_path.exists() and direct_path.is_file():
                return RedirectResponse(f"/docs/{direct_doc_id}/view?path={quote(direct_file, safe='/')}", status_code=303)

    # 2. Strip /index.md suffix — LLM often hallucinates "section_name/index.md"
    #    when the actual file is "section_name.md" or "NN_section_name.md"
    search_name = filename
    if search_name == "index.md" and len(parts) > 1:
        search_name = parts[-2]  # Use the directory name as the search term

    # 3. Search all doc directories across the user's + shared roots
    for _owner_uid, root in roots:
        if not root.exists():
            continue
        for doc_dir in sorted(root.iterdir()):
            if not doc_dir.is_dir():
                continue
            # Try exact filename match
            candidate = doc_dir / filename
            if candidate.exists() and candidate.is_file():
                return RedirectResponse(f"/docs/{doc_dir.name}/view?path={quote(filename, safe='/')}", status_code=303)
            # Try the search_name (after stripping /index.md)
            if search_name != filename:
                candidate = doc_dir / search_name
                if candidate.exists() and candidate.is_file():
                    return RedirectResponse(f"/docs/{doc_dir.name}/view?path={quote(search_name, safe='/')}", status_code=303)
                candidate = doc_dir / (search_name + ".md")
                if candidate.exists() and candidate.is_file():
                    return RedirectResponse(f"/docs/{doc_dir.name}/view?path={quote(search_name + '.md', safe='/')}", status_code=303)
            # Recursive search for exact filename
            for f in doc_dir.rglob(filename):
                if f.is_file():
                    rel = str(f.relative_to(doc_dir))
                    return RedirectResponse(f"/docs/{doc_dir.name}/view?path={quote(rel, safe='/')}", status_code=303)
            # Fuzzy: find files containing the search_name stem
            stem = search_name.replace(".md", "").replace("_", " ")
            for f in doc_dir.glob("*.md"):
                if stem in f.stem.replace("_", " "):
                    rel = str(f.relative_to(doc_dir))
                    return RedirectResponse(f"/docs/{doc_dir.name}/view?path={quote(rel, safe='/')}", status_code=303)

    return RedirectResponse("/", status_code=303)


@router.get("/docs/{doc_id}/cover")
async def doc_cover(request: Request, doc_id: str) -> Response:
    """Serve the cover image (first page as JPG)."""
    owner, _, auth = _resolve_doc_owner(request, doc_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    uid = current_user_id(request)
    cover_path = _data_dir / owner / doc_id / "cover.jpg"
    if cover_path.exists():
        return FileResponse(str(cover_path), media_type="image/jpeg")
    # Fallback: no cover
    return RedirectResponse("/static/no-cover.svg", status_code=303)


@router.get("/docs/{doc_id}/pdf")
async def doc_pdf(request: Request, doc_id: str, page: int = 0) -> Response:
    """Serve the original PDF with optional page jump.

    Uses an HTML wrapper with <embed> + JS for reliable page jumping
    across all browsers (mobile included). The #page= fragment anchor
    doesn't work reliably on mobile PDF viewers.
    """
    owner, _, auth = _resolve_doc_owner(request, doc_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    uid = current_user_id(request)
    pdf_path = _data_dir / owner / doc_id / "original.pdf"
    if pdf_path.exists():
        if page and page > 0:
            # Return HTML wrapper that scrolls to the right page
            return _templates.TemplateResponse(
                request,
                "pdf_viewer.html",
                {"user_id": uid, "doc_id": doc_id, "page": page},
            )
        return FileResponse(str(pdf_path), media_type="application/pdf")
    return RedirectResponse(f"/docs/{doc_id}", status_code=303)


@router.get("/docs/{doc_id}")
async def doc_view(request: Request, doc_id: str) -> Response:
    owner, d, auth = _resolve_doc_owner(request, doc_id)
    if not owner or not d:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    uid = current_user_id(request)
    tree = _build_doc_tree(_data_dir, owner, doc_id)
    return _templates.TemplateResponse(
        request,
        "doc.html",
        {"user_id": uid, "doc": d, "tree": tree},
    )


@router.get("/docs/{doc_id}/view")
async def doc_view_leaf(request: Request, doc_id: str, path: str) -> Response:
    owner, _, auth = _resolve_doc_owner(request, doc_id)
    if not owner:
        return RedirectResponse("/login" if not auth else "/", status_code=303)
    uid = current_user_id(request)
    clean_path = path
    if path.startswith(doc_id + "/"):
        clean_path = path[len(doc_id) + 1:]
    # Build path relative to the OWNER's root (shared collections resolve
    # to the owner's tree): doc_id/file_path
    rel_path = f"{doc_id}/{clean_path}"
    try:
        full = validate_user_path(_data_dir, owner, rel_path)
    except ValueError:
        return RedirectResponse(f"/docs/{doc_id}", status_code=303)
    # Check is_file() too — directory paths return "file not found"
    if not full.exists() or not full.is_file():
        content = f"(file not found: {clean_path})"
    else:
        raw = full.read_text()
        # Strip front-matter (YAML between --- markers)
        if raw.startswith("---\n"):
            end = raw.find("\n---\n", 4)
            if end != -1:
                raw = raw[end + 5:]
        content = raw.strip()
    return _templates.TemplateResponse(
        request,
        "doc_leaf.html",
        {"user_id": uid, "doc_id": doc_id, "path": clean_path, "content": content},
    )


def _build_doc_tree(data_dir: Path, uid: str, doc_id: str) -> list[dict[str, Any]]:
    doc_root = data_dir / uid / doc_id
    if not doc_root.exists():
        return []
    entries = []

    # Check for chapter subdirectories with index.md
    has_dirs = any(p.is_dir() for p in doc_root.iterdir())

    if has_dirs:
        for chap_dir in sorted(doc_root.iterdir()):
            if chap_dir.is_dir():
                idx = chap_dir / "index.md"
                title = chap_dir.name
                if idx.exists():
                    first_line = idx.read_text().splitlines()[0]
                    if first_line.startswith("# "):
                        title = first_line[2:]
                entries.append({"title": title, "path": f"{chap_dir.name}/index.md"})
    else:
        # Flat structure: list .md files directly
        for f in sorted(doc_root.iterdir()):
            if f.is_file() and f.suffix == ".md" and f.name != "index.md":
                title = f.stem.replace("_", " ")
                # Try to read the first heading
                try:
                    first_line = f.read_text().splitlines()[0]
                    if first_line.startswith("# "):
                        title = first_line[2:]
                except (IndexError, UnicodeDecodeError):
                    pass
                entries.append({"title": title, "path": f.name})

    return entries