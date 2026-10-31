"""Check literal retrieval on public historical reports without spending model tokens.

This is a retrieval smoke check, not an accuracy or reproduction result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.config import PIPELINE_VERSION, get_settings
from app.github.client import GitHubClient, public_repo
from app.github.urls import parse_target
from app.pipeline.extract_regex import extract_anchors
from app.pipeline.firewall import build_issue_input, model_visible_text
from app.pipeline.index import build_index
from app.pipeline.ledger import Ledger
from app.pipeline.probes import reference_probes
from app.pipeline.snapshot import RepoCache, resolve_snapshot, assert_not_after


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--urls", required=True)
    parser.add_argument("--out", default=str(ROOT / "examples" / "literal_checks.json"))
    args = parser.parse_args()
    settings = get_settings()
    gh = GitHubClient(settings.github_api, settings.github_token, cache_dir=settings.cache_dir / "http")
    repos = RepoCache(settings.cache_dir, settings.git_timeout_s)
    rows = []
    urls = [line.strip() for line in Path(args.urls).read_text().splitlines() if line.strip() and not line.startswith("#")]
    for url in urls:
        target = parse_target(url)
        info = public_repo(gh, target.owner, target.repo)
        raw = gh.issue(target.owner, target.repo, target.number)
        inp = build_issue_input(target, raw, info.get("default_branch", "main"), "eval")
        anchors = extract_anchors(model_visible_text(inp))
        repo = repos.ensure_clone(target.owner, target.repo)
        snap, _ = resolve_snapshot(repo, inp, anchors)
        assert_not_after(repo, snap.sha, inp.created_at)
        root = repos.worktree(target.owner, target.repo, snap.sha,
                              sparse=info.get("size", 0) > settings.sparse_checkout_over_kb)
        index = build_index(root)
        ledger = Ledger(target.owner, target.repo)
        ledger.register_root(snap.sha, root)
        reference_probes(index, [a for a in anchors if a.kind == "literal"], snap, ledger)
        row = {"url": url, "title": inp.title, "snapshot": snap.model_dump(),
               "literal_queries": len([a for a in anchors if a.kind == "literal"]),
               "matches": [e.model_dump() for e in ledger.items], "pipeline_version": PIPELINE_VERSION,
               "limitations": "Retrieval only; no behavioural verdict and no code execution."}
        rows.append(row)
        print(f"{target.full_name}#{target.number}: {row['literal_queries']} literals, {len(ledger.items)} cited matches", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
