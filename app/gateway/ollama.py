import asyncio
import json
from typing import Any, AsyncGenerator, cast

from httpx import AsyncClient, ConnectError, RemoteProtocolError

from app.constants import OLLAMA_TIMEOUT, DEFAULT_NUM_CTX


class OllamaGateway:
    def __init__(self, host: str, models: dict[str, str], num_ctx: int = DEFAULT_NUM_CTX) -> None:
        self.host = host.rstrip("/")
        self.models = models
        self.num_ctx = num_ctx
        self._client: AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = AsyncClient(base_url=self.host, timeout=OLLAMA_TIMEOUT)
        return self._client

    async def _reset_client(self) -> None:
        # Do NOT hold the lock while closing: httpx aclose() blocks until
        # in-flight requests finish, so a hung request would deadlock every
        # other caller waiting on the lock. Close with a timeout instead.
        client = self._client
        if client is None:
            return
        self._client = None
        try:
            await asyncio.wait_for(client.aclose(), timeout=5.0)
        except Exception:
            pass

    async def call(self, role: str, prompt: str, tools: list[dict[str, Any]] | None = None, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        model = self.models.get(role)
        if not model:
            raise ValueError(f"unknown role: {role}")
        if messages is None:
            messages = [{"role": "user", "content": prompt}]
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": 0.3,
            },
        }
        if tools:
            body["tools"] = tools
        client = await self._get_client()
        try:
            resp = await client.post("/api/chat", json=body)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
        except Exception:
            await self._reset_client()
            raise

    async def pull(self, model: str) -> None:
        client = await self._get_client()
        resp = await client.post("/api/pull", json={"name": model}, timeout=300)
        resp.raise_for_status()

    async def stream(self, role: str, prompt: str, tools: list[dict[str, Any]] | None = None,
                     messages: list[dict[str, Any]] | None = None) -> AsyncGenerator[dict[str, Any], None]:
        """Streamed chat call — yields structured events as the model generates.

        Mirrors call() but with stream:true. Yields:
          {"type": "content", "text": str}  — one content delta per NDJSON line
          {"type": "tool_calls", "calls": [...]} — final chunk when the model
              emitted tool calls (Ollama sends them in the last done-chunk).
        """
        model = self.models.get(role)
        if not model:
            raise ValueError(f"unknown role: {role}")
        if messages is None:
            messages = [{"role": "user", "content": prompt}]
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_ctx": self.num_ctx,
                "temperature": 0.3,
            },
        }
        if tools:
            body["tools"] = tools
        client = await self._get_client()
        try:
            async with client.stream("POST", "/api/chat", json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise RuntimeError(f"ollama stream error: {chunk['error']}")
                    msg = chunk.get("message", {})
                    content = msg.get("content", "")
                    if content:
                        yield {"type": "content", "text": content}
                    tool_calls = msg.get("tool_calls")
                    if tool_calls:
                        yield {"type": "tool_calls", "calls": tool_calls}
        except (ConnectError, RemoteProtocolError):
            await self._reset_client()
            raise

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()