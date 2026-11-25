"""Hybrid retrieval: vector (Chroma + bge-small) and BM25, merged with Reciprocal Rank Fusion."""

from __future__ import annotations

import json
import os
import pickle
from dataclasses import dataclass
from typing import Any

from rag import config
from rag.text import bm25_tokenize

# bge models are trained with this instruction on the *query* side only.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def rrf_merge(ranked_lists: list[list[str]], k: int = 60, top_n: int | None = None) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion.

    Each input is a list of doc IDs, best first. A doc's fused score is
    sum(1 / (k + rank)) over the lists it appears in (rank is 1-based).
    Ties are broken by best single rank, then first appearance, so the output is
    deterministic. Returns [(id, score)] sorted by score descending.
    """
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    order: dict[str, int] = {}
    for lst in ranked_lists:
        seen: set[str] = set()
        for rank, doc_id in enumerate(lst, start=1):
            if doc_id in seen:  # ignore duplicates within one list
                continue
            seen.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            best_rank[doc_id] = min(best_rank.get(doc_id, rank), rank)
            order.setdefault(doc_id, len(order))
    merged = sorted(scores.items(), key=lambda kv: (-kv[1], best_rank[kv[0]], order[kv[0]]))
    return merged[:top_n] if top_n else merged


@dataclass
class Retriever:
    chunks: dict[str, dict]
    bm25_ids: list[str]
    bm25: Any
    collection: Any
    embedder: Any
    query_prefix: str = BGE_QUERY_PREFIX

    @classmethod
    def load(cls) -> Retriever:
        import chromadb
        from fastembed import TextEmbedding

        chunks = {}
        with open(config.CHUNKS_FILE) as f:
            for line in f:
                rec = json.loads(line)
                chunks[rec["id"]] = rec
        with open(config.BM25_FILE, "rb") as f:
            b = pickle.load(f)  # noqa: S301 - file is produced by our own build step
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR),
                                           settings=chromadb.Settings(anonymized_telemetry=False))
        col = client.get_collection(config.COLLECTION, embedding_function=None)
        embedder = TextEmbedding(config.EMBED_MODEL, cache_dir=config.FASTEMBED_CACHE)
        prefix = BGE_QUERY_PREFIX if os.getenv("BGE_QUERY_PREFIX", "1") == "1" else ""
        return cls(chunks, b["ids"], b["bm25"], col, embedder, prefix)

    @property
    def size(self) -> int:
        return len(self.chunks)

    def vector_search(self, query: str, n: int = config.CANDIDATES) -> list[str]:
        vec = next(iter(self.embedder.embed([self.query_prefix + query]))).tolist()
        res = self.collection.query(query_embeddings=[vec], n_results=n, include=[])
        return res["ids"][0]

    def bm25_search(self, query: str, n: int = config.CANDIDATES) -> list[str]:
        toks = bm25_tokenize(query)
        if not toks:
            return []
        scores = self.bm25.get_scores(toks)
        top = sorted(range(len(scores)), key=lambda i: -scores[i])[:n]
        return [self.bm25_ids[i] for i in top if scores[i] > 0]

    def search(self, query: str, k: int = config.TOP_K, mode: str = "hybrid",
               candidates: int = config.CANDIDATES) -> list[dict]:
        """mode: 'hybrid' (RRF of vector+BM25), 'vector', or 'bm25'."""
        if mode == "vector":
            ranked = [(i, 1.0 / (config.RRF_K + r)) for r, i in enumerate(self.vector_search(query, k), 1)]
        elif mode == "bm25":
            ranked = [(i, 1.0 / (config.RRF_K + r)) for r, i in enumerate(self.bm25_search(query, k), 1)]
        elif mode == "hybrid":
            ranked = rrf_merge([self.vector_search(query, candidates), self.bm25_search(query, candidates)],
                               k=config.RRF_K, top_n=k)
        else:
            raise ValueError(f"unknown mode {mode!r}")
        return [{**self.chunks[i], "score": s} for i, s in ranked if i in self.chunks]
