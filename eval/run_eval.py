"""Runs the pipeline in evaluation mode on frozen questions and scores it.

The pipeline only ever sees questions.jsonl. answers.jsonl is read here, after all
predictions are made, and only for scoring.

Usage:
  python eval/run_eval.py --split dev            # tune on this
  python eval/run_eval.py --split test           # run once, at the end
  python eval/run_eval.py --split dev --baseline # also score GPT-OSS on issue text alone
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel  # noqa: E402

from app.config import PIPELINE_VERSION, Settings, get_settings  # noqa: E402
from app.db import Database, new_run_id  # noqa: E402
from app.llm.gateway import Gateway, load_prompt  # noqa: E402
from app.llm.factory import make_gateway  # noqa: E402
from app.pipeline.aggregate import eval_label  # noqa: E402
from app.pipeline.firewall import body_hash  # noqa: E402
from app.pipeline.orchestrator import Deps, analyze  # noqa: E402
from app.pipeline.snapshot import RepoCache  # noqa: E402
from app.schemas import AnalysisInput, PanelResult, Target  # noqa: E402
from metrics import score, to_markdown  # noqa: E402
from storage import isolated_db  # noqa: E402


class BaselineOut(BaseModel):
    label: Literal["confirmed", "invalid", "cannot_reproduce"]
    reason: str = ""


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def to_input(q: dict) -> AnalysisInput:
    target = Target(owner=q["owner"], repo=q["repo"], kind="issue", number=int(q["number"]), url=q["url"])
    return AnalysisInput(
        target=target, title=q["title"], body=q["body"], body_hash=body_hash(q["title"], q["body"]),
        created_at=datetime.fromisoformat(q["created_at"].replace("Z", "+00:00")),
        default_branch=q.get("default_branch", "main"), mode="eval",
        repo_size_kb=q.get("repo_size_kb", 0),
    )


def run_pipeline(deps: Deps, questions: list[dict], out_dir: Path) -> list[dict]:
    preds = []
    for i, q in enumerate(questions, 1):
        t0 = time.monotonic()
        result = PanelResult(run_id=new_run_id())
        deps.db.new_run(f"eval:{q['id']}:{result.run_id}", result)
        result = analyze(deps, result, prepared=to_input(q), mode="eval")
        (out_dir / "runs").mkdir(parents=True, exist_ok=True)
        (out_dir / "runs" / f"{q['id'].replace('/', '__').replace('#', '_')}.json").write_text(result.model_dump_json(indent=1))
        preds.append({"id": q["id"], "repo": f"{q['owner']}/{q['repo']}", "status": result.status,
                      "strict": eval_label(result), "forced": eval_label(result, forced=True),
                      "snapshot": result.snapshot.reliability, "seconds": round(time.monotonic() - t0, 1)})
        print(f"[{i}/{len(questions)}] {q['id']}: {preds[-1]['strict'] or 'abstain'} "
              f"(forced {preds[-1]['forced']}, {result.status}, {preds[-1]['seconds']}s)", file=sys.stderr)
    return preds


def run_baseline(gateway: Gateway, questions: list[dict]) -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {}
    for q in questions:
        try:
            r = gateway.complete_json("baseline", "baseline_v1", load_prompt("baseline_v1"),
                                      f"<untrusted>\nTitle: {q['title']}\n\n{q['body']}\n</untrusted>", BaselineOut)
            out[q["id"]] = r.label
        except Exception as e:
            print(f"baseline failed for {q['id']}: {e}", file=sys.stderr)
            out[q["id"]] = None
    return out


def main(argv: Optional[list[str]] = None, settings: Optional[Settings] = None, gateway=None, github=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "out"))
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--cached-only", action="store_true", help="Never contact the model; cache misses remain visibly unverified")
    ap.add_argument("--limit", type=int, help="Run the first N frozen questions (collection order interleaves strata)")
    args = ap.parse_args(argv)

    data, out_dir = Path(args.data), Path(args.out)
    questions_path = data / "questions.jsonl"
    questions = [q for q in load_jsonl(questions_path) if args.split == "all" or q.get("split") == args.split]
    available = len(questions)
    if args.limit is not None:
        if args.limit < 1:
            ap.error("--limit must be positive")
        questions = questions[:args.limit]
    if not questions:
        sys.exit(f"No questions for split {args.split!r} in {data}")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Refuse accidental repeated test evaluation in the same output directory, including interrupted runs.
    manifest_path = out_dir / f"manifest_{args.split}.json"
    if args.split in ("test", "all") and manifest_path.exists():
        ap.error("This output directory already contains a held-out run; preserve it and do not retune on its answers")
    manifest = {"pipeline_version": PIPELINE_VERSION, "split": args.split, "available": available,
                "selected": len(questions), "ids": [q["id"] for q in questions],
                "questions_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
                "started_at": datetime.now().astimezone().isoformat(), "baseline_requested": args.baseline}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    s = settings or get_settings()
    # The live server marks its own unfinished runs interrupted on auto-reload. Evaluation
    # must use a separate run store so development reloads cannot corrupt its progress.
    db = isolated_db(s, "evaluation")
    manifest["settings"] = {"prompt_budget_tokens": s.llm_prompt_budget_tokens,
                            "max_output_tokens": s.llm_max_tokens, "tokens_per_minute": s.llm_tokens_per_minute,
                            "reasoning_effort": s.llm_reasoning_effort, "sandbox_enabled": s.enable_sandbox}
    s = s.model_copy(update={"llm_cache_only": args.cached_only or s.llm_cache_only})
    gw = gateway if gateway is not None else make_gateway(s, db)
    if gw is not None:
        manifest["settings"].update(model=gw.model, provider=getattr(gw, "provider", "openai-compatible"),
            prompt_budget_tokens=gw.prompt_budget_tokens, max_output_tokens=getattr(gw, "max_tokens", None),
            tokens_per_minute=getattr(gw, "tokens_per_minute", None))
    deps = Deps(settings=s, db=db, repos=RepoCache(s.cache_dir, s.git_timeout_s, s.clone_url_overrides),
                gateway=gw, github=github)

    preds = run_pipeline(deps, questions, out_dir)
    if args.baseline and gw is not None:
        base = run_baseline(gw, questions)
        for p in preds:
            p["baseline"] = base.get(p["id"])

    # Labels are joined only now, after every prediction is final.
    answers = {a["id"]: a for a in load_jsonl(data / "answers.jsonl")}
    if any(p["id"] not in answers for p in preds):
        raise ValueError("Missing reference answers; refusing to silently omit predictions")
    if len(answers) != len(load_jsonl(data / "answers.jsonl")):
        raise ValueError("Duplicate reference IDs")
    rows = [{**p, "label": answers[p["id"]]["label"]} for p in preds]
    modes = ["strict", "forced"] + (["baseline"] if args.baseline and gw is not None else [])
    results = {m: score(rows, m) for m in modes}
    per_repo = {}
    for repo in sorted({r["repo"] for r in rows}):
        rr = [r for r in rows if r["repo"] == repo]
        per_repo[repo] = {m: score(rr, m) for m in modes}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"predictions_{args.split}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = to_markdown(f"Evaluation report ({args.split} split)", results, per_repo)
    report += (f"\n## Run provenance\n\n- Pipeline: {PIPELINE_VERSION}\n- Reasoning model: {gw.model if gw else 'unavailable'}"
               f"\n- Evaluated {len(questions)} of {available} available {args.split} questions."
               f"\n- Frozen questions SHA-256: `{manifest['questions_sha256']}`"
               "\n- Reference labels are repository labels; they have not been independently adjudicated."
               "\n- Forced choice uses model leans and fallback heuristics; it is not a verified verdict."
               "\n- Cannot reproduce and inability to verify are distinct; strict abstentions remain abstentions.\n")
    manifest["model"] = gw.model if gw else None
    manifest["cache_only"] = bool(getattr(gw, "cache_only", False))
    manifest["finished_at"] = datetime.now().astimezone().isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    (out_dir / f"report_{args.split}.md").write_text(report)
    print(report)
    return results


if __name__ == "__main__":
    main()
