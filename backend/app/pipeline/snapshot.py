"""Historical snapshot resolver.

Picks the code version the report is about, in this order:
  exact commit named by the reporter -> release tag for the stated version ->
  last default-branch commit before the issue was created -> unknown.
Everything after this stage uses the chosen full SHA, never a moving branch.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..schemas import AnalysisInput, Anchor, Snapshot
from .gitutil import GitError, commit_time, git, has_commit, rev_parse

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class RepoCache:
    def __init__(self, cache_dir: Path, git_timeout: float = 120.0, clone_url_overrides: Optional[dict] = None):
        self.repos_dir = Path(cache_dir) / "repos"
        self.wt_dir = Path(cache_dir) / "wt"
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        self.wt_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = git_timeout
        self.overrides = clone_url_overrides or {}
        self._fetched: set[str] = set()

    def clone_url(self, owner: str, repo: str) -> str:
        return self.overrides.get(f"{owner}/{repo}", f"https://github.com/{owner}/{repo}.git")

    def ensure_clone(self, owner: str, repo: str) -> Path:
        key = f"{owner}/{repo}"
        path = self.repos_dir / owner / repo
        with _lock_for(key):
            if not (path / "HEAD").exists() and not (path / ".git").exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                # Partial clone: full history, file contents fetched lazily on checkout.
                git(path.parent, "clone", "--filter=blob:none", "--no-checkout", "--bare",
                    self.clone_url(owner, repo), path.name, timeout=self.timeout * 3)
                git(path, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*", timeout=30)
                git(path, "fetch", "--tags", "--prune", "origin", timeout=self.timeout * 2)
                self._fetched.add(key)
            elif key not in self._fetched:
                git(path, "fetch", "--tags", "--prune", "origin", timeout=self.timeout * 2)
                self._fetched.add(key)
        return path

    def fetch_pr(self, repo_path: Path, number: int) -> None:
        git(repo_path, "fetch", "origin", f"+refs/pull/{number}/head:refs/prs/{number}", timeout=self.timeout)

    def ensure_commit(self, repo_path: Path, sha: str) -> bool:
        if has_commit(repo_path, sha):
            return True
        try:
            git(repo_path, "fetch", "origin", sha, timeout=self.timeout)
        except GitError:
            return False
        return has_commit(repo_path, sha)

    def worktree(self, owner: str, repo: str, sha: str, sparse: bool = False) -> Path:
        """Checks out `sha`. With sparse=True only source and docs files are downloaded (large repos)."""
        repo_path = self.repos_dir / owner / repo
        wt = self.wt_dir / owner / repo / (sha + ("-sparse" if sparse else ""))
        with _lock_for(f"wt:{owner}/{repo}"):
            if (wt / ".git").exists():
                return wt
            wt.parent.mkdir(parents=True, exist_ok=True)
            git(repo_path, "worktree", "prune", timeout=30, check=False)
            if not sparse:
                git(repo_path, "worktree", "add", "--detach", "--force", str(wt), sha, timeout=self.timeout * 3)
            else:
                git(repo_path, "worktree", "add", "--detach", "--force", "--no-checkout", str(wt), sha,
                    timeout=self.timeout)
                git(wt, "sparse-checkout", "set", "--no-cone", *SPARSE_PATTERNS, timeout=60)
                git(wt, "read-tree", "-mu", "HEAD", timeout=self.timeout * 3)
        return wt


SPARSE_PATTERNS = [f"*{ext}" for ext in (
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".go", ".rs", ".java", ".kt",
    ".scala", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".dart", ".ex", ".exs", ".lua",
    ".sh", ".md", ".rst", ".toml", ".cfg", ".ini", ".gradle", ".yml", ".yaml")] + [
    "package.json", "composer.json", "go.mod", "Gemfile", "pom.xml", "Pipfile", "requirements*.txt",  # manifests
    "!*.min.js", "!**/node_modules/**", "!**/vendor/**"]


_VERSION_CLEAN = re.compile(r"^[vV]?(\d+(?:\.\d+){1,3}(?:[-.]?(?:a|b|rc|alpha|beta|dev|post)\.?\d*)?)$")


def _tag_candidates(version: str) -> list[str]:
    m = _VERSION_CLEAN.match(version.strip())
    if not m:
        return []
    v = m.group(1)
    return [f"v{v}", v, f"release-{v}", f"release/{v}", f"{v}.0", f"v{v}.0"]


def _prefixed_tags(tags: set[str], version: str, report_text: str, notes: list[str]) -> list[str]:
    """Monorepo tags such as 'pkg@1.2.3', 'pkg-v1.2.3' or 'pkg/v1.2.3'. Used only when unambiguous:
    a single match, or a single match whose package name the report mentions."""
    v = version.lstrip("vV")
    pattern = re.compile(r"(.+?)[@/_-]v?" + re.escape(v) + r"$")
    matches = {t: m.group(1) for t in tags if (m := pattern.fullmatch(t))}
    if len(matches) <= 1:
        return list(matches)
    named = [t for t, pkg in matches.items() if pkg.split("/")[-1].lower() in report_text]
    if len(named) == 1:
        return named
    notes.append(f"Several packages have a {v} tag and the report doesn't say which; using the date instead.")
    return []


STALE_TAG_DAYS = 730
# Words that, right before a version number, mean it is some other software's version.
_OTHER_SOFTWARE = {
    "python", "py", "node", "nodejs", "npm", "yarn", "pnpm", "go", "golang", "rust", "rustc", "cargo", "java", "jdk",
    "jre", "openjdk", "kotlin", "ruby", "gem", "php", "gcc", "clang", "llvm", "cmake", "docker", "kubernetes", "k8s",
    "ubuntu", "debian", "fedora", "centos", "alpine", "macos", "osx", "ios", "android", "windows", "win", "chrome",
    "firefox", "safari", "edge", "electron", "postgres", "postgresql", "mysql", "redis", "sqlite", "django", "react",
    "vue", "angular", "typescript", "ts", "webpack", "babel", "eslint", "pytest", "numpy", "pandas", "torch",
    "tensorflow", "cuda", "openssl", "glibc", "kernel", "linux", "xcode", "swift", "dotnet", ".net", "gradle", "maven",
}


def _project_names(inp: AnalysisInput) -> set[str]:
    repo = inp.target.repo.lower()
    return {repo, repo.replace("-", "_"), repo.replace("_", "-"), repo.removesuffix(".js"), repo.removeprefix("py")}


def _context_words(a: Anchor) -> list[str]:
    return re.findall(r"[a-z][a-z0-9_.+-]*", (a.context or "").lower())


def _names_project(a: Anchor, names: set[str]) -> bool:
    return any(w.strip(".-") in names for w in _context_words(a)[-4:])


def _belongs_elsewhere(a: Anchor, names: set[str]) -> bool:
    """True when the words right before the version name other software ("node v18", "CROSS_VERSION: v0.2.5")."""
    words = [w.strip(".:=-") for w in _context_words(a)]
    while words and words[-1] in ("v", "version", "ver", "release", "is", "at", "of", ""):
        words.pop()
    if not words or _names_project(a, names):
        return False
    last = words[-1]
    if last.endswith(("_version", "-version")) and last[:-8] not in names:
        return True
    return last in _OTHER_SOFTWARE


_BRANCH = re.compile(r"`?([\w][\w./-]{0,60})`?\s+branch\b|\bbranch\s+`?([\w][\w./-]{0,60})`?", re.I)
_NOT_BRANCHES = {"the", "this", "that", "a", "my", "your", "default", "main", "feature", "new", "same", "release",
                 "current", "stable", "development", "dev", "another", "which", "each", "other", "its", "our", "their"}


def _named_branches(text: str) -> list[str]:
    names = []
    for m in _BRANCH.finditer(text):
        name = (m.group(1) or m.group(2) or "").strip("`.,")
        if name and name.lower() not in _NOT_BRANCHES and name not in names:
            names.append(name)
    return names[:5]


def resolve_snapshot(repo_path: Path, inp: AnalysisInput, anchors: list[Anchor], timeout: float = 60.0) -> tuple[Snapshot, list[str]]:
    """Returns (snapshot, notes). Never uses commits created after the report."""
    notes: list[str] = []
    created: datetime = inp.created_at

    # 1. Exact commit named in the report
    for a in [a for a in anchors if a.kind == "commit"][:5]:
        try:
            sha = rev_parse(repo_path, a.value, timeout)
            if commit_time(repo_path, sha, timeout) <= created:
                return Snapshot(sha=sha, method="exact", reliability="exact",
                                ref_label=f"commit {sha[:7]} (named in the report)"), notes
        except GitError:
            continue

    # 2. Release tag matching the stated version (tag must exist before the report)
    tags = set(git(repo_path, "tag", "-l", timeout=timeout).split())
    text = f"{inp.title}\n{inp.body}".lower()
    names = _project_names(inp)
    for a in anchors:
        if a.kind != "version" or _belongs_elsewhere(a, names):
            continue
        for cand in _tag_candidates(a.value) + _prefixed_tags(tags, a.value, text, notes):
            if cand not in tags:
                continue
            try:
                sha = rev_parse(repo_path, f"refs/tags/{cand}", timeout)
                when = commit_time(repo_path, sha, timeout)
                if when > created:
                    notes.append(f"Tag {cand} was created after the report, so it was not used.")
                    continue
                # A years-old tag is more likely another tool's version than the one the reporter ran,
                # unless the report ties the version to this project by name.
                if (created - when).days > STALE_TAG_DAYS and not _names_project(a, names):
                    notes.append(f"Version {a.value} matches a tag from {when.date()}, too old to trust "
                                 f"without the project being named next to it; it was not used.")
                    continue
                return Snapshot(sha=sha, method="release", reliability="release-matched", ref_label=cand), notes
            except GitError:
                continue

    # 3. Last commit before the report on a branch the report names ("on the 4.x branch"), else the default branch
    for name in _named_branches(f"{inp.title}\n{inp.body}"):
        ref = f"refs/remotes/origin/{name}"
        if name == inp.default_branch or not git(repo_path, "rev-parse", "--verify", "--quiet", ref,
                                                 timeout=timeout, check=False).strip():
            continue
        sha = git(repo_path, "rev-list", "-1", f"--before={created.isoformat()}", ref, timeout=timeout).strip()
        if sha:
            return Snapshot(sha=sha, method="date", reliability="inferred",
                            ref_label=f"{name} branch as of {created.date().isoformat()}"), notes
    branch = f"refs/remotes/origin/{inp.default_branch}"
    try:
        sha = git(repo_path, "rev-list", "-1", f"--before={created.isoformat()}", branch, timeout=timeout).strip()
        if sha:
            label = f"{inp.default_branch} as of {created.date().isoformat()}"
            return Snapshot(sha=sha, method="date", reliability="inferred", ref_label=label), notes
    except GitError as e:
        notes.append(f"Could not read branch history: {e}")

    return Snapshot(), notes + ["No reliable code version could be determined."]


def assert_not_after(repo_path: Path, sha: str, created: datetime) -> None:
    """Guard used by probes in eval mode: never read code from after the report."""
    if commit_time(repo_path, sha) > created:
        raise ValueError(f"Commit {sha[:10]} is newer than the report; refusing to read it")
