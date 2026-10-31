"""One provider selection path for the API, evaluation and demos."""
from ..config import Settings
from ..db import Database
from .gateway import CachedGateway, OpenAICompatGateway
from .google import GoogleGemmaGateway


def make_gateway(settings: Settings, db: Database):
    google = settings.llm_provider == "google" or (
        settings.llm_provider == "auto" and bool(settings.google_api_key.get_secret_value()))
    if google:
        gateway = GoogleGemmaGateway(settings.google_model, settings.google_api_key.get_secret_value(), db=db,
            timeout_s=settings.llm_timeout_s, concurrency=settings.llm_concurrency, temperature=settings.llm_temperature,
            max_tokens=settings.google_max_output_tokens, prompt_budget_tokens=settings.google_prompt_budget_tokens,
            tokens_per_minute=settings.google_tokens_per_minute, quota_cooldown_s=settings.llm_quota_cooldown_s)
        gateway.cache_only = settings.llm_cache_only
        return gateway
    if not settings.llm_base_url and not settings.llm_cache_only:
        return None
    return (CachedGateway if settings.llm_cache_only else OpenAICompatGateway)(
        settings.llm_base_url, settings.llm_model, settings.llm_api_key, db,
        settings.llm_timeout_s, settings.llm_concurrency, settings.llm_json_schema,
        settings.llm_temperature, settings.llm_max_tokens, settings.llm_reasoning_effort,
        settings.llm_prompt_budget_tokens, settings.llm_tokens_per_minute,
        quota_cooldown_s=settings.llm_quota_cooldown_s)
