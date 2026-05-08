"""ProviderRegistry：环境变量密钥刷新（改 OPENAI_API_KEY 后无需依赖旧快照）。"""

from __future__ import annotations

import pytest

from backend.app.llm.provider_registry import ProviderRegistry


@pytest.fixture
def clear_openai_env(monkeypatch):
    for k in (
        "OPENAI_API_BASE",
        "OPENAI_API_KEY",
        "MULTIMODAL_API_BASE",
        "MULTIMODAL_API_KEY",
        "EMBEDDING_API_BASE",
        "EMBEDDING_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)


def test_refresh_defaults_from_env_overwrites_api_key(monkeypatch, clear_openai_env):
    monkeypatch.setenv("OPENAI_API_BASE", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-old")
    reg = ProviderRegistry()
    assert reg.require("default_text").api_key == "sk-old"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-new")
    reg.refresh_defaults_from_env()
    assert reg.require("default_text").api_key == "sk-new"
