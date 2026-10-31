"""API tests through FastAPI's TestClient, plus the extension contract check."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.llm.gateway import CachedGateway, FakeGateway
from app.main import Services, create_app
from app.schemas import PanelResult
from app.github.urls import parse_target
from test_diagnostics import blocked

from conftest import adjudicate_response, extract_response

MOCK = Path(__file__).resolve().parents[2] / "extension" / "mock" / "panel_result.example.json"


def _client(settings, fake_github):
    gw = FakeGateway({"extract": extract_response, "extract_pr": extract_response, "adjudicate": adjudicate_response})
    services = Services(settings, gateway=gw, github=fake_github)
    return TestClient(create_app(services))


def _wait(client, run_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(f"/api/runs/{run_id}").json()
        if r["status"] in ("complete", "partial", "error"):
            return r
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_assessment_flow_and_cache(settings, fake_github):
    with _client(settings, fake_github) as client:
        r = client.get("/api/assessment", params={"url": "https://github.com/acme/csvtool/issues/42"})
        assert r.status_code == 200
        first = r.json()
        done = _wait(client, first["run_id"])
        assert done["status"] == "complete" and done["summary_tone"] == "mixed"

        again = client.get("/api/assessment", params={"url": "https://github.com/acme/csvtool/issues/42"}).json()
        assert again["run_id"] == first["run_id"] and again["status"] == "complete"

        fb = client.post("/api/feedback", json={"run_id": first["run_id"], "claim_id": "C1", "vote": "yes"})
        assert fb.status_code == 201


def test_bad_url_and_unknown_run(settings, fake_github):
    with _client(settings, fake_github) as client:
        assert client.get("/api/assessment", params={"url": "https://example.com/x"}).status_code == 400
        assert client.get("/api/runs/nope").status_code == 404


def test_webhook_requires_valid_signature(settings, fake_github):
    settings.github_webhook_secret = "s3cret"
    payload = {"action": "opened", "repository": {"full_name": "acme/csvtool"},
               "issue": {"number": 42, "html_url": "https://github.com/acme/csvtool/issues/42"}}
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    with _client(settings, fake_github) as client:
        bad = client.post("/api/webhooks/github", content=body,
                          headers={"x-github-event": "issues", "x-hub-signature-256": "sha256=00"})
        assert bad.status_code == 401
        ok = client.post("/api/webhooks/github", content=body,
                         headers={"x-github-event": "issues", "x-hub-signature-256": sig,
                                  "content-type": "application/json"})
        assert ok.status_code == 202
        _wait(client, ok.json()["run_id"])


def test_health(settings, fake_github):
    with _client(settings, fake_github) as client:
        h = client.get("/api/health").json()
        assert h["db"] == "ok" and h["model_server"] == "not configured" and h["github"].startswith("ok")


def test_extension_mock_matches_contract():
    PanelResult.model_validate(json.loads(MOCK.read_text()))


def test_cache_only_results_do_not_automatically_rerun(settings, fake_github):
    services = Services(settings, github=fake_github, gateway=CachedGateway("", "openai/gpt-oss-20b"))
    with TestClient(create_app(services)) as client:
        first = client.get("/api/assessment", params={"url": "https://github.com/acme/csvtool/issues/42"}).json()
        done = _wait(client, first["run_id"])
        assert any(s["status"] == "degraded" for s in done["stages"])
        again = client.get("/api/assessment", params={"url": "https://github.com/acme/csvtool/issues/42"}).json()
        assert again["run_id"] == first["run_id"]
        assert client.get("/api/health").json()["inference"] == "cache-only"
        assert client.get("/api/health").json()["inference_ready"] is False


def test_saved_run_api_explains_quota_failure_without_inference(settings, fake_github):
    gw = FakeGateway({})
    services = Services(settings, github=fake_github, gateway=gw)
    original = blocked()
    services.db.new_run("saved", original)
    with TestClient(create_app(services)) as client:
        response = client.get("/api/runs/saved")
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "partial"
        assert result["blockers"][0]["code"] == "model_quota_exhausted"
        assert result["assessments"][0]["technical"] == "inconclusive"
        assert not gw.calls
        assert services.db.get_run("saved").status == "complete"  # historical result stays intact


def test_cache_only_setting_uses_cache_without_a_configured_endpoint(settings, fake_github):
    settings.llm_cache_only = True
    services = Services(settings, github=fake_github)
    assert isinstance(services.gateway, CachedGateway)
    with TestClient(create_app(services)) as client:
        result = client.get("/api/assessment", params={"url": "https://github.com/acme/csvtool/issues/42"}).json()
        done = _wait(client, result["run_id"])
        assert done["status"] == "partial"
        assert any(b["code"] == "model_cache_miss" for b in done["blockers"])
        health = client.get("/api/health").json()
        assert health["inference"] == "cache-only" and not health["inference_ready"]


def test_model_or_endpoint_change_invalidates_run_cache(settings, fake_github):
    from app.llm.gateway import OpenAICompatGateway
    gw = OpenAICompatGateway("http://model-a/v1", "model-a")
    services = Services(settings, gateway=gw, github=fake_github)
    target = parse_target("https://github.com/acme/csvtool/issues/42")
    first = services.cache_key(target, fake_github)
    gw.model = "model-b"
    second = services.cache_key(target, fake_github)
    gw.base_url = "http://model-b/v1"
    third = services.cache_key(target, fake_github)
    gw.cache_only = True
    fourth = services.cache_key(target, fake_github)
    assert len({first, second, third, fourth}) == 4


def test_health_exposes_cooldown_without_claiming_inference_is_ready(settings, fake_github):
    from app.llm.gateway import OpenAICompatGateway
    gw = OpenAICompatGateway("http://model/v1", "x", quota_cooldown_s=600)
    gw.quota_exhausted = True
    services = Services(settings, gateway=gw, github=fake_github)
    with TestClient(create_app(services)) as client:
        health = client.get("/api/health").json()
        assert health["inference"] == "daily quota exhausted"
        assert health["inference_ready"] is False
        assert 0 < health["inference_retry_after_s"] <= 600
