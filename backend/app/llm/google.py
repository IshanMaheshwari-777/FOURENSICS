"""Native Google generateContent transport for hosted open-weight Gemma.

Shares validated caching, repair, concurrency and pacing with the other gateway.
Credentials travel only in a header. Gemma gets a single user prompt, avoiding
unsupported system-instruction/JSON-schema API features in some Gemma releases.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional
from urllib.parse import quote

import httpx

from .gateway import (LLMBadOutput, LLMTooLarge, LLMUnavailable, MAX_RATE_LIMIT_RETRIES,
                      MAX_RATE_LIMIT_WAIT_S, OpenAICompatGateway, estimate_tokens, retry_after_seconds)

GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GoogleGemmaGateway(OpenAICompatGateway):
    provider = "google"

    def __init__(self, model: str, api_key: str, **kwargs):
        if not re.fullmatch(r"gemma-[\w.-]+", model):
            raise ValueError("GOOGLE_MODEL must name a hosted open-weight Gemma model (gemma-...).")
        super().__init__(GOOGLE_BASE_URL, model, api_key, **kwargs)

    def _post(self, messages: list[dict], schema: Optional[dict], name: str, attempt: int = 0,
              reply_tokens: Optional[int] = None) -> str:
        self._check_available()
        if not self.api_key:
            raise LLMUnavailable("GOOGLE_API_KEY is not configured")
        text = "\n\n".join(f"{m['role'].upper()}:\n{m['content']}" for m in messages)
        if schema:
            text += "\n\nReturn only a JSON object matching this schema:\n" + json.dumps(schema)
        max_tokens = self._reply_tokens(reply_tokens)
        prompt_tokens = estimate_tokens(text)
        booking = self._pace(prompt_tokens + max_tokens)
        payload = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                   "generationConfig": {"temperature": self.temperature, "maxOutputTokens": max_tokens}}
        # Gemma 4's off switch leaves the output budget for the requested JSON.
        if self.model.startswith("gemma-4-"):
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "minimal"}
        self._acquire("Google model")
        t0 = time.monotonic()
        try:
            self._check_available()
            response = self._http.post(f"{self.base_url}/models/{quote(self.model, safe='')}:generateContent",
                                       headers={"x-goog-api-key": self.api_key}, json=payload)
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Google model unreachable: {type(e).__name__}") from e
        finally:
            self._stats()["model"] += time.monotonic() - t0
            self._sem.release()
        try:
            data = response.json()
        except ValueError:
            raise LLMBadOutput(f"Google returned non-JSON HTTP {response.status_code}") from None
        if response.status_code >= 400:
            error = data.get("error", {})
            # Never propagate an echoed credential or account identifier into a run.
            message = str(error.get("message", "")).replace(self.api_key, "[redacted]")
            message = re.sub(r"AIza[\w-]+|projects/[^\s,]+", "[redacted]", message)
            details = error.get("details") or []
            violations = [v for d in details for v in d.get("violations", [])]
            metrics = " ".join(str(v.get("quotaMetric", "")) + " " + str(v.get("quotaId", "")) for v in violations)
            wait = retry_after_seconds(response)
            for detail in details:
                retry = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(detail.get("retryDelay", "")))
                if wait is None and retry:
                    wait = float(retry[1])
            daily = bool(re.search(r"per[\s_-]*day|daily", message + metrics, re.I))
            zero_quota = any(str(v.get("quotaValue")) == "0" for v in violations) or "limit: 0" in message.lower()
            if response.status_code == 429 and (daily or zero_quota):
                self._quota_retry_at = time.monotonic() + max(1, wait if wait is not None else self.quota_cooldown_s)
                raise LLMUnavailable("Google model daily quota exhausted or free quota unavailable; check AI Studio limits.")
            if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
                wait = wait if wait is not None else 2 ** attempt
                if attempt < MAX_RATE_LIMIT_RETRIES and wait <= MAX_RATE_LIMIT_WAIT_S:
                    self._wait(wait + 0.25)
                    return self._post(messages, schema, name, attempt + 1, reply_tokens)
            if response.status_code == 413 or (response.status_code == 400 and
                    re.search(r"token.*(?:exceed|limit)|exceed.*token|input.*too long", message, re.I)):
                self.prompt_budget_tokens = max(1000, self.prompt_budget_tokens // 2)
                raise LLMTooLarge("Google input exceeds the model limit; retrying with a smaller evidence prompt")
            if response.status_code in (401, 403):
                raise LLMUnavailable(f"Google access denied (HTTP {response.status_code}); check key and model access.")
            raise LLMUnavailable(f"Google model returned HTTP {response.status_code}: {message[:180]}")
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMBadOutput("Google returned no candidate; the response may have been blocked.")
        candidate = candidates[0]
        finish = candidate.get("finishReason", "")
        usage = data.get("usageMetadata") or {}
        used = usage.get("totalTokenCount")
        if used is None and usage.get("promptTokenCount") is not None:
            used = sum(int(usage.get(k) or 0) for k in
                       ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount"))
        reply = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
        self._settle(booking, used if used is not None else prompt_tokens + estimate_tokens(reply))
        if finish == "MAX_TOKENS" and max_tokens < self.max_tokens:
            # The smaller reply size was not enough: ask again with the full size rather than fail.
            return self._post(messages, schema, name, attempt, None)
        if finish not in ("STOP", ""):
            raise LLMBadOutput(f"Google output incomplete or blocked (finish reason: {finish})")
        return reply
