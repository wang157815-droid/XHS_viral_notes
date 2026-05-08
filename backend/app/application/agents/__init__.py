"""
Agent 节点集合（阶段 4.3pre.3 拓扑）:

- BaseAgent: 约束 Agent 只能经由 TaskContext 读写、ModelGateway 调用模型
- InputParserAgent: 用户自然语言 → 关键词 / 维度 / 调整需求
- CrawlerAgent: 四源采集(category_top / competitor / top_interaction / serp_top)
- ImageAnalysisAgent: 图文 6 要素标注(写 multimodal_output.annotations)
- VideoAnalysisAgent: 视频 6 要素标注(同步并发,共享 multimodal_output.annotations)
- ViralModelAgent: 混合聚类生成爆文模型矩阵(替代原 StrategyAgent)
- Sheet2NarrativeAgent: 为导出 Sheet2 playbook 补全概括/解释/示例(可选 LLM)
- InsightAgent: 一次 LLM 调用产出"内容方向 / 痛点 / SEO" 原生 schema(4.3pre.3 移除旧 3 段适配)
- RAGAgent: 业务约束检索(硬约束,不再输出画布模块)
- CanvasRenderAgent: 将各 Agent 输出整合为 CanvasSchema(8 新模块)

4.3pre.3 已移除:
- StrategyAgent(彻底删除,被 ViralModelAgent 替代)
"""

from .base import AgentContext, AgentResult, BaseAgent
from .canvas_render_agent import CanvasRenderAgent
from .crawler_agent import CrawlerAgent
from .image_analysis_agent import ImageAnalysisAgent
from .input_parser_agent import InputParserAgent
from .insight_agent import InsightAgent
from .rag_agent import RAGAgent
from .sheet2_narrative_agent import Sheet2NarrativeAgent
from .video_analysis_agent import VideoAnalysisAgent
from .viral_model_agent import ViralModelAgent

__all__ = [
    "AgentContext",
    "AgentResult",
    "BaseAgent",
    "InputParserAgent",
    "CrawlerAgent",
    "ImageAnalysisAgent",
    "VideoAnalysisAgent",
    "ViralModelAgent",
    "Sheet2NarrativeAgent",
    "InsightAgent",
    "RAGAgent",
    "CanvasRenderAgent",
]
