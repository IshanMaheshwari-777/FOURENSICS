"""Metrics for the evaluation report. Abstentions are counted, never hidden."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Optional

LABELS = ["confirmed", "invalid", "cannot_reproduce"]


def score(rows: list[dict], key: str) -> dict:
    """rows: [{"id", "repo", "label", <key>: predicted label or None}]"""
    n = len(rows)
    answered = [r for r in rows if r.get(key)]
    correct = [r for r in answered if r[key] == r["label"]]
    per_class = {}
    f1s = []
    for lab in LABELS:
        tp = sum(1 for r in answered if r[key] == lab and r["label"] == lab)
        fp = sum(1 for r in answered if r[key] == lab and r["label"] != lab)
        fn = sum(1 for r in rows if r["label"] == lab and r.get(key) != lab)
        p = tp / (tp + fp) if tp + fp else None
        rc = tp / (tp + fn) if tp + fn else None
        f1 = 2 * p * rc / (p + rc) if p and rc else 0.0
        if any(r["label"] == lab for r in rows):
            f1s.append(f1)
        per_class[lab] = {"precision": p, "recall": rc, "f1": f1, "support": sum(1 for r in rows if r["label"] == lab)}
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        confusion[r["label"]][r.get(key) or "abstain"] += 1
    return {
        "n": n,
        "coverage": len(answered) / n if n else 0.0,
        "accuracy_answered": len(correct) / len(answered) if answered else None,
        "accuracy_all": len(correct) / n if n else 0.0,
        "macro_f1": sum(f1s) / len(f1s) if f1s else 0.0,
        "per_class": per_class,
        "confusion": {k: dict(v) for k, v in confusion.items()},
    }


def pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{100 * x:.0f}%"


def to_markdown(title: str, results: dict[str, dict], per_repo: dict[str, dict[str, dict]]) -> str:
    lines = [f"# {title}", "",
             "Small sample: treat these numbers as indicative, not as a benchmark.", "",
             "| Mode | n | Coverage | Accuracy (answered) | Accuracy (all) | Macro-F1 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for mode, m in results.items():
        lines.append(f"| {mode} | {m['n']} | {pct(m['coverage'])} | {pct(m['accuracy_answered'])} | "
                     f"{pct(m['accuracy_all'])} | {m['macro_f1']:.2f} |")
    for mode, m in results.items():
        lines += ["", f"## {mode}: confusion matrix (rows = reference label)", "",
                  "| reference \\ predicted | " + " | ".join(LABELS + ["abstain"]) + " |",
                  "| --- |" + " --- |" * (len(LABELS) + 1)]
        for lab in LABELS:
            row = m["confusion"].get(lab, {})
            lines.append(f"| {lab} | " + " | ".join(str(row.get(p, 0)) for p in LABELS + ["abstain"]) + " |")
        lines += ["", "| class | precision | recall | support |", "| --- | --- | --- | --- |"]
        for lab, c in m["per_class"].items():
            lines.append(f"| {lab} | {pct(c['precision'])} | {pct(c['recall'])} | {c['support']} |")
    if per_repo:
        lines += ["", "## Per repository (strict mode)", "", "| Repository | n | Coverage | Accuracy (answered) |",
                  "| --- | --- | --- | --- |"]
        for repo, modes in sorted(per_repo.items()):
            m = modes["strict"]
            lines.append(f"| {repo} | {m['n']} | {pct(m['coverage'])} | {pct(m['accuracy_answered'])} |")
    return "\n".join(lines) + "\n"
