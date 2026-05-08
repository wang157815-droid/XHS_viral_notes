"""
ProviderRegistry：Provider 密钥/端点配置层。

- 从环境变量加载已有配置（兼容旧项目）。
- 运行期可通过注册接口追加/覆盖。
- 不包含参数模板（参数归 ModelProfile 管理）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import RLock
from typing import Dict, Optional


@dataclass
class ProviderConfig:
    """Provider 级配置：仅描述 `在哪调用` 和 `用谁的密钥`。"""

    name: str
    base_url: str
    api_key: str
    kind: str = "openai_compatible"
    extra: Dict[str, str] = field(default_factory=dict)

    def is_usable(self) -> bool:
        return bool(self.base_url) and bool(self.api_key) and not self.api_key.startswith("sk-your")


def _provider_configs_from_current_environ() -> list[ProviderConfig]:
    """从当前进程环境变量构造默认 Provider 列表（与启动时逻辑一致）。"""
    out: list[ProviderConfig] = []
    text_base = os.getenv("OPENAI_API_BASE", "").strip()
    text_key = os.getenv("OPENAI_API_KEY", "").strip()
    if text_base and text_key:
        out.append(
            ProviderConfig(
                name="default_text",
                base_url=text_base,
                api_key=text_key,
            )
        )

    mm_base = os.getenv("MULTIMODAL_API_BASE", "").strip()
    mm_key = os.getenv("MULTIMODAL_API_KEY", "").strip()
    if mm_base and mm_key:
        out.append(
            ProviderConfig(
                name="default_multimodal",
                base_url=mm_base,
                api_key=mm_key,
            )
        )

    emb_base = os.getenv("EMBEDDING_API_BASE", "").strip() or text_base
    emb_key = os.getenv("EMBEDDING_API_KEY", "").strip() or text_key
    if emb_base and emb_key:
        out.append(
            ProviderConfig(
                name="default_embedding",
                base_url=emb_base,
                api_key=emb_key,
            )
        )
    return out


class ProviderRegistry:
    """线程安全的 Provider 注册表。"""

    def __init__(self) -> None:
        self._providers: Dict[str, ProviderConfig] = {}
        self._lock = RLock()
        self._bootstrap_from_env()

    def _bootstrap_from_env(self) -> None:
        for cfg in _provider_configs_from_current_environ():
            self.register(cfg)

    def refresh_defaults_from_env(self, *, reload_dotenv_file: bool = False) -> None:
        """用当前环境变量覆盖 default_* Provider（解决改密钥后进程仍用旧值的问题）。

        - 默认只读 ``os.environ``（适合 Docker/systemd 注入新变量、或本机 export 后无需重启）。
        - 若 ``LLM_RELOAD_DOTENV=true`` 或 ``reload_dotenv_file=True``，会先 ``load_dotenv(override=True)``
          再读取（适合只改项目根目录 ``.env`` 且未重启进程的场景）。
        """
        if reload_dotenv_file or os.getenv("LLM_RELOAD_DOTENV", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            try:
                from dotenv import load_dotenv

                path = (os.getenv("DOTENV_PATH") or "").strip()
                if path:
                    load_dotenv(path, override=True)
                else:
                    load_dotenv(override=True)
            except Exception:
                pass

        with self._lock:
            for cfg in _provider_configs_from_current_environ():
                self._providers[cfg.name] = cfg

    def register(self, provider: ProviderConfig) -> None:
        with self._lock:
            self._providers[provider.name] = provider

    def get(self, name: str) -> Optional[ProviderConfig]:
        with self._lock:
            return self._providers.get(name)

    def require(self, name: str) -> ProviderConfig:
        provider = self.get(name)
        if not provider:
            raise KeyError(f"Provider 未注册: {name}")
        if not provider.is_usable():
            raise RuntimeError(f"Provider 可用性校验失败: {name}（base_url/api_key 缺失）")
        return provider

    def list_names(self) -> list[str]:
        with self._lock:
            return sorted(self._providers.keys())


provider_registry = ProviderRegistry()
