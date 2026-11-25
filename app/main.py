"""maint-rag API.

GET  /            demo UI
GET  /health      liveness + index status
POST /ask         authenticated (X-API-Key) question answering
POST /demo/ask    public, 10/hour per IP + global daily cap
GET  /metrics     Prometheus metrics
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.observability import (
    DEMO_REMAINING,
    HTTP_LATENCY,
    HTTP_REQUESTS,
    INDEX_CHUNKS,
    RATE_LIMITED,
    record_answer,
    request_id_var,
    setup_logging,
)
from rag import config

log = logging.getLogger("app")
STATIC = Path(__file__).parent / "static"
VERSION = os.getenv("RENDER_GIT_COMMIT", "")[:7] or os.getenv("APP_VERSION", "dev")
DEMO_RATE = os.getenv("DEMO_RATE_LIMIT", "10/hour")


# ------------------------------------------------------------------ models
class AskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    k: int | None = Field(None, ge=1, le=10)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("question is too short")
        return v


class CitationOut(BaseModel):
    n: int
    source: str
    title: str
    page: str
    snippet: str


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    retrieval_ms: float
    llm_ms: float
    input_tokens: int
    output_tokens: int
    est_cost_usd: float
    grounded: bool
    refused: bool
    model: str
    request_id: str


# ------------------------------------------------------------------ limits
def client_ip(request: Request) -> str:
    """Client IP for per-IP limits. HF Spaces sits behind a proxy that sets X-Forwarded-For.
    The header can be spoofed, which is why the global daily cap exists as the hard stop."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class DailyCap:
    """Global per-UTC-day counter for the public demo. In-memory: resets on restart,
    which is acceptable for a single-replica free Space (documented in RUNBOOK)."""

    def __init__(self, cap: int):
        self.cap = cap
        self._day = ""
        self._used = 0
        self._lock = threading.Lock()

    def _roll(self) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._used = today, 0

    def try_take(self) -> bool:
        with self._lock:
            self._roll()
            if self._used >= self.cap:
                return False
            self._used += 1
            DEMO_REMAINING.set(self.cap - self._used)
            return True

    def remaining(self) -> int:
        with self._lock:
            self._roll()
            return self.cap - self._used


def make_limiter() -> Limiter:
    """One limiter per app instance: slowapi registers route limits on the limiter, so sharing
    one across create_app() calls would count each request once per registration."""
    return Limiter(key_func=client_ip, storage_uri="memory://", headers_enabled=False)


# ------------------------------------------------------------------ app
def create_app(retriever=None, llm=None) -> FastAPI:
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.retriever is None:
            from rag.retrieve import Retriever

            t0 = time.perf_counter()
            try:
                app.state.retriever = Retriever.load()
                log.info("index loaded", extra={"chunks": app.state.retriever.size,
                                                "load_ms": round((time.perf_counter() - t0) * 1000)})
            except Exception:
                log.exception("index failed to load")
        if app.state.retriever is not None:
            INDEX_CHUNKS.set(app.state.retriever.size)
            if os.getenv("WARMUP", "1") == "1":
                # The first ONNX inference is slow on a throttled CPU (38 s at 0.1 CPU), so pay for it
                # at startup instead of on the first visitor's question.
                t0 = time.perf_counter()
                try:
                    app.state.retriever.search("warmup", k=1)
                    log.info("warmup done", extra={"warmup_ms": round((time.perf_counter() - t0) * 1000)})
                except Exception:
                    log.exception("warmup failed")
        if app.state.llm is None and os.getenv("WARMUP", "1") == "1":
            # Importing the SDK and building the client is slow on a throttled CPU; do it now.
            # No network call is made. If the key is missing, get_llm() reports it per request.
            try:
                from rag.llm import get_llm as make_llm

                app.state.llm = make_llm()
            except Exception as e:
                log.warning("llm client not initialised at startup", extra={"error": type(e).__name__})
        DEMO_REMAINING.set(app.state.demo_cap.remaining())
        yield

    app = FastAPI(title="maint-rag", version=VERSION, lifespan=lifespan,
                  description="Maintenance & data center operations RAG assistant")
    app.state.retriever = retriever
    app.state.llm = llm
    app.state.demo_cap = DailyCap(int(os.getenv("DEMO_DAILY_CAP", "200")))
    limiter = make_limiter()
    app.state.limiter = limiter

    # -------------------------------------------------------------- middleware
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            elapsed = time.perf_counter() - t0
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            if path != "/metrics":
                HTTP_LATENCY.labels(path).observe(elapsed)
                log.info("request", extra={"method": request.method, "path": request.url.path,
                                           "status": status, "latency_ms": round(elapsed * 1000, 1)})
            request_id_var.reset(token)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(request: Request, exc: RateLimitExceeded):
        RATE_LIMITED.labels("per_ip").inc()
        log.warning("rate limited", extra={"kind": "per_ip", "limit": str(exc.detail)})
        return JSONResponse(status_code=429, headers={"Retry-After": "3600"},
                            content={"detail": f"Rate limit exceeded ({DEMO_RATE} per IP). Try again later."})

    # -------------------------------------------------------------- deps
    def get_retriever(request: Request):
        r = request.app.state.retriever
        if r is None:
            raise HTTPException(503, "Index not loaded")
        return r

    def get_llm(request: Request):
        if request.app.state.llm is None:
            from rag.llm import get_llm as make_llm

            try:
                request.app.state.llm = make_llm()
            except Exception as e:
                log.error("llm init failed", extra={"error": type(e).__name__})
                raise HTTPException(503, "LLM not configured") from e
        return request.app.state.llm

    def require_api_key(x_api_key: str | None = Header(None)) -> None:
        expected = os.getenv("APP_API_KEY", "")
        if not expected:
            raise HTTPException(503, "API key auth not configured on server")
        if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
            raise HTTPException(401, "Invalid or missing X-API-Key", headers={"WWW-Authenticate": "ApiKey"})

    def run_ask(body: AskRequest, retriever, llm, endpoint: str) -> AskResponse:
        from rag.generate import answer_question
        from rag.llm import LLMError

        try:
            res = answer_question(body.question, retriever, llm, k=body.k or config.TOP_K)
        except LLMError as e:
            log.error("llm error", extra={"error": str(e), "retryable": e.retryable, "llm_status": e.status})
            from app.observability import ANSWERS

            ANSWERS.labels(endpoint, "error").inc()
            if e.status == 429:
                raise HTTPException(503, "LLM quota or rate limit reached. Try again later.") from e
            raise HTTPException(502, "Upstream LLM error") from e
        record_answer(endpoint, res)
        log.info("answer", extra={"endpoint": endpoint, "question_chars": len(body.question),
                                  "k": body.k or config.TOP_K, "grounded": res.grounded,
                                  "refused": res.refused, "citations": len(res.citations),
                                  "retrieval_ms": res.retrieval_ms, "llm_ms": res.llm_ms,
                                  "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                                  "cost_usd": res.est_cost_usd})
        return AskResponse(**res.to_dict(), request_id=request_id_var.get())

    # -------------------------------------------------------------- routes
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health(request: Request):
        r = request.app.state.retriever
        ok = r is not None
        body = {"status": "ok" if ok else "degraded", "index_loaded": ok,
                "chunks": r.size if ok else 0, "model": config.LLM_MODEL, "version": VERSION,
                "demo_remaining_today": request.app.state.demo_cap.remaining()}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.post("/ask", response_model=AskResponse, dependencies=[Depends(require_api_key)])
    def ask(body: AskRequest, retriever=Depends(get_retriever), llm=Depends(get_llm)):
        return run_ask(body, retriever, llm, "ask")

    @app.post("/demo/ask", response_model=AskResponse)
    @limiter.limit(DEMO_RATE)
    def demo_ask(request: Request, body: AskRequest, retriever=Depends(get_retriever), llm=Depends(get_llm)):
        if not request.app.state.demo_cap.try_take():
            RATE_LIMITED.labels("daily_cap").inc()
            log.warning("demo daily cap reached", extra={"kind": "daily_cap"})
            raise HTTPException(429, "The public demo hit its daily question limit. It resets at 00:00 UTC.",
                                headers={"Retry-After": "3600"})
        return run_ask(body, retriever, llm, "demo")

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "7860")), log_config=None)


if __name__ == "__main__":
    run()
