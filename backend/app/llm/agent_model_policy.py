"""
AgentModelPolicy：Agent -> Profile 路由策略。

规则：
- Agent 调模型时仅声明自己的名字（agent_id）和模态（text/multimodal/embedding）。
- 真正用哪个 ModelProfile 由此策略决定。
- 支持运行期覆盖（管理员可通过设置页调整）。
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Dict


@dataclass
class AgentPolicyEntry:
    agent_id: str
    modality: str
    profile_id: str


class AgentModelPolicy:
    _DEFAULT_BY_MODALITY = {
        "text": "text_default",
        "multimodal": "multimodal_default",
        "embedding": "embedding_default",
    }

    def __init__(self) -> None:
        self._entries: Dict[str, AgentPolicyEntry] = {}
        self._lock = RLock()
        self._bootstrap_defaults()

    def _bootstrap_defaults(self) -> None:
        defaults = [
            # 4.3pre.3 最终拓扑
            ("InputParserAgent", "text", "text_default"),
            ("CrawlerAgent", "text", "text_default"),
            ("ImageAnalysisAgent", "multimodal", "multimodal_default"),
            ("VideoAnalysisAgent", "multimodal", "multimodal_default"),
            ("ViralModelAgent", "text", "text_default"),
            ("Sheet2NarrativeAgent", "text", "text_default"),
            ("InsightAgent", "text", "text_default"),
            ("RAGAgent", "text", "text_default"),
            ("RAGAgent", "embedding", "embedding_default"),
            ("CanvasRenderAgent", "text", "text_default"),
            ("ParagraphRefinementAgent", "text", "text_default"),
            ("ConversationQAAgent", "text", "text_default"),
            ("ConversationQAAgent", "multimodal", "multimodal_default"),
            ("ConversationTitleAgent", "text", "text_default"),
            ("ConversationToolAgent", "text", "text_default"),
            ("ConversationIntentRouter", "text", "text_default"),
            ("KnowledgeQAAgent", "text", "text_default"),
            # 向后兼容别名（旧名字调用时仍能路由,新代码不再用）
            ("SemanticAgent", "text", "text_default"),
            ("CoverAgent", "multimodal", "multimodal_default"),
            ("VideoAgent", "multimodal", "multimodal_default"),
            ("StrategyAgent", "text", "text_default"),
            # warmup 品类词扩词（在 ARQ worker 中调用）
            ("WarmupKeywordExpander", "text", "text_default"),
            # 评论分析 pipeline（comment_pipeline.py）
            ("CommentPipeline.InputParser", "text", "text_default"),
            ("CommentPipeline.NoteClassifier", "text", "text_default"),
            ("CommentPipeline.CommentClassifier", "text", "text_default"),
            ("CommentPipeline.Summarizer", "text", "text_default"),
        ]
        for agent_id, modality, profile_id in defaults:
            self.set(agent_id, modality, profile_id)

    def set(self, agent_id: str, modality: str, profile_id: str) -> None:
        with self._lock:
            key = self._key(agent_id, modality)
            self._entries[key] = AgentPolicyEntry(agent_id, modality, profile_id)

    def resolve(self, agent_id: str, modality: str = "text") -> str:
        with self._lock:
            key = self._key(agent_id, modality)
            entry = self._entries.get(key)
            if entry:
                return entry.profile_id
        fallback = self._DEFAULT_BY_MODALITY.get(modality)
        if not fallback:
            raise KeyError(f"无法为 agent={agent_id} modality={modality} 解析 profile")
        return fallback

    @staticmethod
    def _key(agent_id: str, modality: str) -> str:
        return f"{agent_id}::{modality}"

    def list_entries(self) -> Dict[str, AgentPolicyEntry]:
        with self._lock:
            return dict(self._entries)


agent_model_policy = AgentModelPolicy()
