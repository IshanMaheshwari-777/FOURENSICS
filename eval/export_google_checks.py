"""Archive live HTTP results and revalidate their saved evidence without inference.

The original pipeline version is preserved. The replay does not rerun retrieval
or pretend to be a new model assessment or an accuracy benchmark.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.config import PIPELINE_VERSION, get_settings
from app.pipeline.aggregate import apply_strength
from app.pipeline.ledger import Ledger
from app.pipeline.validator import validate
from app.schemas import PanelResult


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "eval/examples/google_api_checks.json")
    args = parser.parse_args()
    settings = get_settings()
    reports = [PanelResult.model_validate(r) for p in args.inputs for r in json.loads(p.read_text())["reports"]]
    checks = []
    for report in reports:
        ledger = Ledger(report.target.owner, report.target.repo)
        ledger.items = report.evidence
        for sha in {e.sha for e in report.evidence}:
            path = settings.cache_dir / "wt" / report.target.owner / report.target.repo / sha
            if not path.is_dir():
                path = path.with_name(path.name + "-sparse")
            if path.is_dir():
                ledger.register_root(sha, path)
        # These API checks deliberately used static analysis only.
        if any(a.verification != "not_attempted" for a in report.assessments):
            raise ValueError("Exporter requires explicit reproduction provenance for executed examples")
        after = validate(report.assessments, report.claims, ledger, report.snapshot, {})
        apply_strength(after, ledger, report.snapshot)
        checks.append({"url": report.target.url, "live_api_result": report.model_dump(mode="json"),
            "validator_replay": {"pipeline_version": PIPELINE_VERSION,
                "inference_calls": 0, "assessments": [a.model_dump(mode="json") for a in after],
                "changed_verdicts": [{"claim_id": a.claim_id,
                    "before": next(b.technical for b in report.assessments if b.claim_id == a.claim_id),
                    "after": a.technical, "reason": a.downgraded_reason} for a in after
                    if any(b.claim_id == a.claim_id and b.technical != a.technical for b in report.assessments)]}})
    payload = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Live Google/Gemma calls through the HTTP assessment API, followed by offline source-hash and verdict validation.",
        "limitations": ["Original API pipeline versions are preserved.",
            "Validator replay is not a fresh model or retrieval run.",
            "No builds, race detectors or reporter snippets were run for these URLs.",
            "These are integration checks, not a labeled accuracy benchmark."], "checks": checks}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n")
    for check in checks:
        print(check["url"], check["live_api_result"]["status"], check["validator_replay"]["changed_verdicts"])


if __name__ == "__main__":
    main()
