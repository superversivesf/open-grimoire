from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class Config:
    ollama_host: str
    models: dict[str, str] = field(default_factory=dict)
    data_dir: Path = Path("./data")
    db_dir: Path = Path("./db")


def load_config(path: str) -> Config:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    ollama = raw.get("ollama", {})
    models = raw.get("models", {})
    paths = raw.get("paths", {})
    return Config(
        ollama_host=ollama.get("host", "http://localhost:11434"),
        models=dict(models),
        data_dir=Path(paths.get("data_dir", "./data")),
        db_dir=Path(paths.get("db_dir", "./db")),
    )