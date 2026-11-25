# maint-rag runbook

Written the way I'd write a PM card for a drive or a conveyor: what you see, what to check, how to fix
it, and how to confirm it's fixed. Start at the top of the section and stop when the symptom clears.
Don't skip the "confirm" step. A fix you didn't verify is a guess.

**Where things live**

| What | Where |
|---|---|
| Live service | https://maint-rag.onrender.com (Render, free web service, Docker) |
| Health | `GET /health`: `status`, `index_loaded`, `chunks`, `version` (the deployed commit), `demo_remaining_today` |
| Logs | Render dashboard > the service > **Logs** (JSON lines, one per request) |
| Metrics | `GET /metrics`; dashboards via `uv run python scripts/scrape_prod.py` |
| Deploys | GitHub Actions: `CI` > Render auto-deploy > `Deploy` workflow (smoke test + rollback) |
| Uptime alarm | `Uptime` workflow every 30 min; opens an issue labeled `incident` |
| Config | Render > Environment: `ANTHROPIC_API_KEY`, `APP_API_KEY`, `LLM_MODEL`, `DEMO_DAILY_CAP` |

## Reading the logs

Every line is JSON with a `request_id` (also returned to callers in the `X-Request-ID` header, so a
user's bug report can be matched to a log line). Useful messages:

| `msg` | Meaning | Key fields |
|---|---|---|
| `request` | one per HTTP request | `method`, `path`, `status`, `latency_ms` |
| `answer` | a completed question | `endpoint`, `grounded`, `refused`, `retrieval_ms`, `llm_ms`, `input_tokens`, `output_tokens`, `cost_usd` |
| `llm error` | Anthropic call failed | `error`, `llm_status`, `retryable` |
| `rate limited` / `demo daily cap reached` | a limit fired | `kind` (`per_ip` or `daily_cap`) |
| `index loaded`, `warmup done` | startup finished | `chunks`, `load_ms`, `warmup_ms` |

In the Logs tab, search for `llm error` or `"status": 5` to jump to trouble. To pull recent logs into a
file for `jq`, copy them from the dashboard. By design, API keys and full prompts are never logged.
Questions are logged as a character count only.

---

## 1. Service is down or `/health` isn't 200

**Symptom:** the Uptime workflow opened an `incident` issue, or the demo page won't load.

1. **Is it just asleep?** Free services sleep after 15 minutes idle. Wait about 90 seconds and retry
   (see section 4). A single slow first request is normal, not an incident.
2. **Check the Render dashboard.** Status `Live`? `Deploy failed`? `Suspended`?
   - `Suspended`: free instance hours (750/month) or bandwidth ran out and there's no payment method.
     It comes back at the start of next month. Fix by reducing pings (see section 4) or upgrading.
   - `Deploy failed`: go to section 6.
3. **`/health` returns 503 with `index_loaded: false`:** the index didn't load. Check the logs for
   `index failed to load`. It's baked into the image, so this means a bad build. Redeploy the last good
   commit (section 7).
4. **Confirm:** `python scripts/smoke.py https://maint-rag.onrender.com` exits 0. Close the incident
   issue if the Uptime workflow didn't already (it auto-closes on recovery).

## 2. API key errors

| Response | Cause | Fix |
|---|---|---|
| `401 Invalid or missing X-API-Key` on `/ask` | caller sent a wrong or missing key | send the header `X-API-Key: <APP_API_KEY>`. The value is in Render > Environment and my local `.env` |
| `503 API key auth not configured on server` | `APP_API_KEY` is unset on Render | set it in Environment, save (the service restarts) |
| `503 LLM not configured` | `ANTHROPIC_API_KEY` missing or the client couldn't start | set it in Environment |
| `502 Upstream LLM error` and `llm error` in logs with `llm_status: 401` | Anthropic key invalid, revoked, or pasted with a stray space or newline | re-create the key in the Anthropic console, paste it again with no quotes, save |

Order of checks: logs first (`llm error` has the status), then Environment, then the Anthropic console.
**Confirm:** `curl -s -X POST $URL/demo/ask -H 'content-type: application/json' -d '{"question":"What is lockout/tagout?"}'`
returns an answer with citations.

Rotating `APP_API_KEY`: change it in Render and in `.env`. Nothing else uses it (the public demo and
CI smoke test don't need it).

## 3. Rate limit hits (`429`)

Two different limits, two different messages:

- **`Rate limit exceeded (10/hour per IP)`:** one visitor asked more than 10 questions in an hour. This is
  working as intended. The limit is per IP and in memory, so it also resets when the service restarts or
  wakes from sleep. Tell the user to wait. To change it, set `DEMO_RATE_LIMIT` (for example `30/hour`).
- **`The public demo hit its daily question limit`:** the global cap (`DEMO_DAILY_CAP`, default 200) is spent.
  It resets at 00:00 UTC. This cap exists so strangers can't run up my Anthropic bill. Raise it only
  after checking the spend panel in Grafana. `/health` shows `demo_remaining_today`.

If I'm testing and keep tripping the per-IP limit, use `/ask` with the API key. It has no per-IP limit.
(`X-Forwarded-For` can be spoofed by a caller, which is exactly why the daily cap is the hard stop.)

## 4. Sleeping and cold starts (expected, not a fault)

**Symptom:** the first request after a quiet period takes 40–90 seconds, or the browser shows Render's
"waking up" page. **This is how the free tier works.** It sleeps after 15 minutes without traffic.

- Startup measured at 0.1 CPU: about 85 s (load index, embedding model, LLM client, one warm-up search).
  After that, questions take about 10 s on this throttled CPU. The first real question is not slower,
  because the warm-up runs at startup.
- Metrics are in-memory counters, so dashboards restart from zero after every wake-up.
- Don't "fix" it with a keep-alive ping. Pinging every <15 min keeps one service up around the clock
  (~720 of 750 free hours a month), and Render treats artificial keep-alive as abuse of the free tier.
  The uptime check (every 30 min) wakes it but doesn't keep it awake, so it costs roughly 12 hours a
  day. If that's too much, change the cron in `uptime.yml` to hourly.
- If cold starts matter, the answer is a paid plan, not a cron job.

## 5. LLM quota exhausted or Anthropic is down

**Symptom:** `503 LLM quota or rate limit reached. Try again later.` (logs: `llm error`, `llm_status: 429`)
or `502 Upstream LLM error` (other statuses).

1. **Check Anthropic status** and the usage and billing page in the console. 429 usually means a spend
   limit or rate limit.
2. **Check my own spend:** Grafana "LLM cost" panel, or `rag_llm_cost_usd_total` on `/metrics`. At
   about $0.0005 to $0.0008 per question, 200 demo questions a day is under $0.20, so a spike means
   something is wrong (a leaked `APP_API_KEY` hammering `/ask` is the first suspect: rotate it).
3. **Fix:** add credit or raise the limit. If the key was abused, rotate both keys. The demo stays
   protected by the daily cap either way.
4. Retrieval still works without the LLM. The app returns an error rather than an ungrounded answer,
   on purpose.

**Confirm:** one successful `/demo/ask`; `llm error` stops appearing in the logs.

## 6. Failed build on Render

**Symptom:** the Render deploy shows `Build failed`; the `Deploy` workflow times out waiting for the new
version, then fails its smoke test.

1. Open the failed deploy's **build log** in Render. Common causes:
   - **Out of memory or timeout during build:** the build runs the ingestion step (embedding about 700
     chunks). Retry once; if it repeats, check whether the dependency set grew.
   - **Dependency resolution:** `uv sync --frozen` fails if `uv.lock` is out of date. Run `uv lock`
     locally, commit, push.
   - **Missing files:** the Dockerfile copies `data/processed` and `data/synthetic`. If they're gitignored
     or renamed, the index build fails.
2. **Reproduce locally** before pushing a fix: `docker build -t maint-rag:local .` then
   `scripts/mem_check.sh` (confirms the image still fits in 512 MB).
3. A failed build doesn't take the site down. Render keeps the last good deploy running. So there's no
   rush, but don't leave `main` red.

**Confirm:** the Render deploy goes `Live`, `/health` shows the new commit in `version`.

## 7. Failed deploy and rollback

The `Deploy` workflow runs after CI passes on `main`. It waits for `/health` to report the pushed
commit, then runs the smoke test (`/health` plus one grounded, cited answer).

- **Smoke test fails:** if the `RENDER_DEPLOY_HOOK` secret is set, the workflow redeploys the last
  commit whose `Deploy` run succeeded and fails loudly. If the secret isn't set, it only fails; roll back
  by hand.
- **Manual rollback:** in Render, **Manual Deploy > Deploy a specific commit** and pick the last good
  SHA. (Or `curl -X POST "$RENDER_DEPLOY_HOOK&ref=<sha>"`.) Then `git revert` the bad commit on `main`,
  so the next push doesn't redeploy the problem.
- **CI red:** nothing deploys (`autoDeployTrigger: checksPass`). Fix forward. The most common red is the
  retrieval gate: hybrid hit@6 fell below 0.80. Run `uv run python -m eval.run_eval --retrieval-only`
  and compare with `eval/results.md` to see which question regressed.
- **Deploy workflow skipped:** the `SERVICE_URL` repo variable is empty. Set it with
  `gh variable set SERVICE_URL --body https://maint-rag.onrender.com`.

**Confirm:** `curl -s $URL/health` shows the expected `version`, and `scripts/smoke.py` exits 0.

## 8. Looking at production metrics

`uv run python scripts/scrape_prod.py` starts a local Prometheus and Grafana against the live service
(Grafana on http://localhost:3001). **Stop it when done** (`--down`): scraping keeps the free service awake.
