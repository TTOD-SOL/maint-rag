"""Post-deploy smoke test (stdlib only).

    python scripts/smoke.py https://maint-rag.onrender.com [--version <sha7>] [--timeout 1500]

1. Waits until /health is ok (and, with --version, reports that commit), allowing for Render's cold start.
2. Sends one /demo/ask and requires a grounded answer with citations.
Exit code 1 on any failure.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import urllib.error
import urllib.request


def call(url: str, data: dict | None = None, timeout: int = 90) -> tuple[int, str]:
    req = urllib.request.Request(url, json.dumps(data).encode() if data else None,
                                 {"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # connection refused/reset while the service is starting
        return 0, str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--version", help="wait until /health reports this version (git sha prefix)")
    ap.add_argument("--timeout", type=int, default=1500)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    t0, last = time.time(), ""
    while True:
        code, body = call(f"{base}/health", timeout=30)
        info = {}
        if code == 200:
            with contextlib.suppress(ValueError):
                info = json.loads(body)
        ok = code == 200 and info.get("index_loaded") and (not a.version or info.get("version") == a.version[:7])
        status = f"health={code} version={info.get('version', '?')}"
        if status != last:
            print(f"[{int(time.time() - t0):>4}s] {status}", flush=True)
            last = status
        if ok:
            break
        if time.time() - t0 > a.timeout:
            print(f"FAIL: /health not ready (wanted version={a.version}) after {a.timeout}s")
            return 1
        time.sleep(10)
    code, body = call(f"{base}/demo/ask", {"question": "How do I verify zero energy state before working on a conveyor?"},
                      timeout=120)
    if code != 200:
        print(f"FAIL: /demo/ask returned {code}: {body[:200]}")
        return 1
    res = json.loads(body)
    if not (res.get("grounded") and res.get("citations")):
        print("FAIL: answer was not grounded with citations")
        return 1
    print(f"OK: /demo/ask grounded, {len(res['citations'])} citations, {res['llm_ms'] + res['retrieval_ms']:.0f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
