from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.auth.middleware import current_user_id
from app.storage.user_db import init_user_db, list_collections, create_collection

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