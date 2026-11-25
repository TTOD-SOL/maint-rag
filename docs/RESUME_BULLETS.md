# Resume bullets and interview script

All numbers come from this build: `eval/results.md`, `docs/DECISIONS.md`, the Docker image, and the CI run.

## Resume bullets

- Built and deployed a production-style RAG assistant over OSHA, FAA, and DOE documents plus synthetic work
  orders: hybrid retrieval (vector + BM25 fused with RRF) and Claude generation with inline citations,
  served by FastAPI. Hybrid retrieval reached **0.93 hit@6 and 0.91 MRR** on a 33-question golden set
  (vector-only: 0.82 MRR), with **100% citation validity** and **3 of 3 correct refusals** on
  out-of-scope questions.
- Cut retrieval and serving cost to **about $0.0005 per query** (p50 1.7 s, p95 3.4 s end to end) using
  CPU-only ONNX embeddings and a small model, with per-IP and global daily rate limits. At 100 queries a
  day the Anthropic bill is about **$2 a month**, on **$0 hosting**.
- Shipped on Docker and Render's free tier by profiling instead of guessing: found peak memory of
  474 MiB against a 512 MiB limit, and cut it to **262 MiB** by capping allocator arenas and ONNX threads.
  Packaged as a **1.0 GB** non-root image with the index baked in, and removed a 38 s first-request stall
  on a 0.1 CPU core by warming the model and LLM client at startup.
- Automated CI/CD and operations with GitHub Actions: ruff, **27 tests** (LLM mocked), and a
  **retrieval-quality gate** that fails the build below 0.80 hit@6, followed by post-deploy smoke tests,
  rollback to the last good commit, a 30-minute uptime monitor that opens and closes incident issues,
  Prometheus and Grafana dashboards, and a symptom-to-fix runbook.

## 60-second interview walkthrough

> "I'm a maintenance technician, so I built the assistant I wanted on the floor: you ask a question,
> it answers from OSHA lockout/tagout, the FAA handbook, DOE data center guidance, and a work-order
> history, and it cites the page. If the documents don't cover it, it says so.
>
> Under the hood it's hybrid retrieval: vector search and BM25, merged with reciprocal rank fusion, then
> Claude Haiku writes a cited answer. I measured it instead of eyeballing it. On 33 questions hybrid gets
> 0.93 hit@6, and ranks the right chunk first more often than vector alone, 0.91 MRR versus 0.82. One thing
> I'm proud of: my first matcher scored 1.0, which was too generous, so I tightened it and reported the
> lower number.
>
> I shipped it, too. It's a Docker image on Render's free tier, behind rate limits and a daily cap so it
> can't run up my bill. It costs about a twentieth of a cent per question. The free tier has 512 MB, and my
> first measurement was 474, too close. I profiled it, found glibc malloc arenas were the problem, and got it
> down to 262.
>
> CI runs the tests and a retrieval-quality gate, deploys are smoke-tested with rollback, and there's an
> uptime monitor and a runbook. I'd say that's the part from my day job: building something is half
> the work, and keeping it running is the other half. Next I'd add a reranker and a managed vector store."

**If they ask about weaknesses:** the golden set is small (33 questions), so it's a regression gate, not a
benchmark. Answer quality is measured by keyword coverage plus citation checks, not a human or LLM judge.
Hugging Face Spaces was my first choice, but Docker Spaces now need a paid plan, so I moved to Render and
documented why.
