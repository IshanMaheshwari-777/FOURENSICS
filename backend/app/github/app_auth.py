"""GitHub App authentication: App JWT -> short-lived installation token (cached)."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import jwt


class AppAuth:
    def __init__(self, app_id: str, private_key_path: str, api: str = "https://api.github.com"):
        self.app_id = app_id
        self.private_key = Path(private_key_path).read_text()
        self.api = api.rstrip("/")
        self._tokens: dict[int, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def app_jwt(self) -> str:
        now = int(time.time())
        # Backdate iat to tolerate clock drift; GitHub caps exp at 10 minutes.
        payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": self.app_id}
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def installation_token(self, installation_id: int) -> str:
        with self._lock:
            cached = self._tokens.get(installation_id)
            if cached and cached[1] - time.time() > 120:
                return cached[0]
        resp = httpx.post(
            f"{self.api}/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self.app_jwt()}", "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        resp.raise_for_status()
        token = resp.json()["token"]
        with self._lock:
            # Installation tokens last one hour.
            self._tokens[installation_id] = (token, time.time() + 3600)
        return token
