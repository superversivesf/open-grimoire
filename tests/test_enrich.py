import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.pipeline.enrich import Enricher


@pytest.mark.asyncio
async def test_enrich_leaf_writes_frontmatter(tmp_path):
    leaf = tmp_path / "goblin.md"
    leaf.write_text("# Goblin\n\nAC 15, HP 7, small humanoid.\n")
    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": '{"summary": "Goblin stat block.", "keywords": ["goblin", "monster", "AC"]}'}})
    e = Enricher(gw)
    result = await e.enrich_leaf(leaf, page=42)
    assert result["summary"] == "Goblin stat block."
    assert "goblin" in result["keywords"]
    content = leaf.read_text()
    assert content.startswith("---\n")
    assert "summary:" in content
    assert "keywords:" in content
    assert "page: 42" in content
    assert "# Goblin" in content


@pytest.mark.asyncio
async def test_enrich_leaf_handles_bad_json(tmp_path):
    leaf = tmp_path / "x.md"
    leaf.write_text("# X\n\ntext\n")
    gw = MagicMock()
    gw.call = AsyncMock(return_value={"message": {"content": "not json at all"}})
    e = Enricher(gw)
    result = await e.enrich_leaf(leaf, page=1)
    assert result["summary"] == ""
    assert result["keywords"] == []