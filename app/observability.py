"""Structured JSON logging + Prometheus metrics."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time

from prometheus_client import Counter, Gauge, Histogram

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# Fields that must never reach the logs, even if someone passes them via `extra=`.
_REDACT = {"api_key", "x-api-key", "authorization", "anthropic_api_key", "prompt", "system"}


class JsonFormatter(logging.Formatter):
    _std = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for k, v in vars(record).items():
            if k not in self._std and not k.startswith("_"):
                out[k] = "[redacted]" if k.lower() in _REDACT else v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "huggingface_hub", "chromadb", "pypdf", "fontTools", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # uvicorn's access log duplicates our request log; keep its error log.
    logging.getLogger("uvicorn.access").disabled = True


# ------------------------------------------------------------------ metrics
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
HTTP_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["path"],
                         buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 20, 30))
RETRIEVAL_LATENCY = Histogram("rag_retrieval_seconds", "Hybrid retrieval latency",
                              buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2))
LLM_LATENCY = Histogram("rag_llm_seconds", "LLM call latency", buckets=(0.5, 1, 2, 3, 5, 8, 13, 20, 30))
LLM_TOKENS = Counter("rag_llm_tokens_total", "LLM tokens", ["direction"])
LLM_COST = Counter("rag_llm_cost_usd_total", "Estimated LLM spend in USD")
ANSWERS = Counter("rag_answers_total", "Answers by outcome", ["endpoint", "outcome"])
RATE_LIMITED = Counter("rag_rate_limited_total", "Requests rejected by limits", ["kind"])
DEMO_REMAINING = Gauge("rag_demo_daily_remaining", "Demo requests left today (global cap)")
INDEX_CHUNKS = Gauge("rag_index_chunks", "Chunks in the loaded index")


def record_answer(endpoint: str, result) -> None:
    RETRIEVAL_LATENCY.observe(result.retrieval_ms / 1000)
    if result.llm_ms:
        LLM_LATENCY.observe(result.llm_ms / 1000)
    LLM_TOKENS.labels("input").inc(result.input_tokens)
    LLM_TOKENS.labels("output").inc(result.output_tokens)
    LLM_COST.inc(result.est_cost_usd)
    outcome = "refused" if result.refused else ("grounded" if result.grounded else "ungrounded")
    ANSWERS.labels(endpoint, outcome).inc()
