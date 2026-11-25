"""Heading-aware chunking.

Pages are split into blocks at detected headings. Blocks are packed into chunks
of ~CHUNK_TARGET_TOKENS (hard cap CHUNK_MAX_TOKENS) with ~15% overlap between
consecutive chunks of the same section. A new heading starts a new chunk once
the current one is big enough to stand alone (CHUNK_MIN_TOKENS). Every chunk is
prefixed with its section heading so both BM25 and the embedder see it.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

from rag import config

_SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with", "&", "vs"}
_NUMBERED = re.compile(r"^(\d+(\.\d+)*|[A-Z]\.)\s+[A-Z]")
_BULLET = re.compile(r"^[•▪●\-*•]")


def est_tokens(text: str) -> int:
    """Cheap token estimate (BPE tokenizers average ~1.3 tokens per English word)."""
    return int(len(text.split()) * 1.3)


def is_heading(line: str) -> bool:
    if not config.CHUNK_HEADINGS:
        return False
    s = line.strip()
    if not (3 <= len(s) <= 70) or _BULLET.match(s):
        return False
    if s[-1] in ".,;:" or not (s[0].isupper() or s[0].isdigit()):
        return False
    words = s.rstrip("?").split()
    if len(words) > 10 or len(words) < 1:
        return False
    if s.endswith("?"):  # question-style headings (common in OSHA booklets)
        return len(words) <= 12 and s[0].isupper()
    if _NUMBERED.match(s):
        return True
    alpha = [w for w in words if w[0].isalpha()]
    if not alpha:
        return False
    caps = [w for w in alpha if w[0].isupper() or w.lower() in _SMALL]
    return len(caps) / len(alpha) >= 0.8 and any(c.islower() for c in s)


@dataclass
class Chunk:
    source: str
    title: str
    page: str
    text: str
    section: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        h = hashlib.sha1(f"{self.source}|{self.page}|{self.text}".encode()).hexdigest()
        return h[:16]

    def metadata(self) -> dict:
        return {"source": self.source, "title": self.title, "page": self.page,
                "section": self.section, "chunk_id": self.chunk_id, **self.extra}


def strip_running_lines(pages: list[dict]) -> list[dict]:
    """Remove running headers/footers: lines equal to the page label or that repeat
    on more than 25% of pages."""
    counts = Counter(ln for p in pages for ln in set(p["text"].splitlines()))
    limit = max(3, int(len(pages) * 0.25))
    out = []
    for p in pages:
        keep = [ln for ln in p["text"].splitlines()
                if ln.strip() != p["page"] and counts[ln] <= limit and not re.fullmatch(r"\d{1,3}", ln.strip())]
        out.append({"page": p["page"], "text": "\n".join(keep)})
    return out


def _blocks(pages: list[dict]) -> list[tuple[str, list[tuple[str, str]]]]:
    """Split pages into (heading, [(page_label, line), ...]) blocks."""
    blocks: list[tuple[str, list[tuple[str, str]]]] = []
    heading, lines = "", []
    for p in pages:
        for ln in p["text"].splitlines():
            if is_heading(ln):
                if lines:
                    blocks.append((heading, lines))
                heading, lines = ln.strip(), []
            else:
                lines.append((p["page"], ln))
    if lines:
        blocks.append((heading, lines))
    return blocks


def chunk_document(pages: list[dict], source: str, title: str,
                   target: int | None = None, max_tokens: int | None = None,
                   min_tokens: int | None = None, overlap: float | None = None) -> list[Chunk]:
    target = target or config.CHUNK_TARGET_TOKENS
    max_tokens = max_tokens or config.CHUNK_MAX_TOKENS
    min_tokens = min_tokens or config.CHUNK_MIN_TOKENS
    overlap = config.CHUNK_OVERLAP if overlap is None else overlap

    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_heading, cur_page = "", ""

    def flush(carry: bool) -> None:
        nonlocal cur
        body = " ".join(cur).strip()
        if est_tokens(body) >= 20:
            text = f"{cur_heading}\n{body}" if cur_heading else body
            chunks.append(Chunk(source, title, cur_page, text, cur_heading))
        if carry and cur:
            words = body.split()
            n = int(len(words) * overlap)
            cur = [" ".join(words[-n:])] if n else []
        else:
            cur = []

    need_page = True
    for heading, lines in _blocks(strip_running_lines(pages)):
        if cur and est_tokens(" ".join(cur)) >= min_tokens:
            flush(carry=False)
        if not cur:
            cur_heading, need_page = heading, True
        elif heading and heading != cur_heading:
            # small previous section: fold this heading in as a sub-heading
            cur.append(f"\n{heading}\n")
        for page, ln in lines:
            if need_page:
                cur_page, need_page = page, False
            cur.append(ln)
            if est_tokens(" ".join(cur)) >= target:
                # finish at a sentence boundary if one is near, else at the hard cap
                if ln.rstrip().endswith((".", "?", "!", ":")) or est_tokens(" ".join(cur)) >= max_tokens:
                    flush(carry=True)
                    need_page = True
    if cur:
        flush(carry=False)
    return chunks
