"""回归测试:4.3pre.3 精简后,各关键 prompt 文件能被 PromptRegistry 加载且约束齐全。

阶段 4.0 起 Agent 不再使用 `str.format()` 模式的 prompt 模板,
全部通过 `prompt_registry.load(name)` 读取纯自然语言文件,
动态数据由 Agent 代码侧 f-string 拼接。
"""

from __future__ import annotations

from backend.app.application.agents.prompts import prompt_registry


def test_insight_summary_prompt_loadable_and_json_hint():
    """4.3pre.3 InsightAgent 只剩一份 insight_summary.md,替代旧的 3 段式。"""
    content = prompt_registry.load("insight_summary.md")
    assert content.strip(), "insight_summary.md 不应为空"
    # 最小契约:文件内约定 JSON 输出(帮助下游解析稳定)
    assert "JSON" in content or "json" in content
    # 应指导 LLM 输出 content_direction 顶级字段
    assert "content_direction" in content
    # 应指导 LLM 可选 stats_axis_label(避免硬编码"皮肤问题")
    assert "stats_axis_label" in content


def test_viral_model_naming_prompt_loadable():
    """4.3pre.3 ViralModel 起名 prompt(替代旧 4 个 strategy_*.md)。"""
    content = prompt_registry.load("viral_model_naming.md")
    assert content.strip(), "viral_model_naming.md 不应为空"


def test_sheet2_narrative_prompt_loadable():
    content = prompt_registry.load("sheet2_narrative.md")
    assert content.strip()
    assert "JSON" in content or "json" in content


def test_multimodal_6elements_prompts_loadable():
    """ImageAnalysisAgent / VideoAnalysisAgent 共用的 6 要素标注 prompt。"""
    for name in ("image_6elements.md", "video_6elements.md"):
        content = prompt_registry.load(name)
        assert content.strip(), f"{name} 不应为空"


def test_input_parser_prompt_contains_expected_fields():
    content = prompt_registry.load("input_parser.md")
    for field in ("keywords", "dimensions", "adjustments", "confidence"):
        assert field in content, f"input_parser.md 应包含字段 {field}"
