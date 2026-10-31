"""Exact-text search: ripgrep when available, pure-Python fallback otherwise."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

# Hidden folders such as .github are searched (CI workflows are often what a report is about); these are not.
SKIP_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "build", "dist", "site-packages",
             "__pycache__", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "vendor", "third_party",
             ".eggs", ".idea", ".vscode", ".cache", ".next", ".nuxt", ".yarn", ".pnpm-store", ".gradle", ".terraform"}
MAX_FILE_BYTES = 512_000


@dataclass
class Hit:
    path: str  # repo-relative
    line: int
    text: str


def _rg_available() -> bool:
    return shutil.which("rg") is not None


def search_text(root: Path, needle: str, max_hits: int = 50, globs: list[str] | None = None,
                word: bool = False, timeout: float = 20.0) -> list[Hit]:
    if not needle.strip():
        return []
    if _rg_available():
        cmd = ["rg", "--no-heading", "--line-number", "--fixed-strings", "--max-count", "20",
               "--max-filesize", "512K", "--sort", "path", "--color", "never", "--hidden", "--glob", "!.git"]
        if word:
            cmd.append("--word-regexp")
        for d in SKIP_DIRS:
            cmd += ["--glob", f"!{d}/**"]
        for g in globs or []:
            cmd += ["--glob", g]
        cmd += ["--", needle, "."]
        try:
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return []
        hits = []
        for line in proc.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[1].isdigit():
                hits.append(Hit(path=parts[0].removeprefix("./"), line=int(parts[1]), text=parts[2][:300]))
                if len(hits) >= max_hits:
                    break
        return hits
    return _py_search(root, needle, max_hits, globs, word)


def _py_search(root: Path, needle: str, max_hits: int, globs: list[str] | None, word: bool) -> list[Hit]:
    pattern = re.compile(r"\b" + re.escape(needle) + r"\b") if word else None
    exts = {g.removeprefix("*") for g in (globs or []) if g.startswith("*.")}
    excludes = [g[1:] for g in (globs or []) if g.startswith("!")]
    hits: list[Hit] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name in SKIP_DIRS:
                continue
            if exts and not any(name.endswith(e) for e in exts):
                continue
            if any(fnmatch.fnmatch(name, x) for x in excludes):
                continue
            p = Path(dirpath) / name
            try:
                if p.is_symlink() or p.stat().st_size > MAX_FILE_BYTES:
                    continue
                lines = p.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            for i, text in enumerate(lines, 1):
                found = pattern.search(text) if pattern else needle in text
                if found:
                    hits.append(Hit(path=str(p.relative_to(root)), line=i, text=text[:300]))
                    if len(hits) >= max_hits:
                        return hits
    return hits


def search_literals(root: Path, needles: list[str], max_hits: int = 30, timeout: float = 15.0) -> dict[str, list[Hit]]:
    """One bounded scan for multiple exact strings. No regex interpretation, no absence claims.

    Unlike symbol search, include documentation, manifests and comments. Skip symlinks and
    binary files; no repository-controlled path may cause reads outside the checkout.
    """
    needles = list(dict.fromkeys(n for n in needles if n.strip() and "\n" not in n))
    found: dict[str, list[Hit]] = {n: [] for n in needles}
    if not needles or max_hits < 1:
        return found

    def rank(hit: Hit) -> tuple:
        path = Path(hit.path)
        code = path.suffix in {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".h",
                               ".cpp", ".cs", ".rb", ".php", ".swift", ".kt", ".vue", ".svelte", ".sh"}
        auxiliary = any(p in {"tests", "test", "fixtures", "examples", "docs"} for p in path.parts) or path.name.startswith("test_")
        return (0 if code and not auxiliary else 1 if code else 2, hit.path, hit.line)

    def record(path: str, line: int, content: str) -> None:
        for needle in needles:
            if needle in content:
                hit = Hit(path=path, line=line, text=content[:300])
                hits = found[needle]
                if len(hits) < max_hits:
                    hits.append(hit)
                    hits.sort(key=rank)
                elif rank(hit) < rank(hits[-1]):
                    hits[-1] = hit
                    hits.sort(key=rank)

    # A single Python scan avoids one process/tree traversal per literal and works without rg.
    deadline = time.monotonic() + timeout
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink())
        for name in sorted(filenames):
            if time.monotonic() >= deadline:
                return found
            if name in SKIP_DIRS:
                continue  # .git is a pointer file, rather than a directory, inside a worktree.
            path = Path(dirpath) / name
            try:
                if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                    continue
                raw = path.read_bytes()
                if b"\0" in raw:
                    continue
                for line, content in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                    record(str(path.relative_to(root)), line, content)
            except OSError:
                continue
    return found
