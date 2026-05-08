"""
Canvas 契约模块。

负责定义 CanvasSchema 数据结构以及生成/更新的辅助函数。
前端仅按 CanvasSchema 渲染，禁止自己拼接模块字段。
"""

from .schema import (
    CanvasDimension,
    CanvasModule,
    CanvasModuleAction,
    CanvasSchema,
    CanvasTheme,
    build_empty_canvas,
)

__all__ = [
    "CanvasDimension",
    "CanvasTheme",
    "CanvasModuleAction",
    "CanvasModule",
    "CanvasSchema",
    "build_empty_canvas",
]
