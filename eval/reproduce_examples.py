"""Execute only the two reviewed, allowlisted public snippets; save real observations.

Does not enable sandbox execution in the user's running server or call the model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.config import PIPELINE_VERSION, get_settings
from app.github.client import GitHubClient, public_repo
from app.github.urls import parse_target
from app.pipeline.extract_regex import extract_anchors
from app.pipeline.sandbox import load_allowlist, reproduce
from app.schemas import Claim


def main():
    settings = get_settings()
    gh = GitHubClient(settings.github_api, settings.github_token)
    entries = load_allowlist(ROOT.parent / "backend" / "sandbox_allowlist.example.json")
    rows = []
    for entry in entries:
        target = parse_target(entry["url"])
        public_repo(gh, target.owner, target.repo)
        report = gh.issue(target.owner, target.repo, target.number)
        anchors = extract_anchors(report.get("body", ""))
        claim = Claim(id="C1", text=report["title"], source_span=report["title"],
                      type="behavior", is_primary=True)
        result = reproduce(entry, claim, anchors)
        rows.append({"url": entry["url"], "title": report["title"], "entry": entry,
                     "status": result.status, "detail": result.detail,
                     "observations": json.loads(result.raw) if result.raw.startswith("{") else result.raw,
                     "pipeline_version": PIPELINE_VERSION,
                     "limitations": "Reproducing a reported exception does not establish that it is an unintended defect."})
        print(f"{entry['url']}: {result.status}: {result.detail}", flush=True)
    out = ROOT / "examples" / "reproduction.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    main()
