from __future__ import annotations

import hashlib
import hmac

import pytest

from app.github.urls import BadURL, parse_target, permalink
from app.github.webhooks import target_from_event, verify_signature
from app.pipeline.claims import span_in_text
from app.pipeline.extract_regex import extract_anchors
from app.pipeline.missing_info import check_missing
from app.pipeline.aggregate import summarize
from app.schemas import Claim, ClaimAssessment

from conftest import BUG_BODY


def test_parse_issue_and_pr_urls():
    t = parse_target("https://github.com/acme/csvtool/issues/42#issuecomment-1")
    assert (t.owner, t.repo, t.kind, t.number) == ("acme", "csvtool", "issue", 42)
    p = parse_target("https://github.com/acme/csvtool/pull/7/files")
    assert p.kind == "pr" and p.url.endswith("/pull/7")
    with pytest.raises(BadURL):
        parse_target("https://gitlab.com/acme/csvtool/issues/1")


def test_permalink_format():
    assert permalink("a", "b", "abc", "x.py", 3, 9) == "https://github.com/a/b/blob/abc/x.py#L3-L9"
    assert permalink("a", "b", "abc", "x.py", 3, 3) == "https://github.com/a/b/blob/abc/x.py#L3"


def test_anchor_extraction():
    anchors = extract_anchors(BUG_BODY)
    kinds = {(a.kind, a.value) for a in anchors}
    assert ("version", "2.3.1") in kinds
    assert ("symbol", "parse_file") in kinds and ("symbol", "validate") in kinds
    assert ("path", "csvtool/checks.py") in kinds
    assert ("error", "KeyError: 'col'") in kinds
    frames = [a for a in anchors if a.kind == "traceback_frame"]
    assert any(a.value.endswith("csvtool/parser.py") and a.line == 20 and a.func == "parse_file" for a in frames)
    assert any(a.kind == "code_block" for a in anchors)


def test_span_validation_tolerates_markdown_but_not_inventions():
    assert span_in_text("`parse_file()` crashes when the CSV header has an empty column", BUG_BODY)
    assert span_in_text("parse_file() crashes when the CSV header has an empty column", BUG_BODY)
    assert not span_in_text("the parser deletes user files", BUG_BODY)


def test_missing_info_rules():
    # The traceback path reveals the Python version, so nothing is missing.
    assert check_missing(BUG_BODY, extract_anchors(BUG_BODY)) == []
    no_env = BUG_BODY.replace("/usr/lib/python3.11/site-packages/", "")
    assert check_missing(no_env, extract_anchors(no_env)) == ["environment"]
    thin = "It is broken."
    assert set(check_missing(thin, extract_anchors(thin))) == {"version", "steps", "expected_actual", "error", "environment"}


def test_webhook_signature_and_routing():
    body = b'{"x":1}'
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3cret", body, sig)
    assert not verify_signature("s3cret", body + b" ", sig)
    assert not verify_signature("", body, sig)
    payload = {"action": "opened", "repository": {"full_name": "acme/csvtool"}, "installation": {"id": 5},
               "issue": {"number": 42, "html_url": "https://github.com/acme/csvtool/issues/42"}}
    target, inst = target_from_event("issues", payload)
    assert target.number == 42 and inst == 5
    assert target_from_event("issues", {**payload, "action": "labeled"}) is None


def test_reproduction_summary_describes_observation():
    claim = Claim(id="C1", text="The call raises TypeError", type="behavior", source_span="raises TypeError", is_primary=True)
    assessment = ClaimAssessment(claim_id="C1", technical="supported", verification="reproduced", strength="high")
    summary, tone = summarize("defect", [claim], [assessment])
    assert summary == "Reported behaviour reproduced in the sandbox." and tone == "supported"
