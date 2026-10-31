"""Read-only GitHub REST/GraphQL client with an ETag disk cache and rate-limit backoff."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

MAX_BACKOFF_S = 10.0


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimited(GitHubError):
    pass


def public_repo(gh, owner: str, repo: str) -> dict:
    """Fail closed before reading issue/PR content from an authenticated account."""
    info = gh.repo(owner, repo)
    if info.get("private") is not False:
        raise GitHubError("FOURENSICS analyzes public repositories only", 403)
    return info


class GitHubClient:
    def __init__(
        self,
        api: str = "https://api.github.com",
        token: str = "",
        cache_dir: Optional[Path] = None,
        token_provider: Optional[Callable[[], str]] = None,
        transport: Optional[httpx.BaseTransport] = None,
        timeout_s: float = 20.0,
    ):
        self.api = api.rstrip("/")
        self._token = token
        self._token_provider = token_provider
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(timeout=timeout_s, transport=transport, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
             "User-Agent": "evidence-triage"}
        token = self._token_provider() if self._token_provider else self._token
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def _cache_path(self, url: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")

    def get(self, path: str, params: Optional[dict] = None) -> Any:
        url = path if path.startswith("http") else f"{self.api}{path}"
        if params:
            url = str(httpx.URL(url, params=params))
        cache_file = self._cache_path(url)
        cached = None
        headers = self._headers()
        if cache_file and cache_file.exists():
            cached = json.loads(cache_file.read_text())
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]

        for attempt in range(3):
            try:
                resp = self._http.get(url, headers=headers)
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise GitHubError(f"GitHub unreachable: {e}") from e
                time.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code == 304 and cached is not None:
                return cached["body"]
            if resp.status_code in (403, 429) and self._is_rate_limited(resp):
                wait = self._retry_wait(resp)
                if wait is None or wait > MAX_BACKOFF_S or attempt == 2:
                    raise RateLimited("GitHub rate limit reached. Add GITHUB_TOKEN or try again later.", resp.status_code)
                time.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            if resp.status_code == 404:
                raise GitHubError("Not found on GitHub (private repo, or wrong URL)", 404)
            if resp.status_code == 401:
                raise GitHubError("GitHub rejected the token (401)", 401)
            if resp.status_code >= 400:
                raise GitHubError(f"GitHub returned {resp.status_code}", resp.status_code)
            body = resp.json()
            if cache_file and resp.headers.get("etag"):
                cache_file.write_text(json.dumps({"etag": resp.headers["etag"], "body": body}))
            return body
        raise GitHubError("GitHub request failed")

    @staticmethod
    def _is_rate_limited(resp: httpx.Response) -> bool:
        return resp.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in resp.headers or resp.status_code == 429

    @staticmethod
    def _retry_wait(resp: httpx.Response) -> Optional[float]:
        if "retry-after" in resp.headers:
            try:
                return float(resp.headers["retry-after"])
            except ValueError:
                return None
        reset = resp.headers.get("x-ratelimit-reset")
        if reset:
            return max(0.0, float(reset) - time.time())
        return None

    def graphql(self, query: str, variables: dict) -> Any:
        resp = self._http.post(f"{self.api}/graphql", headers=self._headers(),
                               json={"query": query, "variables": variables})
        if resp.status_code >= 400:
            raise GitHubError(f"GraphQL returned {resp.status_code}", resp.status_code)
        data = resp.json()
        if data.get("errors"):
            raise GitHubError(f"GraphQL error: {data['errors'][0].get('message')}")
        return data["data"]

    # Convenience wrappers. Only fields the firewall allows are used downstream.
    def repo(self, owner: str, repo: str) -> dict:
        return self.get(f"/repos/{owner}/{repo}")

    def issue(self, owner: str, repo: str, number: int) -> dict:
        return self.get(f"/repos/{owner}/{repo}/issues/{number}")

    def pull(self, owner: str, repo: str, number: int) -> dict:
        return self.get(f"/repos/{owner}/{repo}/pulls/{number}")

    def issue_comments(self, owner: str, repo: str, number: int, max_pages: int = 3) -> list[dict]:
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            batch = self.get(f"/repos/{owner}/{repo}/issues/{number}/comments", {"per_page": 100, "page": page})
            out.extend(batch)
            if len(batch) < 100:
                break
        return out

    def check_runs(self, owner: str, repo: str, sha: str) -> list[dict]:
        data = self.get(f"/repos/{owner}/{repo}/commits/{sha}/check-runs", {"per_page": 100})
        return data.get("check_runs", [])

    def rate_limit(self) -> dict:
        return self.get("/rate_limit")
