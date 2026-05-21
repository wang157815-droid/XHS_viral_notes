"""
ViralModel + ElementCategory + ElementExample: 爆文模型矩阵的数据契约
(阶段 4.3pre.1)。

对齐"抗老精华爆文模型—兴长信达.xlsx"Sheet 2 "爆文总结详情2" 的二维矩阵结构:

    Y 轴 爆文模型(AI 动态 2-6 个,品类自适应)
    X 轴 6 要素(固定: A 封面 / B 封面压字 / C 标题 / D 切入点 / E 引出方式 / F 植入方式)
    Z 轴 每单元格 = 分类枚举 × 占比 × 代表笔记示例

这是 ViralModelAgent(替换原 StrategyAgent)的标准输出结构,
也是 CanvasModule `mod-viral-model-matrix` 的 content 结构。

权威文档: docs/canvas_restructure_spec.md 第 4.2, 5.2 章
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ElementCode(str, Enum):
    """爆文 6 要素编码(X 轴,固定)。"""

    A_COVER = "A_cover"                        # 封面
    B_COVER_TEXT = "B_cover_text"              # 封面压字
    C_TITLE = "C_title"                        # 标题
    D_OPENING = "D_opening"                    # 内容切入点
    E_PRODUCT_INTRO = "E_product_intro"        # 产品引出方式
    F_PRODUCT_PLACEMENT = "F_product_placement"  # 产品植入方式

    @property
    def label(self) -> str:
        """中文展示名(画布渲染用)。"""
        return {
            "A_cover": "封面",
            "B_cover_text": "封面压字",
            "C_title": "标题",
            "D_opening": "内容切入点",
            "E_product_intro": "产品引出方式",
            "F_product_placement": "产品植入方式",
        }[self.value]


# 固定顺序(用于遍历/渲染)
ELEMENT_ORDER: List[ElementCode] = [
    ElementCode.A_COVER,
    ElementCode.B_COVER_TEXT,
    ElementCode.C_TITLE,
    ElementCode.D_OPENING,
    ElementCode.E_PRODUCT_INTRO,
    ElementCode.F_PRODUCT_PLACEMENT,
]


@dataclass
class ElementExample:
    """某要素类型下的一条代表笔记示例(用于画布展示 + Excel 导出 K 列嵌图)。"""

    note_id: str
    title: str
    cover_url: str
    likes: Optional[int] = None  # 用于排序"最具代表性"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ElementCategory:
    """某模型下某要素的一个分类(如 M1 单品推荐 / A 封面 / 纯产品图 = 28%)。"""

    type: str                        # "纯产品图" / "前后对比" / ...
    ratio: float                     # 占比 0.0-1.0
    count: int                       # 参与统计的样本数(ratio = count / total)
    examples: List[ElementExample] = field(default_factory=list)
    # paragraph_id 给前端反馈用(4.3 段落反馈基础设施)
    paragraph_id: Optional[str] = None
    # Sheet2 playbook：概括/解释/示例(可由 Sheet2NarrativeAgent 或人工补全)
    summary: Optional[str] = None
    explanation: Optional[str] = None
    example_text: Optional[str] = None
    track_id: Optional[str] = None   # 如 A1 / C1，导出对齐模板轨号
    col_span: Optional[int] = None   # 横向合并列宽(>=1)，缺省由导出布局配置推断
    children: List["ElementCategory"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "type": self.type,
            "ratio": round(self.ratio, 4),
            "count": self.count,
            "examples": [e.to_dict() for e in self.examples],
        }
        if self.paragraph_id:
            data["paragraph_id"] = self.paragraph_id
        if self.summary:
            data["summary"] = self.summary
        if self.explanation:
            data["explanation"] = self.explanation
        if self.example_text:
            data["example_text"] = self.example_text
        if self.track_id:
            data["track_id"] = self.track_id
        if self.col_span is not None:
            data["col_span"] = int(self.col_span)
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data


@dataclass
class ViralModel:
    """一个爆文模型(如"单品推荐" / "手持单推" / "干货分享" / "知识科普")。

    AI 动态识别,每个品类 2-6 个。
    """

    model_id: str                    # "M1", "M2", ...
    name: str                        # "单品推荐"
    description: str = ""            # 简短描述(LLM 产出,便于用户理解)
    definition: str = ""             # 属加种差法正式定义(LLM 产出,用于 Sheet1 模型定义行)
    coverage: float = 0.0            # 该模型占全部爆款样本的比例 0.0-1.0
    avg_interaction: int = 0         # 该模型下爆款的平均互动量
    sample_note_ids: List[str] = field(default_factory=list)  # 归到这个模型的 note ids
    elements: Dict[ElementCode, List[ElementCategory]] = field(default_factory=dict)
    # 4.3pre.3: 模型级反馈锚点(粗粒度,整个模型的踩/重生/编辑)
    paragraph_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "model_id": self.model_id,
            "name": self.name,
            "description": self.description,
            "coverage": round(self.coverage, 4),
            "avg_interaction": self.avg_interaction,
            "sample_note_ids": list(self.sample_note_ids),
            "elements": {
                code.value: [cat.to_dict() for cat in cats]
                for code, cats in self.elements.items()
            },
        }
        if self.definition:
            data["definition"] = self.definition
        if self.paragraph_id:
            data["paragraph_id"] = self.paragraph_id
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ViralModel":
        """从 to_dict() 输出重建 ViralModel 对象（轻量重建，用于更新操作）。"""
        elements: Dict[ElementCode, List[ElementCategory]] = {}
        for code_str, cats_raw in (data.get("elements") or {}).items():
            try:
                code = ElementCode(code_str)
            except ValueError:
                continue
            cats = []
            for c in (cats_raw or []):
                if not isinstance(c, dict):
                    continue
                cats.append(ElementCategory(
                    type=str(c.get("type") or ""),
                    ratio=float(c.get("ratio") or 0),
                    count=int(c.get("count") or 0),
                    paragraph_id=c.get("paragraph_id"),
                ))
            elements[code] = cats
        return cls(
            model_id=str(data.get("model_id") or ""),
            name=str(data.get("name") or ""),
            description=str(data.get("description") or ""),
            definition=str(data.get("definition") or ""),
            coverage=float(data.get("coverage") or 0),
            avg_interaction=int(data.get("avg_interaction") or 0),
            sample_note_ids=list(data.get("sample_note_ids") or []),
            elements=elements,
            paragraph_id=data.get("paragraph_id"),
        )


@dataclass
class UnusedDirection:
    """不做的方向(对应 Sheet 1 F 列"*不做,闭环验证效果差"标注)。

    即便互动量高,但业务方验证后链路数据差,标记为"不推荐复用"。
    """

    direction: str                   # "日常vlog" / "剧情"
    ratio: float                     # 在样本里的出现占比
    avg_interaction: int             # 平均互动量(即便高也不推荐)
    reason: str                      # "闭环验证后链路数据差" / 其他业务原因

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ViralModelMatrix:
    """ViralModelAgent 的完整输出 — 对应 Canvas mod-viral-model-matrix 的 content。"""

    models: List[ViralModel] = field(default_factory=list)
    unused_directions: List[UnusedDirection] = field(default_factory=list)
    total_sample_count: int = 0      # 参与分析的爆款总数(用于 ratio 分母校验)
    taxonomy_version: str = ""       # 分析时使用的 taxonomy 版本号

    def to_dict(self) -> Dict[str, Any]:
        return {
            "models": [m.to_dict() for m in self.models],
            "unused_directions": [u.to_dict() for u in self.unused_directions],
            "total_sample_count": self.total_sample_count,
            "taxonomy_version": self.taxonomy_version,
        }


__all__ = [
    "ElementCode",
    "ELEMENT_ORDER",
    "ElementExample",
    "ElementCategory",
    "ViralModel",
    "UnusedDirection",
    "ViralModelMatrix",
]
