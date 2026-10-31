"""FastAPI app: assessment API for the extension, webhook receiver for the App."""
from __future__ import annotations

import logging
import hashlib
import shutil
from contextlib import asynccontextmanager
from typing import Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import PIPELINE_VERSION, Settings, get_settings
from .db import Database, new_run_id
from .github.app_auth import AppAuth
from .github.client import GitHubClient, GitHubError, public_repo
from .github.urls import BadURL, parse_target
from .github.webhooks import target_from_event, verify_signature
from .jobs import JobRunner
from .llm.factory import make_gateway
from .llm.google import GoogleGemmaGateway
from .pipeline.firewall import body_hash
from .pipeline.diagnostics import explain_result
from .pipeline.orchestrator import Deps, analyze
from .pipeline.snapshot import RepoCache
from .schemas import PanelResult, Target

log = logging.getLogger("triage")


def model_failed(result: PanelResult) -> bool:
    return any(s.stage in ("claims", "adjudication") and s.status == "degraded"
               and any(w in (s.reason or "").lower() for w in ("unavailable", "rate limit", "unreachable"))
               for s in result.stages)


class Feedback(BaseModel):
    run_id: str
    claim_id: str
    vote: Literal["yes", "no"]


class Services:
    def __init__(self, settings: Settings, gateway=None, github: Optional[GitHubClient] = None):
        self.settings = settings
        self.db = Database(settings.db_path)
        self.db.mark_stale_running_as_error()
        self.jobs = JobRunner(settings.job_workers)
        self.repos = RepoCache(settings.cache_dir, settings.git_timeout_s, settings.clone_url_overrides)
        self.gateway = gateway if gateway is not None else make_gateway(settings, self.db)
        self.github = github or GitHubClient(settings.github_api, settings.github_token,
                                             cache_dir=settings.cache_dir / "http")
        self.app_auth: Optional[AppAuth] = None
        if settings.github_app_id and settings.github_app_private_key_path:
            try:
                self.app_auth = AppAuth(settings.github_app_id, settings.github_app_private_key_path, settings.github_api)
            except OSError as e:
                log.warning("GitHub App key could not be read: %s", e)

    def deps(self, installation_id: Optional[int] = None) -> Deps:
        gh = self.github
        if installation_id and self.app_auth:
            auth = self.app_auth
            gh = GitHubClient(self.settings.github_api, cache_dir=self.settings.cache_dir / "http",
                              token_provider=lambda: auth.installation_token(installation_id))
        return Deps(settings=self.settings, db=self.db, repos=self.repos, gateway=self.gateway, github=gh)

    def cache_key(self, target: Target, gh: GitHubClient) -> str:
        """Cheap metadata read (ETag-cached) so an edited issue or new PR commit gets a fresh run."""
        # A model/endpoint/mode change must not keep serving the old model's run.
        # The model response cache itself remains usable for identical model+prompt pairs.
        model_config = (getattr(self.gateway, "base_url", ""), getattr(self.gateway, "model", ""),
                        bool(getattr(self.gateway, "cache_only", False)))
        model_key = hashlib.sha256(repr(model_config).encode()).hexdigest()[:12]
        public_repo(gh, target.owner, target.repo)
        if target.kind == "issue":
            issue = gh.issue(target.owner, target.repo, target.number)
            if "pull_request" in issue:
                target = target.model_copy(update={"kind": "pr"})
            else:
                h = body_hash((issue.get("title") or "").strip(), issue.get("body") or "")
                return f"issue:{target.full_name}#{target.number}:{h}:{PIPELINE_VERSION}:{model_key}"
        pull = gh.pull(target.owner, target.repo, target.number)
        head = (pull.get("head") or {}).get("sha", "")
        h = body_hash((pull.get("title") or "").strip(), pull.get("body") or "")
        return f"pr:{target.full_name}#{target.number}:{h}:{head}:{PIPELINE_VERSION}:{model_key}"

    def start(self, target: Target, installation_id: Optional[int] = None, force: bool = False) -> PanelResult:
        deps = self.deps(installation_id)
        key = self.cache_key(target, deps.github)
        if not force:
            existing = self.db.latest_for_key(key)
            can_retry_model = self.gateway and not getattr(self.gateway, "cache_only", False) and \
                not getattr(self.gateway, "quota_exhausted", False)
            # A result whose model step failed (rate limit, server down) is retried rather than served forever.
            # Explicit offline mode/daily-quota stops must not trigger endless automatic reruns.
            if existing and existing.status in ("complete", "partial") and not (can_retry_model and model_failed(existing)):
                return explain_result(existing)
            running = self.jobs.inflight_run(key)
            if running:
                found = self.db.get_run(running)
                if found:
                    return found
        result = PanelResult(run_id=new_run_id(), status="queued", target=target, summary_line="Analysis queued")
        self.db.new_run(key, result)
        run_id, started = self.jobs.submit(key, result.run_id, lambda: analyze(deps, result, target=target))
        if not started:
            return self.db.get_run(run_id) or result
        return explain_result(result)


def create_app(services: Optional[Services] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = services or Services(get_settings())
        yield
        app.state.services.jobs.shutdown()

    app = FastAPI(title="FOURENSICS", version=PIPELINE_VERSION, lifespan=lifespan)
    settings = services.settings if services else get_settings()
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["*"])

    def svc(request: Request) -> Services:
        return request.app.state.services

    @app.get("/api/assessment", response_model=PanelResult)
    def assessment(request: Request, url: str = Query(...), refresh: bool = False):
        try:
            target = parse_target(url)
        except BadURL as e:
            raise HTTPException(400, str(e))
        try:
            return svc(request).start(target, force=refresh)
        except GitHubError as e:
            raise HTTPException(e.status if e.status in (403, 404) else 502, str(e))

    @app.get("/api/runs/{run_id}", response_model=PanelResult)
    def get_run(request: Request, run_id: str):
        result = svc(request).db.get_run(run_id)
        if result is None:
            raise HTTPException(404, "Unknown run")
        return explain_result(result)

    @app.post("/api/webhooks/github", status_code=202)
    async def webhook(request: Request):
        s = svc(request)
        if not s.settings.github_webhook_secret:
            raise HTTPException(503, "Webhook secret is not configured")
        body = await request.body()  # raw bytes: the signature covers them exactly
        if not verify_signature(s.settings.github_webhook_secret, body, request.headers.get("x-hub-signature-256")):
            raise HTTPException(401, "Invalid signature")
        event = request.headers.get("x-github-event", "")
        if event == "ping":
            return {"ok": True}
        try:
            payload = await request.json()
        except ValueError:
            raise HTTPException(400, "Invalid JSON")
        routed = target_from_event(event, payload)
        if routed is None:
            return {"ignored": True}
        target, installation_id = routed
        try:
            result = s.start(target, installation_id=installation_id)
        except GitHubError as e:
            raise HTTPException(e.status if e.status in (403, 404) else 502, str(e))
        return {"run_id": result.run_id}

    @app.post("/api/feedback", status_code=201)
    def feedback(request: Request, body: Feedback):
        s = svc(request)
        if s.db.get_run(body.run_id) is None:
            raise HTTPException(404, "Unknown run")
        s.db.add_feedback(body.run_id, body.claim_id, body.vote)
        return {"ok": True}

    @app.get("/api/health")
    def health(request: Request):
        s = svc(request)
        out = {"db": "ok", "pipeline_version": PIPELINE_VERSION}
        out["inference"] = ("cache-only" if getattr(s.gateway, "cache_only", False) else
                            "daily quota exhausted" if getattr(s.gateway, "quota_exhausted", False) else
                            "configured" if s.gateway else "not configured")
        out["inference_ready"] = bool(s.gateway and not getattr(s.gateway, "cache_only", False)
                                      and not getattr(s.gateway, "quota_exhausted", False))
        out["inference_retry_after_s"] = getattr(s.gateway, "quota_retry_after_s", 0)
        out["provider"] = getattr(s.gateway, "provider", "openai-compatible" if s.gateway else "none")
        out["model"] = s.gateway.model if s.gateway else ""
        if isinstance(s.gateway, GoogleGemmaGateway):
            try:
                r = s.gateway._http.get(f"{s.gateway.base_url}/models/{s.gateway.model}", timeout=3,
                                         headers={"x-goog-api-key": s.gateway.api_key})
                out["model_server"] = "ok" if r.status_code < 400 else f"error {r.status_code}"
            except httpx.HTTPError as e:
                out["model_server"] = f"unreachable ({e.__class__.__name__})"
        elif s.settings.llm_base_url:
            try:
                r = httpx.get(f"{s.settings.llm_base_url.rstrip('/')}/models", timeout=3,
                              headers={"Authorization": f"Bearer {s.settings.llm_api_key}"} if s.settings.llm_api_key else {})
                out["model_server"] = "ok" if r.status_code < 400 else f"error {r.status_code}"
            except httpx.HTTPError as e:
                out["model_server"] = f"unreachable ({e.__class__.__name__})"
        else:
            out["model_server"] = "not configured"
        try:
            rl = s.github.rate_limit().get("rate", {})
            out["github"] = f"ok ({rl.get('remaining')}/{rl.get('limit')} requests left)"
        except GitHubError as e:
            out["github"] = f"error: {e}"
        out["docker"] = "available" if shutil.which("docker") else "missing"
        out["sandbox"] = "on" if s.settings.enable_sandbox else "off"
        out["github_app"] = "configured" if s.app_auth else "not configured"
        return out

    return app


app = create_app()
