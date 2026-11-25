# Deploying maint-rag

## Primary: Render (free web service)

`render.yaml` defines one Docker web service with `plan: free` set explicitly. Blueprints default to a
paid plan, which would ask for a card.

1. Push this repo to GitHub.
2. Render dashboard > **New > Blueprint** > select the repo.
3. When prompted, enter the two secrets: `ANTHROPIC_API_KEY` and `APP_API_KEY` (any long random string;
   it protects `POST /ask`; the public demo at `/demo/ask` needs no key).
4. Wait for the first build (~5–10 min). The URL is `https://maint-rag.onrender.com`
   (or with a suffix if the name is taken).

Free-tier facts that shape the design (from Render's docs):

| Limit | Effect here |
|---|---|
| 512 MB RAM, 0.1 CPU | Peak measured at ~262 MiB under a 512 MiB cap (`scripts/mem_check.sh`) |
| Spins down after 15 min idle | The first visit after idle waits for a cold start (see below) |
| 750 free instance hours / month | One service stays within it. Without a payment method, Render suspends free services if you exceed it instead of billing you |
| Ephemeral filesystem | Fine: the index is baked into the image, nothing is written |

**Cold start is expected, not a bug.** Measured locally with `docker run --cpus 0.1 -m 512m`:
startup about 85 s (index load plus model and client warm-up), then about 10 s per question.
Warm-up runs at startup so the first visitor does not pay an extra ~25 s.

## Alternative: Hugging Face Spaces

`infra/deploy_hf.py` creates a Docker Space, sets secrets, uploads an allowlisted copy of the repo with
`infra/hf/SPACE_README.md` swapped in as `README.md`, waits for RUNNING and can run the smoke test.
**Hugging Face now requires a PRO subscription for Docker Spaces on cpu-basic** (`create_repo` returns
`402 Payment Required`), so it is not used for the $0 deployment. On a PRO account:

```
uv run python infra/deploy_hf.py
uv run python infra/deploy_hf.py --smoke
```

## Local

`docker compose up --build` runs the app with Prometheus and Grafana (see README).
