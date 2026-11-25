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

## Docker (Phase 5)

- **Multi-stage on `python:3.11-slim`, deps via uv, runs as uid 1000** (what Hugging Face Spaces uses).
- **Index and embedding model are built into the image.** The build reads PDF text from the committed
  `data/processed/pages.jsonl`, so it never hits .gov servers and the 40 MB of PDFs stay out of the
  image. The container starts in about 3 s and needs no volume.
- **Image size: ~1.0 GB** (no torch). The big items are onnxruntime (64 MB), chromadb's Rust bindings
  (52 MB), numpy (70 MB with libs), and the 65 MB bge-small model. I strip chromadb's transitive
  `kubernetes` client (~125 MB), which the embedded client never imports. I checked that
  retrieval still works in the pruned image.
- **Secrets are passed through from the shell, not stored.** `docker-compose.yml` lists
  `ANTHROPIC_API_KEY` and `APP_API_KEY` without values.
- **Dashboard gotchas:** every Grafana query in a panel needs a unique `refId`, and ratio panels use
  `or vector(0)` so they show 0% instead of "No data" before the first refusal or error.
- **The demo rate limiter is in-memory.** Restarting the app resets the per-IP counters.

## Hosting (Phase 6)

- **Hugging Face Spaces was the plan, but Docker Spaces now need a PRO subscription.** `create_repo`
  returned `402 Payment Required: hosting Gradio and Docker Spaces on free cpu-basic requires a PRO
  subscription`. That breaks the $0 / no-card budget, so the primary target is **Render's free web
  service**. `infra/deploy_hf.py` stays in the repo as a documented alternative.
- **`plan: free` is set explicitly in `render.yaml`** because Blueprints default to a paid plan.
  `autoDeployTrigger: checksPass` deploys a push only after CI passes.
- **Memory under Render's 512 MB cap.** Measured with `scripts/mem_check.sh` (container limited to
  512 MiB, 8 questions through `/demo/ask`, peak from `docker stats`):

  | Config | idle | peak |
  |---|---|---|
  | baseline image | 382 MiB | **474 MiB** (too close to the limit) |
  | `MALLOC_ARENA_MAX=2`, `OMP_NUM_THREADS=1`, `ORT_NUM_THREADS=1` | 204 MiB | **267 MiB** |
  | + startup warm-up (final) | 255 MiB | **262 MiB** |

  glibc keeps a malloc arena per thread and the sync endpoints run in a threadpool, so capping arenas
  was the big win. Profiling the process showed `chromadb` plus the embedding model are ~125 MiB of
  the ~300 MiB, so lazy-loading or dropping Chroma was not needed and I left the retrieval design alone.
- **Cold start on 0.1 CPU** (`docker run --cpus 0.1 -m 512m`): the first question after boot took 38 s.
  Warming up the embedder alone did not help; the cost was the lazily created Anthropic client (SDK
  import + TLS setup). Creating the client and running one search at startup moved that cost out of the
  first request: first question 13 s, steady state ~10 s, startup ~85 s.
- **Version in `/health`** comes from Render's `RENDER_GIT_COMMIT`, falling back to `APP_VERSION`.

## CI/CD (Phase 7)

- **CI** (`ci.yml`, on PR and push): uv, ruff, pytest (LLM mocked, no key), fresh index build, then a
  deterministic **retrieval gate: hybrid hit@6 must stay >= 0.80** (currently 0.933), then a Docker
  build plus a container start check. No secrets are needed, so PRs from forks run safely.
- **Deploy is split between Render and GitHub Actions.** Render deploys a push itself once CI passes
  (`autoDeployTrigger: checksPass`). `deploy.yml` runs after CI succeeds on `main`, waits until the live
  `/health` reports the pushed commit (Render sets `RENDER_GIT_COMMIT`), then runs the smoke test:
  `/health` plus one grounded, cited `/demo/ask`. This replaces "run deploy_hf.py", which needs HF PRO.
- **Rollback.** If the smoke test fails, the workflow calls Render's deploy hook with `ref=<last good
  sha>` and fails loudly. The last good SHA is the head SHA of the previous successful `deploy.yml` run,
  found with `gh run list`. The plan said to store it in a repo variable, but writing repo variables needs
  a personal access token, while reading run history works with the built-in `GITHUB_TOKEN`; the result is
  the same without another long-lived secret.
- The deploy job is skipped, not failed, until the `SERVICE_URL` repo variable is set.
