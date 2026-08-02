import pytest
from pathlib import Path
import tempfile
import os


@pytest.fixture
def tmp_dirs(tmp_path):
    data_dir = tmp_path / "data"
    db_dir = tmp_path / "db"
    data_dir.mkdir()
    db_dir.mkdir()
    return {"data": data_dir, "db": db_dir}