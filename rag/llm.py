"""LLM provider interface. Only Anthropic is implemented; OpenAI/Bedrock would be a
new class implementing `LLM.complete` and an entry in `get_llm`."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rag import config


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    model: str
    stop_reason: str | None = None


class LLMError(RuntimeError):
    """Raised for provider failures the API layer should surface as 502/503."""

    def __init__(self, message: str, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLM(Protocol):
    model: str

    def complete(self, system: str, user: str, max_tokens: int) -> LLMResponse: ...


def load_local_env() -> None:
    """Load .env, then ~/.openclaw/env, without overriding real env vars. Never prints values."""
    from dotenv import load_dotenv

    load_dotenv(config.ROOT / ".env", override=False)
    openclaw = Path.home() / ".openclaw" / "env"
    if openclaw.exists():
        load_dotenv(openclaw, override=False)


class AnthropicLLM:
    def __init__(self, model: str | None = None, effort: str | None = None):
        import anthropic

        self.model = model or config.LLM_MODEL
        self.effort = effort or config.LLM_EFFORT
        if not os.getenv("ANTHROPIC_API_KEY"):
            load_local_env()
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(timeout=60.0, max_retries=2)

    def complete(self, system: str, user: str, max_tokens: int = config.LLM_MAX_TOKENS) -> LLMResponse:
        a = self._anthropic
        t0 = time.perf_counter()
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort},
            )
        except a.AuthenticationError as e:
            raise LLMError("LLM authentication failed (check ANTHROPIC_API_KEY)", status=401) from e
        except a.RateLimitError as e:
            raise LLMError("LLM rate limited or quota exhausted", retryable=True, status=429) from e
        except a.APIStatusError as e:
            raise LLMError(f"LLM API error {e.status_code}", retryable=e.status_code >= 500,
                           status=e.status_code) from e
        except a.APIConnectionError as e:
            raise LLMError("LLM connection error", retryable=True) from e
        latency = (time.perf_counter() - t0) * 1000
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        if msg.stop_reason == "refusal":
            text = "I can't help with that request."
        return LLMResponse(text, msg.usage.input_tokens, msg.usage.output_tokens, latency, self.model,
                           msg.stop_reason)


def get_llm(provider: str | None = None) -> LLM:
    provider = (provider or config.LLM_PROVIDER).lower()
    if provider == "anthropic":
        return AnthropicLLM()
    raise ValueError(f"LLM provider {provider!r} not implemented (only 'anthropic')")


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = config.PRICING.get(model, (0.0, 0.0))
    return round(input_tokens * pin / 1e6 + output_tokens * pout / 1e6, 6)
