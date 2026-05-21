import os

from pydantic import BaseModel


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    app_name: str = "RedMuse API"
    app_version: str = "0.1.0-phase0"
    api_prefix: str = "/api/v1"
    conversation_os_enabled: bool = _env_bool("CONVERSATION_OS_ENABLED", True)
    conversation_knowledge_qa_enabled: bool = _env_bool("CONVERSATION_KNOWLEDGE_QA_ENABLED", True)
    serp_cache_days: int = int(os.getenv("SERP_CACHE_DAYS", "90"))


settings = Settings()

