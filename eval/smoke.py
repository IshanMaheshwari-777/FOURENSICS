"""Robustness smoke test on real, varied issues and PRs (not an accuracy benchmark).

  python eval/smoke.py --sample          # pick recent issues/PRs from varied repos into eval/data/smoke_urls.txt
  python eval/smoke.py                   # run every URL in that file through the live pipeline

For each URL it records whether the run finished, how long it took, which stages were degraded
or failed, and the verdicts, then writes eval/out/smoke_report.md for a person to review.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import Database, new_run_id  # noqa: E402
from app.github.client import GitHubClient  # noqa: E402
from app.github.urls import parse_target  # noqa: E402
from app.llm.factory import make_gateway  # noqa: E402
from app.pipeline.orchestrator import Deps, analyze  # noqa: E402
from app.pipeline.snapshot import RepoCache  # noqa: E402
from app.schemas import PanelResult  # noqa: E402

URLS = ROOT / "data" / "smoke_urls.txt"

# Varied on purpose: languages, sizes, monorepos, web frameworks and CLIs.
SAMPLE_REPOS = {
    "psf/requests": ("python", "issue,pr"),
    "pallets/flask": ("python", "issue"),
    "fastapi/fastapi": ("python", "issue,pr"),
    "django/django": ("python-large", "pr"),
    "pydantic/pydantic": ("python", "issue"),
    "expressjs/express": ("javascript", "issue,pr"),
    "axios/axios": ("javascript", "issue"),
    "vitejs/vite": ("typescript-monorepo", "issue,pr"),
    "spf13/cobra": ("go", "issue,pr"),
    "gin-gonic/gin": ("go", "issue"),
    "BurntSushi/ripgrep": ("rust", "issue"),
    "clap-rs/clap": ("rust", "pr"),
    "google/gson": ("java", "issue"),
}


def sample(gh: GitHubClient, since: str) -> list[str]:
    urls = []
    for repo, (_, kinds) in SAMPLE_REPOS.items():
        for kind in kinds.split(","):
            q = f"repo:{repo} is:{kind} created:>={since}"
            items = gh.get("/search/issues", {"q": q, "sort": "created", "order": "desc", "per_page": 5}).get("items", [])
            # Skip bot-opened items (dependency bumps); keep the newest human report.
            human = [i for i in items if not (i.get("user") or {}).get("login", "").endswith("[bot]")]
            if human:
                urls.append(human[0]["html_url"])
                print(f"{repo} {kind}: {human[0]['html_url']}", file=sys.stderr)
            time.sleep(2.5)  # search API allows 30 requests/minute
    return urls


def summarize(url: str, r: PanelResult, seconds: float) -> dict:
    by = {a.claim_id: a for a in r.assessments}
    return {
        "url": url, "status": r.status, "seconds": round(seconds, 1), "error": r.error,
        "kind": r.target.kind if r.target else None, "report_kind": r.report_kind,
        "snapshot": f"{r.snapshot.method}/{r.snapshot.reliability}", "summary": r.summary_line,
        "tone": r.summary_tone, "claims": len(r.claims), "evidence": len(r.evidence),
        "verdicts": [f"{c.id}{'*' if c.is_primary else ''}:{by[c.id].technical}/{by[c.id].decided_by}"
                     for c in r.claims if c.id in by],
        "problem_stages": [f"{s.stage}:{s.status}({(s.reason or '')[:80]})" for s in r.stages
                           if s.status in ("failed", "degraded") or (s.status == "skipped" and s.stage != "reproduction")],
        "notes": r.notes[:6], "run_id": r.run_id,
    }


def report(rows: list[dict]) -> str:
    n = len(rows)
    status = Counter(r["status"] for r in rows)
    secs = sorted(r["seconds"] for r in rows)
    lines = [
        "# Smoke test report", "",
        "Robustness on real, varied issues and PRs. Not an accuracy measurement.", "",
        f"- Runs: {n}; status: " + ", ".join(f"{k} {v}" for k, v in status.most_common()),
        f"- Time per run: median {secs[n // 2] if n else 0}s, max {secs[-1] if n else 0}s",
        f"- Runs with a degraded/failed stage: {sum(1 for r in rows if r['problem_stages'])}",
        "", "| URL | Status | s | Kind | Version | Claims | Evidence | Verdicts | Summary |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        short = r["url"].replace("https://github.com/", "")
        lines.append(f"| {short} | {r['status']} | {r['seconds']} | {r['report_kind'] or r['kind']} | {r['snapshot']} | "
                     f"{r['claims']} | {r['evidence']} | {' '.join(r['verdicts'])} | {r['summary']} |")
    lines += ["", "## Problems and notes", ""]
    for r in rows:
        if r["problem_stages"] or r["error"] or r["notes"]:
            lines.append(f"- **{r['url'].replace('https://github.com/', '')}**")
            for p in r["problem_stages"]:
                lines.append(f"  - stage {p}")
            if r["error"]:
                lines.append(f"  - error: {r['error']}")
            for note in r["notes"]:
                lines.append(f"  - note: {note[:160]}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--since", default="2025-06-01")
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--urls", default=str(URLS), help="file with one URL per line")
    args = ap.parse_args()

    s = get_settings()
    gh = GitHubClient(s.github_api, s.github_token, cache_dir=s.cache_dir / "http")
    if args.sample:
        URLS.parent.mkdir(parents=True, exist_ok=True)
        URLS.write_text("\n".join(sample(gh, args.since)) + "\n")
        print(f"Wrote {URLS}", file=sys.stderr)
        return

    urls = [u.strip() for u in Path(args.urls).read_text().splitlines() if u.strip() and not u.startswith("#")]
    db = Database(s.db_path)
    gw = make_gateway(s, db)
    deps = Deps(settings=s, db=db, repos=RepoCache(s.cache_dir, s.git_timeout_s), gateway=gw, github=gh)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, url in enumerate(urls, 1):
        t0 = time.monotonic()
        result = PanelResult(run_id=new_run_id())
        try:
            target = parse_target(url)
            db.new_run(f"smoke:{url}:{result.run_id}", result)
            result = analyze(deps, result, target=target, mode="live")
        except Exception as e:  # the orchestrator should never raise; record it if it does
            result.status, result.error = "error", f"{e.__class__.__name__}: {e} | {traceback.format_exc(limit=2)[-300:]}"
        row = summarize(url, result, time.monotonic() - t0)
        rows.append(row)
        print(f"[{i}/{len(urls)}] {row['status']:8} {row['seconds']:6}s {url} :: {row['summary']}", flush=True)
        (out / "smoke_results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        (out / "smoke_report.md").write_text(report(rows))
    print(f"\nReport: {out / 'smoke_report.md'}")


if __name__ == "__main__":
    main()
