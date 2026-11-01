"""Runtime settings, read from environment variables or backend/.env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Bump whenever prompts or rules change, so cached results are not reused.
PIPELINE_VERSION = "30"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    # Model server (OpenAI-compatible chat completions endpoint)
    llm_base_url: str = ""
    llm_provider: Literal["auto", "google", "openai"] = "auto"
    llm_cache_only: bool = False
    llm_api_key: str = ""
    llm_model: str = "openai/gpt-oss-20b"
    llm_timeout_s: float = 120.0
    llm_concurrency: int = 2
    llm_json_schema: str = "auto"  # auto | on | off
    llm_temperature: float = 0.2
    llm_max_tokens: int = 2500  # counts against per-minute token caps; enough for the JSON replies
    # Upper bound for the evidence prompt. Shrunk automatically if the server reports a smaller limit.
    llm_prompt_budget_tokens: int = 12000
    # Optional: "low" | "medium" | "high" for gpt-oss on servers that accept reasoning_effort.
    llm_reasoning_effort: str = ""
    # Client-side pacing for servers with a tokens-per-minute cap (Groq free tier: 8000). 0 = learn it
    # from the server's first rate-limit message.
    llm_tokens_per_minute: int = 0
    # Used only when a daily-quota response does not provide Retry-After.
    llm_quota_cooldown_s: float = 3600.0

    # With provider=auto, a Google key selects hosted open-weight Gemma, even
    # when a previous OpenAI-compatible endpoint remains in the environment.
    google_api_key: SecretStr = SecretStr("")
    google_model: str = "gemma-4-26b-a4b-it"
    google_prompt_budget_tokens: int = 7000
    google_max_output_tokens: int = 3500
    google_tokens_per_minute: int = 14000

    # GitHub
    github_api: str = "https://api.github.com"
    github_token: str = ""
    github_app_id: str = ""
    github_app_private_key_path: str = ""
    github_webhook_secret: str = ""

    # Storage
    data_dir: Path = BACKEND_DIR / ".data"

    # Features
    enable_embeddings: bool = False
    enable_sandbox: bool = False
    sandbox_allowlist: Path = BACKEND_DIR / "sandbox_allowlist.json"
    sandbox_image: str = "python:3.11-slim"

    # Budgets
    git_timeout_s: float = 120.0
    # Repos larger than this (GitHub's size, in KB) are checked out sparsely: source and docs files only.
    sparse_checkout_over_kb: int = 150_000
    run_timeout_s: float = 300.0
    job_workers: int = 2

    # Test hook: map "owner/repo" to a local clone URL (used by fixtures)
    clone_url_overrides: dict[str, str] = {}

    cors_origins: list[str] = ["*"]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "triage.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


@lru_cache
def get_settings() -> Settings:
    return Settings()
