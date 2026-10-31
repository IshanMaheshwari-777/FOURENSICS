from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))

from build_fixture_repo import build  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.llm.gateway import FakeGateway  # noqa: E402
from app.pipeline.orchestrator import Deps  # noqa: E402
from app.pipeline.snapshot import RepoCache  # noqa: E402

OWNER, REPO = "acme", "csvtool"

BUG_BODY = """Using csvtool version 2.3.1, `parse_file()` crashes when the CSV header has an empty column.

```python
from csvtool.parser import parse_file
parse_file("a,,c\\n1,2,3")
```

```
Traceback (most recent call last):
  File "repro.py", line 2, in <module>
  File "/usr/lib/python3.11/site-packages/csvtool/parser.py", line 20, in parse_file
    row[name] = cells[index[name]]
KeyError: 'col'
```

I expected it to parse the row, but instead it raises KeyError. This is because `validate()` in csvtool/checks.py doesn't handle None.
"""

FABRICATED_BODY = """In version 2.3.1 the function `parse_stream()` in csvtool/stream.py never closes the file handle.

```
  File "/app/csvtool/parser.py", line 40, in parse_stream
ResourceWarning: unclosed file
```
"""

QUESTION_BODY = "Is there a way to parse tab-separated files with csvtool? Thanks!"

PR_BODY = ("Fixes #42. `parse_file()` now builds the column index from the normalized names, "
           "so empty header cells no longer raise KeyError. Adds a regression test.")


def issue_json(number: int, title: str, body: str, created: str) -> dict:
    return {
        "number": number, "title": title, "body": body, "created_at": created,
        "user": {"login": "reporter"}, "state": "closed",
        "labels": [{"name": "confirmed"}],  # must never reach the pipeline
        "html_url": f"https://github.com/{OWNER}/{REPO}/issues/{number}",
    }


class FakeGitHub:
    def __init__(self, shas: dict[str, str]):
        self.shas = shas
        self.issues = {
            42: issue_json(42, "parse_file crashes with KeyError on an empty column", BUG_BODY, "2025-03-10T12:00:00Z"),
            43: issue_json(43, "parse_stream() leaks file handles", FABRICATED_BODY, "2025-03-12T12:00:00Z"),
            44: issue_json(44, "How do I read TSV files?", QUESTION_BODY, "2025-03-12T12:00:00Z"),
        }
        self.comments = {42: [
            {"user": {"login": "maintainer"}, "created_at": "2025-03-11T09:00:00Z",
             "body": "SECRET-MAINTAINER-VERDICT: confirmed, fixed in v2.4.0"},
            {"user": {"login": "reporter"}, "created_at": "2025-03-11T10:00:00Z",
             "body": "Also happens on Python 3.12."},
        ]}
        self.pulls = {7: {
            "number": 7, "title": "Fix KeyError for empty header cells in parse_file", "body": PR_BODY,
            "created_at": "2025-03-19T12:00:00Z", "merged": True,  # must never reach the pipeline
            "base": {"sha": shas["pre_issue"], "ref": "main"}, "head": {"sha": shas["fix"]},
            "html_url": f"https://github.com/{OWNER}/{REPO}/pull/7",
        }}
        self.calls: list[str] = []

    def repo(self, owner, repo):
        self.calls.append("repo")
        return {"default_branch": "main", "private": False}

    def issue(self, owner, repo, number):
        self.calls.append(f"issue:{number}")
        if number in self.pulls:
            return {**self.pulls[number], "pull_request": {}}
        return self.issues[number]

    def pull(self, owner, repo, number):
        self.calls.append(f"pull:{number}")
        return self.pulls[number]

    def issue_comments(self, owner, repo, number, max_pages=3):
        self.calls.append(f"comments:{number}")
        return self.comments.get(number, [])

    def check_runs(self, owner, repo, sha):
        return []

    def rate_limit(self):
        return {"rate": {"remaining": 5000, "limit": 5000}}


def _by_kind(items, kind, claim_id=None, path_contains=None):
    for i in items:
        if i.kind == kind and (claim_id is None or claim_id in i.claim_ids) and \
                (path_contains is None or (i.path and path_contains in i.path)):
            return i.id
    return None


def extract_response(ctx) -> dict:
    inp = ctx["input"]
    n = inp.target.number
    if n == 42:
        return {"report_kind": "defect", "claims": [
            {"text": "parse_file() crashes with KeyError: 'col' when the header has an empty column",
             "type": "behavior", "is_primary": True,
             "source_span": "`parse_file()` crashes when the CSV header has an empty column",
             "condition": "header has an empty column", "observed": "KeyError: 'col'", "symbols": ["parse_file"]},
            {"text": "validate() in csvtool/checks.py does not handle None", "type": "implementation",
             "source_span": "`validate()` in csvtool/checks.py doesn't handle None",
             "condition": "value is None", "symbols": ["validate", "csvtool/checks.py"]},
            {"text": "The crash is caused by validate() not handling None", "type": "cause",
             "source_span": "This is because `validate()` in csvtool/checks.py doesn't handle None",
             "symbols": ["validate"]},
            {"text": "Invented claim", "type": "behavior", "source_span": "this sentence is not in the report at all",
             "symbols": []},
        ]}
    if n == 43:
        return {"report_kind": "defect", "claims": [
            {"text": "parse_stream() never closes the file handle", "type": "behavior", "is_primary": True,
             "source_span": "the function `parse_stream()` in csvtool/stream.py never closes the file handle",
             "symbols": ["parse_stream", "csvtool/stream.py"]},
            {"text": "parse_stream() exists in csvtool/stream.py", "type": "existence",
             "source_span": "the function `parse_stream()` in csvtool/stream.py", "symbols": ["parse_stream"]},
        ]}
    if n == 44:
        return {"report_kind": "question", "claims": [
            {"text": "User asks how to parse tab-separated files", "type": "behavior", "is_primary": True,
             "source_span": "Is there a way to parse tab-separated files with csvtool", "symbols": []}]}
    if n == 7:
        return {"report_kind": "defect", "claims": [
            {"text": "Empty header cells no longer raise KeyError in parse_file()", "type": "pr_fix", "is_primary": True,
             "source_span": "empty header cells no longer raise KeyError", "symbols": ["parse_file"]},
            {"text": "Adds a regression test", "type": "pr_test", "source_span": "Adds a regression test",
             "symbols": []},
        ]}
    raise AssertionError(f"unexpected issue {n}")


def adjudicate_response(ctx) -> dict:
    items = ctx["items"]
    out = []
    for c in ctx["claims"]:
        if c.type == "behavior" and "KeyError" in (c.observed or ""):
            ids = [x for x in [_by_kind(items, "lookup_sites", c.id), _by_kind(items, "code_excerpt", c.id)] if x]
            out.append({"claim_id": c.id, "technical": "supported",
                        "evidence": [{"id": i, "relation": "supports"} for i in ids] + [{"id": "E999", "relation": "supports"}],
                        "explanation": "parse_file looks up index[name] with names that include 'col', which is not a key.",
                        "limitations": ["Not executed."]})
        elif c.type == "implementation":
            gid = _by_kind(items, "guards_present", c.id, path_contains="checks.py")
            out.append({"claim_id": c.id, "technical": "contradicted",
                        "evidence": [{"id": gid, "relation": "contradicts"}] if gid else [],
                        "explanation": "validate() checks for None on line 3."})
        elif c.type == "cause":
            nid = _by_kind(items, "no_test_found", c.id) or _by_kind(items, "guard_not_found", c.id)
            # Tries to contradict with an absence result: the validator must reject this.
            out.append({"claim_id": c.id, "technical": "contradicted",
                        "evidence": [{"id": nid, "relation": "contradicts"}] if nid else [],
                        "explanation": "The crash happens before validate() runs."})
        elif c.type == "pr_fix":
            tid = _by_kind(items, "pr_diff_touches_claim", c.id)
            out.append({"claim_id": c.id, "technical": "supported",
                        "evidence": [{"id": tid, "relation": "supports"}] if tid else [],
                        "explanation": "The diff changes parse_file's index construction."})
        else:
            out.append({"claim_id": c.id, "technical": "inconclusive", "explanation": "Not enough evidence.",
                        "lean": None})
    return {"assessments": out}


@pytest.fixture(scope="session")
def origin(tmp_path_factory):
    path = tmp_path_factory.mktemp("origin") / "csvtool"
    shas = build(path)
    return path, shas


@pytest.fixture
def settings(tmp_path, origin) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", llm_base_url="", github_token="",
                    llm_provider="openai", google_api_key="",
                    clone_url_overrides={f"{OWNER}/{REPO}": str(origin[0])}, run_timeout_s=120)


@pytest.fixture
def fake_github(origin) -> FakeGitHub:
    return FakeGitHub(origin[1])


@pytest.fixture
def make_deps(settings, fake_github):
    def _make(gateway="default"):
        db = Database(settings.db_path)
        gw = FakeGateway({"extract": extract_response, "extract_pr": extract_response,
                          "adjudicate": adjudicate_response}, db=db) if gateway == "default" else gateway
        return Deps(settings=settings, db=db, repos=RepoCache(settings.cache_dir, 60, settings.clone_url_overrides),
                    gateway=gw, github=fake_github)
    return _make
