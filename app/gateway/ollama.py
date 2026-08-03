from httpx import AsyncClient


class OllamaGateway:
    def __init__(self, host: str, models: dict[str, str], num_ctx: int = 32768):
        self.host = host.rstrip("/")
        self.models = models
        self.num_ctx = num_ctx
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = AsyncClient(base_url=self.host, timeout=300.0)
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

    async def pull(self, model: str) -> None:
        resp = await self._get_client().post("/api/pull", json={"name": model}, timeout=None)
        resp.raise_for_status()

    async def close(self):
        if self._client is not None:
            await self._client.aclose()