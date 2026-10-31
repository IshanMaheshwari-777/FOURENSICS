"""Dataset integrity: edited/conflicting reports cannot become reference answers."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from collect import assign_splits, collect


class PublicIssues:
    def repo(self, owner, name):
        return {"private": False, "default_branch": "main", "size": 123}

    def get(self, path, params):
        label = "confirmed" if 'label:"confirmed"' in params["q"] else "invalid"
        return {"total_count": 3, "items": [
            {"number": n, "title": f"Report {n}", "body": "public body", "created_at": "2025-07-03T00:00:00Z",
             "html_url": f"https://github.com/o/r/issues/{n}",
             "labels": [{"name": label}] + ([{"name": "invalid"}] if n == 3 else [])}
            for n in ([1, 2, 3] if label == "confirmed" else [3])
        ]}

    def graphql(self, query, variables):
        return {"repository": {"issue": {"lastEditedAt": "2025-07-04" if variables["number"] == 2 else None}}}


def test_edited_and_conflicting_reports_are_excluded():
    qs, answers, _ = collect(PublicIssues(), ["o/r"], {"confirmed": "confirmed", "invalid": "invalid"}, "2025-07-01", 10)
    assert [q["id"] for q in qs] == ["o/r#1"]
    assert answers[0]["label"] == "confirmed"
    assert not any(key in qs[0] for key in ("label", "labels", "github_label", "state"))
    assert qs[0]["repo_size_kb"] == 123


def test_private_repository_is_rejected_before_search():
    gh = PublicIssues()
    gh.repo = lambda *_: {"private": True}
    with pytest.raises(ValueError, match="Public repository"):
        collect(gh, ["o/r"], {"confirmed": "confirmed"}, "2025-07-01", 10)


def test_stratified_split_is_reproducible_and_interleaved():
    qs = [{"id": f"o/r#{group}{n}", "owner": "o", "repo": "r"}
          for group in "abc" for n in range(10)]
    answers = [{"id": q["id"], "label": q["id"][4]} for q in qs]
    copy = [q.copy() for q in reversed(qs)]
    assign_splits(qs, answers, 0.4, 7)
    assign_splits(copy, answers, 0.4, 7)
    assert qs == copy
    labels = {a["id"]: a["label"] for a in answers}
    for label in "abc":
        assert sum(q["split"] == "dev" for q in qs if labels[q["id"]] == label) == 4
    assert len({labels[q["id"]] for q in qs[:3]}) == 3
