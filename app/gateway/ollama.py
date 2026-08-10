import asyncio
from httpx import AsyncClient
import json
from typing import Any, cast
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
        async with self._client_lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

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

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()