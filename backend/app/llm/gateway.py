"""The only path to a language model: timeouts, a concurrency cap, a response cache,
schema validation with one repair retry, and a log of every prompt."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..db import Database

T = TypeVar("T", bound=BaseModel)
PROMPTS_DIR = Path(__file__).parent / "prompts"


class LLMUnavailable(RuntimeError):
    pass


class LLMBadOutput(RuntimeError):
    pass


class LLMTooLarge(LLMUnavailable):
    """The request exceeds the server's token limit. `limit` is that limit when the server states it."""

    def __init__(self, message: str, limit: Optional[int] = None):
        super().__init__(message)
        self.limit = limit


MAX_RATE_LIMIT_WAIT_S = 65.0
MAX_RATE_LIMIT_RETRIES = 3
MAX_PACING_WAIT_S = 150.0
# Reply size requested per call type, capped by the configured maximum. Claim lists are short
# (observed replies: under ~2,200 characters, i.e. ~550 tokens), so reserving the full maximum only
# held later calls back under per-minute token caps. A cut-off reply is retried once at full size.
REPLY_TOKENS = {"extract": 2000, "extract_pr": 2000}


def _short(msg: str) -> str:
    """Server error text without account identifiers, short enough for a panel note."""
    msg = re.sub(r"\s+in organization `[^`]*`", "", msg)
    msg = re.sub(r"\s+service tier `[^`]*`", "", msg)
    return msg[:140]


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return str(err.get("message") or err)[:300]
    except ValueError:
        return resp.text[:300]


def retry_after_seconds(resp: httpx.Response) -> Optional[float]:
    """Retry-After may contain seconds or an HTTP date; never sleep on invalid values."""
    value = resp.headers.get("retry-after", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def estimate_tokens(text: str) -> int:
    """Rough count (about 4 characters per token for English and code)."""
    return len(text) // 4 + 1


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def parse_json_object(text: str) -> Any:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise json.JSONDecodeError("no JSON object found", text, 0)
    return json.loads(text[start:end + 1])


class Gateway(Protocol):
    model: str

    def complete_json(self, purpose: str, prompt_version: str, system: str, user: str, out_model: type[T],
                      run_id: Optional[str] = None, context: Optional[dict] = None) -> T: ...


class OpenAICompatGateway:
    cache_only = False

    def __init__(self, base_url: str, model: str, api_key: str = "", db: Optional[Database] = None,
                 timeout_s: float = 120.0, concurrency: int = 2, json_schema: str = "auto",
                 temperature: float = 0.2, max_tokens: int = 4000, reasoning_effort: str = "",
                 prompt_budget_tokens: int = 12000, tokens_per_minute: int = 0,
                 transport: Optional[httpx.BaseTransport] = None, quota_cooldown_s: float = 3600.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.db = db
        self.timeout_s = timeout_s
        self.json_schema = json_schema
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        # Callers size their prompts with this. Lowered automatically when the server reports a smaller limit.
        self.prompt_budget_tokens = prompt_budget_tokens
        self.tokens_per_minute = tokens_per_minute
        # Bookings [time, tokens] of recent requests: estimated when sent, corrected to the provider's
        # reported usage when the reply arrives.
        self._usage: list[list] = []
        self._pace_lock = threading.Lock()
        self._local = threading.local()  # per-call timing, kept per thread
        self._sem = threading.BoundedSemaphore(max(1, concurrency))
        self._schema_rejected = json_schema == "off"
        self.quota_cooldown_s = max(1.0, quota_cooldown_s)
        self._quota_retry_at = 0.0
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    @property
    def quota_exhausted(self) -> bool:
        return time.monotonic() < self._quota_retry_at

    @quota_exhausted.setter
    def quota_exhausted(self, value: bool) -> None:
        self._quota_retry_at = time.monotonic() + self.quota_cooldown_s if value else 0.0

    @property
    def quota_retry_after_s(self) -> int:
        return math.ceil(max(0.0, self._quota_retry_at - time.monotonic()))

    def _check_available(self) -> None:
        if self.cache_only:
            raise LLMUnavailable("Live inference disabled; no cached response matches this prompt")
        if self.quota_exhausted:
            raise LLMUnavailable(f"Model daily quota exhausted; retry eligible in {self.quota_retry_after_s}s; "
                                 "only cached responses are available")

    # --- timing: separates deliberate waiting from time spent inside the model call -------------
    def _stats(self) -> dict:
        stats = getattr(self._local, "stats", None)
        if stats is None:
            stats = self._local.stats = {"wait": 0.0, "model": 0.0, "tokens": None}
        return stats

    def _wait(self, seconds: float) -> None:
        """Every deliberate delay (pacing, rate-limit backoff) goes through here so it is logged."""
        if seconds > 0:
            time.sleep(seconds)
            self._stats()["wait"] += seconds

    def _acquire(self, what: str) -> None:
        t0 = time.monotonic()
        if not self._sem.acquire(timeout=self.timeout_s):
            raise LLMUnavailable(f"{what} is busy (concurrency limit reached)")
        self._stats()["wait"] += time.monotonic() - t0

    def _pace(self, tokens: int) -> Optional[list]:
        """Waits until this request fits the per-minute token budget (when one is known).
        Returns the booking so it can be corrected with actual usage afterwards."""
        if not self.tokens_per_minute:
            return None
        tokens = min(tokens, self.tokens_per_minute)
        deadline = time.monotonic() + MAX_PACING_WAIT_S
        while True:
            with self._pace_lock:
                now = time.monotonic()
                self._usage = [u for u in self._usage if now - u[0] < 60]
                used = sum(u[1] for u in self._usage)
                if used + tokens <= self.tokens_per_minute or now >= deadline:
                    booking = [now, tokens]
                    self._usage.append(booking)
                    return booking
                wait = 60 - (now - self._usage[0][0]) + 0.2
            self._wait(min(wait, max(0.1, deadline - time.monotonic())))

    def _settle(self, booking: Optional[list], used: Optional[int]) -> None:
        """Replace an estimated booking with the provider's reported usage. Reply tokens that were
        reserved but never generated stop holding the next call back; an underestimate is raised."""
        self._stats()["tokens"] = used
        if booking is not None and used is not None and used >= 0:
            with self._pace_lock:
                booking[1] = used

    def _reply_tokens(self, requested: Optional[int]) -> int:
        return min(requested or self.max_tokens, self.max_tokens)

    def _post(self, messages: list[dict], schema: Optional[dict], name: str, attempt: int = 0,
              reply_tokens: Optional[int] = None) -> str:
        self._check_available()
        if not self.base_url:
            raise LLMUnavailable("No model server configured (LLM_BASE_URL is empty)")
        max_tokens = self._reply_tokens(reply_tokens)
        prompt_tokens = sum(estimate_tokens(m["content"]) for m in messages)
        booking = self._pace(prompt_tokens + max_tokens)
        payload: dict[str, Any] = {"model": self.model, "messages": messages,
                                   "temperature": self.temperature, "max_tokens": max_tokens}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        use_schema = schema is not None and not self._schema_rejected
        if use_schema:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": name, "schema": schema, "strict": False}}
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        self._acquire("Model server")
        t0 = time.monotonic()
        try:
            # Another worker may have exhausted the shared quota while this request waited.
            self._check_available()
            resp = self._http.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Model server unreachable: {e.__class__.__name__}") from e
        finally:
            self._stats()["model"] += time.monotonic() - t0
            self._sem.release()
        if resp.status_code == 400 and use_schema and self.json_schema == "auto":
            self._schema_rejected = True  # server does not support json_schema; fall back to prompt-only JSON
            return self._post(messages, None, name, attempt, reply_tokens)
        if resp.status_code in (413, 429):
            msg = _error_message(resp)
            if resp.status_code == 429 and re.search(
                    r"tokens?[\s_-]*per[\s_-]*day|requests?[\s_-]*per[\s_-]*day|"
                    r"\bTPD\b|\bRPD\b|daily (?:cap|limit)|free-models-per-day", msg, re.I):
                wait = retry_after_seconds(resp)
                self._quota_retry_at = time.monotonic() + max(1.0, wait if wait is not None else self.quota_cooldown_s)
                raise LLMUnavailable(f"Model daily quota exhausted ({_short(msg)})")
            m = re.search(r"Limit (\d+)", msg)
            limit = int(m.group(1)) if m else None
            if limit and ("per minute" in msg.lower() or "tpm" in msg.lower()) and not self.tokens_per_minute:
                self.tokens_per_minute = limit  # learn the cap so later requests are paced, not rejected
            if resp.status_code == 413 or "too large" in msg.lower():
                if limit:
                    # Leave room for the reply inside the same limit, and remember it for later prompts.
                    self.max_tokens = min(self.max_tokens, max(800, limit // 4))
                    self.prompt_budget_tokens = min(self.prompt_budget_tokens,
                                                    max(1000, int(limit * 0.8) - self.max_tokens))
                raise LLMTooLarge(f"Request is larger than the model server allows ({_short(msg)})", limit)
            wait = retry_after_seconds(resp)
            if wait is None:
                wait = 20.0 if self.tokens_per_minute else None
            if attempt < MAX_RATE_LIMIT_RETRIES and wait is not None and wait <= MAX_RATE_LIMIT_WAIT_S:
                self._wait(wait + 0.5)
                return self._post(messages, schema, name, attempt + 1, reply_tokens)
            raise LLMUnavailable(f"Model server rate limit ({_short(msg)})")
        if resp.status_code >= 400:
            raise LLMUnavailable(f"Model server returned {resp.status_code}: {_error_message(resp)}")
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (KeyError, IndexError, ValueError, AttributeError) as e:
            raise LLMBadOutput("Unexpected model server response shape") from e
        usage = data.get("usage") or {}
        used = usage.get("total_tokens")
        if used is None and usage.get("prompt_tokens") is not None:
            used = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        self._settle(booking, used if used is not None else prompt_tokens + estimate_tokens(content))
        if choice.get("finish_reason") == "length" and max_tokens < self.max_tokens:
            # The smaller reply size was not enough: ask again with the full size rather than parse a fragment.
            return self._post(messages, schema, name, attempt, None)
        return content

    def complete_json(self, purpose: str, prompt_version: str, system: str, user: str, out_model: type[T],
                      run_id: Optional[str] = None, context: Optional[dict] = None) -> T:
        prompt_hash = hashlib.sha256(f"{self.model}|{prompt_version}|{system}|{user}".encode()).hexdigest()
        if self.db is not None:
            cached = self.db.cache_get(prompt_hash)
            if cached:
                try:
                    return out_model.model_validate(parse_json_object(cached))
                except (ValidationError, json.JSONDecodeError):
                    pass

        schema = out_model.model_json_schema()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        t0 = time.monotonic()
        self._local.stats = {"wait": 0.0, "model": 0.0, "tokens": None}
        content, ok = "", False
        try:
            content = self._post(messages, schema, purpose, reply_tokens=REPLY_TOKENS.get(purpose))
            try:
                result = out_model.model_validate(parse_json_object(content))
            except (ValidationError, json.JSONDecodeError) as err:
                repair = messages + [
                    {"role": "assistant", "content": content[:6000]},
                    {"role": "user", "content": "That reply was not valid JSON for the required schema "
                                                f"({str(err)[:400]}). Reply with only the corrected JSON object."},
                ]
                content = self._post(repair, schema, purpose)
                try:
                    result = out_model.model_validate(parse_json_object(content))
                except (ValidationError, json.JSONDecodeError) as err2:
                    raise LLMBadOutput(f"Model output failed validation twice: {str(err2)[:200]}") from err2
            ok = True
            if self.db is not None:
                self.db.cache_put(prompt_hash, content)
            return result
        finally:
            if self.db is not None:
                stats = self._stats()
                self.db.log_llm_call(run_id=run_id, purpose=purpose, model=self.model, prompt_version=prompt_version,
                                     prompt_hash=prompt_hash, prompt_text=f"{system}\n\n---\n\n{user}",
                                     response_text=content[:20000], ok=int(ok), ms=int((time.monotonic() - t0) * 1000),
                                     wait_ms=int(stats["wait"] * 1000), model_ms=int(stats["model"] * 1000),
                                     tokens_used=stats["tokens"])


class CachedGateway(OpenAICompatGateway):
    """Use validated response-cache hits only; cache misses cannot reach the network."""
    cache_only = True


class FakeGateway:
    """Scripted gateway for offline tests: purpose -> dict, or callable(context) -> dict."""

    def __init__(self, responses: dict[str, Any], db: Optional[Database] = None, model: str = "fake-model",
                 prompt_budget_tokens: int = 12000):
        self.responses = responses
        self.db = db
        self.model = model
        self.prompt_budget_tokens = prompt_budget_tokens
        self.calls: list[dict] = []

    def complete_json(self, purpose: str, prompt_version: str, system: str, user: str, out_model: type[T],
                      run_id: Optional[str] = None, context: Optional[dict] = None) -> T:
        self.calls.append({"purpose": purpose, "system": system, "user": user, "context": context})
        if self.db is not None:
            self.db.log_llm_call(run_id=run_id, purpose=purpose, model=self.model, prompt_version=prompt_version,
                                 prompt_hash="fake", prompt_text=f"{system}\n\n---\n\n{user}", response_text="",
                                 ok=1, ms=0)
        resp = self.responses.get(purpose)
        if resp is None:
            raise LLMUnavailable(f"No scripted response for {purpose}")
        if isinstance(resp, list):  # a sequence of responses, one per call
            resp = resp.pop(0) if len(resp) > 1 else resp[0]
        if isinstance(resp, Exception):
            raise resp
        data = resp(context or {}) if callable(resp) else resp
        return out_model.model_validate(data)


GatewayFactory = Callable[[], Gateway]
