from fastapi import FastAPI
from pathlib import Path
import asyncio
from app.config import Config
from app.auth.middleware import AuthMiddleware
from app.auth.routes import router as auth_router, init_auth_routes
from app.web.routes import router as web_router, init_web_routes
from app.agent.routes import router as agent_router, init_agent_routes
from app.gateway.ollama import OllamaGateway
from app.pipeline.runner import PipelineRunner
from app.queue.worker import QueueWorker


def create_app(cfg: Config, session_secret: str) -> FastAPI:
    app = FastAPI(title="RPG Master")
    app.state.config = cfg
    app.state.session_secret = session_secret
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.db_dir.mkdir(parents=True, exist_ok=True)
    init_auth_routes(cfg.db_dir)
    init_web_routes(cfg.db_dir, cfg.data_dir)
    gateway = OllamaGateway(cfg.ollama_host, cfg.models)
    app.state.gateway = gateway
    init_agent_routes(cfg.db_dir, cfg.data_dir, gateway)
    app.add_middleware(AuthMiddleware, session_secret=session_secret)
    app.include_router(auth_router)
    app.include_router(web_router)
    app.include_router(agent_router)

    runner = PipelineRunner(gateway, cfg.data_dir, cfg.db_dir)
    worker = QueueWorker(runner, cfg.db_dir, poll_interval=2.0)
    app.state.worker = worker

    @app.on_event("startup")
    async def _start_worker():
        asyncio.create_task(worker.run_forever())

    @app.on_event("shutdown")
    async def _close_gateway():
        await gateway.close()

    return app