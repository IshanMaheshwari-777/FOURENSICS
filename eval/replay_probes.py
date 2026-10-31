"""Replay source probes and validation on real saved runs, without model calls.

This checks evidence provenance, not the complete new pipeline's accuracy. Old
model verdicts are revalidated with updated probe tiers; no new verdict is invented.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.config import PIPELINE_VERSION, get_settings
from app.pipeline.index import build_index
from app.pipeline.ledger import Ledger
from app.pipeline.probes import claim_probes
from app.pipeline.validator import validate
from app.schemas import PanelResult

URLS = [
    "https://github.com/marimo-team/marimo/issues/5743",
    "https://github.com/pallets/flask/issues/6177",
    "https://github.com/expressjs/express/pull/7574",
    "https://github.com/spf13/cobra/pull/2557",
]


def key(e):
    return (e.kind, e.sha, e.path, e.start, e.end, e.result)


def main():
    s = get_settings()
    results = []
    with sqlite3.connect(f"file:{s.db_path}?mode=ro", uri=True) as db:
        for url in URLS:
            row = db.execute("SELECT result_json FROM runs WHERE status='complete' AND "
                             "json_extract(result_json,'$.target.url')=? ORDER BY updated_at DESC LIMIT 1", (url,)).fetchone()
            if row is None:
                raise RuntimeError(f"No saved public run for {url}")
            saved = PanelResult.model_validate_json(row[0])
            root = s.cache_dir / "wt" / saved.target.owner / saved.target.repo / saved.snapshot.sha
            if not root.is_dir():
                root = root.with_name(root.name + "-sparse")
            if not root.is_dir():
                raise RuntimeError(f"Snapshot checkout missing for {url}")
            index = build_index(root)
            fresh = Ledger(saved.target.owner, saved.target.repo)
            fresh.register_root(saved.snapshot.sha, root)
            for claim in saved.claims:
                claim_probes(index, claim, saved.snapshot, fresh)
            tiers = {key(e): e.tier for e in fresh.items}
            validation = Ledger(saved.target.owner, saved.target.repo)
            validation.items = [e.model_copy(deep=True) for e in saved.evidence]
            for sha in {e.sha for e in saved.evidence}:
                path = s.cache_dir / "wt" / saved.target.owner / saved.target.repo / sha
                if not path.is_dir():
                    path = path.with_name(path.name + "-sparse")
                if path.is_dir():
                    validation.register_root(sha, path)
            changed = []
            for item in validation.items:
                tier = tiers.get(key(item))
                if tier and tier != item.tier:
                    changed.append({"id": item.id, "kind": item.kind, "path": item.path,
                                    "before": item.tier, "after": tier})
                    item.tier = tier
            after = validate(saved.assessments, saved.claims, validation, saved.snapshot, {})
            results.append({"url": url, "original_pipeline": saved.pipeline_version,
                            "probe_pipeline": PIPELINE_VERSION, "tier_changes": changed,
                            "assessments": [{"claim_id": a.claim_id,
                                             "original": next(x.technical for x in saved.assessments if x.claim_id == a.claim_id),
                                             "revalidated": a.technical, "reason": a.downgraded_reason} for a in after],
                            "limitation": "Probe/validator replay only; the model was not rerun and this is not a new full assessment."})
            print(url, "tier changes", len(changed), "verdicts", [(a["original"], a["revalidated"]) for a in results[-1]["assessments"]], flush=True)
    out = ROOT / "examples" / "provenance_checks.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
