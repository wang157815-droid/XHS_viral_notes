"""
ModelGateway：统一大模型调用入口。

特性：
- Agent 只传 agent_id + 请求内容，不关心 provider/model。
- 内部：AgentModelPolicy -> ModelProfile -> ProviderConfig -> 实际调用。
- 统一异常 ModelInvocationError，错误码对齐 MODEL_*。
- 内置审计记录（task_id / agent / profile / model / 耗时 / token 估算）。
- 大模型 SDK 延迟加载（避免 backend 启动依赖 openai 包）。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from ..core.tracing import get_trace_id
from ..infrastructure.db import TaskMetricRecord, metrics_store
from .agent_model_policy import agent_model_policy as default_policy, AgentModelPolicy
from .model_profiles import ModelProfile, model_profile_registry as default_profile_registry, ModelProfileRegistry
from .provider_registry import ProviderConfig, provider_registry as default_provider_registry, ProviderRegistry


class ModelInvocationError(RuntimeError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class ModelAuditRecord:
    task_id: Optional[str]
    trace_id: str
    agent_id: str
    profile_id: str
    model_name: str
    provider: str
    modality: str
    duration_ms: int
    input_tokens_estimate: int
    output_tokens_estimate: int
    ok: bool
    error_code: Optional[str] = None


@dataclass
class ModelGatewayConfig:
    policy: AgentModelPolicy = field(default_factory=lambda: default_policy)
    profiles: ModelProfileRegistry = field(default_factory=lambda: default_profile_registry)
    providers: ProviderRegistry = field(default_factory=lambda: default_provider_registry)


class ModelGateway:
    def __init__(self, config: Optional[ModelGatewayConfig] = None) -> None:
        cfg = config or ModelGatewayConfig()
        self.policy = cfg.policy
        self.profiles = cfg.profiles
        self.providers = cfg.providers
        self._audit_log: List[ModelAuditRecord] = []

    async def chat(
        self,
        agent_id: str,
        messages: List[Dict[str, Any]],
        *,
        modality: str = "text",
        task_id: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        通用 chat 调用。

        messages 约定 OpenAI Chat Completion 格式：
        [{"role": "user", "content": "..."}]
        """
        profile, provider = self._resolve(agent_id, modality)
        params = self._merge_params(profile, overrides)

        start = time.monotonic()
        try:
            content, usage = await self._invoke_chat(provider, profile, messages, params)
            duration_ms = int((time.monotonic() - start) * 1000)
            self._record_audit(
                task_id=task_id,
                agent_id=agent_id,
                profile=profile,
                provider=provider,
                duration_ms=duration_ms,
                input_tokens=usage.get("prompt_tokens", self._estimate_tokens_from_messages(messages)),
                output_tokens=usage.get("completion_tokens", self._estimate_tokens(content)),
                ok=True,
            )
            return {
                "content": content,
                "profile_id": profile.profile_id,
                "model_name": profile.model_name,
                "provider": profile.provider,
                "duration_ms": duration_ms,
                "usage": usage,
            }
        except ModelInvocationError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._record_audit(
                task_id=task_id,
                agent_id=agent_id,
                profile=profile,
                provider=provider,
                duration_ms=duration_ms,
                input_tokens=self._estimate_tokens_from_messages(messages),
                output_tokens=0,
                ok=False,
                error_code=exc.code,
            )
            raise

    async def chat_with_tools(
        self,
        agent_id: str,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]],
        tool_choice: str | Dict[str, Any] = "auto",
        task_id: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Chat completion with OpenAI-compatible tool calling.

        If a provider rejects tool parameters, fall back to a JSON-only prompt so
        conversation routing remains usable across DeepSeek/Qwen/GLM compatible APIs.
        """

        profile, provider = self._resolve(agent_id, "text")
        params = self._merge_params(profile, overrides)
        start = time.monotonic()
        try:
            content, tool_calls, usage = await self._invoke_chat_with_tools(
                provider,
                profile,
                messages,
                tools,
                tool_choice,
                params,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            self._record_audit(
                task_id=task_id,
                agent_id=agent_id,
                profile=profile,
                provider=provider,
                duration_ms=duration_ms,
                input_tokens=usage.get("prompt_tokens", self._estimate_tokens_from_messages(messages)),
                output_tokens=usage.get("completion_tokens", self._estimate_tokens(content)),
                ok=True,
            )
            return {
                "content": content,
                "tool_calls": tool_calls,
                "profile_id": profile.profile_id,
                "model_name": profile.model_name,
                "provider": profile.provider,
                "duration_ms": duration_ms,
                "usage": usage,
            }
        except ModelInvocationError as exc:
            logger.warning("Tool calling failed, falling back to JSON routing: {} {}", exc.code, exc)
            return await self._chat_with_tools_json_fallback(
                agent_id,
                messages,
                tools=tools,
                task_id=task_id,
                overrides=overrides,
                original_error=exc,
            )

    async def chat_stream(
        self,
        agent_id: str,
        messages: List[Dict[str, Any]],
        *,
        modality: str = "text",
        task_id: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield text chunks for user-visible conversation answers."""

        profile, provider = self._resolve(agent_id, modality)
        params = self._merge_params(profile, overrides)
        message_id = f"msg_{uuid4().hex}"
        yield {"type": "message_start", "message_id": message_id}
        start = time.monotonic()
        content_parts: List[str] = []
        input_tokens = self._estimate_tokens_from_messages(messages)
        try:
            async for delta in self._invoke_chat_stream(provider, profile, messages, params):
                if not delta:
                    continue
                content_parts.append(delta)
                yield {"type": "message_delta", "message_id": message_id, "delta": delta}
            content = "".join(content_parts).strip()
            duration_ms = int((time.monotonic() - start) * 1000)
            self._record_audit(
                task_id=task_id,
                agent_id=agent_id,
                profile=profile,
                provider=provider,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=self._estimate_tokens(content),
                ok=True,
            )
            yield {"type": "message_done", "message_id": message_id, "content": content}
        except ModelInvocationError as exc:
            logger.warning("Streaming chat failed, falling back to non-stream chat: {} {}", exc.code, exc)
            try:
                fallback = await self.chat(
                    agent_id,
                    messages,
                    modality=modality,
                    task_id=task_id,
                    overrides=overrides,
                )
                content = str(fallback.get("content") or "").strip()
                if content:
                    yield {"type": "message_delta", "message_id": message_id, "delta": content}
                yield {"type": "message_done", "message_id": message_id, "content": content}
            except ModelInvocationError as final_exc:
                yield {
                    "type": "message_error",
                    "message_id": message_id,
                    "code": final_exc.code,
                    "message": str(final_exc),
                }

    async def embed(
        self,
        agent_id: str,
        texts: List[str],
        *,
        task_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        profile, provider = self._resolve(agent_id, "embedding")
        start = time.monotonic()
        try:
            vectors = await self._invoke_embedding(provider, profile, texts)
            duration_ms = int((time.monotonic() - start) * 1000)
            input_tokens = sum(self._estimate_tokens(t) for t in texts)
            self._record_audit(
                task_id=task_id,
                agent_id=agent_id,
                profile=profile,
                provider=provider,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=0,
                ok=True,
            )
            return {
                "vectors": vectors,
                "profile_id": profile.profile_id,
                "model_name": profile.model_name,
                "provider": profile.provider,
                "duration_ms": duration_ms,
            }
        except ModelInvocationError as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._record_audit(
                task_id=task_id,
                agent_id=agent_id,
                profile=profile,
                provider=provider,
                duration_ms=duration_ms,
                input_tokens=sum(self._estimate_tokens(t) for t in texts),
                output_tokens=0,
                ok=False,
                error_code=exc.code,
            )
            raise

    def _resolve(self, agent_id: str, modality: str) -> tuple[ModelProfile, ProviderConfig]:
        # Provider 密钥在 ProviderRegistry 初始化时快照；此处每次调用前从环境刷新 default_*，避免换密钥不生效
        self.providers.refresh_defaults_from_env()
        try:
            profile_id = self.policy.resolve(agent_id, modality)
            profile = self.profiles.require(profile_id)
        except KeyError as exc:
            raise ModelInvocationError(
                "MODEL_POLICY_MISSING",
                f"未找到 agent={agent_id} modality={modality} 的模型策略",
                details={"agent": agent_id, "modality": modality},
            ) from exc

        try:
            provider = self.providers.require(profile.provider)
        except (KeyError, RuntimeError) as exc:
            raise ModelInvocationError(
                "MODEL_PROVIDER_UNAVAILABLE",
                str(exc),
                details={"provider": profile.provider, "profile": profile.profile_id},
            ) from exc
        return profile, provider

    @staticmethod
    def _merge_params(profile: ModelProfile, overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
            "timeout": profile.timeout_seconds,
        }
        params.update(profile.extra_params or {})
        if overrides:
            params.update(overrides)
        return params

    async def _invoke_chat(
        self,
        provider: ProviderConfig,
        profile: ModelProfile,
        messages: List[Dict[str, Any]],
        params: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        openai = self._load_openai()
        client = openai.OpenAI(base_url=provider.base_url, api_key=provider.api_key)
        timeout = params.pop("timeout", profile.timeout_seconds)

        retries = profile.max_retries
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=profile.model_name,
                    messages=messages,
                    timeout=timeout,
                    **{k: v for k, v in params.items() if v is not None},
                )
                choice = response.choices[0]
                content = (choice.message.content or "").strip() if choice.message else ""
                # 注意：对于 DeepSeek-v4-pro 等思考型模型，reasoning_content 是思考过程，
                # content 才是正式输出（JSON）。不应将 reasoning_content 作为 content 兜底，
                # 否则会把思考文字当作 LLM 输出返回，导致 JSON 解析失败。
                # 若 content 为空，保持空字符串，上层调用方应重试或跳过。
                usage = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                    "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                }
                return content, usage
            except Exception as exc:
                last_error = exc
                if self._is_retryable(exc) and attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break

        code, message = self._classify_error(last_error)
        raise ModelInvocationError(
            code,
            message,
            details={
                "profile": profile.profile_id,
                "provider": profile.provider,
                "error_type": type(last_error).__name__ if last_error else "Unknown",
            },
        )

    async def _invoke_chat_with_tools(
        self,
        provider: ProviderConfig,
        profile: ModelProfile,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str | Dict[str, Any],
        params: Dict[str, Any],
    ) -> tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
        openai = self._load_openai()
        client = openai.OpenAI(base_url=provider.base_url, api_key=provider.api_key)
        timeout = params.pop("timeout", profile.timeout_seconds)
        retries = profile.max_retries
        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model=profile.model_name,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    timeout=timeout,
                    **{k: v for k, v in params.items() if v is not None},
                )
                choice = response.choices[0]
                message = choice.message
                content = (message.content or "").strip() if message else ""
                tool_calls: List[Dict[str, Any]] = []
                for call in getattr(message, "tool_calls", None) or []:
                    fn = getattr(call, "function", None)
                    raw_args = getattr(fn, "arguments", "{}") if fn else "{}"
                    try:
                        args = json.loads(raw_args or "{}")
                    except Exception:
                        args = {}
                    tool_calls.append(
                        {
                            "id": getattr(call, "id", None),
                            "name": getattr(fn, "name", "") if fn else "",
                            "arguments": args,
                        }
                    )
                usage = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
                    "total_tokens": getattr(response.usage, "total_tokens", 0) if response.usage else 0,
                }
                return content, tool_calls, usage
            except Exception as exc:
                last_error = exc
                if self._is_retryable(exc) and attempt < retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break
        code, message = self._classify_error(last_error)
        raise ModelInvocationError(
            code,
            message,
            details={
                "profile": profile.profile_id,
                "provider": profile.provider,
                "error_type": type(last_error).__name__ if last_error else "Unknown",
                "mode": "tools",
            },
        )

    async def _chat_with_tools_json_fallback(
        self,
        agent_id: str,
        messages: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]],
        task_id: Optional[str],
        overrides: Optional[Dict[str, Any]],
        original_error: ModelInvocationError,
    ) -> Dict[str, Any]:
        tool_names = [
            str((item.get("function") or {}).get("name") or "")
            for item in tools
            if isinstance(item, dict)
        ]
        fallback_messages = [
            *messages,
            {
                "role": "system",
                "content": (
                    "当前模型不使用原生 tool calling。请只输出 JSON，不要解释："
                    '{"tool_calls":[{"name":"工具名","arguments":{},"confidence":0.0,"reason":"原因"}],"content":""}。'
                    f"可选工具：{', '.join(name for name in tool_names if name)}。"
                ),
            },
        ]
        result = await self.chat(
            agent_id,
            fallback_messages,
            modality="text",
            task_id=task_id,
            overrides={**(overrides or {}), "temperature": 0.1},
        )
        raw = str(result.get("content") or "{}").strip()
        parsed: Dict[str, Any]
        try:
            parsed = json.loads(raw)
        except Exception:
            start = raw.find("{")
            end = raw.rfind("}")
            try:
                parsed = json.loads(raw[start : end + 1]) if start >= 0 and end > start else {}
            except Exception:
                parsed = {}
        tool_calls = parsed.get("tool_calls") or []
        if isinstance(tool_calls, dict):
            tool_calls = [tool_calls]
        normalized = []
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            normalized.append(
                {
                    "id": call.get("id"),
                    "name": call.get("name") or call.get("tool_name"),
                    "arguments": call.get("arguments") or {},
                    "confidence": call.get("confidence"),
                    "reason": call.get("reason"),
                }
            )
        return {
            **result,
            "content": str(parsed.get("content") or result.get("content") or ""),
            "tool_calls": normalized,
            "fallback_from": original_error.code,
        }

    async def _invoke_chat_stream(
        self,
        provider: ProviderConfig,
        profile: ModelProfile,
        messages: List[Dict[str, Any]],
        params: Dict[str, Any],
    ) -> AsyncIterator[str]:
        openai = self._load_openai()
        client = openai.OpenAI(base_url=provider.base_url, api_key=provider.api_key)
        timeout = params.pop("timeout", profile.timeout_seconds)
        try:
            stream = await asyncio.to_thread(
                client.chat.completions.create,
                model=profile.model_name,
                messages=messages,
                stream=True,
                timeout=timeout,
                **{k: v for k, v in params.items() if v is not None},
            )
            in_reasoning = False  # 是否正在输出 reasoning_content（思考链）
            while True:
                chunk = await asyncio.to_thread(self._next_stream_chunk, stream)
                if chunk is None:
                    break
                choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
                delta_obj = getattr(choice, "delta", None) if choice else None

                # DeepSeek thinking 模式：reasoning_content 与 content 是独立字段
                reasoning_delta = getattr(delta_obj, "reasoning_content", None) if delta_obj else None
                content_delta = getattr(delta_obj, "content", None) if delta_obj else None

                if reasoning_delta:
                    # 第一个 reasoning chunk：先 yield 开标签
                    if not in_reasoning:
                        yield "<think>"
                        in_reasoning = True
                    yield str(reasoning_delta)
                elif content_delta:
                    # 从 reasoning 切换到 content：先关闭 think 标签
                    if in_reasoning:
                        yield "</think>"
                        in_reasoning = False
                    yield str(content_delta)

            # 流结束时若仍在 reasoning 状态，补上关闭标签
            if in_reasoning:
                yield "</think>"
        except Exception as exc:
            code, message = self._classify_error(exc)
            raise ModelInvocationError(
                code,
                message,
                details={
                    "profile": profile.profile_id,
                    "provider": profile.provider,
                    "error_type": type(exc).__name__,
                    "mode": "stream",
                },
            ) from exc

    @staticmethod
    def _next_stream_chunk(stream: Any) -> Any:
        try:
            return next(stream)
        except StopIteration:
            return None

    async def _invoke_embedding(
        self,
        provider: ProviderConfig,
        profile: ModelProfile,
        texts: List[str],
    ) -> List[List[float]]:
        openai = self._load_openai()
        client = openai.OpenAI(base_url=provider.base_url, api_key=provider.api_key)
        try:
            response = await asyncio.to_thread(
                client.embeddings.create,
                model=profile.model_name,
                input=texts,
                timeout=profile.timeout_seconds,
            )
            return [item.embedding for item in response.data]
        except Exception as exc:
            code, message = self._classify_error(exc)
            raise ModelInvocationError(
                code,
                message,
                details={
                    "profile": profile.profile_id,
                    "provider": profile.provider,
                    "error_type": type(exc).__name__,
                },
            ) from exc

    @staticmethod
    def _load_openai():
        try:
            import openai  # type: ignore
        except ImportError as exc:
            raise ModelInvocationError(
                "MODEL_DEPENDENCY_MISSING",
                "缺少依赖 openai，请执行: pip install openai",
            ) from exc
        return openai

    @staticmethod
    def _is_retryable(exc: Optional[BaseException]) -> bool:
        if exc is None:
            return False
        text = str(exc).lower()
        keywords = ("timeout", "timed out", "rate limit", "429", "503", "502", "504")
        return any(k in text for k in keywords)

    @staticmethod
    def _classify_error(exc: Optional[BaseException]) -> tuple[str, str]:
        if exc is None:
            return "MODEL_UNKNOWN", "模型调用失败"
        # asyncio.TimeoutError / asyncio.CancelledError 的 str() 是空字符串，需要先按类型判断
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return "MODEL_TIMEOUT", f"模型调用超时: {type(exc).__name__}"
        text = str(exc).lower()
        if "timeout" in text or "timed out" in text or "504" in text or "gateway timeout" in text:
            return "MODEL_TIMEOUT", f"模型调用超时: {exc}"
        if "rate limit" in text or "429" in text:
            return "MODEL_RATE_LIMIT", f"模型限流: {exc}"
        if (
            "401" in text
            or "403" in text
            or "unauthorized" in text
            or "forbidden" in text
            or "access denied" in text
            or "invalid api" in text
            or "invalid key" in text
            or "api key" in text
            or "apikey" in text
            or "incorrect api key" in text
            or "invalid token" in text
            or "authentication" in text
            or "鉴权" in text
            or "密钥" in text
        ):
            return "MODEL_AUTH_FAILED", f"模型认证失败: {exc}"
        if "content filter" in text or "content_policy" in text or "content rejected" in text:
            return "MODEL_CONTENT_REJECTED", f"模型内容策略拒绝: {exc}"
        return "MODEL_UPSTREAM_ERROR", f"模型上游错误: {exc}"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // 2)

    def _estimate_tokens_from_messages(self, messages: List[Dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, str):
                total += self._estimate_tokens(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        total += self._estimate_tokens(part["text"])
        return total

    def _record_audit(
        self,
        *,
        task_id: Optional[str],
        agent_id: str,
        profile: ModelProfile,
        provider: ProviderConfig,
        duration_ms: int,
        input_tokens: int,
        output_tokens: int,
        ok: bool,
        error_code: Optional[str] = None,
    ) -> None:
        record = ModelAuditRecord(
            task_id=task_id,
            trace_id=get_trace_id(""),
            agent_id=agent_id,
            profile_id=profile.profile_id,
            model_name=profile.model_name,
            provider=provider.name,
            modality=profile.modality,
            duration_ms=duration_ms,
            input_tokens_estimate=input_tokens,
            output_tokens_estimate=output_tokens,
            ok=ok,
            error_code=error_code,
        )
        self._audit_log.append(record)
        if len(self._audit_log) > 500:
            self._audit_log = self._audit_log[-250:]
        metrics_store.record_task_metric(
            TaskMetricRecord(
                task_id=task_id,
                trace_id=record.trace_id,
                metric_type="model_call",
                agent=agent_id,
                stage="model_gateway",
                duration_ms=duration_ms,
                tokens_in=input_tokens,
                tokens_out=output_tokens,
                status="ok" if ok else "error",
                error_code=error_code,
                metadata={
                    "profile_id": profile.profile_id,
                    "model_name": profile.model_name,
                    "provider": provider.name,
                    "modality": profile.modality,
                },
            )
        )
        logger.debug(
            "[ModelGateway] task={} agent={} profile={} model={} ok={} ms={} err={}",
            task_id,
            agent_id,
            profile.profile_id,
            profile.model_name,
            ok,
            duration_ms,
            error_code,
        )

    def get_audit_log(self, limit: int = 100) -> List[ModelAuditRecord]:
        return list(self._audit_log[-limit:])


model_gateway = ModelGateway()
