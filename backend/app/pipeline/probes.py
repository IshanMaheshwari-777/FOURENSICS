"""Deterministic probes. Each answers one narrow question about the code at the
snapshot and records the answer in the ledger. No model is involved here."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..schemas import Anchor, Claim, Snapshot
from .extract_regex import symbol_leaf
from .gitutil import GitError, commit_time, git, rev_parse
from .index import RepoIndex, Symbol, route_matches
from .manifests import Declaration, mentioned
from .ledger import Ledger
from .retrieval import retrieve
from .search import search_text, search_literals
from .snapshot import _tag_candidates

AnchorKey = tuple
_CONDITION = re.compile(r"\b(none|null|empty|blank|missing|zero|negative|nan|whitespace|unicode|0)\b", re.I)
MAX_ITEMS_PER_CLAIM = 12
NON_DOC_GLOBS = ["!*.md", "!*.rst", "!*.txt", "!LICENSE*", "!COPYING*", "!*.lock", "!*.svg", "!*.min.js"]
_NOISE_DIRS = ("migrations/", "tests/", "test/", "cypress/", "fixtures/", "examples/", "scripts/")


_ARITY = re.compile(
    r"(?P<func>\w+)\(\) (?:missing \d+ required (?:positional|keyword-only) arguments?"
    r"|takes (?:from \d+ to )?\d+ positional arguments? but \d+ (?:were|was) given"
    r"|got an unexpected keyword argument '(?P<kw>\w+)')")


def _call_arity_probe(index: RepoIndex, error: str, ledger: Ledger, sha: str) -> list:
    """For TypeErrors about arguments: compare every call site with the function's signature."""
    m = _ARITY.search(error)
    defs = [s for s in index.lookup(m["func"]) if s.kind != "class" and not s.in_test_file]
    if not defs:
        return []
    d = defs[0]
    items = []
    bad, good = [], 0
    for caller in index.symbols:
        if caller.in_test_file:
            continue
        for name, line, n_pos, kws, star in caller.call_sites:
            if name != d.name or star or d.flexible:
                continue
            missing = [p for p in d.required[n_pos:] if p not in kws]
            unknown = [k for k in kws if k not in d.params]
            too_many = n_pos > len([p for p in d.params if p not in ("self", "cls")])
            if missing or unknown or too_many:
                bad.append((caller, line, n_pos, kws, missing, unknown))
            else:
                good += 1
    sig = f"`{d.name}({', '.join(d.params)})` requires {', '.join(f'`{r}`' for r in d.required) or 'no arguments'}"
    items.append(ledger.add("signature", "E2", sig, sha, path=d.path, start=d.start, end=d.start))
    for caller, line, n_pos, kws, missing, unknown in bad[:3]:
        if missing:
            given = len(d.required) - len(missing)
            problem = f"without {', '.join(f'`{m}`' for m in missing)} (passes {given} of {len(d.required)} required)"
        elif unknown:
            problem = f"with unknown keyword {', '.join(f'`{u}`' for u in unknown)}"
        else:
            problem = f"with too many arguments ({n_pos})"
        items.append(ledger.add("call_site_mismatch", "E2", f"`{caller.qualname}` calls `{d.name}` {problem}",
                                sha, path=caller.path, start=line, end=line))
    if not bad and good:
        items.append(ledger.add("call_sites_match", "E2",
                                f"All {good} call site(s) of `{d.name}` in non-test code pass the required arguments",
                                sha))
    return items


DEF_WINDOW = 30  # lines shown after a definition found by text match


def _def_regex(name: str) -> re.Pattern:
    """Definition-looking lines across common languages (heuristic, so results are tier E3)."""
    n = re.escape(name)
    return re.compile(
        rf"\b(?:def|function|fn|class|interface|struct|enum|trait|type|module|object|record|protocol)\s+\*?{n}\b"
        rf"|\bfunc\s+(?:\([^)]*\)\s*)?{n}\s*[\[(]"                       # Go functions and methods
        rf"|^\s*(?:export\s+)?(?:default\s+)?(?:const|let|var|val)\s+{n}\b"  # JS/TS/Kotlin bindings
        rf"|\b{n}\s*[:=]\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>|\w+\s*=>)"  # name = (...) =>
        rf"|^\s*(?:(?:public|private|protected|internal|static|final|abstract|override|async|export|pub(?:\([^)]*\))?)\s+)+"
        rf"(?:[\w<>\[\],.?]+\s+)?{n}\s*\("                               # Java/C#/Rust/TS methods
    )


def _is_noise_path(path: str) -> bool:
    """Code that rarely explains a reported bug: migrations, tests, fixtures, scripts."""
    p = f"/{path}"
    return any(f"/{d}" in p for d in _NOISE_DIRS) or path.rsplit("/", 1)[-1].startswith("test_")


def anchor_key(a: Anchor) -> AnchorKey:
    return (a.kind, a.value, a.line)


def _sym_range(s: Symbol) -> tuple[int, int]:
    return s.start, min(s.end, s.start + 59)


def _match_repo_path(index: RepoIndex, path: str) -> str | None:
    p = path.replace("\\", "/")
    while p.startswith(("./", "/")):
        p = p[2:] if p.startswith("./") else p[1:]
    if p in index.files:
        return p
    candidates = [rel for rel in index.files if p.endswith("/" + rel) or rel.endswith("/" + p)]
    if "/" not in p and len(candidates) > 1:
        return None  # a bare file name like "main.go" that several files share: too ambiguous to check
    if not candidates:
        return None
    longest = max(map(len, candidates))
    best = [p for p in candidates if len(p) == longest]
    return best[0] if len(best) == 1 else None


_BUILT = re.compile(r"(^|/)(node_modules|site-packages|dist-packages|dist|build|out|target|\.next|esm|cjs|umd|"
                    r"__pycache__)/")
_INSTALL_ROOT = re.compile(r"^.*?(?:node_modules/(?:@[^/]+/)?[^/]+|(?:site|dist)-packages/[^/]+|"
                           r"(?:^|/)(?:dist|build|out|target|\.next|esm|cjs|umd))/")
_SOURCE_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go", ".java", ".kt")


def _is_built_path(path: str) -> bool:
    return bool(_BUILT.search(path.replace("\\", "/")))


def _source_for_built(index: RepoIndex, path: str) -> str | None:
    """node_modules/zod/v4/core/util.js -> packages/zod/src/v4/core/util.ts (same stem, a source extension)."""
    tail = _INSTALL_ROOT.sub("", path.replace("\\", "/"), count=1)
    stem = re.sub(r"\.(d\.ts|[cm]?[jt]sx?|pyc?|map)$", "", tail)
    if not stem or "/" not in stem and len(stem) < 4:
        return None
    candidates = [f for f in index.files if f.endswith(_SOURCE_EXTS) and not _is_built_path(f)
                  and re.sub(r"\.[^./]+$", "", f).endswith(("/" + stem) if not f.startswith(stem) else stem)]
    candidates.sort(key=lambda f: (not f.endswith((".ts", ".tsx", ".py", ".rs", ".go")), len(f), f))
    return candidates[0] if candidates else None


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def reference_probes(index: RepoIndex, anchors: list[Anchor], snap: Snapshot, ledger: Ledger,
                     repo_path: Path | None = None, created=None) -> dict[AnchorKey, list[str]]:
    """Checks every concrete reference in the report. Returns anchor -> evidence IDs."""
    out: dict[AnchorKey, list[str]] = {}
    sha = snap.sha
    frame_paths = {a.value for a in anchors if a.kind == "traceback_frame"}
    referenced = {symbol_leaf(a.value) for a in anchors if a.kind == "symbol"}
    literals = search_literals(index.root, [a.value for a in anchors if a.kind == "literal"])

    def rec(a: Anchor, item) -> None:
        out.setdefault(anchor_key(a), []).append(item.id)

    for a in anchors:
        if a.kind == "literal":
            hits = sorted(literals.get(a.value, []),
                          key=lambda h: (h.path.endswith((".md", ".rst", ".txt")), _is_noise_path(h.path), h.path, h.line))
            # Retain distinct code and documentation locations; matching text alone establishes no behaviour.
            selected = []
            for h in hits:
                is_doc = h.path.endswith((".md", ".rst", ".txt"))
                if sum(x.path.endswith((".md", ".rst", ".txt")) == is_doc for x in selected) < 2:
                    selected.append(h)
            for h in selected:
                kind = "literal_in_docs" if h.path.endswith((".md", ".rst", ".txt")) else "literal_match"
                rec(a, ledger.add(kind, "E3", f"Exact report text {a.value!r} occurs in {h.path}:{h.line}; "
                                  "a text match alone does not establish the reported behaviour", sha,
                                  path=h.path, start=max(1, h.line - 3), end=h.line + 5))
        elif a.kind == "symbol":
            full = a.value.removesuffix("()")
            leaf = symbol_leaf(full)
            matches = index.lookup(full) or index.lookup(leaf)
            matches = [m for m in matches if not m.in_test_file] or [m for m in matches if not m.is_test] or matches
            if matches:
                s = matches[0]
                start, end = _sym_range(s)
                more = f" (+{len(matches) - 1} more)" if len(matches) > 1 else ""
                rec(a, ledger.add("symbol_found", "E2", f"`{leaf}` is defined in {s.path} at line {s.start}{more}",
                                  sha, path=s.path, start=start, end=end))
                continue
            # A name the report uses for a parameter of a function it also names.
            owners = [s for s in index.symbols if leaf in s.params and s.name in referenced
                      and not s.in_test_file and s.name != leaf]
            if owners:
                s = owners[0]
                rec(a, ledger.add("symbol_is_parameter", "E2",
                                  f"`{leaf}` is a parameter of `{s.qualname}` ({', '.join(s.params)})",
                                  sha, path=s.path, start=s.start, end=s.start))
                continue
            hits = search_text(index.root, leaf, max_hits=40, word=True, globs=NON_DOC_GLOBS)
            hits.sort(key=lambda h: (_is_noise_path(h.path), h.path, h.line))
            # Other languages: a definition-looking line (function/fn/func/class/const ... name).
            defs = [h for h in hits if _def_regex(leaf).search(h.text)]
            if defs:
                h = defs[0]
                more = f" (+{len(defs) - 1} more)" if len(defs) > 1 else ""
                rec(a, ledger.add("symbol_defined_text", "E3",
                                  f"`{leaf}` looks defined in {h.path} at line {h.line}{more} (text match)",
                                  sha, path=h.path, start=h.line, end=h.line + DEF_WINDOW))
            elif hits:
                h = hits[0]
                rec(a, ledger.add("symbol_mentioned", "E3" if not _is_noise_path(h.path) else "E4",
                                  f"`{leaf}` has no definition found, but appears in {h.path}:{h.line}",
                                  sha, path=h.path, start=max(1, h.line - 3), end=h.line + 5))
            elif search_text(index.root, leaf, max_hits=1, word=True):
                rec(a, ledger.add("symbol_in_docs_only", "E3",
                                  f"`{leaf}` appears only in documentation or text files, not in code", sha))
            elif "." in full and not index.lookup(full.split(".")[0]) and \
                    not search_text(index.root, full.split(".")[0], max_hits=1, word=True, globs=NON_DOC_GLOBS):
                # `task.cancel`, `os.path.join`: a method on something this repository doesn't define.
                rec(a, ledger.add("symbol_external", "E3",
                                  f"`{full}` isn't defined in this repository (likely another library's API)",
                                  sha, absence=True))
            elif not index.complete:
                # Sparse checkout: it may live in a file type that wasn't downloaded. Weak, not a fact.
                rec(a, ledger.add("symbol_not_in_sources", "E3",
                                  f"`{leaf}` was not found in the checked-out source files (large repository, "
                                  f"partial checkout)", sha, absence=True))
            else:
                rec(a, ledger.add("symbol_absent", "E2",
                                  f"`{leaf}` does not appear anywhere in the repository at this version", sha))

        elif a.kind == "path":
            if a.value in frame_paths and "/" not in a.value:
                continue  # the reporter's own script named in a traceback, not a repo file
            if _is_built_path(a.value) and _match_repo_path(index, a.value) is None:
                # An installed or compiled file (node_modules/pkg/x.js, dist/, site-packages/): never "missing";
                # point at the source file it was built from when there is one.
                src = _source_for_built(index, a.value)
                if src:
                    rec(a, ledger.add("path_built_from", "E3",
                                      f"{a.value} is the published/built form of {src} in this repository",
                                      sha, path=src))
                continue
            rel = _match_repo_path(index, a.value)
            if rel:
                rec(a, ledger.add("path_found", "E2", f"File {rel} exists at this version", sha, path=rel))
            else:
                base = a.value.rsplit("/", 1)[-1]
                similar = sorted(f for f in index.files if f.rsplit("/", 1)[-1] == base)
                if similar:
                    rec(a, ledger.add("path_similar", "E4",
                                      f"No file at {a.value}; a file with the same name exists at {similar[0]}",
                                      sha, path=similar[0]))
                elif "/" in a.value:
                    rec(a, ledger.add("path_absent", "E2", f"No file matching {a.value} exists at this version", sha))

        elif a.kind == "traceback_frame":
            if _is_built_path(a.value) and not a.value.endswith(".py"):
                continue  # compiled/bundled output: its line numbers don't correspond to the source
            rel = _match_repo_path(index, a.value)
            if not rel or a.line is None:
                continue  # frame from the standard library or another package
            if not (index.root / rel).exists():
                continue  # in the commit but outside a sparse checkout: can't check its lines
            n_lines = index.py_lines.get(rel) or _count_lines(index.root / rel)
            if a.line > n_lines:
                rec(a, ledger.add("frame_line_out_of_range", "E2",
                                  f"Traceback cites {rel}:{a.line}, but the file has only {n_lines} lines at this version",
                                  sha, path=rel))
                continue
            sym = index.enclosing(rel, a.line)
            if a.func and a.func != "<module>" and sym and sym.name != a.func:
                rec(a, ledger.add("frame_func_mismatch", "E2",
                                  f"Traceback says line {a.line} of {rel} is in `{a.func}`, but at this version it is in `{sym.name}`",
                                  sha, path=rel, start=a.line, end=a.line))
            elif a.func and a.func != "<module>" and sym is None:
                rec(a, ledger.add("frame_func_mismatch", "E2",
                                  f"Traceback says line {a.line} of {rel} is in `{a.func}`, but that line is not inside any function at this version",
                                  sha, path=rel, start=a.line, end=a.line))
            else:
                rec(a, ledger.add("frame_match", "E2",
                                  f"Traceback frame {rel}:{a.line} matches the code at this version"
                                  + (f" (in `{sym.name}`)" if sym else ""), sha, path=rel, start=a.line, end=a.line))

        elif a.kind == "route":
            handlers = [s for s in index.symbols if not s.in_test_file and any(route_matches(r, a.value) for r in s.routes)]
            # Prefer the most specific pattern (fewest parameters) when several match.
            handlers.sort(key=lambda s: min(r.count("<") + r.count("{") for r in s.routes if route_matches(r, a.value)))
            for s in handlers[:1]:
                start, end = _sym_range(s)
                pattern = next(r for r in s.routes if route_matches(r, a.value))
                rec(a, ledger.add("route_handler", "E2", f"`{a.value}` is handled by `{s.qualname}` (route \"{pattern}\")",
                                  sha, path=s.path, start=start, end=end, raw=f"{s.start}-{s.end}"))

        elif a.kind == "error" and _ARITY.search(a.value):
            for item in _call_arity_probe(index, a.value, ledger, sha):
                rec(a, item)

        if a.kind == "error":
            exc, _, msg = a.value.partition(":")
            exc = exc.split(".")[-1].strip()
            msg = msg.strip().strip("'\"")
            found_any = False
            if len(msg) >= 3:
                for h in search_text(index.root, msg[:120], max_hits=3, globs=NON_DOC_GLOBS):
                    found_any = True
                    rec(a, ledger.add("error_text_found", "E3", f"The text \"{msg}\" appears in {h.path}:{h.line}",
                                      sha, path=h.path, start=h.line, end=h.line))
            raisers = [(s, ln) for s in index.symbols for ln, name in s.raises if name == exc and not s.is_test]
            for s, ln in raisers[:2]:
                found_any = True
                rec(a, ledger.add("raise_site", "E3", f"`{s.name}` explicitly raises {exc} at {s.path}:{ln}",
                                  sha, path=s.path, start=ln, end=ln))
            if not found_any and msg:
                rec(a, ledger.add("error_text_absent", "E3",
                                  f"Neither the message \"{msg[:120]}\" nor an explicit raise of {exc} was found in the code",
                                  sha, absence=True))

    # Version diff: two stated versions that both existed before the report.
    if repo_path is not None and created is not None:
        versions = [a for a in anchors if a.kind == "version"]
        tags = []
        try:
            existing = set(git(repo_path, "tag", "-l", timeout=30).split())
            for a in versions:
                for cand in _tag_candidates(a.value):
                    if cand in existing and commit_time(repo_path, rev_parse(repo_path, f"refs/tags/{cand}")) <= created:
                        tags.append((a, cand))
                        break
        except GitError:
            tags = []
        if len(tags) >= 2:
            (a1, t1), (a2, t2) = tags[0], tags[1]
            try:
                stat = git(repo_path, "diff", "--stat", t1, t2, "--", "*.py", timeout=60)
                item = ledger.add("changed_between_versions", "E2",
                                  f"Python files changed between {t1} and {t2}: "
                                  f"{max(0, len(stat.strip().splitlines()) - 1)}", sha, raw=stat)
                out.setdefault(anchor_key(a1), []).append(item.id)
                out.setdefault(anchor_key(a2), []).append(item.id)
            except GitError:
                pass
    return out


def _text_evidence(index: RepoIndex, claim: Claim, add) -> None:
    """Language-independent evidence: definitions and error texts found by search, then similar code."""
    shown = 0
    for a in claim.anchors:
        if shown >= 3:
            break
        if a.kind == "symbol":
            leaf = symbol_leaf(a.value)
            if index.lookup(leaf):
                continue  # already covered by the Python structure
            hits = search_text(index.root, leaf, max_hits=40, word=True, globs=NON_DOC_GLOBS)
            defs = sorted((h for h in hits if _def_regex(leaf).search(h.text)),
                          key=lambda h: (_is_noise_path(h.path), h.path, h.line))
            for h in defs[:1]:
                shown += 1
                add("code_excerpt", "E3", f"Definition of `{leaf}` ({h.path}, found by text match)",
                    path=h.path, start=h.line, end=h.line + DEF_WINDOW)
        elif a.kind == "error" and ":" in a.value:
            msg = a.value.split(":", 1)[1].strip().strip("'\"")
            if len(msg) < 6:
                continue
            for h in search_text(index.root, msg[:80], max_hits=3, globs=NON_DOC_GLOBS)[:1]:
                shown += 1
                add("code_excerpt", "E3", f"Code containing the reported error text ({h.path})",
                    path=h.path, start=max(1, h.line - 6), end=h.line + 10)
    query = " ".join(filter(None, [claim.text, claim.condition, claim.observed]))
    for chunk, _ in index.search_chunks(query, k=2):
        if not _is_noise_path(chunk.path):
            add("code_excerpt", "E4", f"Possibly related code ({chunk.path}, by similarity)",
                path=chunk.path, start=chunk.start, end=chunk.end)


def claim_probes(index: RepoIndex, claim: Claim, snap: Snapshot, ledger: Ledger,
                 decls: Optional[list[Declaration]] = None) -> None:
    """Probes specific to one claim: target code, guards, related code, tests, docs, dependencies."""
    sha = snap.sha
    cid = claim.id
    added = 0

    def add(*args, **kwargs):
        nonlocal added
        added += 1
        kwargs.setdefault("claim_ids", [cid])
        return ledger.add(*args, sha=sha, **kwargs)

    # Dependencies the claim names, as declared in the project's manifest files.
    for d in mentioned(decls or [], " ".join(filter(None, [claim.text, claim.source_span]))):
        add("dependency_declared", "E2", f"{d.path} declares {d.text}", path=d.path, start=d.line, end=d.line)

    # Target code: anchored symbols, traceback frames in the repo, then retrieval.
    targets: list[Symbol] = []
    direct_targets: set[tuple[str, int]] = set()
    for a in claim.anchors:
        if a.kind == "route":
            for s in index.symbols:
                if not s.in_test_file and s not in targets and any(route_matches(r, a.value) for r in s.routes):
                    targets.insert(0, s)  # the endpoint's handler is where the reported request starts
                    direct_targets.add((s.path, s.start))
                    break
        elif a.kind == "symbol":
            for s in index.lookup(a.value.removesuffix("()")) or index.lookup(symbol_leaf(a.value)):
                if not s.in_test_file and s.kind != "class" and s not in targets:
                    targets.append(s)
                    direct_targets.add((s.path, s.start))
        elif a.kind == "traceback_frame" and a.line:
            rel = _match_repo_path(index, a.value)
            s = index.enclosing(rel, a.line) if rel else None
            if s and s not in targets:
                targets.append(s)
                direct_targets.add((s.path, s.start))
    candidates = retrieve(index, claim)
    for c in candidates:
        if len(targets) >= 4:
            break
        # Similarity-only matches from migrations, tests or scripts are rarely the code a report is about.
        if c.symbol not in targets and not c.symbol.in_test_file and (c.anchored or not _is_noise_path(c.symbol.path)):
            targets.append(c.symbol)
    anchored_names = {t.name for t in targets[:4]}
    direct_targets.update((c.symbol.path, c.symbol.start) for c in candidates if c.anchored)

    def anchored(t: Symbol) -> bool:
        return (t.path, t.start) in direct_targets

    anchored_targets = 0
    for t in targets[:4]:
        start, end = _sym_range(t)
        anchored_targets += anchored(t)
        add("code_excerpt", "E3" if anchored(t) else "E4", f"Implementation of `{t.qualname}` ({t.path})",
            path=t.path, start=start, end=end)

    # Code the Python parser can't see (other languages, or names it couldn't resolve): text matches.
    if anchored_targets == 0 or index.python_share < 0.5:
        _text_evidence(index, claim, add)

    # Guards, when the claim is about a condition such as None or empty input.
    text = " ".join(filter(None, [claim.text, claim.condition, claim.observed]))
    if _CONDITION.search(text):
        for t in targets[:3]:
            if t.guards:
                desc = "; ".join(f"line {ln}: {d}" for ln, d in t.guards[:6])
                lines = [ln for ln, _ in t.guards]
                add("guards_present", "E2" if anchored(t) else "E4", f"`{t.name}` contains input checks: {desc}",
                    path=t.path, start=min(lines), end=max(lines))
            else:
                add("guard_not_found", "E3" if anchored(t) else "E4", f"No None/empty/length/try-except checks were found inside `{t.name}`",
                    path=t.path, start=t.start, end=t.start, absence=True)

    # Lookup sites for KeyError/IndexError claims.
    if re.search(r"\b(KeyError|IndexError)\b", text + " " + " ".join(a.value for a in claim.anchors)):
        for t in targets[:2]:
            if t.subscripts:
                lines = sorted(set(t.subscripts))[:8]
                add("lookup_sites", "E2" if anchored(t) else "E4", f"`{t.name}` does key/index lookups at lines {', '.join(map(str, lines))}",
                    path=t.path, start=lines[0], end=lines[-1])

    # One hop: callers and callees inside the repo.
    for t in targets[:2]:
        callers = [c for c in index.callers_of(t.name) if not c.in_test_file and not _is_noise_path(c.path)]
        for caller in callers[:2]:
            s, e = _sym_range(caller)
            add("caller", "E3" if anchored(t) else "E4", f"`{caller.qualname}` calls `{t.name}`", path=caller.path, start=s, end=e)
        for callee_name in sorted(t.calls)[:12]:
            # Resolve only to module-level functions, or methods in the same file: a bare method name
            # like `json` would otherwise match unrelated classes elsewhere.
            options = [s for s in index.lookup(callee_name) if not s.in_test_file and s.kind != "class"
                       and (s.kind == "function" or s.path == t.path)]
            options.sort(key=lambda s: (s.path != t.path, s.path))
            for callee in options[:1]:
                if callee.name in anchored_names or added >= MAX_ITEMS_PER_CLAIM:
                    continue
                s, e = _sym_range(callee)
                add("callee", "E3" if anchored(t) else "E4", f"`{t.name}` calls `{callee.qualname}`", path=callee.path, start=s, end=e)

    # Tests that exercise the target code.
    found_test = False
    for t in targets[:3]:
        for test in index.tests_for(t.name)[:2]:
            found_test = True
            s, e = _sym_range(test)
            add("test_found", "E3" if anchored(t) else "E4", f"Test `{test.qualname}` calls `{t.name}`", path=test.path, start=s, end=e)
    if targets and not found_test:
        add("no_test_found", "E3", f"No test calling {', '.join(f'`{t.name}`' for t in targets[:3])} was found",
            absence=True)

    # Documentation and docstrings.
    for doc, _ in index.search_docs(text, k=2):
        add("doc_excerpt", "E3", f"Documentation in {doc.path}", path=doc.path, start=doc.start, end=doc.end)
    for t in targets[:2]:
        if t.docstring:
            add("docstring", "E3" if anchored(t) else "E4", f"Docstring of `{t.name}`: {t.docstring[:200]}", path=t.path,
                start=t.start, end=t.start)
