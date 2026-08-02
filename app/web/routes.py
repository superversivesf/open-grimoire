from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import hashlib
import uuid
import shutil
from app.auth.middleware import current_user_id
from app.storage.user_db import (
    init_user_db, list_collections, create_collection, list_docs,
    create_doc, get_doc as _get_doc, delete_doc as _delete_doc, update_doc_status,
)
from app.storage.shared_db import init_shared_db, enqueue_job
from app.storage.paths import user_data_dir, validate_user_path

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_db_dir = None
_data_dir = None


def init_web_routes(db_dir: Path, data_dir: Path):
    global _db_dir, _data_dir
    _db_dir = db_dir
    _data_dir = data_dir


@router.get("/")
async def library(request: Request):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    cols = list_collections(conn)
    conn.close()
    return _templates.TemplateResponse(
        request,
        "library.html",
        {"user_id": uid, "collections": cols},
    )


@router.post("/collections")
async def create_collection_route(request: Request, name: str = Form(...)):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    conn = init_user_db(_db_dir, uid)
    create_collection(conn, name)
    conn.close()
    return RedirectResponse("/", status_code=303)


@router.get("/collections/{collection_id}")
async def collection_view(request: Request, collection_id: str):
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


@router.get("/collections/{collection_id}/upload")
async def upload_form(request: Request, collection_id: str):
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
        {"user_id": uid, "collection": col},
    )


@router.post("/upload")
async def upload(request: Request, collection_id: str = Form(...), files: list[UploadFile] = File(...)):
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
async def reprocess_doc(request: Request, doc_id: str):
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
async def delete_doc_route(request: Request, doc_id: str):
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
    doc_dir = _data_dir / uid / doc_id
    if doc_dir.exists():
        shutil.rmtree(doc_dir)
    return RedirectResponse("/", status_code=303)


@router.get("/docs/{doc_id}")
async def doc_view(request: Request, doc_id: str):
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
async def doc_view_leaf(request: Request, doc_id: str, path: str):
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    try:
        full = validate_user_path(_data_dir, uid, str(_data_dir / uid / doc_id / path))
    except ValueError:
        return RedirectResponse(f"/docs/{doc_id}", status_code=303)
    content = full.read_text() if full.exists() else "(file not found)"
    return _templates.TemplateResponse(
        request,
        "doc_leaf.html",
        {"user_id": uid, "doc_id": doc_id, "path": path, "content": content},
    )


def _build_doc_tree(data_dir: Path, uid: str, doc_id: str) -> list[dict]:
    doc_root = data_dir / uid / doc_id
    if not doc_root.exists():
        return []
    entries = []
    for chap_dir in sorted(doc_root.iterdir()):
        if chap_dir.is_dir():
            idx = chap_dir / "index.md"
            title = chap_dir.name
            if idx.exists():
                first_line = idx.read_text().splitlines()[0]
                if first_line.startswith("# "):
                    title = first_line[2:]
            entries.append({"title": title, "path": f"{chap_dir.name}/index.md"})
    return entries