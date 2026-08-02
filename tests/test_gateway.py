import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.gateway.ollama import OllamaGateway


@pytest.mark.asyncio
async def test_call_maps_role_to_model():
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b", "enrich": "gemma:4b"})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"content": "hello"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.post = AsyncMock(return_value=mock_resp)
        result = await gw.call("query", "hi")
        assert result["message"]["content"] == "hello"
        called_kwargs = client_inst.post.call_args
        body = called_kwargs.kwargs["json"]
        assert body["model"] == "qwen:7b"
        assert body["messages"][0]["content"] == "hi"


@pytest.mark.asyncio
async def test_call_unknown_role_raises():
    gw = OllamaGateway("http://x", {"query": "m"})
    with pytest.raises(ValueError, match="unknown role"):
        await gw.call("bogus", "hi")


@pytest.mark.asyncio
async def test_call_with_tools():
    gw = OllamaGateway("http://x", {"query": "m"})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"content": "ok"}}
    mock_resp.raise_for_status = MagicMock()
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.post = AsyncMock(return_value=mock_resp)
        await gw.call("query", "hi", tools=[{"type": "function", "function": {"name": "f"}}])
        body = client_inst.post.call_args.kwargs["json"]
        assert "tools" in body
        assert len(body["tools"]) == 1