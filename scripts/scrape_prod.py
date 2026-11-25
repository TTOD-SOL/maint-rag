"""View PRODUCTION metrics in the local Grafana dashboard.

    uv run python scripts/scrape_prod.py                 # start Prometheus+Grafana against the live service
    uv run python scripts/scrape_prod.py --down          # stop them (do this when you're done)
    uv run python scripts/scrape_prod.py --url https://other.example.com --interval 60s

Grafana: http://localhost:3001 (same dashboard as the local stack)   Prometheus: http://localhost:9091

WARNING: on Render's free tier any request, including a Prometheus scrape, resets the 15-minute idle
timer. Leaving this running keeps the service awake around the clock (~720 of your 750 free instance
hours per month). Run it while you're looking at the dashboard, then `--down`. Also remember the
metrics are in-memory counters, so they reset every time the service restarts or wakes from sleep.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = "https://maint-rag.onrender.com"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.prod.yml")]


def render_config(url: str, interval: str) -> str:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        sys.exit(f"bad --url {url!r}; expected https://host")
    port = u.port or (443 if u.scheme == "https" else 80)
    return (f"global:\n  scrape_interval: {interval}\n  scrape_timeout: 25s\n"
            "scrape_configs:\n  - job_name: maint-rag\n"
            f"    scheme: {u.scheme}\n    metrics_path: /metrics\n"
            f"    static_configs:\n      - targets: [\"{u.hostname}:{port}\"]\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--interval", default="30s", help="scrape interval (default 30s)")
    ap.add_argument("--down", action="store_true")
    a = ap.parse_args()
    if a.down:
        subprocess.run([*COMPOSE, "down"], check=True)
        return
    cfg = ROOT / ".cache" / "prometheus.prod.yml"
    cfg.parent.mkdir(exist_ok=True)
    cfg.write_text(render_config(a.url, a.interval))
    subprocess.run([*COMPOSE, "up", "-d"], check=True)
    print(f"\nscraping {a.url} every {a.interval}\nGrafana:    http://localhost:3001\n"
          "Prometheus: http://localhost:9091\nStop with:   uv run python scripts/scrape_prod.py --down")


if __name__ == "__main__":
    main()
