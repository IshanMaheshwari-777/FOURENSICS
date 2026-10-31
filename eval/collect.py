"""Collects closed, labelled issues for the evaluation set.

Writes two separate files so labels never reach the pipeline:
  eval/data/questions.jsonl  what the pipeline may see (title, original body, dates)
  eval/data/answers.jsonl    the reference label only

Issues whose body was edited after creation are skipped (GraphQL lastEditedAt),
because the current body may contain hindsight ("EDIT: my mistake").

Usage (needs GITHUB_TOKEN):
  python eval/collect.py --repo owner/name --label invalid=invalid --label "cannot reproduce=cannot_reproduce" \
      --label confirmed=confirmed --since 2025-01-01 --per-label 15
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.github.client import GitHubClient  # noqa: E402
from app.config import get_settings  # noqa: E402

EDIT_QUERY = """query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) { issue(number:$number) { lastEditedAt createdAt } }
}"""


def assign_splits(questions: list[dict], answers: list[dict], fraction: float, seed: int) -> None:
    """Stratify by repository and reference label before sealing the question file."""
    labels = {a["id"]: a["label"] for a in answers}
    groups: dict[tuple, list[dict]] = {}
    for q in questions:
        groups.setdefault((q["owner"], q["repo"], labels[q["id"]]), []).append(q)
    rng = random.Random(seed)
    for key in sorted(groups):
        group = sorted(groups[key], key=lambda q: q["id"])
        rng.shuffle(group)
        groups[key] = group
        count = round(len(group) * fraction)
        if len(group) >= 2:
            count = max(1, min(len(group) - 1, count))
        for i, q in enumerate(group):
            q["split"] = "dev" if i < count else "test"
    # Interleave strata so a bounded run is not dominated by the first repository/label.
    ordered = []
    for i in range(max((len(g) for g in groups.values()), default=0)):
        for key in sorted(groups):
            if i < len(groups[key]):
                ordered.append(groups[key][i])
    questions[:] = ordered


def collect(gh, repos: list[str], label_map: dict[str, str], since: str, per_label: int) -> tuple[list, list, list]:
    questions, answers, audit = [], [], []
    seen: dict[str, str] = {}
    conflicts: set[str] = set()
    for repo in repos:
        owner, name = repo.split("/", 1)
        info = gh.repo(owner, name)
        if info.get("private") is not False:
            raise ValueError(f"Public repository required: {repo}")
        default_branch = info.get("default_branch", "main")
        for gh_label, eval_label in label_map.items():
            taken = edited = 0
            total = 0
            for page in range(1, 11):  # GitHub Search exposes at most 1,000 results.
                q = f'repo:{repo} is:public is:issue is:closed label:"{gh_label}" created:>={since}'
                result = gh.get("/search/issues", {"q": q, "sort": "created", "order": "asc",
                                                   "per_page": 100, "page": page})
                total = result.get("total_count", 0)
                if result.get("incomplete_results"):
                    raise ValueError(f"Incomplete GitHub search for {repo} {gh_label!r}; retry collection")
                items = result.get("items", [])
                for it in items:
                    qid = f"{repo}#{it['number']}"
                    if qid in seen:
                        if seen[qid] != eval_label:
                            conflicts.add(qid)
                        continue
                    # Conflicting mapped labels on one issue are not a usable reference answer.
                    refs = {label_map[x["name"]] for x in it.get("labels", []) if x["name"] in label_map}
                    if len(refs) > 1:
                        conflicts.add(qid)
                        continue
                    meta = gh.graphql(EDIT_QUERY, {"owner": owner, "name": name, "number": it["number"]})
                    issue = meta["repository"]["issue"]
                    if issue is None or issue.get("lastEditedAt"):
                        edited += 1
                        continue
                    seen[qid] = eval_label
                    questions.append({
                        "id": qid, "url": it["html_url"], "owner": owner, "repo": name, "number": it["number"],
                        "title": it["title"], "body": it.get("body") or "", "created_at": it["created_at"],
                        "default_branch": default_branch, "repo_size_kb": int(info.get("size") or 0),
                    })
                    answers.append({"id": qid, "label": eval_label, "github_label": gh_label,
                                    "label_reliability": "unreviewed", "notes": "Repository label is a proxy, not independently established truth."})
                    taken += 1
                    if taken >= per_label:
                        break
                if taken >= per_label or len(items) < 100:
                    break
            audit.append({"repo": repo, "github_label": gh_label, "eval_label": eval_label,
                          "search_total": total, "selected": taken, "edited_or_unavailable_skipped": edited})
            print(f"{repo} {gh_label!r}: {taken} selected ({edited} edited/unavailable skipped)", file=sys.stderr)
    questions = [q for q in questions if q["id"] not in conflicts]
    answers = [a for a in answers if a["id"] not in conflicts]
    if conflicts:
        audit.append({"conflicting_ids_excluded": sorted(conflicts)})
    return questions, answers, audit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append", required=True, help="owner/name (repeatable)")
    ap.add_argument("--label", action="append", required=True,
                    help="'github label=eval label' where eval label is confirmed|invalid|cannot_reproduce")
    ap.add_argument("--since", default="2025-01-01")
    ap.add_argument("--per-label", type=int, default=15)
    ap.add_argument("--dev-fraction", type=float, default=0.4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "data"))
    args = ap.parse_args()
    if not 0 < args.dev_fraction < 1 or args.per_label < 1:
        ap.error("--dev-fraction must be between 0 and 1; --per-label must be positive")
    s = get_settings()
    if not s.github_token:
        sys.exit("Set GITHUB_TOKEN (read-only is enough).")
    gh = GitHubClient(s.github_api, token=s.github_token, cache_dir=s.cache_dir / "http")
    label_map = dict(item.split("=", 1) for item in args.label)
    if not set(label_map.values()) <= {"confirmed", "invalid", "cannot_reproduce"}:
        ap.error("Evaluation labels must be confirmed, invalid, or cannot_reproduce")
    out = Path(args.out)
    if (out / "questions.jsonl").exists() or (out / "answers.jsonl").exists():
        ap.error("Dataset already exists; choose a new --out directory to preserve the frozen split")
    questions, answers, audit = collect(gh, args.repo, label_map, args.since, args.per_label)
    assign_splits(questions, answers, args.dev_fraction, args.seed)
    out.mkdir(parents=True, exist_ok=True)
    (out / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in questions))
    (out / "answers.jsonl").write_text("".join(json.dumps(a) + "\n" for a in answers))
    (out / "collection.json").write_text(json.dumps({"since": args.since, "seed": args.seed,
        "per_label": args.per_label, "dev_fraction": args.dev_fraction, "audit": audit}, indent=2) + "\n")
    print(f"Wrote {len(questions)} questions to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
