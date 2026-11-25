"""Grounded answer generation: retrieve -> prompt -> cite -> structured result."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field

from rag import config
from rag.llm import LLM, estimate_cost

REFUSAL = "I don't have enough information in the provided documents"

SYSTEM_PROMPT = f"""You are a maintenance assistant for technicians working on conveyors, sortation equipment, drives, sensors, aircraft, and data center power and cooling.

Rules:
1. Answer ONLY from the numbered context passages in the user message. Do not use outside knowledge, even if you know the answer.
2. Cite every factual sentence inline with the passage number in square brackets, like [1] or [2][4]. Only cite numbers that appear in the context.
3. If the context does not contain enough information to answer at all, reply exactly: "{REFUSAL} to answer that." You may add one sentence saying what kind of document would help. Do not guess. If the context answers only part of the question, answer that part with citations and say in one plain sentence what is not covered (do not use the exact refusal sentence for partial answers).
4. Be practical and concise, like a lead tech briefing a coworker: short steps or bullets when it is a procedure, plain sentences otherwise. Under 250 words.
5. Safety: if the question involves lockout/tagout, hazardous energy, or electrical work, end with a line reminding the reader to follow their site's LOTO procedures and verify zero energy before starting work.
6. Work-order data in the context is synthetic demo data; say so if you quote numbers from it."""

SAFETY_KEYWORDS = re.compile(
    r"\b(lockout|tagout|loto|hazardous energy|zero energy|energi[sz]|de-?energi|electric|voltage|"
    r"breaker|arc flash|pdu|ups|vfd|drive|motor|wiring|live)\w*", re.I)
SAFETY_LINE = ("Safety: follow your site's lockout/tagout (LOTO) procedures and verify zero energy "
               "before starting work.")
_CITE = re.compile(r"\[(\d{1,2})\]")


@dataclass
class Citation:
    n: int
    source: str
    title: str
    page: str
    snippet: str


@dataclass
class AskResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    retrieval_ms: float = 0.0
    llm_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    est_cost_usd: float = 0.0
    grounded: bool = False
    refused: bool = False
    model: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def build_context(hits: list[dict]) -> str:
    parts = []
    for i, h in enumerate(hits, 1):
        loc = f"p.{h['page']}" if h["source"] != "work_orders.csv" else h["page"]
        parts.append(f"[{i}] Source: {h['title']} ({loc})\n{h['text']}")
    return "\n\n".join(parts)


def build_user_message(question: str, hits: list[dict]) -> str:
    return f"Context passages:\n\n{build_context(hits)}\n\nQuestion: {question}"


def cited_numbers(answer: str) -> list[int]:
    return sorted({int(n) for n in _CITE.findall(answer)})


def is_refusal(answer: str) -> bool:
    """A full refusal: the answer leads with the refusal sentence, or uses it without citing
    anything. A cited partial answer that mentions a gap is not a refusal."""
    norm = answer.lower().replace("’", "'").strip()
    if REFUSAL.lower() not in norm:
        return False
    return norm.startswith(REFUSAL.lower()) or not _CITE.search(answer)


def needs_safety_line(question: str, answer: str) -> bool:
    if not SAFETY_KEYWORDS.search(question):
        return False
    return not re.search(r"\b(loto|lockout)", answer, re.I)


def snippet(text: str, n: int = 240) -> str:
    body = text.split("\n", 1)[-1] if "\n" in text else text
    body = " ".join(body.split())
    return body[:n] + ("..." if len(body) > n else "")


def answer_question(question: str, retriever, llm: LLM, k: int = config.TOP_K) -> AskResult:
    t0 = time.perf_counter()
    hits = retriever.search(question, k=k)
    retrieval_ms = (time.perf_counter() - t0) * 1000

    if not hits:
        return AskResult(answer=f"{REFUSAL} to answer that.", retrieval_ms=retrieval_ms, refused=True,
                         model=llm.model)

    resp = llm.complete(SYSTEM_PROMPT, build_user_message(question, hits), config.LLM_MAX_TOKENS)
    answer = resp.text
    refused = is_refusal(answer)
    if not refused and needs_safety_line(question, answer):
        answer = f"{answer}\n\n{SAFETY_LINE}"

    nums = cited_numbers(answer)
    valid = [n for n in nums if 1 <= n <= len(hits)]
    citations = [Citation(n, hits[n - 1]["source"], hits[n - 1]["title"], str(hits[n - 1]["page"]),
                          snippet(hits[n - 1]["text"])) for n in valid]
    grounded = (not refused) and bool(valid) and len(valid) == len(nums)
    return AskResult(
        answer=answer,
        citations=citations,
        retrieval_ms=round(retrieval_ms, 1),
        llm_ms=round(resp.latency_ms, 1),
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        est_cost_usd=estimate_cost(resp.model, resp.input_tokens, resp.output_tokens),
        grounded=grounded,
        refused=refused,
        model=resp.model,
    )
