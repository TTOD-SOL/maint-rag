"""Deploy maint-rag to a Hugging Face Docker Space. Reproducible and idempotent.

ALTERNATIVE TARGET. Render (render.yaml) is the primary deployment. Hugging Face now requires a PRO
subscription to host Docker Spaces on cpu-basic (create_repo returns 402), so this script only works
on a PRO account. It is kept, tested up to that paywall, as a documented alternative.

    uv run python infra/deploy_hf.py                  # create/update Space, set secrets, upload, wait
    uv run python infra/deploy_hf.py --no-wait
    uv run python infra/deploy_hf.py --ref <git-sha>  # deploy that commit (used for rollback)
    uv run python infra/deploy_hf.py --smoke          # only run the smoke test against the live Space

Auth: HF_TOKEN env var, or the local `hf auth login` token. The Space owner is taken from the token.
Secrets come from the environment or ~/.openclaw/env (ANTHROPIC_API_KEY) and .env (APP_API_KEY, which
is generated into .env on first run). Secret values are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[1]
SPACE_NAME = "maint-rag"

# Explicit allowlist: only these paths ever reach the Space repo.
INCLUDE = ["Dockerfile", ".dockerignore", "pyproject.toml", "uv.lock", ".python-version",
           "app", "rag", "data/processed", "data/synthetic"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
BAD_STAGES = {"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"}


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for ln in path.read_text().splitlines():
            ln = ln.strip().removeprefix("export ")
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    return out


def ensure_app_key() -> str:
    """APP_API_KEY: env > .env > generate and append to .env (gitignored)."""
    key = os.getenv("APP_API_KEY") or read_env_file(ROOT / ".env").get("APP_API_KEY")
    if key:
        return key
    key = secrets.token_urlsafe(32)
    with open(ROOT / ".env", "a") as f:
        f.write(f"APP_API_KEY={key}\n")
    os.chmod(ROOT / ".env", 0o600)
    print("generated APP_API_KEY and saved it to .env")
    return key


def anthropic_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY") or read_env_file(ROOT / ".env").get("ANTHROPIC_API_KEY") \
        or read_env_file(Path.home() / ".openclaw" / "env").get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY not found in env, .env, or ~/.openclaw/env")
    return key


def stage(dest: Path, ref: str | None) -> None:
    """Copy allowlisted files (from a git ref if given, else the working tree) into dest."""
    src = ROOT
    tmp = None
    if ref:
        tmp = Path(tempfile.mkdtemp())
        archive = subprocess.run(["git", "archive", ref], cwd=ROOT, check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(tmp)], input=archive, check=True)
        src = tmp
    for rel in INCLUDE:
        s, d = src / rel, dest / rel
        if not s.exists():
            sys.exit(f"missing required path: {rel}")
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(s, d, ignore=IGNORE) if s.is_dir() else shutil.copy2(s, d)
    shutil.copy2(src / "infra" / "hf" / "SPACE_README.md", dest / "README.md")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


def wait_running(api: HfApi, repo_id: str, timeout: int, started: float) -> str:
    last = ""
    while time.time() - started < timeout:
        rt = api.get_space_runtime(repo_id)
        stage_name = str(rt.stage)
        if stage_name != last:
            print(f"  [{int(time.time() - started):>4}s] space stage: {stage_name}", flush=True)
            last = stage_name
        if stage_name == "RUNNING":
            return stage_name
        if stage_name in BAD_STAGES:
            print("\n--- build/run logs (tail) ---")
            for build in (True, False):
                try:
                    lines = list(api.fetch_space_logs(repo_id, build=build))
                    print("\n".join(str(x) for x in lines[-60:]))
                except Exception as e:  # logs are best-effort
                    print(f"(could not fetch {'build' if build else 'run'} logs: {type(e).__name__})")
            sys.exit(f"Space ended in {stage_name}")
        time.sleep(10)
    sys.exit(f"timed out after {timeout}s waiting for RUNNING (last stage: {last})")


def http(url: str, data: dict | None = None, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(url, json.dumps(data).encode() if data else None,
                                 {"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def smoke(base: str, retries: int = 12) -> None:
    """/health must be ok and one /demo/ask must return a grounded, cited answer."""
    for i in range(retries):  # the Space can take a moment after RUNNING (and wakes from sleep)
        code, body = http(f"{base}/health")
        if code == 200 and json.loads(body).get("index_loaded"):
            print(f"smoke: /health ok ({json.loads(body)['chunks']} chunks)")
            break
        print(f"smoke: /health -> {code}, retry {i + 1}/{retries}", flush=True)
        time.sleep(10)
    else:
        sys.exit("smoke FAILED: /health never became ok")
    code, body = http(f"{base}/demo/ask", {"question": "How do I verify zero energy state before working on a conveyor?"})
    if code != 200:
        sys.exit(f"smoke FAILED: /demo/ask -> {code} {body[:200]}")
    res = json.loads(body)
    if not (res.get("grounded") and res.get("citations")):
        sys.exit("smoke FAILED: answer was not grounded with citations")
    print(f"smoke: /demo/ask ok ({len(res['citations'])} citations, ${res['est_cost_usd']:.5f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", help="git ref to deploy instead of the working tree")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--smoke", action="store_true", help="only run the smoke test")
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--space", help="override repo id (default: <whoami>/maint-rag)")
    args = ap.parse_args()

    api = HfApi(token=os.getenv("HF_TOKEN") or None)
    user = api.whoami()["name"]
    repo_id = args.space or f"{user}/{SPACE_NAME}"
    base = f"https://{repo_id.replace('/', '-').replace('_', '-').lower()}.hf.space"
    if args.smoke:
        return smoke(base)

    print(f"deploying to https://huggingface.co/spaces/{repo_id}")
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    api.add_space_secret(repo_id, "ANTHROPIC_API_KEY", anthropic_key())
    api.add_space_secret(repo_id, "APP_API_KEY", ensure_app_key())
    api.add_space_variable(repo_id, "LLM_MODEL", os.getenv("LLM_MODEL", "claude-haiku-5-5"))
    api.add_space_variable(repo_id, "DEMO_DAILY_CAP", os.getenv("DEMO_DAILY_CAP", "200"))
    sha = subprocess.run(["git", "rev-parse", "--short", args.ref or "HEAD"], cwd=ROOT, capture_output=True,
                         text=True).stdout.strip() or "dev"
    api.add_space_variable(repo_id, "APP_VERSION", sha)

    with tempfile.TemporaryDirectory() as td:
        stage(Path(td), args.ref)
        started = time.time()
        info = api.upload_folder(folder_path=td, repo_id=repo_id, repo_type="space",
                                 commit_message=f"Deploy {sha}", delete_patterns=["*"])
        print(f"uploaded {sha}: {info.commit_url}")

    if args.no_wait:
        return
    print("waiting for the Space to build (first build takes several minutes)...")
    wait_running(api, repo_id, args.timeout, started)
    print(f"RUNNING: {base}")


if __name__ == "__main__":
    main()
