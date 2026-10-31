"""Enforce public data and confine all repository reads to the snapshot."""
import hashlib
import hmac
import json

import pytest

from fastapi.testclient import TestClient

from app.github.client import GitHubError
from app.main import Services, create_app
from app.pipeline.index import build_index
from app.pipeline.ledger import Ledger
from app.pipeline.manifests import declarations
from app.pipeline.orchestrator import fetch_input
from app.pipeline.search import search_text
from app.schemas import Target


@pytest.mark.parametrize("kind", ["issue", "pr"])
def test_private_repo_never_fetches_content(settings, fake_github, make_deps, kind):
    fake_github.repo = lambda *_: {"private": True}
    target = Target(owner="acme", repo="csvtool", kind=kind, number=42,
                    url=f"https://github.com/acme/csvtool/{'issues' if kind == 'issue' else 'pull'}/42")
    with pytest.raises(GitHubError, match="public repositories only"):
        fetch_input(make_deps(), target, "live")
    assert fake_github.calls == []
    services = Services(settings, github=fake_github)
    with TestClient(create_app(services)) as client:
        response = client.get("/api/assessment", params={"url": target.url})
        assert response.status_code == 403 and "public repositories" in response.json()["detail"]
    assert fake_github.calls == []


def test_signed_webhook_cannot_bypass_public_policy(settings, fake_github):
    fake_github.repo = lambda *_: {"private": True}
    settings.github_webhook_secret = "fixture-secret"
    body = json.dumps({"action": "opened", "repository": {"full_name": "acme/csvtool"},
                       "issue": {"number": 42, "html_url": "https://github.com/acme/csvtool/issues/42"}}).encode()
    signature = "sha256=" + hmac.new(b"fixture-secret", body, hashlib.sha256).hexdigest()
    with TestClient(create_app(Services(settings, github=fake_github))) as client:
        result = client.post("/api/webhooks/github", content=body,
                             headers={"x-github-event": "issues", "x-hub-signature-256": signature})
        assert result.status_code == 403
    assert fake_github.calls == []


def test_checkout_symlinks_cannot_read_host_files(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    root.mkdir()
    outside = tmp_path / "host.py"
    outside.write_text("def host_only_function():\n    return 'host-only-marker'\n")
    (root / "escape.py").symlink_to(outside)
    (root / "requirements.txt").symlink_to(outside)
    (root / ".git").write_text("gitdir: /private/host-only-marker")
    index = build_index(root)
    assert not index.lookup("host_only_function")
    assert declarations(root, {"requirements.txt"}) == []
    monkeypatch.setattr("app.pipeline.search._rg_available", lambda: False)
    assert search_text(root, "host-only-marker") == []
    ledger = Ledger("o", "r")
    ledger.register_root("s", root)
    assert ledger._read("s", "escape.py", 1, 2) is None
    assert ledger._read("s", "../host.py", 1, 2) is None
