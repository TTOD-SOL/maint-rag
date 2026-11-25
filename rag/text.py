"""Text helpers shared by indexing and retrieval."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*")
STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does", "for", "from", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "may", "must", "of", "on", "or", "should", "so", "such", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "was", "we", "what", "when", "where", "which", "while", "who", "why", "will", "with", "you", "your"]
)


def bm25_tokenize(text: str) -> list[str]:
    """Lowercase word tokens without stopwords. Keeps codes like 'wo-00042', '1910.147', 'ups'."""
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]
