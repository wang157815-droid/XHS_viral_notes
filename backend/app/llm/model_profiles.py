"""
ModelProfile：模型档案（参数模板）。

一个档案描述：
- 使用哪个 Provider
- 模型名（model_name）
- 模态（text / multimodal / embedding）
- 默认参数（temperature / max_tokens / timeout / retries）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, List, Optional


@dataclass
class ModelProfile:
    profile_id: str
    provider: str
    model_name: str
    modality: str = "text"
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_seconds: int = 60
    max_retries: int = 2
    extra_params: Dict[str, Any] = field(default_factory=dict)
    # 备用 profile 列表：主供应商失败时依次尝试（仅 chat/multimodal 生效）
    fallback_profile_ids: List[str] = field(default_factory=list)


class ModelProfileRegistry:
    def __init__(self) -> None:
        self._profiles: Dict[str, ModelProfile] = {}
        self._lock = RLock()
        self._bootstrap_defaults()

    def _bootstrap_defaults(self) -> None:
        text_model = os.getenv("MODEL_NAME", "deepseek-chat").strip() or "deepseek-chat"
        self.register(
            ModelProfile(
                profile_id="text_default",
                provider="default_text",
                model_name=text_model,
                modality="text",
                temperature=0.6,
                max_tokens=8192,
                timeout_seconds=int(os.getenv("REQUEST_TIMEOUT", "300")),
                max_retries=int(os.getenv("MAX_RETRIES", "2")),
            )
        )

        multimodal_model = os.getenv("MULTIMODAL_MODEL_NAME", "qwen3-vl-plus").strip() or "qwen3-vl-plus"

        # Xiaomi MiMo 备用多模态 profile（仅在配置了 MULTIMODAL_FALLBACK_* 时注册）
        xiaomi_base = os.getenv("MULTIMODAL_FALLBACK_API_BASE", "").strip()
        xiaomi_key = os.getenv("MULTIMODAL_FALLBACK_API_KEY", "").strip()
        # 默认使用 mimo-v2.5：API 平台唯一支持图/音/视频多模态的模型
        # mimo-v2.5-pro 为纯文本模型，MiMo-VL-7B-RL 为 HuggingFace 本地模型，均不适用
        xiaomi_model = os.getenv("MULTIMODAL_FALLBACK_MODEL_NAME", "mimo-v2.5").strip() or "mimo-v2.5"
        fallback_ids: List[str] = []
        if xiaomi_base and xiaomi_key:
            self.register(
                ModelProfile(
                    profile_id="multimodal_xiaomi",
                    provider="xiaomi_multimodal",
                    model_name=xiaomi_model,
                    modality="multimodal",
                    temperature=0.3,
                    max_tokens=2048,
                    timeout_seconds=120,
                    max_retries=1,
                    # MiMo 关闭 thinking 的方式与 Qwen 不同：
                    # Qwen 用 extra_body={"enable_thinking": False}（DashScope 专有）
                    # MiMo 用 extra_body={"thinking": {"type": "disabled"}}（官方文档要求）
                    # 注意：mimo-v2.5-pro/v2.5 的 thinking 默认开启，关闭后可自定义 temperature
                    extra_params={"extra_body": {"thinking": {"type": "disabled"}}},
                )
            )
            fallback_ids = ["multimodal_xiaomi"]

        self.register(
            ModelProfile(
                profile_id="multimodal_default",
                provider="default_multimodal",
                model_name=multimodal_model,
                modality="multimodal",
                temperature=0.3,
                max_tokens=2048,
                timeout_seconds=120,
                max_retries=1,
                # qwen3 系列默认开启 thinking 模式,但 thinking 模式:
                # 1) 必须 stream=True(本项目用非流式调用)
                # 2) 与 response_format=json_object 不兼容
                # 因此显式关闭 thinking,保证 JSON 结构化输出正常工作
                extra_params={"extra_body": {"enable_thinking": False}},
                fallback_profile_ids=fallback_ids,
            )
        )

        embedding_model = os.getenv("EMBEDDING_MODEL", "text-embedding-v4").strip() or "text-embedding-v4"
        self.register(
            ModelProfile(
                profile_id="embedding_default",
                provider="default_embedding",
                model_name=embedding_model,
                modality="embedding",
                temperature=0.0,
                max_tokens=0,
                timeout_seconds=30,
                max_retries=3,
            )
        )

    def register(self, profile: ModelProfile) -> None:
        with self._lock:
            self._profiles[profile.profile_id] = profile

    def get(self, profile_id: str) -> Optional[ModelProfile]:
        with self._lock:
            return self._profiles.get(profile_id)

    def require(self, profile_id: str) -> ModelProfile:
        profile = self.get(profile_id)
        if not profile:
            raise KeyError(f"ModelProfile 未注册: {profile_id}")
        return profile

    def list_profiles(self) -> Dict[str, ModelProfile]:
        with self._lock:
            return dict(self._profiles)


model_profile_registry = ModelProfileRegistry()
