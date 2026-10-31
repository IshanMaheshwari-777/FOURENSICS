"""Speed-ups that must not change what the model receives or how results are judged:
bookings are corrected with real usage, extraction requests a realistic reply size (and retries at
full size if cut off), and logs separate waiting from model time."""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
from pydantic import BaseModel

from app.db import Database
from app.llm.gateway import OpenAICompatGateway
from app.llm.google import GoogleGemmaGateway


class Out(BaseModel):
    ok: bool


@pytest.fixture
def clock(monkeypatch):
    """A controllable clock: sleeps advance time instantly and are recorded."""
    state = {"t": 1000.0, "sleeps": []}
    monkeypatch.setattr("app.llm.gateway.time.monotonic", lambda: state["t"])

    def sleep(s):
        state["sleeps"].append(s)
        state["t"] += s

    monkeypatch.setattr("app.llm.gateway.time.sleep", sleep)
    return state


def openai_reply(used: int, finish: str = "stop", content: str = '{"ok": true}') -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                     "usage": {"total_tokens": used}})


def test_actual_usage_frees_the_budget_for_the_next_call(clock):
    """Extraction reserves prompt + reply size, but the provider reports far less. The evidence call
    that follows must not wait for tokens that were never used (the old ~60 s pause)."""
    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", max_tokens=3500, tokens_per_minute=14000,
                             transport=httpx.MockTransport(lambda req: openai_reply(used=1500)))
    gw.complete_json("extract", "v1", "s", "u" * 6000, Out)       # books ~1.5K prompt + 2K reply
    gw.complete_json("adjudicate", "v1", "s", "e" * 28000, Out)   # books ~7K prompt + 3.5K reply
    assert clock["sleeps"] == [], "the evidence call waited although real usage left room"


def test_the_cap_is_still_respected_when_real_usage_is_high(clock):
    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", max_tokens=3500, tokens_per_minute=14000,
                             transport=httpx.MockTransport(lambda req: openai_reply(used=9000)))
    gw.complete_json("extract", "v1", "s", "u" * 6000, Out)
    gw.complete_json("adjudicate", "v1", "s", "e" * 28000, Out)
    assert any(s > 30 for s in clock["sleeps"]), "9K used + 10.5K booked exceeds 14K: must wait"


def test_extraction_requests_a_realistic_reply_size_and_others_the_full_size():
    sent = []

    def handler(req):
        sent.append(json.loads(req.content)["max_tokens"])
        return openai_reply(used=500)

    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", max_tokens=3500,
                             transport=httpx.MockTransport(handler))
    gw.complete_json("extract", "v1", "s", "u1", Out)
    gw.complete_json("extract_pr", "v1", "s", "u2", Out)
    gw.complete_json("adjudicate", "v1", "s", "u3", Out)
    assert sent == [2000, 2000, 3500]


def test_reply_size_never_exceeds_the_configured_maximum():
    sent = []

    def handler(req):
        sent.append(json.loads(req.content)["max_tokens"])
        return openai_reply(used=500)

    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", max_tokens=1200,
                             transport=httpx.MockTransport(handler))
    gw.complete_json("extract", "v1", "s", "u", Out)
    assert sent == [1200]


def test_a_cut_off_reply_is_retried_at_full_size_not_parsed():
    sent = []

    def handler(req):
        sent.append(json.loads(req.content)["max_tokens"])
        if len(sent) == 1:
            return openai_reply(used=2100, finish="length", content='{"ok": tr')
        return openai_reply(used=2600)

    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", max_tokens=3500,
                             transport=httpx.MockTransport(handler))
    assert gw.complete_json("extract", "v1", "s", "u", Out).ok
    assert sent == [2000, 3500]


def google_reply(used: int, finish: str = "STOP", text: str = '{"ok": true}') -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
                                     "usageMetadata": {"totalTokenCount": used}})


def test_google_corrects_bookings_and_retries_cut_off_extraction(clock):
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append(body["generationConfig"]["maxOutputTokens"])
        if len(sent) == 1:
            return google_reply(used=900, finish="MAX_TOKENS", text='{"ok"')
        return google_reply(used=1200)

    gw = GoogleGemmaGateway("gemma-4-26b-a4b-it", "k", max_tokens=3500, tokens_per_minute=14000,
                            transport=httpx.MockTransport(handler))
    assert gw.complete_json("extract", "v1", "s", "u" * 6000, Out).ok
    gw.complete_json("adjudicate", "v1", "s", "e" * 28000, Out)
    assert sent == [2000, 3500, 3500]
    # 900 + 1,200 really used, plus ~10.6K booked for the evidence call, fits under 14K.
    assert clock["sleeps"] == [], "real usage left room under the per-minute cap"


def test_logs_separate_waiting_from_model_time(tmp_path, clock):
    db = Database(tmp_path / "t.sqlite3")
    replies = iter([httpx.Response(429, headers={"retry-after": "4"}, json={"error": {"message": "slow down"}}),
                    openai_reply(used=777)])
    gw = OpenAICompatGateway("http://m/v1", "x", json_schema="off", db=db, tokens_per_minute=8000,
                             transport=httpx.MockTransport(lambda req: next(replies)))
    gw.complete_json("extract", "v1", "s", "u", Out, run_id="r1")
    row = db.llm_calls_for("r1")[0]
    assert row["wait_ms"] >= 4000, "the rate-limit backoff counts as waiting"
    assert row["model_ms"] is not None and row["tokens_used"] == 777


def test_old_databases_gain_the_timing_columns_without_losing_rows(tmp_path):
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE llm_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, purpose TEXT, "
                "model TEXT, prompt_version TEXT, prompt_hash TEXT, prompt_text TEXT, response_text TEXT, "
                "ok INTEGER, ms INTEGER, created_at TEXT)")
    con.execute("INSERT INTO llm_calls (run_id, purpose, ok, ms) VALUES ('old', 'extract', 1, 55000)")
    con.commit()
    con.close()
    db = Database(path)
    rows = db.llm_calls_for("old")
    assert rows[0]["ms"] == 55000 and rows[0]["wait_ms"] is None
    db.log_llm_call(run_id="new", purpose="extract", model="m", prompt_version="v", prompt_hash="h",
                    prompt_text="p", response_text="r", ok=1, ms=10, wait_ms=2, model_ms=8, tokens_used=99)
    assert db.llm_calls_for("new")[0]["tokens_used"] == 99
