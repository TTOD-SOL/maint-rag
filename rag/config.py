"""Central settings. Everything tunable lives here and can be overridden by env vars."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
PAGES_FILE = PROCESSED_DIR / "pages.jsonl"
WORK_ORDERS_CSV = ROOT / "data" / "synthetic" / "work_orders.csv"

INDEX_DIR = Path(os.getenv("INDEX_DIR", ROOT / "index"))
CHROMA_DIR = INDEX_DIR / "chroma"
CHUNKS_FILE = INDEX_DIR / "chunks.jsonl"
BM25_FILE = INDEX_DIR / "bm25.pkl"
COLLECTION = "maint_rag"

EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
# fastembed model cache; baked into the Docker image at build time.
FASTEMBED_CACHE = os.getenv("FASTEMBED_CACHE_PATH", str(ROOT / ".cache" / "fastembed"))

# Chunking (tokens are estimated as words * 1.3; see rag/chunking.py)
CHUNK_TARGET_TOKENS = int(os.getenv("CHUNK_TARGET_TOKENS", "350"))
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", "500"))
CHUNK_MIN_TOKENS = int(os.getenv("CHUNK_MIN_TOKENS", "150"))
CHUNK_OVERLAP = float(os.getenv("CHUNK_OVERLAP", "0.15"))
CHUNK_HEADINGS = os.getenv("CHUNK_HEADINGS", "1") == "1"  # 0 = plain fixed-size windows (ablation)

# Retrieval
CANDIDATES = int(os.getenv("RETRIEVAL_CANDIDATES", "20"))
TOP_K = int(os.getenv("TOP_K", "6"))
RRF_K = int(os.getenv("RRF_K", "60"))

# Generation
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4000"))
LLM_EFFORT = os.getenv("LLM_EFFORT", "low")

# USD per million tokens (input, output). Update when pricing changes.
PRICING = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# Source documents: file in data/raw -> display title.
SOURCES = {
    "osha_3120_loto.pdf": "OSHA 3120: Control of Hazardous Energy (Lockout/Tagout)",
    "doe_femp_datacenter_best_practices.pdf": "DOE/FEMP Best Practices Guide for Energy-Efficient Data Center Design (2024)",
    "faa_h_8083_30b_selected.pdf": "FAA-H-8083-30B Aviation Maintenance Technician Handbook - General",
}
