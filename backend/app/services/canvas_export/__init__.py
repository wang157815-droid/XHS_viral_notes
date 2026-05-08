"""Canvas Excel 导出服务(4.3pre.5)。

公开入口:
    from backend.app.services.canvas_export import build_excel_bytes

    data = await build_excel_bytes(
        task_record=record,
        canvas=canvas,
        task_context=ctx,
        total_timeout=60.0,
    )

产出的 xlsx 对齐 `docs/templates/kanglao_spec.md` 的 7-sheet 模板结构。
"""

from .excel_exporter import build_excel_bytes

__all__ = ["build_excel_bytes"]
