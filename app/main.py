from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import RequestResponseEndpoint
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from urllib.parse import urlparse
import asyncio
import secrets
import threading
import uuid
import httpx
import os
from contextlib import asynccontextmanager
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
from app.constants import WORKER_POLL_INTERVAL, READYZ_TIMEOUT, MAX_UPLOAD_BYTES

log = get_logger("main")


def create_app(cfg: Config, session_secret: str) -> FastAPI:
    # Configure structured logging before creating app
    configure_logging(json_output=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: retry orphaned doc-dir cleanup from previous crashes.
        try:
            from app.storage.cleanup import sweep_orphans
            cleared = sweep_orphans(cfg.data_dir)
            if cleared:
                log.info(f"startup: swept {cleared} orphaned doc dirs")
        except Exception:
            log.exception("startup: orphan sweep failed")

        # Startup: start the worker thread
        app.state.worker.runner = PipelineRunner(app.state.worker_gateway, cfg.data_dir, cfg.db_dir)

        async def _run_worker() -> None:
            try:
                await app.state.worker.run_forever()
            finally:
                # Close in the SAME loop that created the client (I1)
                await app.state.worker_gateway.close()

        app.state.worker_thread = threading.Thread(
            target=lambda: asyncio.run(_run_worker()), daemon=True
        )
        app.state.worker_thread.start()
        yield
        # Shutdown: stop worker and close gateways
        app.state.worker.stop()
        t = getattr(app.state, "worker_thread", None)
        if t is not None:
            t.join(timeout=30)
        # NOTE (I2): stop() only lands between jobs — a job mid-flight runs
        # to completion (or until the process exits; the daemon flag
        # guarantees exit). The lease reclaims abandoned jobs on next start.
        # A thread stuck in a sync subprocess (tesseract/poppler) cannot be
        # interrupted from outside — task.cancel only lands at the next await.
        await app.state.gateway.close()

    app = FastAPI(title="Open Grimoire", lifespan=lifespan)
    app.state.config = cfg
    app.state.session_secret = session_secret
    app.state.db_dir = cfg.db_dir
    app.state.data_dir = cfg.data_dir
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

    # Security headers — defense-in-depth for cookie_secure deployments.
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Per-request CSP nonce, generated before the response so templates can
        # stamp it on inline <script>/<style> blocks. Nonce-based CSP lets us
        # drop 'unsafe-inline' from script-src/style-src; inline style
        # attributes (style="...") stay allowed via the narrow style-src-attr
        # directive, which is a much smaller surface than style-src.
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # X-Frame-Options: DENY would block the PDF viewer's same-origin
        # <iframe> from loading the raw PDF — skip it for PDF responses.
        if not response.headers.get("content-type", "").startswith("application/pdf"):
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            f"style-src 'self' 'nonce-{nonce}'; style-src-attr 'unsafe-inline'; "
            f"script-src 'self' 'nonce-{nonce}'"
        )
        if getattr(request.app.state.config, "cookie_secure", False):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # CSRF protection: reject cross-origin state-changing requests.
    # SameSite=Lax blocks most browser cross-site POSTs; this covers the rest
    # (top-level navigation POSTs, older clients, same-site subdomains).
    @app.middleware("http")
    async def csrf_origin_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin") or request.headers.get("referer", "")
            host = request.headers.get("host", "")
            if origin == "null":
                return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
            if origin and urlparse(origin).netloc != host:
                return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
        return await call_next(request)

    # Request body size limit middleware — prevents large payload DoS.
    MAX_BODY_SIZE = int(os.environ.get("MAX_BODY_SIZE_BYTES", str(MAX_UPLOAD_BYTES)))

    @app.middleware("http")
    async def body_size_limit_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_SIZE:
            return JSONResponse(
                {"error": f"Request body too large. Maximum size is {MAX_BODY_SIZE} bytes."},
                status_code=413,
            )
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
        except Exception:
            checks["database"] = "unhealthy"

        # Check Ollama (non-blocking, just verify host is reachable)
        try:
            gateway = getattr(app.state, "gateway", None)
            if gateway:
                client = await gateway._get_client()
                resp = await client.get("/api/tags")
            else:
                async with httpx.AsyncClient(timeout=READYZ_TIMEOUT) as client:
                    resp = await client.get(f"{cfg.ollama_host}/api/tags")
            if resp.status_code == 200:
                checks["ollama"] = "ok"
            else:
                checks["ollama"] = "unhealthy"
        except Exception:
            checks["ollama"] = "unreachable"

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
    # Build the worker gateway at create_app time (lazy AsyncClient — no
    # loop binding until first use; no runner swap needed).
    worker_gateway = OllamaGateway(cfg.ollama_host, cfg.models, num_ctx=cfg.num_ctx)
    app.state.worker_gateway = worker_gateway

    return app