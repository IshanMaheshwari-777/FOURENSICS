"""Pull request probes. They read the diff between the exact base and head commits
and existing CI results. PR code is never executed."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..schemas import Claim, Snapshot
from .extract_regex import extract_anchors, symbol_leaf
from .gitutil import GitError, git
from .index import RepoIndex, Symbol
from .ledger import Ledger

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.M)
DOC_EXTS = (".md", ".rst", ".txt")
MAX_DIFF_FILES = 6
MAX_DIFF_CHARS = 1800


@dataclass
class FileChange:
    path: str
    status: str
    old_ranges: list[tuple[int, int]]
    new_ranges: list[tuple[int, int]]


def diff_files(repo_path: Path, base: str, head: str) -> list[FileChange]:
    names = git(repo_path, "diff", "--name-status", "--no-renames", base, head, timeout=120)
    changes: dict[str, FileChange] = {}
    for line in names.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            changes[parts[-1]] = FileChange(parts[-1], parts[0][:1], [], [])
    patch = git(repo_path, "diff", "--unified=0", "--no-renames", base, head, timeout=120)
    current: Optional[FileChange] = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            current = changes.get(path)
        elif line.startswith("--- ") and line[4:] != "/dev/null":
            path = line[4:].removeprefix("a/")
            current = changes.get(path, current)
        elif line.startswith("@@") and current is not None:
            m = _HUNK.match(line)
            if m:
                os_, ol = int(m[1]), int(m[2] if m[2] is not None else 1)
                ns, nl = int(m[3]), int(m[4] if m[4] is not None else 1)
                if ol:
                    current.old_ranges.append((os_, os_ + ol - 1))
                if nl:
                    current.new_ranges.append((ns, ns + nl - 1))
    return list(changes.values())


def _overlapping(index: RepoIndex, path: str, ranges: list[tuple[int, int]]) -> list[Symbol]:
    out = []
    for s in index.symbols:
        if s.path != path or s.kind == "class":
            continue
        if any(a <= s.end and b >= s.start for a, b in ranges):
            out.append(s)
    # Innermost symbols only (a changed method, not also its class or outer function).
    return [s for s in out if not any(o is not s and o.start >= s.start and o.end <= s.end for o in out)]


_TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "testdata", "e2e", "integration-tests"}
# Test file names across languages: test_x.py, x_test.go, x.test.ts, x.spec.js, XTests.swift, XTest.java, x_spec.rb
_TEST_NAME = re.compile(r"(^test_|_test\.\w+$|\.(test|spec)\.\w+$|Tests?\.(swift|java|kt|cs|scala|m)$|_spec\.rb$)",
                        re.I)


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    return any(p.lower() in _TEST_DIRS for p in parts[:-1]) or bool(_TEST_NAME.search(parts[-1]))


def pr_probes(repo_path: Path, base_idx: RepoIndex, head_idx: RepoIndex, snap: Snapshot, claims: list[Claim],
              ledger: Ledger, title: str, body: str, gh=None, owner: str = "", repo: str = "") -> list[str]:
    """Adds PR evidence to the ledger. Returns notes."""
    notes: list[str] = []
    base, head = snap.base_sha or "", snap.head_sha or snap.sha
    all_ids = [c.id for c in claims]

    def ids_for(*types: str) -> list[str]:
        return [c.id for c in claims if c.type in types or c.is_primary] or all_ids

    try:
        changes = diff_files(repo_path, base, head)
    except GitError as e:
        notes.append(f"Could not read the diff: {e}")
        return notes
    if not changes:
        ledger.add("pr_empty_diff", "E2", "The PR changes no files", head, claim_ids=all_ids)
        return notes

    changed_head: list[Symbol] = []
    changed_base: dict[str, Symbol] = {}
    for ch in changes:
        if ch.path.endswith(".py"):
            changed_head += _overlapping(head_idx, ch.path, ch.new_ranges)
            for s in _overlapping(base_idx, ch.path, ch.old_ranges):
                changed_base[s.qualname] = s

    # What the diff touches
    for s in [s for s in changed_head if not s.is_test][:8]:
        ledger.add("pr_changed_symbol", "E2", f"`{s.qualname}` is changed in this PR", head,
                   claim_ids=all_ids, path=s.path, start=s.start, end=min(s.end, s.start + 59))
    changed_names = {s.name for s in changed_head} | {s.name for s in changed_base.values()}

    # The diff itself, for every language: what was removed and added, file by file.
    lockfile = re.compile(r"(\.lock|-lock\.json|\.sum|lock\.ya?ml)$")
    ordered = sorted(changes, key=lambda ch: (bool(lockfile.search(ch.path)), _is_test_path(ch.path), ch.path))
    for ch in ordered[:MAX_DIFF_FILES]:
        try:
            patch = git(repo_path, "diff", "--unified=2", "--no-renames", base, head, "--", ch.path, timeout=60)
        except GitError:
            continue
        body = "\n".join(l for l in patch.splitlines() if not l.startswith(("diff --git", "index ", "--- ", "+++ ")))
        if len(body) > MAX_DIFF_CHARS:
            body = body[:MAX_DIFF_CHARS] + "\n[... diff truncated ...]"
        start = ch.new_ranges[0][0] if ch.new_ranges else None
        end = ch.new_ranges[0][1] if ch.new_ranges else None
        verb = {"A": "added", "D": "deleted", "M": "changed"}.get(ch.status, "changed")
        ledger.add("pr_diff", "E2", f"Diff: {ch.path} ({verb})", head if ch.status != "D" else base,
                   claim_ids=all_ids, path=ch.path if ch.status != "D" else None,
                   start=start if ch.status != "D" else None, end=end, raw=body)
    if len(changes) > MAX_DIFF_FILES:
        notes.append(f"The PR changes {len(changes)} files; the diff of the first {MAX_DIFF_FILES} was used as evidence.")

    # Diff alignment with each claim's named code
    # Words on the diff's added/removed lines: a name counts as touched if the change edits it in any language.
    try:
        changed_text = "\n".join(l[1:] for l in git(repo_path, "diff", "--unified=0", "--no-renames", base, head,
                                                       timeout=120).splitlines()
                                 if l[:1] in "+-" and not l.startswith(("+++", "---")))
    except GitError:
        changed_text = ""
    for c in claims:
        named = {symbol_leaf(a.value) for a in c.anchors if a.kind == "symbol"}
        named_paths = {a.value.lstrip("./") for a in c.anchors if a.kind == "path"}
        if not named and not named_paths:
            continue
        hit = {n for n in named if n in changed_names or re.search(r"\b" + re.escape(n) + r"\b", changed_text)}
        hit |= {p for p in named_paths if any(ch.path.endswith(p) for ch in changes)}
        missed = (named | named_paths) - hit
        if hit:
            ledger.add("pr_diff_touches_claim", "E2", f"The diff changes {', '.join(f'`{t}`' for t in sorted(hit))}",
                       head, claim_ids=[c.id])
        if missed:
            ledger.add("pr_diff_misses_claim", "E2",
                       f"The diff does not change {', '.join(f'`{t}`' for t in sorted(missed))}",
                       head, claim_ids=[c.id])

    # Tests
    test_files = [ch.path for ch in changes if _is_test_path(ch.path)]
    test_funcs = [s for s in changed_head if s.is_test]
    if test_files or test_funcs:
        detail = ", ".join(sorted({s.qualname for s in test_funcs})[:6]) or ", ".join(test_files[:6])
        ledger.add("pr_tests_changed", "E2", f"Test code changed: {detail}", head, claim_ids=ids_for("pr_test", "pr_fix"),
                   path=(test_funcs[0].path if test_funcs else test_files[0]),
                   start=(test_funcs[0].start if test_funcs else None),
                   end=(min(test_funcs[0].end, test_funcs[0].start + 59) if test_funcs else None))
    else:
        ledger.add("pr_no_tests_changed", "E2", "No test files or test functions are changed", head,
                   claim_ids=ids_for("pr_test", "pr_fix"))

    # Guards added / signatures changed in functions present on both sides
    for s in changed_head:
        before = changed_base.get(s.qualname)
        if before is None or s.is_test:
            continue
        if len(s.guards) > len(before.guards):
            new_lines = [ln for ln, _ in s.guards]
            ledger.add("pr_guard_added", "E2",
                       f"`{s.name}` gains input checks ({len(before.guards)} → {len(s.guards)})", head,
                       claim_ids=ids_for("pr_fix"), path=s.path, start=min(new_lines), end=max(new_lines))
        if s.params != before.params and not s.name.startswith("_"):
            ledger.add("pr_signature_changed", "E2",
                       f"`{s.qualname}` signature changes from ({', '.join(before.params)}) to ({', '.join(s.params)})",
                       head, claim_ids=ids_for("pr_refactor"), path=s.path, start=s.start, end=s.start)
    head_names = {s.qualname for s in head_idx.symbols}
    for qual, s in changed_base.items():
        if qual not in head_names and not s.name.startswith("_") and not s.is_test:
            ledger.add("pr_symbol_removed", "E2", f"`{qual}` is removed by this PR", base,
                       claim_ids=ids_for("pr_refactor"), path=s.path, start=s.start, end=min(s.end, s.start + 59))

    # Low-substance signals (neutral wording; never about the author)
    if all(ch.path.endswith(DOC_EXTS) for ch in changes):
        ledger.add("pr_docs_only", "E2", "Only documentation or text files change", head, claim_ids=all_ids)
    try:
        if not git(repo_path, "diff", "-w", "--ignore-blank-lines", "--stat", base, head, timeout=60).strip():
            ledger.add("pr_whitespace_only", "E2", "The diff changes only whitespace", head, claim_ids=all_ids)
    except GitError:
        pass
    if not body.strip():
        notes.append("The PR has no description; claims come from the title only.")

    # Linked issues ("fixes #N"): does the diff touch what the issue names?
    linked = sorted({int(n) for n in re.findall(r"#(\d+)", f"{title}\n{body}")})[:3]
    if gh is not None:
        for n in linked:
            try:
                issue = gh.issue(owner, repo, n)
            except Exception:
                continue
            names = {symbol_leaf(a.value) for a in extract_anchors(f"{issue.get('title', '')}\n{issue.get('body') or ''}")
                     if a.kind == "symbol"}
            hit = sorted(names & changed_names)
            if hit:
                ledger.add("pr_linked_issue_touched", "E2", f"Linked issue #{n} names {', '.join(hit)}, which the diff changes",
                           head, claim_ids=ids_for("pr_fix"))
            elif names:
                ledger.add("pr_linked_issue_not_touched", "E3",
                           f"Linked issue #{n} names {', '.join(sorted(names)[:5])}; the diff changes none of them",
                           head, claim_ids=ids_for("pr_fix"))

        # CI results for the exact head commit
        try:
            runs = gh.check_runs(owner, repo, head)
        except Exception:
            runs = []
        if runs:
            done = [r for r in runs if r.get("status") == "completed"]
            conclusions: dict[str, int] = {}
            for r in done:
                conclusions[r.get("conclusion") or "unknown"] = conclusions.get(r.get("conclusion") or "unknown", 0) + 1
            pending = len(runs) - len(done)
            summary = ", ".join(f"{k}: {v}" for k, v in sorted(conclusions.items()))
            ledger.add("pr_ci_status", "E2", f"CI checks on the head commit: {summary or 'none finished'}"
                       + (f"; {pending} still running" if pending else ""), head, claim_ids=all_ids)
        else:
            ledger.add("pr_ci_unavailable", "E3", "No CI results are visible for the head commit (common for fork PRs)",
                       head, claim_ids=all_ids, absence=True)
    return notes
