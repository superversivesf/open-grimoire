from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, Response
from pathlib import Path
from typing import Any
from app.auth.middleware import current_user_id
from app.auth.csrf import require_csrf
from app.storage.shared_db import init_shared_db, get_usage_summary, list_users, set_user_status, list_users_by_status
from app.storage.user_db import init_user_db, list_collections
from app.usage.tokens import estimate_cost_usd
from app.web.template_utils import create_templates

router = APIRouter()
_templates = create_templates(str(Path(__file__).parent.parent / "web" / "templates"))
# Late-init module config: set by init_admin_routes() from create_app() before
# any request is served.
_db_dir: Path = Path()


def init_admin_routes(db_dir: Path) -> None:
    global _db_dir
    _db_dir = db_dir


def _project_cost_per_1000(summary: dict[str, Any], total_query_cost: float) -> float:
    """Estimate cost per 1000 queries based on current usage."""
    query_count = summary["queries"].get("count", 0) or 0
    if query_count == 0:
        return 0.0
    return total_query_cost / query_count * 1000


@router.get("/admin")
async def admin_dashboard(request: Request) -> Response:
    uid = current_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    sconn = init_shared_db(_db_dir)
    user = None
    for u in list_users(sconn):
        if u["user_id"] == uid and u["is_admin"]:
            user = u
            break
    if not user:
        sconn.close()
        return RedirectResponse("/", status_code=303)

    summary = get_usage_summary(sconn, days=30)

    users = list_users(sconn)

    # Calculate costs
    total_query_cost = 0.0
    total_enrich_cost = 0.0

    for model_entry in summary["by_model"]:
        model = model_entry["model"]
        input_t = model_entry.get("total_input") or 0
        output_t = model_entry.get("total_output") or 0
        total_query_cost += estimate_cost_usd(model, input_t, output_t)

    enrichments = summary["enrichments"]
    if enrichments:
        enrich_input = enrichments.get("total_input") or 0
        enrich_output = enrichments.get("total_output") or 0
        total_enrich_cost = estimate_cost_usd("deepseek-v4-flash:0731-cloud", enrich_input, enrich_output)

    sconn.close()

    cost_per_1000 = _project_cost_per_1000(summary, total_query_cost)

    sconn2 = init_shared_db(_db_dir)
    pending_users = list_users_by_status(sconn2, "pending")
    sconn2.close()

    return _templates.TemplateResponse(
        request,
        "admin.html",
        {
            "user_id": uid,
            "summary": summary,
            "users": users,
            "pending_users": pending_users,
            "total_query_cost": total_query_cost,
            "total_enrich_cost": total_enrich_cost,
            "total_cost": total_query_cost + total_enrich_cost,
            "cost_per_1000": cost_per_1000,
        },
    )


def _is_admin_request(request: Request) -> bool:
    """Reuse the admin_dashboard authz check: scan users for uid + is_admin."""
    uid = current_user_id(request)
    if not uid:
        return False
    sconn = init_shared_db(_db_dir)
    try:
        for u in list_users(sconn):
            if u["user_id"] == uid and u["is_admin"]:
                return True
        return False
    finally:
        sconn.close()


@router.post("/admin/users/{user_id}/approve")
async def approve_user_route(request: Request, user_id: str, _: None = Depends(require_csrf)) -> Response:
    if not _is_admin_request(request):
        return RedirectResponse("/", status_code=303)
    sconn = init_shared_db(_db_dir)
    set_user_status(sconn, user_id, "active")
    sconn.close()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/users/{user_id}/reject")
async def reject_user_route(request: Request, user_id: str, _: None = Depends(require_csrf)) -> Response:
    if not _is_admin_request(request):
        return RedirectResponse("/", status_code=303)
    sconn = init_shared_db(_db_dir)
    set_user_status(sconn, user_id, "rejected")
    sconn.close()
    return RedirectResponse("/admin", status_code=303)