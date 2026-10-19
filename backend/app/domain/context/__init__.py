"""上下文清单领域模型（M2，设计 v1.1 §B.0 / §D.1 / §E.1）。

纯数据结构，无 IO。装配器（M3a）产出 ``ContextManifest``，随调用传给
``ModelGateway.chat(..., context_manifest=...)``，写入 ``task_metrics.metadata.context``。

约束：清单只记录 token 分块与 id，**不含任何记忆/文档的值**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Trust(str, Enum):
    """信任分级（§D.1）。``derived`` 不是独立级别，而是 ContextBlock 上的标记。"""

    INSTRUCTION = "instruction"
    USER_DATA = "user_data"
    OWNER_DOC = "owner_doc"
    SHARED_DOC = "shared_doc"
    UNTRUSTED = "untrusted"


class BlockKind(str, Enum):
    SYSTEM = "system"
    TOOLS = "tools"
    PROFILE = "profile"
    SUMMARY = "summary"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    HISTORY = "history"
    OBSERVATIONS = "observations"
    USER = "user"


@dataclass(frozen=True)
class ContextBlock:
    kind: BlockKind
    tokens: int
    trust: Optional[Trust] = None
    derived: bool = False
    truncated: Optional[bool] = None
    status: Optional[str] = None  # memory 块：ok | empty | degraded | error
    group: Optional[str] = None  # tools 块：工具分组

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind.value, "tokens": int(self.tokens)}
        if self.trust is not None:
            out["trust"] = self.trust.value
        if self.derived:
            out["derived"] = True
        for key in ("truncated", "status", "group"):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        return out


# memory_hits 只允许这些键：id 与分类信息，不允许 content/value 等值字段
_MEMORY_HIT_KEYS = ("id", "kind", "slot", "score")


@dataclass
class ContextManifest:
    caller: str
    conversation_id: Optional[str] = None
    iteration: Optional[int] = None
    budget: Optional[int] = None
    blocks: List[ContextBlock] = field(default_factory=list)
    memory_hits: List[Dict[str, Any]] = field(default_factory=list)
    compression: Optional[Dict[str, Any]] = None
    trimmed: List[Dict[str, Any]] = field(default_factory=list)
    prompt_versions: Dict[str, str] = field(default_factory=dict)

    @property
    def total_est(self) -> int:
        return sum(int(b.tokens) for b in self.blocks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "caller": self.caller,
            "conversation_id": self.conversation_id,
            "iteration": self.iteration,
            "budget": self.budget,
            "total_est": self.total_est,
            "blocks": [b.to_dict() for b in self.blocks],
            "memory_hits": [
                {k: hit[k] for k in _MEMORY_HIT_KEYS if k in hit} for hit in self.memory_hits
            ],
            "compression": dict(self.compression) if self.compression else None,
            "trimmed": [dict(t) for t in self.trimmed],
            "prompt_versions": dict(self.prompt_versions),
        }


__all__ = ["BlockKind", "ContextBlock", "ContextManifest", "Trust"]
