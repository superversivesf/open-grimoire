from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from urllib.parse import urlparse
import asyncio
import uuid
import httpx
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.config import Config
from app.auth.middleware import AuthMiddleware
from app.auth.routes import router as auth_router, init_auth_routes, _get_limiter, _is_rate_limited
from app.web.routes import router as web_router, init_web_routes
from app.web.admin_routes import router as admin_router, init_admin_routes
from app.agent.routes import router as agent_router, init_agent_routes
from app.gateway.ollama import OllamaGateway
from app.pipeline.runner import PipelineRunner
from app.queue.worker import QueueWorker
from app.storage.shared_db import init_shared_db
from app.logging_utils import configure_logging, get_logger, set_request_id
from app.constants import WORKER_POLL_INTERVAL, READYZ_TIMEOUT

log = get_logger("main")


def create_app(cfg: Config, session_secret: str) -> FastAPI:
    # Configure structured logging before creating app
    configure_logging(json_output=True)

    app = FastAPI(title="Open Grimoire")
    app.state.config = cfg
    app.state.session_secret = session_secret
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_dir.mkdir(parents=True, exist_ok=True)

    # Paths that skip request ID / logging
    SKIP_LOG_PATHS = {"/healthz", "/readyz", "/favicon.ico"}

    # Request ID middleware for correlation
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip request ID and logging for health checks
        if request.url.path in SKIP_LOG_PATHS:
            return await call_next(request)

        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        set_request_id(req_id)
        response = await call_next(request)
        response.headers["x-request-id"] = req_id
        return response

    # CSRF protection: reject cross-origin state-changing requests.
    # SameSite=Lax blocks most browser cross-site POSTs; this covers the rest
    # (top-level navigation POSTs, older clients, same-site subdomains).
    @app.middleware("http")
    async def csrf_origin_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin") or request.headers.get("referer", "")
            host = request.headers.get("host", "")
            if origin:
                if urlparse(origin).netloc != host:
                    return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
        return await call_next(request)

    # Rate limiter
    app.state.limiter = _get_limiter()
    # Gate the @limiter.limit decorators: enforce only in production, not in
    # dev/test (where DEV_MODE / TEST_MODE are set).
    app.state.limiter.enabled = _is_rate_limited()
    app.add_exception_handler(RateLimitExceeded, lambda request, exc: JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    ))

    # Health check endpoints (no auth required)
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness probe - returns ok if process is alive."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Readiness probe - checks DB and Ollama connectivity."""
        checks: dict[str, str] = {}

        # Check database
        try:
            conn = init_shared_db(cfg.db_dir)
            conn.execute("SELECT 1").fetchone()
            conn.close()
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"failed: {e}"

        # Check Ollama (non-blocking, just verify host is reachable)
        try:
            async with httpx.AsyncClient(timeout=READYZ_TIMEOUT) as client:
                resp = await client.get(f"{cfg.ollama_host}/api/tags")
                if resp.status_code == 200:
                    checks["ollama"] = "ok"
                else:
                    checks["ollama"] = f"unhealthy: {resp.status_code}"
        except Exception as e:
            checks["ollama"] = f"unreachable: {e}"

        all_ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            status_code=200 if all_ok else 503,
            content={"status": "ready" if all_ok else "not ready", "checks": checks},
        )

    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    init_auth_routes(cfg.db_dir)
    init_web_routes(cfg.db_dir, cfg.data_dir)
    init_admin_routes(cfg.db_dir)
    gateway = OllamaGateway(cfg.ollama_host, cfg.models, num_ctx=cfg.num_ctx)
    app.state.gateway = gateway
    init_agent_routes(cfg.db_dir, cfg.data_dir, gateway)
    app.add_middleware(AuthMiddleware, session_secret=session_secret, db_dir=cfg.db_dir)
    app.include_router(auth_router)
    app.include_router(web_router)
    app.include_router(admin_router)
    app.include_router(agent_router)

    runner = PipelineRunner(gateway, cfg.data_dir, cfg.db_dir)
    worker = QueueWorker(runner, cfg.db_dir, poll_interval=WORKER_POLL_INTERVAL)
    app.state.worker = worker

    @app.on_event("startup")
    async def _start_worker() -> None:
        asyncio.create_task(worker.run_forever())

    @app.on_event("shutdown")
    async def _close_gateway() -> None:
        await gateway.close()

    return app