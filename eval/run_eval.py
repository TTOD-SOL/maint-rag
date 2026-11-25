"""Evaluate retrieval (deterministic, no API) and answers (calls the LLM).

    uv run python -m eval.run_eval                         # retrieval + answers -> eval/results.md
    uv run python -m eval.run_eval --retrieval-only        # no API key needed
    uv run python -m eval.run_eval --retrieval-only --min-hit 0.80   # CI gate (exit 1 if below)

Retrieval metrics use only the answerable questions:
  hit@k  - share of questions where any expected source is in the top k
  MRR@k  - mean of 1/rank of the first expected source (0 if not in top k)
A retrieved chunk matches an expected source if the file matches and the chunk starts on
the expected page or the page before (chunks can run across a page break), or, for work
orders, the wo_id matches or the chunk text contains the given string.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rag import config

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden.jsonl"
RESULTS = HERE / "results.md"


def load_golden(path: Path = GOLDEN) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def prev_page(label: str) -> str:
    if "-" in label:
        ch, n = label.rsplit("-", 1)
        return f"{ch}-{int(n) - 1}" if n.isdigit() else label
    return str(int(label) - 1) if label.isdigit() else label


def matches(chunk: dict, exp: dict, keywords: list[str] | None = None) -> bool:
    """Lenient match: right file + page. Strict match (keywords given): the chunk must also
    contain at least one must_include keyword, so a different chunk from the same page
    doesn't count as a hit."""
    if chunk["source"] != exp["source"]:
        return False
    if "contains" in exp:
        ok = exp["contains"].lower() in chunk["text"].lower()
    else:
        page = str(chunk["page"])
        ok = page == exp["page"] or page == prev_page(exp["page"])
    if ok and keywords:
        text = chunk["text"].lower()
        ok = any(k.lower() in text for k in keywords)
    return ok


def first_hit_rank(hits: list[dict], expected: list[dict], keywords: list[str] | None = None) -> int | None:
    for rank, h in enumerate(hits, 1):
        if any(matches(h, e, keywords) for e in expected):
            return rank
    return None


def retrieval_metrics(retriever, golden: list[dict], k: int, mode: str, strict: bool = True) -> dict:
    rows = []
    for g in golden:
        if not g["answerable"]:
            continue
        hits = retriever.search(g["question"], k=k, mode=mode)
        rank = first_hit_rank(hits, g["expected_sources"], g["must_include"] if strict else None)
        rows.append({"id": g["id"], "category": g["category"], "rank": rank})
    n = len(rows)
    hit = sum(r["rank"] is not None for r in rows) / n
    mrr = sum(1 / r["rank"] for r in rows if r["rank"]) / n
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["rank"] is not None)
    return {"mode": mode, "k": k, "n": n, "hit": hit, "mrr": mrr, "rows": rows,
            "by_category": {c: sum(v) / len(v) for c, v in by_cat.items()}}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[idx]


def answer_metrics(retriever, llm, golden: list[dict], k: int) -> dict:
    from rag.generate import answer_question, cited_numbers

    rows = []
    for g in golden:
        t0 = time.perf_counter()
        res = answer_question(g["question"], retriever, llm, k=k)
        total_ms = (time.perf_counter() - t0) * 1000
        nums = cited_numbers(res.answer)
        cite_valid = all(1 <= n <= k for n in nums)
        kws = g["must_include"]
        found = [kw for kw in kws if kw.lower() in res.answer.lower()]
        rows.append({
            "id": g["id"], "category": g["category"], "answerable": g["answerable"],
            "refused": res.refused, "grounded": res.grounded, "n_cites": len(nums),
            "cite_valid": cite_valid,
            "kw_coverage": (len(found) / len(kws)) if kws else None,
            "missing": [kw for kw in kws if kw not in found],
            "total_ms": total_ms, "llm_ms": res.llm_ms, "cost": res.est_cost_usd,
            "in_tok": res.input_tokens, "out_tok": res.output_tokens,
        })
        print(f"  {g['id']} {g['category']:<12} refused={res.refused!s:<5} cites={len(nums)} "
              f"kw={rows[-1]['kw_coverage']} {total_ms:.0f}ms", flush=True)
    ans = [r for r in rows if r["answerable"]]
    una = [r for r in rows if not r["answerable"]]
    lat = [r["total_ms"] for r in rows]
    return {
        "n": len(rows),
        "kw_coverage": statistics.mean(r["kw_coverage"] for r in ans),
        "citation_validity": sum(r["cite_valid"] for r in rows) / len(rows),
        "cited_answer_rate": sum(r["n_cites"] > 0 and not r["refused"] for r in ans) / len(ans),
        "false_refusals": sum(r["refused"] for r in ans),
        "n_answerable": len(ans),
        "correct_refusals": sum(r["refused"] for r in una),
        "n_unanswerable": len(una),
        "p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95),
        "total_cost": sum(r["cost"] for r in rows),
        "avg_cost": sum(r["cost"] for r in rows) / len(rows),
        "avg_in": statistics.mean(r["in_tok"] for r in rows),
        "avg_out": statistics.mean(r["out_tok"] for r in rows),
        "rows": rows,
    }


def render(ret: list[dict], ans: dict | None, model: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# Evaluation results\n\nGenerated {now} by `eval/run_eval.py` on `eval/golden.jsonl` "
           f"({sum(1 for _ in load_golden())} questions; {ret[0]['n']} answerable).\n",
           "## Retrieval (deterministic, no LLM)\n",
           "| Mode | hit@{k} | MRR@{k} | LOTO | Aviation | Data center | Work orders |".format(k=ret[0]["k"]),
           "|---|---|---|---|---|---|---|"]
    for r in ret:
        c = r["by_category"]
        out.append(f"| {r['mode']} | {r['hit']:.3f} | {r['mrr']:.3f} | {c.get('loto', 0):.2f} | "
                   f"{c.get('aviation', 0):.2f} | {c.get('datacenter', 0):.2f} | {c.get('workorders', 0):.2f} |")
    if ans:
        out += ["", f"## Answers (model `{model}`)\n", "| Metric | Value |", "|---|---|",
                f"| Keyword coverage (answerable) | {ans['kw_coverage']:.3f} |",
                f"| Citation validity (every [n] maps to a retrieved chunk) | {ans['citation_validity']:.3f} |",
                f"| Answerable questions answered with citations | {ans['cited_answer_rate']:.3f} |",
                f"| Correct refusals (unanswerable) | {ans['correct_refusals']}/{ans['n_unanswerable']} |",
                f"| False refusals (answerable) | {ans['false_refusals']}/{ans['n_answerable']} |",
                f"| Latency p50 / p95 (end to end) | {ans['p50_ms'] / 1000:.2f} s / {ans['p95_ms'] / 1000:.2f} s |",
                f"| Avg tokens in / out | {ans['avg_in']:.0f} / {ans['avg_out']:.0f} |",
                f"| Cost: total / per query | ${ans['total_cost']:.4f} / ${ans['avg_cost']:.5f} |",
                "", "<details><summary>Per-question answer results</summary>\n",
                "| id | category | refused | cites | kw coverage | missing | ms |", "|---|---|---|---|---|---|---|"]
        for r in ans["rows"]:
            kw = "-" if r["kw_coverage"] is None else f"{r['kw_coverage']:.2f}"
            out.append(f"| {r['id']} | {r['category']} | {r['refused']} | {r['n_cites']} | {kw} | "
                       f"{', '.join(r['missing'])} | {r['total_ms']:.0f} |")
        out.append("\n</details>")
    hyb = next((r for r in ret if r["mode"] == "hybrid"), ret[0])
    misses = [r["id"] for r in hyb["rows"] if r["rank"] is None]
    out += ["", f"Hybrid retrieval misses: {', '.join(misses) or 'none'}", ""]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=config.TOP_K)
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--modes", default="hybrid,vector,bm25")
    ap.add_argument("--min-hit", type=float, default=None, help="fail if hybrid hit@k is below this")
    ap.add_argument("--out", default=str(RESULTS))
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    from rag.retrieve import Retriever

    retriever = Retriever.load()
    golden = load_golden()
    ret = [retrieval_metrics(retriever, golden, args.k, m) for m in args.modes.split(",")]
    for r in ret:
        print(f"{r['mode']:>7}: hit@{r['k']}={r['hit']:.3f}  MRR={r['mrr']:.3f}  {r['by_category']}")

    ans = None
    if not args.retrieval_only:
        from rag.llm import get_llm

        print("Answer eval (calls the LLM)...")
        ans = answer_metrics(retriever, get_llm(), golden, args.k)
        print(f"kw={ans['kw_coverage']:.3f} cites={ans['citation_validity']:.3f} "
              f"refusals={ans['correct_refusals']}/{ans['n_unanswerable']} "
              f"p50={ans['p50_ms']:.0f}ms p95={ans['p95_ms']:.0f}ms cost=${ans['total_cost']:.4f}")
    if not args.no_write:
        Path(args.out).write_text(render(ret, ans, config.LLM_MODEL))
        print(f"wrote {args.out}")

    if args.min_hit is not None:
        hyb = next(r for r in ret if r["mode"] == "hybrid")
        if hyb["hit"] < args.min_hit:
            print(f"FAIL: hybrid hit@{args.k} {hyb['hit']:.3f} < {args.min_hit}", file=sys.stderr)
            return 1
        print(f"PASS: hybrid hit@{args.k} {hyb['hit']:.3f} >= {args.min_hit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
