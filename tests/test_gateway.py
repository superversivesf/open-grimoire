import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import ConnectError
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


class _AsyncLineIter:
    """Async-iterable of NDJSON lines, mimicking httpx stream response.aiter_lines()."""

    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        self._it = iter(self._lines)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _make_stream_resp(lines):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = MagicMock(return_value=_AsyncLineIter(lines))
    return mock_resp


class _FakeStreamCtx:
    """Mimics httpx AsyncClient.stream() context manager returning the response."""

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_stream_yields_content_deltas():
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b"})
    lines = [
        '{"message": {"content": "Go", "role": "assistant"}}',
        '{"message": {"content": "blins", "role": "assistant"}}',
        '{"message": {"content": "", "role": "assistant"}}',
    ]
    mock_resp = _make_stream_resp(lines)
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.stream = MagicMock(return_value=_FakeStreamCtx(mock_resp))
        deltas = [d async for d in gw.stream("query", "hi")]
        assert deltas == [
            {"type": "content", "text": "Go"},
            {"type": "content", "text": "blins"},
        ]
        body = client_inst.stream.call_args.kwargs["json"]
        assert body["stream"] is True
        assert body["model"] == "qwen:7b"


@pytest.mark.asyncio
async def test_stream_yields_tool_calls_from_final_chunk():
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b"})
    lines = [
        '{"message": {"content": "", "role": "assistant"}}',
        '{"message": {"content": "", "role": "assistant", "tool_calls": '
        '[{"function": {"name": "fts_search", "arguments": "{\\"query\\": \\"goblin\\"}"}}]}}',
    ]
    mock_resp = _make_stream_resp(lines)
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.stream = MagicMock(return_value=_FakeStreamCtx(mock_resp))
        deltas = [d async for d in gw.stream("query", "hi", tools=[{"type": "function"}])]
        assert deltas == [
            {"type": "tool_calls", "calls": [
                {"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}
            ]}
        ]


@pytest.mark.asyncio
async def test_stream_unknown_role_raises():
    gw = OllamaGateway("http://x", {"query": "m"})
    with pytest.raises(ValueError, match="unknown role"):
        async for _ in gw.stream("bogus", "hi"):
            pass


@pytest.mark.asyncio
async def test_stream_midstream_error_raises():
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b"})
    lines = [
        '{"message": {"content": "Go", "role": "assistant"}}',
        '{"error": "model exploded"}',
    ]
    mock_resp = _make_stream_resp(lines)
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.stream = MagicMock(return_value=_FakeStreamCtx(mock_resp))
        got = []
        with pytest.raises(RuntimeError, match="model exploded"):
            async for d in gw.stream("query", "hi"):
                got.append(d)
        assert got == [{"type": "content", "text": "Go"}]


@pytest.mark.asyncio
async def test_stream_connection_error_resets_client():
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b"})
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.stream = MagicMock(side_effect=ConnectError("boom"))
        client_inst.aclose = AsyncMock()
        with pytest.raises(ConnectError):
            async for _ in gw.stream("query", "hi"):
                pass
        assert client_inst.aclose.await_count >= 1, "client must be reset on connection error"


@pytest.mark.asyncio
async def test_call_http_error_does_not_reset_client():
    """An HTTP 4xx/5xx (raise_for_status) must NOT tear down the shared
    client — only connection-level failures warrant a reset."""
    from httpx import HTTPStatusError, Request, Response as HttpxResponse
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b"})
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.raise_for_status = MagicMock(
        side_effect=HTTPStatusError("boom", request=Request("POST", "http://x"), response=HttpxResponse(500))
    )
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.post = AsyncMock(return_value=mock_resp)
        client_inst.aclose = AsyncMock()
        with pytest.raises(HTTPStatusError):
            await gw.call("query", "hi")
        assert client_inst.aclose.await_count == 0, \
            "HTTP status errors must not reset the shared client"


@pytest.mark.asyncio
async def test_call_connection_error_resets_client():
    """Connection-level failures must still reset the client."""
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b"})
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        client_inst = MockClient.return_value
        client_inst.post = AsyncMock(side_effect=ConnectError("boom"))
        client_inst.aclose = AsyncMock()
        with pytest.raises(ConnectError):
            await gw.call("query", "hi")
        assert client_inst.aclose.await_count >= 1, \
            "connection errors must reset the client"


@pytest.mark.asyncio
async def test_reset_client_swaps_atomically():
    """_reset_client must swap in a fresh client under the lock so a
    concurrent _get_client never returns a client that is being closed."""
    gw = OllamaGateway("http://ollama:11434", {"query": "qwen:7b"})
    with patch("app.gateway.ollama.AsyncClient") as MockClient:
        old = MagicMock()
        old.aclose = AsyncMock()
        fresh = MagicMock()
        fresh.aclose = AsyncMock()
        MockClient.side_effect = [fresh]
        gw._client = old
        await gw._reset_client()
        assert gw._client is fresh, "a fresh client must be swapped in"
        assert old.aclose.await_count >= 1, "old client must be closed"