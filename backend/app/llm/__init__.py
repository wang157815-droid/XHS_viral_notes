"""
ModelGateway 模块：统一大模型调用入口。

阶段2原则：
- 业务层/Agent 禁止直连 OpenAI/SDK，必须经过 ModelGateway。
- 配置分层：ProviderConfig / ModelProfile / AgentModelPolicy。
- 错误码统一归一到 MODEL_*（见 backend/app/domain/error_codes.py）。
"""

from .provider_registry import ProviderConfig, ProviderRegistry, provider_registry
from .model_profiles import ModelProfile, ModelProfileRegistry, model_profile_registry
from .agent_model_policy import AgentModelPolicy, agent_model_policy
from .model_gateway import ModelGateway, ModelInvocationError, model_gateway

__all__ = [
    "ProviderConfig",
    "ProviderRegistry",
    "provider_registry",
    "ModelProfile",
    "ModelProfileRegistry",
    "model_profile_registry",
    "AgentModelPolicy",
    "agent_model_policy",
    "ModelGateway",
    "ModelInvocationError",
    "model_gateway",
]
