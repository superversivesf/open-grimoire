from dataclasses import dataclass, field
from pathlib import Path
import os
import yaml
from app.constants import DEFAULT_NUM_CTX


@dataclass
class Config:
    # Required fields (no defaults) must come first
    ollama_host: str
    session_secret: str

    # Optional fields with defaults
    models: dict[str, str] = field(default_factory=dict)
    data_dir: Path = Path("./data")
    db_dir: Path = Path("./db")
    host: str = "0.0.0.0"
    port: int = 8000
    cookie_secure: bool = True
    num_ctx: int = DEFAULT_NUM_CTX
    allow_registration: bool = False


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    ollama = raw.get("ollama", {})
    models = raw.get("models", {})
    paths = raw.get("paths", {})
    server = raw.get("server", {})
    options = raw.get("options", {})

    session_secret = os.environ.get("SESSION_SECRET", server.get("secret"))
    placeholders = {
        "change-me-in-production",
        "change-me-via-SESSION_SECRET-env",
        "dev-secret-not-for-production",
    }
    if (
        not session_secret
        or session_secret in placeholders
        or len(session_secret) < 32
    ):
        if not os.environ.get("DEV_MODE"):
            raise ValueError(
                "SESSION_SECRET must be set to a random value of at least 32 "
                "characters in production (set DEV_MODE=1 to allow a default)"
            )
        session_secret = "dev-secret-not-for-production"

    # Default to secure cookies outside dev/test; explicit env always wins.
    cookie_secure = os.environ.get("COOKIE_SECURE")
    if cookie_secure is None:
        cookie_secure = not (os.environ.get("DEV_MODE") == "1" or os.environ.get("TEST_MODE") == "1")
    else:
        cookie_secure = cookie_secure.lower() in ("1", "true", "yes")

    return Config(
        ollama_host=os.environ.get("OLLAMA_HOST", ollama.get("host", "http://localhost:11434")),
        models=dict(models),
        data_dir=Path(os.environ.get("DATA_DIR", paths.get("data_dir", "./data"))),
        db_dir=Path(os.environ.get("DB_DIR", paths.get("db_dir", "./db"))),
        host=os.environ.get("HOST", server.get("host", "0.0.0.0")),
        port=int(os.environ.get("PORT", server.get("port", 8000))),
        session_secret=session_secret,
        cookie_secure=cookie_secure,
        allow_registration=os.environ.get("ALLOW_REGISTRATION", "").lower() in ("1", "true", "yes"),
        num_ctx=int(os.environ.get("NUM_CTX", options.get("num_ctx", DEFAULT_NUM_CTX))),
    )