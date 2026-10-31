"""Model gateway behaviour against a mocked OpenAI-compatible server."""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm.gateway import CachedGateway, LLMTooLarge, LLMUnavailable, OpenAICompatGateway, retry_after_seconds
from app.db import Database
from app.pipeline.adjudicate import build_adjudication_prompt
from app.pipeline.extract_regex import extract_anchors
from app.schemas import Claim, EvidenceItem, Snapshot


class Out(BaseModel):
    ok: bool


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_schema_rejection_falls_back_and_bad_json_is_repaired():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "json_schema not supported"}})
        return _reply("not json at all") if len(seen) == 2 else _reply('```json\n{"ok": true}\n```')

    gw = OpenAICompatGateway("http://m/v1", "x", transport=httpx.MockTransport(handler))
    assert gw.complete_json("p", "v1", "sys", "user", Out).ok is True
    assert len(seen) == 3 and "response_format" not in seen[2]


def test_too_large_lowers_budget_from_stated_limit():
    def handler(req):
        return httpx.Response(413, json={"error": {"message": "Request too large ... (TPM): Limit 8000, Requested 10049"}})

    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", max_tokens=4000, prompt_budget_tokens=12000,
                             transport=httpx.MockTransport(handler))
    with pytest.raises(LLMTooLarge) as e:
        gw.complete_json("p", "v1", "sys", "user", Out)
    assert e.value.limit == 8000
    assert gw.max_tokens == 2000 and gw.prompt_budget_tokens == 4400


def test_rate_limit_waits_once_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.llm.gateway.time.sleep", lambda s: None)
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "TPM exceeded"}})
        return _reply('{"ok": true}')

    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", transport=httpx.MockTransport(handler))
    assert gw.complete_json("p", "v1", "s", "u", Out).ok and len(calls) == 2


def test_learns_per_minute_cap_and_paces_later_requests(monkeypatch):
    clock = {"t": 1000.0}
    sleeps = []
    monkeypatch.setattr("app.llm.gateway.time.monotonic", lambda: clock["t"])

    def fake_sleep(s):
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr("app.llm.gateway.time.sleep", fake_sleep)
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "5"}, json={"error": {
                "message": "Rate limit reached for model in organization `org_X` on tokens per minute (TPM): Limit 3000"}})
        # The provider reports what each call really used.
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}],
                                         "usage": {"total_tokens": 1400}})

    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", max_tokens=1000,
                             transport=httpx.MockTransport(handler))
    assert gw.complete_json("p", "v1", "s", "u1", Out).ok
    assert gw.tokens_per_minute == 3000
    gw.complete_json("p", "v1", "s", "u2", Out)  # 2 x 1400 real tokens fit within 3000
    sleeps.clear()
    gw.complete_json("p", "v1", "s", "u3", Out)  # a third 1400 would exceed 3000 this minute: waits
    assert any(s > 30 for s in sleeps)


def test_rate_limit_with_long_wait_is_reported():
    def handler(req):
        return httpx.Response(429, headers={"retry-after": "600"}, json={"error": {"message": "daily cap"}})

    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMUnavailable, match="daily cap"):
        gw.complete_json("p", "v1", "s", "u", Out)


def test_prompt_packing_respects_budget_and_keeps_strong_evidence_first():
    claims = [Claim(id="C1", text="x crashes", type="behavior", is_primary=True, source_span="x")]
    items = [EvidenceItem(id=f"E{n:03d}", claim_ids=["C1"], kind="code_excerpt", tier="E3", sha="s",
                          result=f"item {n}", excerpt="y = 1\n" * 400) for n in range(1, 30)]
    items.append(EvidenceItem(id="E099", claim_ids=["C1"], kind="frame_match", tier="E2", sha="s", result="frame ok"))
    prompt = build_adjudication_prompt(claims, items, Snapshot(sha="s"), {}, [], budget_tokens=1500)
    assert len(prompt) <= 1500 * 4 + 200
    assert "E099" in prompt and prompt.index("E099") < prompt.index("E001")
    assert "left out to fit the size limit" in prompt


def test_anchor_noise_filters():
    text = ("TypeError: f() missing 1 required positional argument: 'x'\n"
            "Raised a TypeError when `datetime.now()` and `json.loads` were used in `my_func()`.")
    anchors = extract_anchors(text)
    values = {(a.kind, a.value) for a in anchors}
    assert ("error", "TypeError") not in values
    assert any(k == "error" and v.startswith("TypeError: f()") for k, v in values)
    assert ("symbol", "datetime.now") not in values and ("symbol", "json.loads") not in values
    assert ("symbol", "my_func") in values


def test_daily_quota_stops_live_calls_but_preserves_cache(tmp_path):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(429, json={"error": {"message": "tokens per day (TPD): Limit 200000"}})
    db = Database(tmp_path / "calls.sqlite3")
    gw = OpenAICompatGateway("http://m/v1", "x", db=db, transport=httpx.MockTransport(handler))
    for text in ("first", "second"):
        with pytest.raises(LLMUnavailable, match="daily quota"):
            gw.complete_json("p", "v", "s", text, Out)
    assert len(seen) == 1


def test_cached_gateway_never_uses_network_on_miss_or_hit(tmp_path):
    seen = []
    def handler(req):
        seen.append(req)
        return _reply('{"ok": true}')
    db = Database(tmp_path / "cache.sqlite3")
    online = OpenAICompatGateway("http://m/v1", "x", db=db, transport=httpx.MockTransport(handler))
    assert online.complete_json("p", "v", "s", "cached", Out).ok
    offline = CachedGateway("http://m/v1", "x", db=db, transport=httpx.MockTransport(handler))
    assert offline.complete_json("p", "v", "s", "cached", Out).ok
    with pytest.raises(LLMUnavailable, match="no cached response"):
        offline.complete_json("p", "v", "s", "new", Out)
    assert len(seen) == 1


def test_daily_quota_recovers_on_next_request_after_provider_retry_time(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr("app.llm.gateway.time.monotonic", lambda: clock["t"])
    seen = []
    def handler(req):
        seen.append(req)
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "600"},
                                  json={"error": {"message": "tokens per day (TPD) exceeded"}})
        return _reply('{"ok": true}')
    gw = OpenAICompatGateway("http://m/v1", "x", transport=httpx.MockTransport(handler), quota_cooldown_s=10)
    with pytest.raises(LLMUnavailable, match="daily quota"):
        gw.complete_json("p", "v", "s", "u", Out)
    assert gw.quota_exhausted and gw.quota_retry_after_s == 600
    clock["t"] += 599
    with pytest.raises(LLMUnavailable, match="retry eligible in 1s"):
        gw.complete_json("p", "v", "s", "u", Out)
    assert len(seen) == 1
    clock["t"] += 1
    assert not gw.quota_exhausted and gw.quota_retry_after_s == 0
    assert len(seen) == 1  # expiry itself never starts a background request
    assert gw.complete_json("p", "v", "s", "u", Out).ok
    assert len(seen) == 2


def test_cooldown_keeps_validated_cache_usable(tmp_path, monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr("app.llm.gateway.time.monotonic", lambda: clock["t"])
    seen = []
    def handler(req):
        seen.append(req)
        if len(seen) == 1:
            return _reply('{"ok": true}')
        return httpx.Response(429, headers={"retry-after": "invalid"},
                              json={"error": {"message": "requests per day (RPD) exceeded"}})
    db = Database(tmp_path / "cache.sqlite3")
    gw = OpenAICompatGateway("http://m/v1", "x", db=db, transport=httpx.MockTransport(handler), quota_cooldown_s=120)
    assert gw.complete_json("p", "v", "s", "cached", Out).ok
    with pytest.raises(LLMUnavailable, match="daily quota"):
        gw.complete_json("p", "v", "s", "uncached", Out)
    assert gw.quota_retry_after_s == 120
    assert gw.complete_json("p", "v", "s", "cached", Out).ok
    assert len(seen) == 2


@pytest.mark.parametrize("value", ["NaN", "inf", "not-a-time", ""])
def test_invalid_retry_after_cannot_poison_cooldown(value):
    assert retry_after_seconds(httpx.Response(429, headers={"retry-after": value})) is None


def test_http_date_retry_after():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    when = format_datetime(datetime.now(timezone.utc) + timedelta(minutes=5), usegmt=True)
    wait = retry_after_seconds(httpx.Response(429, headers={"retry-after": when}))
    assert 295 < wait <= 300


@pytest.mark.parametrize("message", [
    "Rate limit exceeded: free-models-per-day", "Quota exceeded: GenerateContentRequestsPerDayPerProjectPerModel-FreeTier",
])
def test_other_provider_daily_limits_do_not_trigger_repeated_network_calls(message):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(429, headers={"retry-after": "120"}, json={"error": {"message": message}})
    gw = OpenAICompatGateway("http://m/v1", "x", transport=httpx.MockTransport(handler))
    for text in ("first", "second"):
        with pytest.raises(LLMUnavailable, match="daily quota"):
            gw.complete_json("p", "v", "s", text, Out)
    assert len(seen) == 1
