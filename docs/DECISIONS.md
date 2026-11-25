# Design decisions

Short log of the choices made while building, and why. Numbers come from
`eval/results.md` and `eval/experiments.md`; regenerate them with the commands below.

## Corpus (Phase 1)

- **Sources:** OSHA 3120 (LOTO), DOE/FEMP data center best-practices guide, FAA-H-8083-30B,
  all fetched from official .gov URLs by `scripts/download_corpus.py`.
- **FAA handbook trimmed** to chapters 1, 10, 11, 14. The full PDF is ~92 MB and ~600 pages, and
  most of it doesn't apply to a ground maintainer. A sidecar JSON maps each trimmed page back to
  the handbook's printed page label (e.g. `1-12`), so citations point at real pages.
- **Work orders are synthetic:** 300 seeded rows with correlated failure modes. Each row is its
  own chunk, and roll-up summaries per asset type let the model answer "most common root cause" questions.

## Retrieval (Phases 2–3)

- **Embeddings:** `BAAI/bge-small-en-v1.5` via fastembed (ONNX on CPU, no torch, no API cost).
  Queries get the BGE instruction prefix. Removing it barely changed results (see table), but it's
  the model's documented usage, so it stays.
- **Hybrid = vector top-20 + BM25 top-20, merged with RRF (k=60), top-6 returned.**
- **Chunking: heading-aware, 350-token target / 500 max / 150 min, 15% overlap.** This is smaller
  than the 500–800 tokens in the original plan, a deliberate change based on the ablation:

  | Chunking | hybrid hit@6 | hybrid MRR@6 | vector MRR@6 |
  |---|---|---|---|
  | fixed-600, no headings | 0.967 | 0.793 | 0.766 |
  | heading-aware 600/min200 | 0.933 | 0.886 | 0.851 |
  | heading-aware 600/min450 | 0.933 | 0.883 | 0.774 |
  | **heading-aware 350/min150 (chosen)** | 0.933 | **0.907** | 0.816 |
  | heading-aware 600/min450, no overlap | 0.900 | 0.873 | 0.815 |

  Heading awareness gives the big jump in MRR (0.79 → 0.89): the right chunk ranks first more
  often, which matters because the model reads chunk [1] first. Smaller chunks push MRR a bit
  higher and keep prompts cheaper. Removing overlap hurts every metric.
- **Hybrid vs. vector-only (final config):** both reach hit@6 = 0.933, but hybrid MRR is
  0.907 vs. 0.816, and hit@3 is 0.900 vs. 0.833. BM25 alone has the best hit@6 (0.967),
  because this corpus is full of exact terms (fault codes, CFR sections, "energy isolating
  device"). Hybrid keeps those lexical wins while also handling paraphrased floor-language
  questions that BM25 misses, so it's the default.
- **Strict hit matching:** a retrieved chunk only counts if it is on the expected page (or
  the page before) *and* contains at least one `must_include` keyword. An earlier lenient
  version scored hybrid hit@6 = 1.000, which overstated quality. Remaining misses: q02 and q12.

## Generation

- **Model:** `claude-haiku-5-5` by default (`LLM_MODEL` env). It's fast and cheap (~$0.0005/query
  in eval), with 3/3 correct refusals and 100% citation validity.
- Provider sits behind `rag/llm.py` so another backend can be swapped in.

## Reproduce

```
uv run python -m rag.ingest
uv run python -m eval.run_eval --retrieval-only   # free, deterministic
uv run python -m eval.run_eval                    # + answers (~$0.02)
uv run python -m eval.experiments                 # chunking ablation
```
