from fastapi import FastAPI, Request
from pathlib import Path
from app.config import Config
from app.auth.middleware import AuthMiddleware
from app.auth.routes import router as auth_router, init_auth_routes
from app.web.routes import router as web_router, init_web_routes


def create_app(cfg: Config, session_secret: str) -> FastAPI:
    app = FastAPI(title="RPG Master")
    app.state.config = cfg
    app.state.session_secret = session_secret
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_dir.mkdir(parents=True, exist_ok=True)
    init_auth_routes(cfg.db_dir)
    init_web_routes(cfg.db_dir, cfg.data_dir)
    app.add_middleware(AuthMiddleware, session_secret=session_secret)
    app.include_router(auth_router)
    app.include_router(web_router)
    return app