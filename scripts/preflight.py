"""Pre-flight checks. Run before building features or a demo:  python scripts/preflight.py

Prints PASS / WARN / FAIL per item. WARN means a fallback will be used.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

results: list[tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"{status:4}  {name}{(' - ' + detail) if detail else ''}")


def version_of(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def main() -> int:
    s = get_settings()

    v = sys.version_info
    report("PASS" if v >= (3, 11) else "FAIL", "Python >= 3.11", f"{v.major}.{v.minor}.{v.micro}")

    node = version_of(["node", "--version"])
    report("PASS" if node and int(node.lstrip("v").split(".")[0]) >= 20 else "FAIL", "Node >= 20", node or "not found")

    git = version_of(["git", "--version"]) or ""
    m = re.search(r"(\d+)\.(\d+)", git)
    ok = bool(m and (int(m[1]), int(m[2])) >= (2, 30))
    report("PASS" if ok else "FAIL", "git >= 2.30 (partial clone, worktrees)", git or "not found")

    rg = version_of(["rg", "--version"])
    report("PASS" if rg else "WARN", "ripgrep", rg.splitlines()[0] if rg else "missing; slower Python search will be used")

    docker_ok = shutil.which("docker") and subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    report("PASS" if docker_ok else "WARN", "Docker (sandbox only)",
           "running" if docker_ok else "unavailable; reproduction will be skipped")

    # GitHub
    headers = {"Accept": "application/vnd.github+json"}
    if s.github_token:
        headers["Authorization"] = f"Bearer {s.github_token}"
    try:
        r = httpx.get(f"{s.github_api}/rate_limit", headers=headers, timeout=10)
        if r.status_code == 401:
            report("FAIL", "GitHub token", "rejected (401)")
        else:
            core = r.json().get("resources", {}).get("core", {})
            status = "PASS" if s.github_token else "WARN"
            report(status, "GitHub API", f"{core.get('remaining')}/{core.get('limit')} requests left"
                   + ("" if s.github_token else " (no GITHUB_TOKEN: only 60/hour)"))
    except httpx.HTTPError as e:
        report("FAIL", "GitHub API", f"unreachable ({e.__class__.__name__})")

    # Model server
    if not s.llm_base_url:
        report("WARN", "Model server", "LLM_BASE_URL not set; pipeline runs with tool results only")
    else:
        base = s.llm_base_url.rstrip("/")
        auth = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
        try:
            r = httpx.get(f"{base}/models", headers=auth, timeout=10)
            ids = [m.get("id") for m in r.json().get("data", [])] if r.status_code < 400 else []
            report("PASS" if s.llm_model in ids else "WARN", "Model listed",
                   f"{s.llm_model} {'found' if s.llm_model in ids else 'not in ' + str(ids[:5])}")
        except (httpx.HTTPError, ValueError) as e:
            report("WARN", "Model list", f"/models failed ({e.__class__.__name__})")
        try:
            t0 = time.monotonic()
            r = httpx.post(f"{base}/chat/completions", headers=auth, timeout=s.llm_timeout_s, json={
                "model": s.llm_model, "max_tokens": 64,
                "messages": [{"role": "user", "content": "Reply with the JSON object {\"ok\": true} only."}]})
            report("PASS" if r.status_code < 400 else "FAIL", "Chat completion",
                   f"HTTP {r.status_code} in {time.monotonic() - t0:.1f}s")
            r = httpx.post(f"{base}/chat/completions", headers=auth, timeout=s.llm_timeout_s, json={
                "model": s.llm_model, "max_tokens": 64,
                "messages": [{"role": "user", "content": "Return ok=true."}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "probe", "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                                                "required": ["ok"]}}}})
            report("PASS" if r.status_code < 400 else "WARN", "json_schema output",
                   "supported" if r.status_code < 400 else f"HTTP {r.status_code}; set LLM_JSON_SCHEMA=off")
            t0 = time.monotonic()
            r = httpx.post(f"{base}/chat/completions", headers=auth, timeout=s.llm_timeout_s, json={
                "model": s.llm_model, "max_tokens": 32,
                "messages": [{"role": "user", "content": ("lorem ipsum dolor sit amet " * 300) + "\nSay done."}]})
            report("PASS" if r.status_code < 400 else "WARN", "~2k-token prompt latency",
                   f"{time.monotonic() - t0:.1f}s (HTTP {r.status_code})")
        except httpx.HTTPError as e:
            report("FAIL", "Chat completion", f"{e.__class__.__name__}")

    fails = sum(1 for r in results if r[0] == "FAIL")
    print(f"\n{fails} failure(s), {sum(1 for r in results if r[0] == 'WARN')} warning(s).")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
