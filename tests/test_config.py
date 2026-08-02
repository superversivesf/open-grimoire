from pathlib import Path
from app.config import load_config, Config


def test_load_config_reads_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """
ollama:
  host: http://localhost:11434
models:
  query: qwen2.5:7b-instruct-q4
  enrich: gemma3:4b-it-q4
  structure: qwen2.5:7b-instruct-q4
  vision: gemma3:4b-it-q4
paths:
  data_dir: ./data
  db_dir: ./db
"""
    )
    cfg = load_config(str(cfg_file))
    assert isinstance(cfg, Config)
    assert cfg.ollama_host == "http://localhost:11434"
    assert cfg.models["query"] == "qwen2.5:7b-instruct-q4"
    assert cfg.models["enrich"] == "gemma3:4b-it-q4"
    assert cfg.data_dir == Path("./data")
    assert cfg.db_dir == Path("./db")


def test_load_config_defaults_models_to_empty(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("ollama:\n  host: http://x\npaths:\n  data_dir: ./d\n  db_dir: ./b\n")
    cfg = load_config(str(cfg_file))
    assert cfg.models == {}