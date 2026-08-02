from dataclasses import dataclass, field
from pathlib import Path
import os
import yaml


@dataclass
class Config:
    ollama_host: str
    models: dict[str, str] = field(default_factory=dict)
    data_dir: Path = Path("./data")
    db_dir: Path = Path("./db")
    host: str = "0.0.0.0"
    port: int = 8000
    session_secret: str = "change-me-in-production"


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    ollama = raw.get("ollama", {})
    models = raw.get("models", {})
    paths = raw.get("paths", {})
    server = raw.get("server", {})

    return Config(
        ollama_host=os.environ.get("OLLAMA_HOST", ollama.get("host", "http://localhost:11434")),
        session_secret=os.environ.get("SESSION_SECRET", server.get("secret", "change-me-in-production")),
        models=dict(models),
        data_dir=Path(os.environ.get("DATA_DIR", paths.get("data_dir", "./data"))),
        db_dir=Path(os.environ.get("DB_DIR", paths.get("db_dir", "./db"))),
        host=os.environ.get("HOST", server.get("host", "0.0.0.0")),
        port=int(os.environ.get("PORT", server.get("port", 8000))),
    )