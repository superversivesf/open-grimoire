from httpx import AsyncClient
import json
import typing
from app.constants import OLLAMA_TIMEOUT, DEFAULT_NUM_CTX


class OllamaGateway:
    def __init__(self, host: str, models: dict[str, str], num_ctx: int = DEFAULT_NUM_CTX):
        self.host = host.rstrip("/")
        self.models = models
        self.num_ctx = num_ctx
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = AsyncClient(base_url=self.host, timeout=OLLAMA_TIMEOUT)
        return self._client

    async def call(self, role: str, prompt: str, tools: list | None = None, messages: list | None = None) -> dict:
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
        resp = await self._get_client().post("/api/chat", json=body)
        resp.raise_for_status()
        return resp.json()

    async def call_stream(self, role: str, messages: list, tools: list | None = None) -> typing.AsyncGenerator[str, None]:
        """Stream tokens from the LLM. Yields content chunks as they arrive.

        If the model makes tool calls, they arrive in the final chunk — this
        method yields content tokens only and returns the full final message
        via the `last_message` attribute.
        """
        model = self.models.get(role)
        if not model:
            raise ValueError(f"unknown role: {role}")
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
        self.last_message = {}
        async with self._get_client().stream("POST", "/api/chat", json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                content = msg.get("content", "")
                if msg.get("tool_calls"):
                    self.last_message = msg
                if content:
                    yield content
                if chunk.get("done"):
                    if not self.last_message:
                        self.last_message = msg

    async def pull(self, model: str) -> None:
        resp = await self._get_client().post("/api/pull", json={"name": model}, timeout=None)
        resp.raise_for_status()

    async def close(self):
        if self._client is not None:
            await self._client.aclose()