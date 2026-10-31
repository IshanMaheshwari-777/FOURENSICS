"""Runs the fixed pipeline. Holds no analysis logic itself.

Each stage is wrapped so a failure becomes a StageResult and the run continues with
what it has. The partial result is saved after every stage so the panel can show
progress (tool results first, model interpretation after).
"""
from __future__ import annotations

import logging
import re
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import PIPELINE_VERSION, Settings
from ..db import Database
from ..github.client import GitHubClient, public_repo
from ..llm.gateway import Gateway
from ..schemas import AnalysisInput, Anchor, Claim, Mode, PanelResult, Snapshot, StageResult, Target, Verification
from . import aggregate, missing_info
from .adjudicate import adjudicate
from .claims import extract_claims, fallback_claims
from .diagnostics import explain_result
from .extract_regex import MAX_LITERALS, extract_anchors
from .firewall import build_issue_input, build_pr_input, model_visible_text
from .gitutil import git
from .index import RepoIndex, get_index
from .ledger import Ledger
from .manifests import declarations
from .pr import pr_probes
from .probes import anchor_key, claim_probes, reference_probes
from .sandbox import find_entry, load_allowlist, reproduce
from .snapshot import RepoCache, assert_not_after, resolve_snapshot
from .validator import validate

log = logging.getLogger(__name__)

@dataclass
class Deps:
    settings: Settings
    db: Database
    repos: RepoCache
    gateway: Optional[Gateway] = None
    github: Optional[GitHubClient] = None


class _Skip(Exception):
    pass


@dataclass
class _Run:
    deps: Deps
    result: PanelResult
    deadline: float
    stages: list[StageResult] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str, required: bool = False):
        if time.monotonic() > self.deadline:
            self.result.stages.append(StageResult(stage=name, status="skipped", reason="time budget exceeded"))
            self.save()
            raise _Skip(name)
        t0 = time.monotonic()
        entry = StageResult(stage=name, status="ok")
        try:
            yield entry
        except _Skip:
            entry.status, entry.reason = "skipped", entry.reason or "skipped"
        except Exception as e:  # never crash the run
            entry.status, entry.reason = "failed", f"{e.__class__.__name__}: {str(e)[:300]}"
            if required:
                entry.ms = int((time.monotonic() - t0) * 1000)
                self.result.stages.append(entry)
                raise
        entry.ms = int((time.monotonic() - t0) * 1000)
        self.result.stages.append(entry)
        self.save()

    def save(self) -> None:
        self.deps.db.save_run(self.result)


def fetch_input(deps: Deps, target: Target, mode: Mode) -> tuple[Target, AnalysisInput]:
    gh = deps.github
    if gh is None:
        raise RuntimeError("No GitHub client configured")
    repo_info = public_repo(gh, target.owner, target.repo)
    if target.kind == "issue":
        issue = gh.issue(target.owner, target.repo, target.number)
        # A transferred issue redirects to its new repository: analyse the code it now belongs to.
        moved = re.search(r"/repos/([^/]+)/([^/]+)$", issue.get("repository_url") or "")
        if moved and (moved[1], moved[2]) != (target.owner, target.repo):
            number = int(issue.get("number") or target.number)
            target = target.model_copy(update={"owner": moved[1], "repo": moved[2], "number": number,
                                               "url": f"https://github.com/{moved[1]}/{moved[2]}/issues/{number}"})
            repo_info = public_repo(gh, target.owner, target.repo)
        if "pull_request" in issue:  # the issues endpoint also serves PRs
            target = target.model_copy(update={"kind": "pr", "url": target.url.replace("/issues/", "/pull/")})
        else:
            comments = gh.issue_comments(target.owner, target.repo, target.number) if mode == "live" else None
            inp = build_issue_input(target, issue, repo_info.get("default_branch") or "main", mode, comments)
            return target, inp.model_copy(update={"repo_size_kb": int(repo_info.get("size") or 0)})
    pull = gh.pull(target.owner, target.repo, target.number)
    inp = build_pr_input(target, pull, mode)
    return target, inp.model_copy(update={"repo_size_kb": int(repo_info.get("size") or 0)})


def analyze(deps: Deps, result: PanelResult, target: Optional[Target] = None,
            prepared: Optional[AnalysisInput] = None, mode: Mode = "live") -> PanelResult:
    s = deps.settings
    run = _Run(deps, result, deadline=time.monotonic() + s.run_timeout_s)
    result.status = "running"
    result.summary_line = "Reading the report"
    result.pipeline_version = PIPELINE_VERSION
    result.models = {"reasoning": deps.gateway.model if deps.gateway else "unavailable"}
    if getattr(deps.gateway, "cache_only", False):
        result.notes.append(f"Inference is restricted to cached responses for {deps.gateway.model}. Cache misses are shown as degraded "
                            "model stages; no new model calls were made.")
    run.save()

    inp: Optional[AnalysisInput] = prepared
    anchors: list[Anchor] = []
    snap = Snapshot()
    repo_path: Optional[Path] = None
    root: Optional[Path] = None
    index: Optional[RepoIndex] = None
    base_index: Optional[RepoIndex] = None
    claims: list[Claim] = []
    report_kind = None
    repro: dict[str, Verification] = {}
    sandbox_ran = False

    try:
        with run.stage("fetch", required=True):
            if inp is None:
                assert target is not None
                target, inp = fetch_input(deps, target, mode)
            result.target = inp.target
        ledger = Ledger(inp.target.owner, inp.target.repo)

        with run.stage("anchors"):
            anchors = extract_anchors(f"{inp.title}\n{model_visible_text(inp)}")
            if sum(a.kind == "literal" for a in anchors) == MAX_LITERALS:
                result.notes.append(f"Literal retrieval is capped at {MAX_LITERALS} distinct quoted strings, "
                                    "annotations and log messages; additional literals may not have been searched.")

        # Claim extraction needs only the report text and anchors, so it runs while the code is checked
        # out, indexed and searched. Same inputs and the same single call as before; it only starts sooner.
        extraction: Optional[Future] = None
        if deps.gateway is not None:
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="triage-extract")
            extraction = pool.submit(extract_claims, inp, anchors, deps.gateway, run_id=result.run_id)
            pool.shutdown(wait=False)

        with run.stage("snapshot") as st:
            repo_path = deps.repos.ensure_clone(inp.target.owner, inp.target.repo)
            if inp.pr:
                deps.repos.fetch_pr(repo_path, inp.target.number)
                if not deps.repos.ensure_commit(repo_path, inp.pr.base_sha):
                    raise RuntimeError("Base commit is not available")
                snap = Snapshot(sha=inp.pr.head_sha, method="pr", reliability="exact",
                                ref_label=f"PR #{inp.target.number} head {inp.pr.head_sha[:7]}",
                                base_sha=inp.pr.base_sha, head_sha=inp.pr.head_sha)
            else:
                snap, notes = resolve_snapshot(repo_path, inp, anchors)
                result.notes += notes
                if snap.sha and inp.mode == "eval":
                    assert_not_after(repo_path, snap.sha, inp.created_at)
            result.snapshot = snap
            sparse = inp.repo_size_kb > s.sparse_checkout_over_kb
            if sparse:
                result.notes.append(f"Large repository (~{inp.repo_size_kb // 1024} MB): only source and "
                                    "documentation files were checked out.")
            if snap.sha:
                root = deps.repos.worktree(inp.target.owner, inp.target.repo, snap.sha, sparse=sparse)
                ledger.register_root(snap.sha, root)
            else:
                st.status, st.reason = "degraded", "no code version could be determined"

        with run.stage("missing_info"):
            if not inp.pr:
                missing = missing_info.check_missing(model_visible_text(inp), anchors)
                result.missing_info = missing_info.human_labels(missing)
                result.reply_draft = missing_info.reply_draft(missing)

        with run.stage("index") as st:
            if root is None:
                raise _Skip()
            sparse = inp.repo_size_kb > s.sparse_checkout_over_kb
            # The commit's full file list comes from git, so files outside a sparse checkout still "exist".
            all_files = set(git(repo_path, "ls-tree", "-r", "--name-only", snap.sha, timeout=120).splitlines())
            index = get_index(root, f"{inp.target.full_name}@{root.name}", all_files=all_files, complete=not sparse)
            if index.unparsed:
                result.notes.append(f"{len(index.unparsed)} Python file(s) could not be parsed and were searched as text only.")
            if inp.pr and snap.base_sha:
                base_root = deps.repos.worktree(inp.target.owner, inp.target.repo, snap.base_sha,
                                                sparse=inp.repo_size_kb > s.sparse_checkout_over_kb)
                ledger.register_root(snap.base_sha, base_root)
                base_files = set(git(repo_path, "ls-tree", "-r", "--name-only", snap.base_sha, timeout=120).splitlines())
                base_index = get_index(base_root, f"{inp.target.full_name}@{base_root.name}", all_files=base_files,
                                       complete=not sparse)
            if index.code_chunks and index.python_share < 0.5:
                result.notes.append("Mostly non-Python code: structural checks are limited; evidence comes from "
                                    "text search.")

        anchor_items: dict = {}
        with run.stage("reference_checks"):
            if index is None:
                raise _Skip()
            anchor_items = reference_probes(index, anchors, snap, ledger,
                                            repo_path=None if inp.pr else repo_path, created=inp.created_at)
            result.evidence = list(ledger.items)
            result.summary_line = "Reference checks done; interpreting claims"

        with run.stage("claims") as st:
            if extraction is None:
                claims = fallback_claims(inp, anchors)
                report_kind = "pr" if inp.pr else None
                st.status, st.reason = "degraded", "model unavailable; using the title as the main claim"
            else:
                try:
                    report_kind, claims, notes = extraction.result()  # re-raises the call's own error
                    result.notes += notes
                except Exception as e:
                    claims = fallback_claims(inp, anchors)
                    report_kind = "pr" if inp.pr else None
                    st.status, st.reason = "degraded", f"model unavailable ({str(e)[:160] or e.__class__.__name__}); using the title"
            result.claims = claims
            result.report_kind = report_kind
            for c in claims:
                for a in c.anchors:
                    for eid in anchor_items.get(anchor_key(a), []):
                        ledger.link(eid, c.id)

        with run.stage("claim_checks"):
            if index is None:
                raise _Skip()
            decls = declarations(index.root, index.files)
            for c in claims:
                claim_probes(index, c, snap, ledger, decls=decls)
            if inp.pr and base_index is not None and repo_path is not None:
                result.notes += pr_probes(repo_path, base_index, index, snap, claims, ledger, inp.title, inp.body,
                                          gh=deps.github, owner=inp.target.owner, repo=inp.target.repo)
            result.evidence = list(ledger.items)

        with run.stage("reproduction") as st:
            entry = None
            if s.enable_sandbox and not inp.pr and claims:
                entry = find_entry(load_allowlist(s.sandbox_allowlist), inp.target.url)
            if entry is None:
                raise _Skip()
            if entry.get("notes"):
                result.notes.append("Sandbox scope: " + str(entry["notes"]))
            primary = next(c for c in claims if c.is_primary)
            rr = reproduce(entry, primary, anchors, image=s.sandbox_image)
            sandbox_ran = rr.status in ("reproduced", "not_reproduced")
            repro[primary.id] = rr.status
            ledger.add("sandbox_run", "E1" if sandbox_ran else "E3", rr.detail, snap.sha,
                       claim_ids=[primary.id], raw=rr.raw)
            if not sandbox_ran:
                st.status, st.reason = "degraded", rr.detail

        with run.stage("adjudication") as st:
            limits = aggregate.not_checked(snap, [], bool(inp.pr), sandbox_ran)
            assessments, notes = adjudicate(claims, ledger, snap, repro, deps.gateway, limits, run_id=result.run_id)
            result.notes += notes
            failed = [n for n in notes if "unavailable" in n]
            if failed:
                st.status, st.reason = "degraded", failed[0]

        with run.stage("validation"):
            assessments = validate(assessments, claims, ledger, snap, repro)
            aggregate.apply_strength(assessments, ledger, snap)
            result.assessments = assessments

        result.evidence = list(ledger.items)
        if report_kind in ("question", "feature", "docs"):
            result.missing_info, result.reply_draft = [], None
        elif result.missing_info and aggregate.main_claim_backed(claims, result.assessments):
            # The code already confirms the main problem: asking the reporter for more adds work for nobody.
            result.missing_info, result.reply_draft = [], None
        result.summary_line, result.summary_tone = aggregate.summarize(report_kind, claims, result.assessments)
        result.not_checked = aggregate.not_checked(snap, result.assessments, bool(inp.pr), sandbox_ran)
        bad = [x for x in result.stages if x.status in ("failed", "skipped") and x.stage not in ("reproduction",)]
        result.status = "partial" if bad else "complete"
    except _Skip:
        result.status = "partial"
        result.summary_line = "Analysis stopped early (time budget exceeded)"
    except Exception as e:
        log.warning("run %s failed:\n%s", result.run_id, traceback.format_exc())
        result.status = "error"
        result.error = f"{e.__class__.__name__}: {str(e)[:300]}"
        result.summary_line = "Analysis could not run"
        result.summary_tone = "neutral"
    result = explain_result(result)
    run.result = result
    run.save()
    return result
