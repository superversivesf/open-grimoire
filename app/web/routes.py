from fastapi import APIRouter, Request, Form, UploadFile, File, Depends
from fastapi.responses import RedirectResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Any
import hashlib
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
from app.storage.shared_db import init_shared_db, unlink_user_book, enqueue_job
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


@router.get("/")
async def library(request: Request) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    cols = list_collections(conn)
    conn.close()
    return _templates.TemplateResponse(
        request,
        "library.html",
        {"user_id": uid, "collections": cols, "storage": _storage_info(_data_dir, uid)},
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
    conn = init_user_db(_db_dir, uid)
    rename_collection(conn, collection_id, name)
    conn.close()
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@router.post("/collections/{collection_id}/delete")
async def delete_collection_route(request: Request, collection_id: str, _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    # Delete all doc files from disk
    docs = list_docs(conn, collection_id)
    for d in docs:
        doc_dir = _data_dir / uid / d["doc_id"]
        if doc_dir.exists():
            shutil.rmtree(doc_dir)
    delete_collection(conn, collection_id)
    conn.close()
    return RedirectResponse("/", status_code=303)


@router.get("/collections/{collection_id}")
async def collection_view(request: Request, collection_id: str) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    cols = list_collections(conn)
    col = next((c for c in cols if c["collection_id"] == collection_id), None)
    docs = list_docs(conn, collection_id)
    conn.close()
    if not col:
        return RedirectResponse("/", status_code=303)
    return _templates.TemplateResponse(
        request,
        "collection.html",
        {"user_id": uid, "collection": col, "docs": docs},
    )


@router.get("/collections/{collection_id}/table")
async def collection_table(request: Request, collection_id: str) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    docs = list_docs(conn, collection_id)
    conn.close()
    return _templates.TemplateResponse(
        request,
        "_table.html",
        {"user_id": uid, "collection_id": collection_id, "docs": docs},
    )


@router.get("/collections/{collection_id}/upload")
async def upload_form(request: Request, collection_id: str) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
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


def _user_storage_used(data_dir: Path, uid: str) -> int:
    user_dir = data_dir / uid
    if not user_dir.exists():
        return 0
    total = 0
    for f in user_dir.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
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
    udata = user_data_dir(_data_dir, uid)
    sconn = init_shared_db(_db_dir)
    uconn = init_user_db(_db_dir, uid)
    try:
        for f in files:
            if not f.filename or not f.filename.lower().endswith(".pdf"):
                continue
            data = await f.read()
            if len(data) > MAX_UPLOAD_BYTES:
                continue
            used = _user_storage_used(_data_dir, uid)
            if used + len(data) > USER_STORAGE_LIMIT:
                continue
            sha = hashlib.sha256(data).hexdigest()
            doc_id = uuid.uuid4().hex
            doc_dir = udata / doc_id
            doc_dir.mkdir(parents=True, exist_ok=True)
            (doc_dir / "original.pdf").write_bytes(data)
            create_doc(uconn, doc_id, collection_id, f.filename.rsplit(".", 1)[0], sha)
            enqueue_job(sconn, uid, doc_id, str(doc_dir / "original.pdf"))
    finally:
        uconn.close()
        sconn.close()
    return RedirectResponse(f"/collections/{collection_id}", status_code=303)


@router.post("/docs/{doc_id}/reprocess")
async def reprocess_doc(request: Request, doc_id: str, _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    uconn = init_user_db(_db_dir, uid)
    sconn = init_shared_db(_db_dir)
    d = None
    try:
        d = _get_doc(uconn, doc_id)
        if not d:
            return RedirectResponse("/", status_code=303)
        pdf_path = _data_dir / uid / doc_id / "original.pdf"
        update_doc_status(uconn, doc_id, "queued")
        enqueue_job(sconn, uid, doc_id, str(pdf_path))
    finally:
        uconn.close()
        sconn.close()
    return RedirectResponse(f"/collections/{d['collection_id']}", status_code=303)


@router.post("/docs/{doc_id}/delete")
async def delete_doc_route(request: Request, doc_id: str, _: None = Depends(require_csrf)) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    uconn = init_user_db(_db_dir, uid)
    try:
        d = _get_doc(uconn, doc_id)
        if d:
            _delete_doc(uconn, doc_id)
    finally:
        uconn.close()
    sconn = init_shared_db(_db_dir)
    unlink_user_book(sconn, doc_id)
    sconn.close()
    doc_dir = _data_dir / uid / doc_id
    if doc_dir.exists():
        shutil.rmtree(doc_dir)
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
    user_root = _data_dir / uid
    if not user_root.exists():
        return RedirectResponse("/", status_code=303)

    parts = path.split("/")
    filename = parts[-1]

    # 1. If path starts with a 32-char hex, try that as the doc_id directly
    if len(parts) > 1 and len(parts[0]) == 32:
        direct_doc_id = parts[0]
        direct_file = "/".join(parts[1:])
        direct_path = user_root / direct_doc_id / direct_file
        if direct_path.exists() and direct_path.is_file():
            return RedirectResponse(f"/docs/{direct_doc_id}/view?path={direct_file}", status_code=303)

    # 2. Strip /index.md suffix — LLM often hallucinates "section_name/index.md"
    #    when the actual file is "section_name.md" or "NN_section_name.md"
    search_name = filename
    if search_name == "index.md" and len(parts) > 1:
        search_name = parts[-2]  # Use the directory name as the search term

    # 3. Search all doc directories
    for doc_dir in sorted(user_root.iterdir()):
        if not doc_dir.is_dir():
            continue
        # Try exact filename match
        candidate = doc_dir / filename
        if candidate.exists() and candidate.is_file():
            return RedirectResponse(f"/docs/{doc_dir.name}/view?path={filename}", status_code=303)
        # Try the search_name (after stripping /index.md)
        if search_name != filename:
            candidate = doc_dir / search_name
            if candidate.exists() and candidate.is_file():
                return RedirectResponse(f"/docs/{doc_dir.name}/view?path={search_name}", status_code=303)
            candidate = doc_dir / (search_name + ".md")
            if candidate.exists() and candidate.is_file():
                return RedirectResponse(f"/docs/{doc_dir.name}/view?path={search_name}.md", status_code=303)
        # Recursive search for exact filename
        for f in doc_dir.rglob(filename):
            if f.is_file():
                rel = str(f.relative_to(doc_dir))
                return RedirectResponse(f"/docs/{doc_dir.name}/view?path={rel}", status_code=303)
        # Fuzzy: find files containing the search_name stem
        stem = search_name.replace(".md", "").replace("_", " ")
        for f in doc_dir.glob("*.md"):
            if stem in f.stem.replace("_", " "):
                rel = str(f.relative_to(doc_dir))
                return RedirectResponse(f"/docs/{doc_dir.name}/view?path={rel}", status_code=303)

    return RedirectResponse("/", status_code=303)


@router.get("/docs/{doc_id}/cover")
async def doc_cover(request: Request, doc_id: str) -> Response:
    """Serve the cover image (first page as JPG)."""
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    cover_path = _data_dir / uid / doc_id / "cover.jpg"
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
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    pdf_path = _data_dir / uid / doc_id / "original.pdf"
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
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    uconn = init_user_db(_db_dir, uid)
    d = _get_doc(uconn, doc_id)
    uconn.close()
    if not d:
        return RedirectResponse("/", status_code=303)
    tree = _build_doc_tree(_data_dir, uid, doc_id)
    return _templates.TemplateResponse(
        request,
        "doc.html",
        {"user_id": uid, "doc": d, "tree": tree},
    )


@router.get("/docs/{doc_id}/view")
async def doc_view_leaf(request: Request, doc_id: str, path: str) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    clean_path = path
    if path.startswith(doc_id + "/"):
        clean_path = path[len(doc_id) + 1:]
    # Build path relative to user root: doc_id/file_path
    rel_path = f"{doc_id}/{clean_path}"
    try:
        full = validate_user_path(_data_dir, uid, rel_path)
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