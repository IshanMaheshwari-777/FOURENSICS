"""Thin, timeout-guarded git wrapper used by the snapshot resolver and probes."""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, timeout: float = 120.0, check: bool = True) -> str:
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after {timeout:.0f}s") from e
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def commit_time(repo: Path, rev: str, timeout: float = 30.0) -> datetime:
    out = git(repo, "show", "-s", "--format=%cI", f"{rev}^{{commit}}", timeout=timeout).strip()
    return datetime.fromisoformat(out)


def rev_parse(repo: Path, rev: str, timeout: float = 30.0) -> str:
    return git(repo, "rev-parse", f"{rev}^{{commit}}", timeout=timeout).strip()


def has_commit(repo: Path, sha: str) -> bool:
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True, timeout=30)
    return proc.returncode == 0
