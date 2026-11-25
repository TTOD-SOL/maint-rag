"""PDF -> per-page text, keeping page numbers (printed labels where available)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_WS = re.compile(r"[ \t]+")


def _clean(text: str) -> str:
    text = text.replace("­", "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    # re-join words hyphenated across line breaks: "main-\ntenance" -> "maintenance"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    lines = [_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _page_labels(pdf: Path, n_pages: int) -> list[str]:
    sidecar = pdf.with_suffix(".pages.json")
    if sidecar.exists():
        pages = json.loads(sidecar.read_text())["pages"]
        if len(pages) == n_pages:
            return [p["label"] for p in pages]
    return [str(i + 1) for i in range(n_pages)]


def extract_pages(pdf: Path) -> list[dict]:
    """Return [{"page": label, "text": str}] using pypdf, falling back to pdfplumber
    for pages where pypdf returns (almost) nothing."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf))
    labels = _page_labels(pdf, len(reader.pages))
    out: list[dict] = []
    weak: list[int] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if len(text.strip()) < 40:
            weak.append(i)
        out.append({"page": labels[i], "text": _clean(text)})

    if weak:
        try:
            import pdfplumber

            with pdfplumber.open(str(pdf)) as doc:
                for i in weak:
                    alt = doc.pages[i].extract_text() or ""
                    if len(alt.strip()) > len(out[i]["text"]):
                        out[i]["text"] = _clean(alt)
        except Exception as e:
            log.warning("pdfplumber fallback failed for %s: %s", pdf.name, e)
    return [p for p in out if p["text"]]
