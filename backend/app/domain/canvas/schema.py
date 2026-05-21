"""
CanvasSchema：后端下发、前端只渲染的契约。

业务依据：
- 业务方案：按"框架-洞察-原始数据"编排。
- 原型 02-workspace.html：LAYER 1 / 2 / 3 三层模块结构。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

from ..module_status import ModuleStatus


CommandType = Literal[
    "regen_sheet2_narrative",
    "rename_models",
    "regenerate",
    "regenerate_cascade",
    "delete",
    "restore",
    "deep_dive",
    "export",
]


@dataclass
class CanvasDimension:
    id: str
    label: str
    active: bool = True
    highlighted: bool = False


@dataclass
class CanvasTheme:
    id: str
    label: str
    description: Optional[str] = None


@dataclass
class CanvasModuleAction:
    id: str
    label: str
    command: CommandType


@dataclass
class CanvasModule:
    module_id: str
    title: str
    layer: Literal[1, 2, 3]
    status: ModuleStatus = ModuleStatus.PENDING
    version: int = 0
    highlighted: bool = False
    default_expanded: bool = False
    summary: Optional[str] = None
    content: Dict[str, Any] = field(default_factory=dict)
    actions: List[CanvasModuleAction] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    dirty_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        base = asdict(self)
        base["status"] = self.status.value if isinstance(self.status, ModuleStatus) else self.status
        return base


@dataclass
class CanvasSchema:
    task_id: str
    title: str
    canvas_version: int = 0
    subtitle: Optional[str] = None
    dimensions: List[CanvasDimension] = field(default_factory=list)
    themes: List[CanvasTheme] = field(default_factory=list)
    disclaimer: Optional[str] = None
    modules: List[CanvasModule] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "canvas_version": self.canvas_version,
            "subtitle": self.subtitle,
            "dimensions": [asdict(d) for d in self.dimensions],
            "themes": [asdict(t) for t in self.themes],
            "disclaimer": self.disclaimer,
            "modules": [m.to_dict() for m in self.modules],
        }

    def bump_version(self) -> int:
        self.canvas_version += 1
        return self.canvas_version

    def find_module(self, module_id: str) -> Optional[CanvasModule]:
        for module in self.modules:
            if module.module_id == module_id:
                return module
        return None

    def upsert_module(self, module: CanvasModule) -> None:
        for idx, existing in enumerate(self.modules):
            if existing.module_id == module.module_id:
                self.modules[idx] = module
                return
        self.modules.append(module)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CanvasSchema":
        """从字典反序列化（用于从 TaskContext.canvas_document 恢复）。"""
        dims = [
            CanvasDimension(**d)
            for d in (data.get("dimensions") or [])
            if isinstance(d, dict)
        ]
        themes = [
            CanvasTheme(**t)
            for t in (data.get("themes") or [])
            if isinstance(t, dict)
        ]
        modules: List[CanvasModule] = []
        for m in data.get("modules") or []:
            if not isinstance(m, dict):
                continue
            status_val = m.get("status")
            try:
                status = ModuleStatus(status_val) if status_val is not None else ModuleStatus.PENDING
            except ValueError:
                status = ModuleStatus.PENDING
            actions = [
                CanvasModuleAction(**a)
                for a in (m.get("actions") or [])
                if isinstance(a, dict)
            ]
            modules.append(
                CanvasModule(
                    module_id=str(m.get("module_id") or ""),
                    title=str(m.get("title") or ""),
                    layer=int(m.get("layer", 1)),  # type: ignore[arg-type]
                    status=status,
                    version=int(m.get("version", 0)),
                    highlighted=bool(m.get("highlighted", False)),
                    default_expanded=bool(m.get("default_expanded", False)),
                    summary=m.get("summary"),
                    content=m.get("content") or {},
                    actions=actions,
                    depends_on=list(m.get("depends_on") or []),
                    dirty_reason=m.get("dirty_reason"),
                )
            )
        return cls(
            task_id=str(data.get("task_id") or ""),
            title=str(data.get("title") or ""),
            canvas_version=int(data.get("canvas_version", 0)),
            subtitle=data.get("subtitle"),
            dimensions=dims,
            themes=themes,
            disclaimer=data.get("disclaimer"),
            modules=modules,
        )


def _default_disclaimer() -> str:
    return (
        "本画布内容基于对小红书公开笔记的规律归纳生成，仅作为创作参考，"
        "不涉及原始素材的直接复用，不构成对任何品牌或平台的意见。"
    )


def build_empty_canvas(
    task_id: str,
    *,
    title: str,
    subtitle: Optional[str] = None,
    dimensions: Optional[List[CanvasDimension]] = None,
    themes: Optional[List[CanvasTheme]] = None,
) -> CanvasSchema:
    """创建空白 CanvasSchema（MVP 默认维度 + 默认主题）。"""
    default_dims = [
        CanvasDimension(id="industry", label="行业洞察", active=True, highlighted=False),
        CanvasDimension(id="competitor", label="竞品洞察", active=True),
        CanvasDimension(id="brand", label="本品洞察", active=True),
    ]
    default_themes = [CanvasTheme(id="default", label="默认主题")]
    return CanvasSchema(
        task_id=task_id,
        title=title,
        subtitle=subtitle,
        dimensions=dimensions or default_dims,
        themes=themes or default_themes,
        disclaimer=_default_disclaimer(),
        modules=[],
    )
