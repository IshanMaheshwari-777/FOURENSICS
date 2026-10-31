"""Optional scoped reproduction. Runs only the reporter's own snippet, only for
allowlisted reports, in a locked-down container. Off unless ENABLE_SANDBOX=true.

Allowlist file (JSON list):
  [{"url": "https://github.com/o/r/issues/1", "package": "csvtool", "version": "2.3.1",
    "python": "3.11", "snippet_index": 0}]
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..schemas import Anchor, Claim, Verification

HARNESS = textwrap.dedent('''
    import json, sys, traceback
    out = {"exception": None, "message": None, "stdout": ""}
    import io, contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(open("/work/snippet.py").read(), "snippet.py", "exec"), {"__name__": "__main__"})
    except BaseException as e:
        out["exception"] = type(e).__name__
        out["message"] = str(e)[:500]
    out["stdout"] = buf.getvalue()[-2000:]
    print("@@RESULT@@" + json.dumps(out))
''')


@dataclass
class ReproResult:
    status: Verification
    detail: str
    raw: str = ""


def load_allowlist(path: Path) -> list[dict]:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []


def find_entry(allowlist: list[dict], url: str) -> Optional[dict]:
    return next((e for e in allowlist if e.get("url", "").rstrip("/") == url.rstrip("/")), None)


def _docker(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def _matches(claim: Claim, anchors: list[Anchor], observed: dict) -> bool:
    exc = observed.get("exception")
    msg = observed.get("message") or ""
    wanted = [a.value for a in anchors if a.kind == "error"]
    if claim.observed:
        wanted.append(claim.observed)
    for w in wanted:
        name, _, text = w.partition(":")
        name = name.strip().split(".")[-1]
        if exc and name == exc and (not text.strip() or text.strip().strip("'\"") in msg):
            return True
    return False


def reproduce(entry: dict, claim: Claim, anchors: list[Anchor], image: str = "python:3.11-slim",
              timeout_s: float = 30.0) -> ReproResult:
    if shutil.which("docker") is None:
        return ReproResult("environment_unavailable", "Docker is not available")
    blocks = [a.value for a in anchors if a.kind == "code_block"]
    idx = int(entry.get("snippet_index", 0))
    if idx >= len(blocks):
        return ReproResult("environment_unavailable", "The report has no runnable snippet")
    image = f"python:{entry['python']}-slim" if entry.get("python") else image

    with tempfile.TemporaryDirectory(prefix="triage-sbx-") as tmp:
        work = Path(tmp)
        (work / "snippet.py").write_text(blocks[idx])
        (work / "harness.py").write_text(HARNESS)
        (work / "site").mkdir()
        # Phase 1: install the pinned package from PyPI (network on, nothing from the report runs here)
        try:
            inst = _docker(["run", "--rm", "-v", f"{work}:/work", image, "pip", "install", "--quiet",
                            "--target", "/work/site", f"{entry['package']}=={entry['version']}"], timeout=300)
        except subprocess.TimeoutExpired:
            return ReproResult("environment_unavailable", "Installing the package timed out")
        if inst.returncode != 0:
            return ReproResult("environment_unavailable", "The package version could not be installed",
                               inst.stderr[-1500:])
        # Phase 2: run the reporter's snippet with no network and strict limits, twice
        results = []
        for _ in range(2):
            try:
                run = _docker([
                    "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
                    "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--pids-limit", "128", "--memory", "512m", "--cpus", "1",
                    "-e", "PYTHONPATH=/work/site", "-e", "HOME=/tmp", "-v", f"{work}:/work:ro",
                    image, "python", "/work/harness.py"], timeout=timeout_s + 15)
            except subprocess.TimeoutExpired:
                return ReproResult("environment_unavailable", "The snippet timed out")
            marker = run.stdout.rfind("@@RESULT@@")
            if marker == -1:
                return ReproResult("environment_unavailable", "The sandbox produced no result", run.stderr[-1500:])
            results.append(json.loads(run.stdout[marker + len("@@RESULT@@"):].strip().splitlines()[0]))

    hits = [_matches(claim, anchors, r) for r in results]
    raw = json.dumps({"package": entry["package"], "version": entry["version"], "image": image,
                      "snippet_index": idx, "runs": results})[:2000]
    environment = f" ({entry['package']}=={entry['version']}; {image}; reporter snippet {idx})"
    if all(hits):
        return ReproResult("reproduced", f"Both runs raised {results[0]['exception']}: {results[0]['message']}" + environment, raw)
    if not any(hits) and all(r.get("exception") is None for r in results):
        return ReproResult("not_reproduced", "Both runs completed without the reported error" + environment, raw)
    return ReproResult("environment_unavailable", "Runs were inconsistent or raised a different error", raw)
