"""Google transport/provider selection, offline and with synthetic credentials."""
import json

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.db import Database
from app.llm.factory import make_gateway
from app.llm.gateway import LLMBadOutput, LLMUnavailable
from app.llm.google import GoogleGemmaGateway


class Output(BaseModel):
    ok: bool


def reply(text='{"ok": true}', finish="STOP"):
    return httpx.Response(200, json={"candidates": [{"finishReason": finish, "content": {"parts": [
        {"thought": True, "text": "not the answer"}, {"text": text}]}}]})


def test_native_google_request_and_cached_response(tmp_path):
    seen = []
    def handler(request):
        seen.append(request)
        return reply()
    gateway = GoogleGemmaGateway("gemma-4-31b-it", "synthetic-secret", db=Database(tmp_path / "c.sqlite3"),
                                  transport=httpx.MockTransport(handler))
    assert gateway.complete_json("extract", "v", "trusted instructions", "<untrusted>report</untrusted>", Output, run_id="test").ok
    gateway.cache_only = True
    assert gateway.complete_json("extract", "v", "trusted instructions", "<untrusted>report</untrusted>", Output).ok
    assert len(seen) == 1
    request = seen[0]
    assert request.headers["x-goog-api-key"] == "synthetic-secret"
    assert "synthetic-secret" not in str(request.url)
    assert str(request.url).endswith("gemma-4-31b-it:generateContent")
    body = json.loads(request.content)
    assert "systemInstruction" not in body and "responseJsonSchema" not in body["generationConfig"]
    assert "trusted instructions" in body["contents"][0]["parts"][0]["text"]
    assert "<untrusted>report</untrusted>" in body["contents"][0]["parts"][0]["text"]
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}
    calls = gateway.db.llm_calls_for("test")
    assert len(calls) == 1 and "synthetic-secret" not in json.dumps(calls)


def test_bad_output_repair_still_uses_google_transport():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return reply("not json") if len(seen) == 1 else reply()
    gateway = GoogleGemmaGateway("gemma-4-31b-it", "synthetic", transport=httpx.MockTransport(handler))
    assert gateway.complete_json("p", "v", "s", "u", Output).ok
    assert len(seen) == 2
    assert "corrected JSON" in seen[1]["contents"][0]["parts"][0]["text"]


def test_daily_quota_uses_google_error_details_and_stops_repeated_calls(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(429, json={"error": {"message": "Quota exceeded", "details": [
            {"violations": [{"quotaMetric": "generate_content_free_tier_requests", "quotaId": "RequestsPerDay"}]},
            {"retryDelay": "120s"}]}})
    gateway = GoogleGemmaGateway("gemma-4-31b-it", "synthetic", transport=httpx.MockTransport(handler))
    for text in ("first", "second"):
        with pytest.raises(LLMUnavailable, match="quota"):
            gateway.complete_json("p", "v", "s", text, Output)
    assert len(seen) == 1 and 0 < gateway.quota_retry_after_s <= 120


def test_transient_rate_limit_waits_then_recovers(monkeypatch):
    monkeypatch.setattr("app.llm.google.time.sleep", lambda _: None)
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(429, json={"error": {"message": "Per-minute quota", "details": [
            {"retryDelay": "2s"}]}}) if len(seen) == 1 else reply()
    gateway = GoogleGemmaGateway("gemma-4-31b-it", "synthetic", transport=httpx.MockTransport(handler))
    assert gateway.complete_json("p", "v", "s", "u", Output).ok
    assert len(seen) == 2


@pytest.mark.parametrize("finish", ["MAX_TOKENS", "SAFETY", "RECITATION"])
def test_incomplete_or_blocked_output_is_not_a_verdict(finish):
    gateway = GoogleGemmaGateway("gemma-4-31b-it", "synthetic", transport=httpx.MockTransport(lambda _: reply(finish=finish)))
    with pytest.raises(LLMBadOutput, match="incomplete or blocked"):
        gateway.complete_json("p", "v", "s", "u", Output)


def test_credentials_are_redacted_from_google_errors():
    key = "synthetic-secret"
    gateway = GoogleGemmaGateway("gemma-4-31b-it", key, transport=httpx.MockTransport(
        lambda _: httpx.Response(400, json={"error": {"message": f"Bad credential {key}"}})))
    with pytest.raises(LLMUnavailable) as error:
        gateway.complete_json("p", "v", "s", "u", Output)
    assert key not in str(error.value)


def test_google_key_takes_precedence_over_previous_groq_endpoint(settings):
    settings.llm_provider = "auto"
    settings.google_api_key = SecretStr("synthetic")
    settings.llm_base_url = "https://old-provider.invalid/v1"
    gateway = make_gateway(settings, Database(settings.db_path))
    assert isinstance(gateway, GoogleGemmaGateway) and gateway.model == "gemma-4-26b-a4b-it"
    assert "synthetic" not in repr(settings)
    settings.llm_cache_only = True
    assert make_gateway(settings, Database(settings.db_path)).cache_only


def test_explicit_provider_override_and_open_weight_model_constraint(settings):
    settings.google_api_key = SecretStr("synthetic")
    settings.llm_provider = "openai"
    assert make_gateway(settings, Database(settings.db_path)) is None
    with pytest.raises(ValueError, match="open-weight Gemma"):
        GoogleGemmaGateway("gemini-flash", "synthetic")


def test_google_native_api_runs_issue_pipeline(settings, fake_github):
    from fastapi.testclient import TestClient
    from app.main import Services, create_app
    from test_api import _wait
    seen = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"name": "models/gemma-4-31b-it"})
        text = json.loads(request.content)["contents"][0]["parts"][0]["text"]
        seen.append(text)
        if '"assessments"' in text:
            return reply(json.dumps({"assessments": [{"claim_id": "C1", "technical": "inconclusive",
                "explanation": "The static evidence does not establish runtime behavior.", "evidence": []}]}))
        return reply(json.dumps({"report_kind": "defect", "claims": [{"text": fake_github.issues[42]["title"],
            "type": "behavior", "is_primary": True, "source_span": fake_github.issues[42]["title"]}]}))
    gateway = GoogleGemmaGateway("gemma-4-31b-it", "synthetic", transport=httpx.MockTransport(handler))
    with TestClient(create_app(Services(settings, gateway=gateway, github=fake_github))) as client:
        started = client.get("/api/assessment", params={"url": "https://github.com/acme/csvtool/issues/42"}).json()
        result = _wait(client, started["run_id"])
        assert result["status"] == "complete"
        assert result["models"]["reasoning"] == "gemma-4-31b-it"
        assert not result["blockers"] and len(seen) == 2
        assert result["evidence"] and result["assessments"][0]["technical"] == "inconclusive"
        health = client.get("/api/health").json()
        assert health["provider"] == "google" and health["model_server"] == "ok"


def test_literal_presence_cannot_verify_runtime_behavior(tmp_path):
    from app.pipeline.ledger import Ledger
    from app.pipeline.validator import validate
    from app.schemas import Claim, ClaimAssessment, Snapshot
    (tmp_path / "client.py").write_text('MESSAGE = "Retrying request"\n')
    ledger = Ledger("o", "r")
    ledger.register_root("sha", tmp_path)
    item = ledger.add("literal_match", "E2", "warning found", "sha", path="client.py", start=1, end=1, claim_ids=["C1"])
    claim = Claim(id="C1", text="Client emits this warning on every request", source_span="warning", type="behavior")
    assessment = ClaimAssessment(claim_id="C1", technical="supported", evidence_ids=[item.id],
                                 relations={item.id: "supports"}, decided_by="model")
    checked = validate([assessment], [claim], ledger, Snapshot(sha="sha", reliability="exact"), {})[0]
    assert checked.technical == "inconclusive" and "source presence" in checked.downgraded_reason
    claim.type = "existence"
    assert validate([assessment], [claim], ledger, Snapshot(sha="sha", reliability="exact"), {})[0].technical == "inconclusive"
    claim.text = "The warning string exists in client.py"
    assert validate([assessment], [claim], ledger, Snapshot(sha="sha", reliability="exact"), {})[0].technical == "supported"
