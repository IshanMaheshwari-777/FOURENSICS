"""Save real end-to-end PanelResults for reviewed public demo issues.

Sandbox is enabled only for this process when requested, never by editing .env.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.config import get_settings
from app.db import new_run_id
from app.github.client import GitHubClient
from app.github.urls import parse_target
from app.llm.factory import make_gateway
from app.pipeline.orchestrator import Deps, analyze
from app.pipeline.snapshot import RepoCache
from app.schemas import PanelResult
from storage import isolated_db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", action="append", required=True)
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--cached-only", action="store_true")
    args = parser.parse_args()
    s = get_settings().model_copy(update={"enable_sandbox": args.sandbox,
        "sandbox_allowlist": ROOT.parent / "backend" / "sandbox_allowlist.example.json"})
    db = isolated_db(s, "demo")
    s = s.model_copy(update={"llm_cache_only": args.cached_only or s.llm_cache_only})
    gw = make_gateway(s, db)
    deps = Deps(settings=s, db=db, repos=RepoCache(s.cache_dir, s.git_timeout_s), gateway=gw,
                github=GitHubClient(s.github_api, s.github_token, cache_dir=s.cache_dir / "http"))
    out = ROOT / "examples"
    out.mkdir(parents=True, exist_ok=True)
    for url in args.url:
        target = parse_target(url)
        result = PanelResult(run_id=new_run_id())
        db.new_run(f"demo:{url}:{result.run_id}", result)
        result = analyze(deps, result, target=target, mode="live")
        path = out / f"panel_{target.owner}__{target.repo}_{target.number}.json"
        path.write_text(result.model_dump_json(indent=2) + "\n")
        print(f"{url}: {result.status}: {result.summary_line}", flush=True)
        print("verification:", [(a.claim_id, a.verification, a.technical) for a in result.assessments], flush=True)


if __name__ == "__main__":
    main()
