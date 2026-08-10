"""Token estimation utilities for usage logging.

Rough estimate: ~4 characters per token for English text.
Not exact, but sufficient for cost tracking.
"""
from typing import Any


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate input tokens for a chat request — includes all messages + tool defs."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if content:
            total += estimate_tokens(content)
        role = msg.get("role", "")
        total += 4  # role + formatting overhead
        name = msg.get("name", "")
        if name:
            total += estimate_tokens(name)
    return total


def estimate_response_tokens(text: str) -> int:
    """Estimate output tokens from response text."""
    return estimate_tokens(text)


# Explicit list of cloud models (not determined by "cloud" in name)
CLOUD_MODELS = {
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "gemma4:31b-cloud",
    "nemotron-3-nano:30b-cloud",
    "nemotron-3-ultra:cloud",
    "glm-5.2:cloud",
}

# Cost per 1M tokens (rough, based on DeepSeek API pricing)
# Cloud models cost more, local models are free
CLOUD_MODEL_COSTS = {
    "deepseek-v4-flash": {"input": 0.14, "output": 0.28},
    "deepseek-v4-pro": {"input": 0.27, "output": 1.10},
    "gemma4:31b-cloud": {"input": 0.10, "output": 0.20},
    "nemotron-3-nano:30b-cloud": {"input": 0.08, "output": 0.16},
    "nemotron-3-ultra:cloud": {"input": 0.20, "output": 0.40},
    "glm-5.2:cloud": {"input": 0.15, "output": 0.30},
    "default": {"input": 0.14, "output": 0.28},
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a model call."""
    is_cloud = model in CLOUD_MODELS
    if not is_cloud:
        return 0.0
    costs = CLOUD_MODEL_COSTS.get(model, CLOUD_MODEL_COSTS["default"])
    input_cost = (input_tokens / 1_000_000) * costs["input"]
    output_cost = (output_tokens / 1_000_000) * costs["output"]
    return round(input_cost + output_cost, 6)