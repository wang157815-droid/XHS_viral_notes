"""PromptRegistry 加载 + 缓存 + 枚举契约(4.3pre.3 精简后清单)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.application.agents.prompts import PromptRegistry, prompt_registry


# 4.3pre.3 精简后现役 prompt 清单(删除了 strategy_*.md / insight_{industry,competitor,brand}.md
# / image_analysis.md / video_analysis.md 5+3 = 8 个旧文件)
EXPECTED_PROMPTS = {
    "input_parser.md",
    "insight_summary.md",
    "rag_query_rewrite.md",
    "image_6elements.md",
    "video_6elements.md",
    "viral_model_naming.md",
    "canvas_render_disclaimer.md",
}

# 确认已被 4.3pre.3 物理删除的旧 prompt,不应再出现在 list_all 结果里
REMOVED_PROMPTS = {
    "insight_industry.md",
    "insight_competitor.md",
    "insight_brand.md",
    "image_analysis.md",
    "video_analysis.md",
    "strategy_title.md",
    "strategy_product.md",
    "strategy_cover.md",
    "strategy_structure.md",
}


def test_list_all_returns_expected_count():
    names = set(prompt_registry.list_all())
    assert names >= EXPECTED_PROMPTS, f"缺少 prompt 文件: {EXPECTED_PROMPTS - names}"


def test_removed_prompts_no_longer_listed():
    names = set(prompt_registry.list_all())
    leaked = names & REMOVED_PROMPTS
    assert not leaked, f"4.3pre.3 应已删除的 prompt 仍出现: {leaked}"


def test_load_all_prompts_non_empty():
    for name in EXPECTED_PROMPTS:
        content = prompt_registry.load(name)
        assert content.strip(), f"{name} 内容不应为空"


def test_cache_hits_twice_no_reread(tmp_path: Path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "sample.md").write_text("v1", encoding="utf-8")
    reg = PromptRegistry(root)
    assert reg.load("sample.md") == "v1"

    # 物理修改文件内容,但缓存已生效,再次 load 仍返回 v1
    (root / "sample.md").write_text("v2", encoding="utf-8")
    assert reg.load("sample.md") == "v1"

    # clear_cache 后应读到新内容
    reg.clear_cache()
    assert reg.load("sample.md") == "v2"


def test_load_missing_raises():
    with pytest.raises(FileNotFoundError):
        prompt_registry.load("_does_not_exist.md")


def test_readme_not_in_business_list():
    """README.md 不应被 list_all 暴露(以 README 命名约定)。"""
    names = prompt_registry.list_all()
    assert "README.md" not in names
    assert "readme.md" not in names
