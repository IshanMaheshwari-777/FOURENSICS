"""Recover an interrupted dev report using saved outputs and cached baselines only.

Never evaluates held-out questions or makes inference requests. The optional
current-probe replay can only revalidate an existing model assessment; it is not
a full run of the current pipeline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.config import PIPELINE_VERSION, get_settings
from app.llm.gateway import CachedGateway, LLMUnavailable, load_prompt
from app.pipeline.aggregate import eval_label
from app.pipeline.index import build_index
from app.pipeline.ledger import Ledger
from app.pipeline.probes import claim_probes
from app.pipeline.validator import validate
from app.schemas import PanelResult
from metrics import score, to_markdown
from run_eval import BaselineOut
from storage import isolated_db


def signature(e):
    return (e.kind, e.sha, e.path, e.start, e.end, e.result)


def replay(saved, settings):
    parent = settings.cache_dir / "wt" / saved.target.owner / saved.target.repo
    def checkout(sha):
        plain = parent / sha
        return plain if plain.is_dir() else parent / (sha + "-sparse")
    root = checkout(saved.snapshot.sha)
    if not root.is_dir():
        raise ValueError(f"Missing frozen checkout: {saved.target.url}")
    index = build_index(root)
    fresh = Ledger(saved.target.owner, saved.target.repo)
    fresh.register_root(saved.snapshot.sha, root)
    for c in saved.claims:
        claim_probes(index, c, saved.snapshot, fresh)
    tiers = {signature(e): e.tier for e in fresh.items}
    ledger = Ledger(saved.target.owner, saved.target.repo)
    ledger.items = [e.model_copy(deep=True) for e in saved.evidence]
    for sha in {e.sha for e in saved.evidence}:
        if checkout(sha).is_dir():
            ledger.register_root(sha, checkout(sha))
    for e in ledger.items:
        if signature(e) in tiers:
            # This recovery report only downgrades; new evidence requires a full fresh assessment.
            e.tier = max(e.tier, tiers[signature(e)])
    result = saved.model_copy(deep=True)
    result.assessments = validate(saved.assessments, saved.claims, ledger, saved.snapshot, {})
    return result


def main():
    s = get_settings()
    run_dir = ROOT / "out" / "dev_v16"
    data = ROOT / "data" / "public_20261101"
    manifest = json.loads((run_dir / "manifest_dev.json").read_text())
    questions = {q["id"]: q for q in map(json.loads, (data / "questions.jsonl").read_text().splitlines()) if q["split"] == "dev"}
    # Only selected dev answers are used; no held-out case is analysed.
    answers = {a["id"]: a["label"] for a in map(json.loads, (data / "answers.jsonl").read_text().splitlines())
               if a["id"] in manifest["ids"]}
    db = isolated_db(s, "evaluation")
    gw = CachedGateway(s.llm_base_url, s.llm_model, db=db)
    rows, details = [], []
    for qid in manifest["ids"]:
        q = questions[qid]
        path = run_dir / "runs" / (qid.replace("/", "__").replace("#", "_") + ".json")
        saved = PanelResult.model_validate_json(path.read_text())
        checked = replay(saved, s)
        base = None
        try:
            base = gw.complete_json("baseline", "baseline_v1", load_prompt("baseline_v1"),
                f"<untrusted>\nTitle: {q['title']}\n\n{q['body']}\n</untrusted>", BaselineOut).label
        except LLMUnavailable:
            pass
        row = {"id": qid, "repo": f"{q['owner']}/{q['repo']}", "label": answers[qid],
               "strict": eval_label(saved), "forced": eval_label(saved, forced=True), "baseline_partial": base,
               "revalidated_cached": eval_label(checked), "status": saved.status,
               "original_pipeline": saved.pipeline_version, "probe_replay_pipeline": PIPELINE_VERSION}
        rows.append(row)
        primary = next((c for c in saved.claims if c.is_primary), None)
        assessment = next((a for a in saved.assessments if primary and a.claim_id == primary.id), None)
        details.append({"id": qid, "url": q["url"], "title": q["title"], "primary": primary.model_dump() if primary else None,
                        "assessment": assessment.model_dump() if assessment else None, "predictions": row})
    modes = ["strict", "forced", "baseline_partial", "revalidated_cached"]
    scores = {m: score(rows, m) for m in modes}
    report = to_markdown("FOURENSICS development exploration — saved real outputs", scores, {})
    report += ("\n## Interpretation and limits\n\n"
               "- Nine of thirteen dev cases were analysed; the held-out split was not run.\n"
               "- Live inference reached its 200,000-token daily quota during the baseline. Missing baseline answers are unavailable, not wrong model predictions.\n"
               "- This exploratory batch ran while code/prompts were being developed; its original pipeline field is 16. It is not a frozen evaluation of the final implementation.\n"
               "- `revalidated_cached` replays current source probes and citation validation on old model verdicts. It makes no model calls and is not a full current-pipeline accuracy result.\n"
               "- The table-selection false confirmation used unrelated chart code. Preserving similarity provenance removes its directional support in the replay.\n"
               "- A confirmed documentation request is mapped to invalid by the original issue-kind mapping; this exposes a mismatch between closure-label semantics and defect triage.\n"
               "- Repository and label are confounded; the confirmed class is tiny. No broad accuracy or competitor-superiority claim is justified.\n"
               "- Baseline coverage is incomplete, so these rows do not establish that either approach is better.\n"
               "\n## Case audit\n\n| Case | Reference proxy | Strict | Forced | Cached baseline | Revalidated old verdict |\n"
               "| --- | --- | --- | --- | --- | --- |\n")
    for d in details:
        p = d["predictions"]
        report += f"| [{d['id']}]({d['url']}) | {p['label']} | {p['strict'] or 'abstain'} | {p['forced'] or 'abstain'} | {p['baseline_partial'] or 'unavailable'} | {p['revalidated_cached'] or 'abstain'} |\n"
    for d in details:
        a = d["assessment"] or {}
        report += f"\n### {d['id']}\n\n{d['title']}\n\n"
        report += f"Primary finding: {a.get('technical', 'unavailable')}. {a.get('explanation', '')}\n"
        if a.get("downgraded_reason"):
            report += f"\nValidator: {a['downgraded_reason']}.\n"
    out = ROOT / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report_dev.md").write_text(report)
    (out / "predictions_dev.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "dev_case_audit.json").write_text(json.dumps(details, indent=2) + "\n")
    (out / "report_test.md").write_text("# Held-out evaluation — not run\n\n"
        "The 20 held-out questions remain unrun. The configured model reached its daily quota, and the user chose cache-only completion. "
        "No test accuracy is reported, no synthetic predictions substitute for model output, and no tuning used held-out outcomes. "
        "A full frozen dev rerun and the single held-out run remain required when inference capacity returns.\n")
    examples = ROOT / "examples"
    (examples / "report_dev.md").write_text(report)
    (examples / "report_test.md").write_text((out / "report_test.md").read_text())
    print(json.dumps({m: {k: v for k,v in metrics.items() if k in ('n','coverage','accuracy_answered','accuracy_all')}
                      for m,metrics in scores.items()}, indent=2))


if __name__ == "__main__":
    main()
