"""Ingestion: PDFs + synthetic work orders -> chunks -> Chroma (vectors) + BM25.

    uv run python -m rag.ingest            # build/refresh the index
    uv run python -m rag.ingest --rebuild  # wipe and rebuild from scratch

Idempotent: chunk IDs are content hashes, so re-running upserts the same IDs and
deletes any IDs that no longer exist. Nothing is ever duplicated.

PDF text is cached in data/processed/pages.jsonl (committed to git, ~1 MB). That
lets Docker and CI build the index without hitting .gov servers, which sometimes
block datacenter IPs. If the PDFs are present locally they win.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import pickle
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

from rag import config
from rag.chunking import Chunk, chunk_document
from rag.text import bm25_tokenize

log = logging.getLogger("rag.ingest")


# ---------------------------------------------------------------- sources
def load_pages() -> dict[str, list[dict]]:
    """source file -> pages. Re-extract from PDFs when present, else use the cache."""
    cached: dict[str, list[dict]] = defaultdict(list)
    if config.PAGES_FILE.exists():
        for line in config.PAGES_FILE.read_text().splitlines():
            rec = json.loads(line)
            cached[rec["source"]].append({"page": rec["page"], "text": rec["text"]})

    from rag.pdf import extract_pages

    out: dict[str, list[dict]] = {}
    for fname in config.SOURCES:
        pdf = config.RAW_DIR / fname
        if pdf.exists():
            out[fname] = extract_pages(pdf)
        elif cached.get(fname):
            out[fname] = cached[fname]
        else:
            log.warning("missing source %s (no PDF, no cache) - skipped", fname)

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.PAGES_FILE, "w") as f:
        for fname, pages in out.items():
            for p in pages:
                f.write(json.dumps({"source": fname, **p}, ensure_ascii=False) + "\n")
    return out


def read_work_orders(path: Path = config.WORK_ORDERS_CSV) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return list(csv.DictReader(ln for ln in f if not ln.startswith("#")))


def work_order_chunks(rows: list[dict]) -> list[Chunk]:
    chunks = []
    for r in rows:
        text = (
            f"Work order {r['wo_id']} ({r['date']}) - {r['asset_type']} {r['asset_id']}\n"
            f"Symptom: {r['symptom']}\nFault code: {r['fault_code']}\n"
            f"Root cause: {r['root_cause']}\nAction taken: {r['action_taken']}\n"
            f"Downtime: {r['downtime_min']} min\nTechnician notes: {r['technician_notes']}"
        )
        chunks.append(Chunk("work_orders.csv", f"Synthetic work order {r['wo_id']}", r["wo_id"], text,
                            r["asset_type"], {"wo_id": r["wo_id"], "asset_type": r["asset_type"]}))
    return chunks


def work_order_summaries(rows: list[dict]) -> list[Chunk]:
    """Pre-computed analytics chunks, one per asset type plus a fleet overview.

    Top-k retrieval can't count across 300 rows, so questions like "most common
    root cause of VFD faults" are answered from these roll-ups instead."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_type[r["asset_type"]].append(r)
    chunks = []
    overview = []
    for atype, rs in sorted(by_type.items()):
        causes = Counter(r["root_cause"] for r in rs)
        codes = Counter(r["fault_code"] for r in rs)
        assets = Counter(r["asset_id"] for r in rs)
        down = [int(r["downtime_min"]) for r in rs]
        dt_by_cause: dict[str, int] = defaultdict(int)
        for r in rs:
            dt_by_cause[r["root_cause"]] += int(r["downtime_min"])
        lines = [
            f"Work order analytics summary: {atype} (synthetic data, {len(rs)} work orders, "
            f"{rs[0]['date'][:4]})",
            f"Total downtime: {sum(down)} min; average {sum(down) / len(down):.0f} min per work order.",
            "Most common root causes (count, share, total downtime):",
            *[f"- {c}: {n} work orders ({n / len(rs):.0%}), {dt_by_cause[c]} min downtime"
              for c, n in causes.most_common()],
            "Fault codes: " + ", ".join(f"{c} x{n}" for c, n in codes.most_common()),
            "Assets with the most work orders (repeat offenders): "
            + ", ".join(f"{a} ({n})" for a, n in assets.most_common(5)),
        ]
        wo_ids = ",".join(r["wo_id"] for r in rs)
        chunks.append(Chunk("work_orders.csv", f"Work order summary - {atype}", f"summary:{atype}",
                            "\n".join(lines), atype, {"wo_id": f"summary:{atype}", "asset_type": atype,
                                                      "wo_count": len(rs), "wo_ids": wo_ids[:2000]}))
        top_cause, top_n = causes.most_common(1)[0]
        overview.append((atype, len(rs), sum(down), top_cause, top_n))

    lines = ["Work order analytics overview: all asset types (synthetic data)",
             f"Total work orders: {len(rows)}; total downtime {sum(o[2] for o in overview)} min.",
             "By asset type (work orders, downtime, most common root cause):"]
    for atype, n, dt, cause, cn in sorted(overview, key=lambda o: -o[2]):
        lines.append(f"- {atype}: {n} work orders, {dt} min downtime; top cause: {cause} ({cn})")
    chunks.append(Chunk("work_orders.csv", "Work order summary - all assets", "summary:all",
                        "\n".join(lines), "overview", {"wo_id": "summary:all", "asset_type": "all"}))
    return chunks


def build_chunks() -> list[Chunk]:
    chunks: list[Chunk] = []
    for fname, pages in load_pages().items():
        chunks += chunk_document(pages, fname, config.SOURCES[fname])
    rows = read_work_orders()
    chunks += work_order_chunks(rows)
    if rows:
        chunks += work_order_summaries(rows)
    # de-dup identical content (same hash) while keeping order
    seen, uniq = set(), []
    for c in chunks:
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            uniq.append(c)
    return uniq


# ---------------------------------------------------------------- indexes
def get_embedder():
    from fastembed import TextEmbedding

    return TextEmbedding(config.EMBED_MODEL, cache_dir=config.FASTEMBED_CACHE)


def build_index(rebuild: bool = False) -> dict[str, int]:
    import chromadb

    t0 = time.perf_counter()
    if rebuild and config.INDEX_DIR.exists():
        shutil.rmtree(config.INDEX_DIR)
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)

    chunks = build_chunks()
    ids = [c.chunk_id for c in chunks]

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR),
                                       settings=chromadb.Settings(anonymized_telemetry=False))
    col = client.get_or_create_collection(config.COLLECTION, metadata={"hnsw:space": "cosine"},
                                          embedding_function=None)
    existing = set(col.get(include=[])["ids"])
    new = [c for c in chunks if c.chunk_id not in existing]
    stale = list(existing - set(ids))
    if stale:
        col.delete(ids=stale)
    if new:
        embedder = get_embedder()
        vecs = list(embedder.embed([c.text for c in new], batch_size=32))
        for i in range(0, len(new), 256):
            batch = new[i:i + 256]
            col.upsert(ids=[c.chunk_id for c in batch],
                       embeddings=[v.tolist() for v in vecs[i:i + 256]],
                       documents=[c.text for c in batch],
                       metadatas=[c.metadata() for c in batch])

    with open(config.CHUNKS_FILE, "w") as f:
        for c in chunks:
            f.write(json.dumps({"id": c.chunk_id, "text": c.text, **c.metadata()}, ensure_ascii=False) + "\n")

    from rank_bm25 import BM25Okapi

    bm25 = BM25Okapi([bm25_tokenize(c.text) for c in chunks])
    with open(config.BM25_FILE, "wb") as f:
        pickle.dump({"ids": ids, "bm25": bm25}, f)

    counts = Counter(c.source for c in chunks)
    print(f"Index built in {time.perf_counter() - t0:.1f}s -> {config.INDEX_DIR}")
    print(f"  chunks total: {len(chunks)} (new {len(new)}, removed {len(stale)}, collection {col.count()})")
    for src, n in sorted(counts.items()):
        print(f"  {n:5d}  {src}")
    return dict(counts)


def smoke_query(q: str = "How do I verify zero energy state before working on a conveyor?") -> None:
    from rag.retrieve import Retriever

    r = Retriever.load()
    print(f"\nSmoke query: {q}")
    for i, h in enumerate(r.search(q, k=4), 1):
        print(f"  [{i}] {h['source']} p.{h['page']} ({h['score']:.4f}) {h['text'][:110]!r}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("pypdf", "httpx", "huggingface_hub", "fontTools"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--no-smoke", action="store_true")
    args = ap.parse_args()
    build_index(rebuild=args.rebuild)
    if not args.no_smoke:
        smoke_query()


if __name__ == "__main__":
    main()
