"""Shared fixtures. Tests never touch the real index or the Anthropic API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rag.llm import LLMResponse

CHUNKS = [
    {"chunk_id": "c1", "source": "osha_3120_loto.pdf", "title": "OSHA 3120", "page": "7",
     "text": "Verify isolation: attempt to start the machine to confirm zero energy state."},
    {"chunk_id": "c2", "source": "wo", "title": "Work orders", "page": "WO-0042",
     "text": "VFD drive overcurrent fault traced to failed cooling fan."},
]


class FakeRetriever:
    size = len(CHUNKS)

    def search(self, query: str, k: int = 6, **_) -> list[dict]:
        return [dict(c, score=1.0) for c in CHUNKS[:k]]


class FakeLLM:
    model = "fake-model"

    def __init__(self, text: str = "Try to start the machine to verify zero energy [1]."):
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> LLMResponse:
        self.calls.append((system, user))
        return LLMResponse(text=self.text, input_tokens=100, output_tokens=20, latency_ms=5.0,
                           model=self.model)


@pytest.fixture
def make_client(monkeypatch):
    """Build a TestClient around a fresh app (own rate limiter) with fakes."""
    from app.main import create_app

    monkeypatch.setenv("APP_API_KEY", "test-key")

    def _make(retriever=None, llm=None, daily_cap: int | None = None):
        if daily_cap is not None:
            monkeypatch.setenv("DEMO_DAILY_CAP", str(daily_cap))
        app = create_app(retriever=retriever or FakeRetriever(), llm=llm or FakeLLM())
        return TestClient(app)

    return _make


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c
