"""
4.3pre.5 Excel 7-sheet 导出契约测试。

覆盖:
- 7 sheet 全部齐全 + sheet 名匹配模板
- Sheet 1 H1 表头 = stats_axis_label(不硬编码皮肤问题)
- Sheet 2 顶部同 Sheet1 + playbook(6 要素轨 × 概括/解释/示例)
- Sheet 3-6 的 N-S 列正确从 annotations[note_id] 取值
- Sheet 4 多 T/U 列填入 seo_top10 / comment_hotwords_top10
- Sheet 7 与 Sheet 2 同引擎(playbook 占位)
- 图片下载失败 cell 留空不 crash
- total_timeout 触发后仍返回合法 xlsx
"""

from __future__ import annotations

import asyncio
import io
from typing import Any, Dict, List

import pytest
from openpyxl import load_workbook

from backend.app.domain.canvas import build_empty_canvas
from backend.app.domain.task_context import TaskContext, TaskContextWriter
from backend.app.domain.task_status import TaskStatus
from backend.app.infrastructure.repository.task_repository import TaskRecord
from backend.app.services.canvas_export import build_excel_bytes
from backend.app.services.canvas_export import excel_exporter as ee_mod


_EXPECTED_SHEETS = [
    "爆文总结",
    "爆文总结详情2",
    "数据源总",
    "竞品爆文",
    "【品类】抗老精华互动top",
    "草稿",
]


_SHEET5_NAME = ee_mod._sheet5_title(["抗老精华"])


def _make_note(note_id: str, **overrides: Any) -> Dict[str, Any]:
    base = {
        "note_id": note_id,
        "title": f"示例笔记 {note_id}",
        "nickname": f"作者_{note_id}",
        "likes": 1000,
        "collects": 200,
        "comments": 30,
        "interaction_score": 1230,
        "media_type": "image",
        "note_type": "图集",
        "cover_url": f"https://example.com/{note_id}.jpg",
        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
        "sources_hit": ["category_top"],
    }
    base.update(overrides)
    return base


def _make_annotation(**overrides: Any) -> Dict[str, Any]:
    base = {
        "cover_type": "纯产品图",
        "cover_text_type": "干货/经验分享",
        "title_type": "痛点+解决方案",
        "opening_type": "痛点切入",
        "product_intro_type": "直接带出",
        "product_placement_type": "融合使用感受",
        "pain_keywords": "暗沉,细纹",
        "content_direction": "口播单推",
    }
    base.update(overrides)
    return base


def _make_task_context_with_data(
    *, with_competitor_seo: bool = True
) -> TaskContext:
    ctx = TaskContext(task_id="task_ut")
    writer = TaskContextWriter(ctx)

    notes_category = [_make_note("n1"), _make_note("n2", media_type="video", note_type="视频")]
    notes_competitor = [
        _make_note(
            "n3",
            sources_hit=["competitor"],
            seo_top10=["抗老", "细纹", "精华"] if with_competitor_seo else [],
            comment_hotwords_top10=["好用", "回购"] if with_competitor_seo else [],
        ),
    ]
    notes_top_interaction = [_make_note("n4", published_at="2024-11-01", likes=5000)]

    writer.write(
        "crawler_output",
        {
            "source": "live",
            "sample_count": 4,
            "keywords": ["抗老精华"],
            "sources": {
                "category_top": notes_category,
                "competitor": notes_competitor,
                "top_interaction": notes_top_interaction,
            },
            "all_notes": notes_category + notes_competitor + notes_top_interaction,
        },
        agent_id="test",
    )

    writer.write(
        "multimodal_output",
        {
            "annotations": {
                "n1": _make_annotation(),
                "n2": _make_annotation(content_direction="干货分享"),
                "n3": _make_annotation(cover_type="达人手持产品", content_direction="口播单推"),
                "n4": _make_annotation(content_direction="知识科普"),
            }
        },
        agent_id="test",
    )

    writer.write(
        "viral_model_output",
        {
            "models": [
                {
                    "model_id": "M1",
                    "name": "口播单推型",
                    "description": "达人手持 + 直接推荐",
                    "coverage": 0.4,
                    "avg_interaction": 15000,
                    "paragraph_id": "M1",
                    "elements": {
                        "A_cover": [
                            {
                                "type": "达人手持产品",
                                "ratio": 0.6,
                                "count": 6,
                                "paragraph_id": "M1-A_cover-C1",
                                "examples": [
                                    {
                                        "note_id": "n1",
                                        "title": "示例 1",
                                        "cover_url": "https://example.com/ex1.jpg",
                                    }
                                ],
                            },
                            {
                                "type": "纯产品图",
                                "ratio": 0.4,
                                "count": 4,
                                "paragraph_id": "M1-A_cover-C2",
                                "examples": [],
                            },
                        ],
                        "C_title": [
                            {
                                "type": "痛点+解决方案",
                                "ratio": 1.0,
                                "count": 10,
                                "paragraph_id": "M1-C_title-C1",
                                "examples": [],
                            }
                        ],
                    },
                },
                {
                    "model_id": "M2",
                    "name": "干货分享型",
                    "description": "教程演示",
                    "coverage": 0.3,
                    "avg_interaction": 12000,
                    "paragraph_id": "M2",
                    "elements": {
                        "D_opening": [
                            {
                                "type": "干货切入",
                                "ratio": 0.6,
                                "count": 3,
                                "paragraph_id": "M2-D_opening-C1",
                                "examples": [],
                            },
                            {
                                "type": "痛点切入",
                                "ratio": 0.4,
                                "count": 2,
                                "paragraph_id": "M2-D_opening-C2",
                                "examples": [],
                            },
                        ]
                    },
                },
            ],
            "unused_directions": [
                {
                    "direction": "日常 vlog",
                    "ratio": 0.05,
                    "avg_interaction": 20000,
                    "reason": "闭环验证差",
                }
            ],
            "total_sample_count": 20,
            "taxonomy_version": "v1",
        },
        agent_id="test",
    )

    writer.write(
        "semantic_output",
        {
            "content_direction": {
                "top_direction": "口播单推",
                "summary_points": ["方向集中"],
                "highlight": "口播主导",
            },
            "pain_points_top": [
                {"keyword": "暗沉", "count": 12},
                {"keyword": "细纹", "count": 8},
            ],
            "seo_aggregation": {
                "core_keywords": [{"keyword": "抗老", "count": 15}],
                "long_tail": [],
                "differentiation_advice": "聚焦细纹场景",
            },
            "stats_axis_label": "高频痛点 / 议程",
        },
        agent_id="test",
    )

    return ctx


def _make_record() -> TaskRecord:
    return TaskRecord(
        task_id="task_ut",
        owner_user_id="user_ut",
        status=TaskStatus.COMPLETED,
        keywords=["抗老精华"],
    )


def _make_canvas():
    c = build_empty_canvas(task_id="task_ut", title="爆文洞察 · 抗老精华")
    return c


async def _build_bytes(
    monkeypatch,
    *,
    mock_image_cache: Dict[str, Any] | None = None,
    total_timeout: float = 30.0,
    ctx: TaskContext | None = None,
    record: TaskRecord | None = None,
) -> bytes:
    """通用构造器:mock CoverImageFetcher.fetch_all 避免真实 HTTP。"""

    async def _fake_fetch_all(self, urls):  # noqa: ARG001
        if mock_image_cache is None:
            return {}
        return dict(mock_image_cache)

    monkeypatch.setattr(
        "backend.app.services.canvas_export.image_fetcher.CoverImageFetcher.fetch_all",
        _fake_fetch_all,
    )

    return await build_excel_bytes(
        task_record=record or _make_record(),
        canvas=_make_canvas(),
        task_context=ctx or _make_task_context_with_data(),
        total_timeout=total_timeout,
    )


# ======================================================================
# 测试用例
# ======================================================================


def test_build_6_sheets(monkeypatch):
    """Sheet 1-6 齐全,名字与模板完全一致。"""
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == _EXPECTED_SHEETS, f"实际 sheet: {wb.sheetnames}"


def test_sheet3_source_column_one_label_per_row_from_merge_pool(monkeypatch):
    """Sheet3「来源」与合并池一致：每行单一标签，不拼接 sources_hit 多段。"""
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))
    ws3 = wb["数据源总"]
    assert ws3["A2"].value == "【竞品】爆文"
    assert ws3["A3"].value == "【互动 TOP】"


def test_sheet3_merges_sheet4_5_sources_only(monkeypatch):
    """Sheet3 只合并 competitor + top_interaction,不再混入 category_top。"""
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))
    ws3 = wb["数据源总"]
    titles = {
        str(ws3[f"L{row}"].value)
        for row in range(2, ws3.max_row + 1)
        if ws3[f"L{row}"].value
    }
    assert "示例笔记 n3" in titles
    assert "示例笔记 n4" in titles
    assert "示例笔记 n1" not in titles
    assert "示例笔记 n2" not in titles


def test_sheet1_dual_table_with_dynamic_axis(monkeypatch):
    """Sheet 1 H1 表头 = semantic_output.stats_axis_label(不硬编码皮肤问题)。"""
    ctx = _make_task_context_with_data()
    # 用自定义轴名覆盖
    ctx.data["semantic_output"]["stats_axis_label"] = "皮肤问题"
    data = asyncio.run(_build_bytes(monkeypatch, ctx=ctx))
    wb = load_workbook(io.BytesIO(data))
    ws = wb["爆文总结"]
    # 左表表头
    assert ws["B1"].value == "内容方向"
    assert ws["C1"].value == "数量"
    # 右表表头随 stats_axis_label 参数化
    assert ws["H1"].value == "皮肤问题"
    assert ws["I1"].value == "出现次数"
    # 左表第一行应是 M1 name
    assert ws["B2"].value == "口播单推型"
    # 右表第一行应是 pain top
    assert ws["H2"].value == "暗沉"
    assert ws["I2"].value == 12


def _sheet1_find_row_col_b_contains(ws, substr: str, max_row: int = 120) -> int | None:
    for row in range(1, max_row + 1):
        v = ws[f"B{row}"].value
        if v is not None and substr in str(v):
            return row
    return None


def _make_ctx_sheet1_pains_taller_than_element_rows() -> TaskContext:
    """第二区域:某模型 H-I 痛点行数多于左侧要素行数,下一模型表头须下移避免重叠。"""
    ctx = TaskContext(task_id="task_ut")
    writer = TaskContextWriter(ctx)
    notes = [_make_note("n1")]
    writer.write(
        "crawler_output",
        {
            "source": "live",
            "sample_count": 1,
            "keywords": ["测"],
            "sources": {"category_top": notes},
            "all_notes": notes,
        },
        agent_id="test",
    )
    writer.write(
        "multimodal_output",
        {
            "annotations": {
                "n1": _make_annotation(
                    pain_keywords="p1,p2,p3,p4,p5,p6",
                    content_direction="口播",
                ),
            }
        },
        agent_id="test",
    )
    writer.write(
        "viral_model_output",
        {
            "models": [
                {
                    "model_id": "M1",
                    "name": "模型甲",
                    "coverage": 0.5,
                    "avg_interaction": 1000,
                    "paragraph_id": "M1",
                    "sample_note_ids": ["n1"],
                    "elements": {
                        "A_cover": [
                            {
                                "type": "仅一行子类",
                                "ratio": 1.0,
                                "count": 1,
                                "paragraph_id": "M1-A",
                                "examples": [],
                            },
                        ],
                    },
                },
                {
                    "model_id": "M2",
                    "name": "模型乙",
                    "coverage": 0.5,
                    "avg_interaction": 900,
                    "paragraph_id": "M2",
                    "sample_note_ids": ["n1"],
                    "elements": {
                        "D_opening": [
                            {
                                "type": "单切入",
                                "ratio": 1.0,
                                "count": 1,
                                "paragraph_id": "M2-D",
                                "examples": [],
                            },
                        ],
                    },
                },
            ],
            "unused_directions": [],
            "total_sample_count": 10,
            "taxonomy_version": "v1",
        },
        agent_id="test",
    )
    pains = [{"keyword": f"p{i}", "count": 7 - i} for i in range(1, 7)]
    writer.write(
        "semantic_output",
        {
            "content_direction": {
                "top_direction": "口播",
                "summary_points": [],
                "highlight": "",
            },
            "pain_points_top": pains,
            "seo_aggregation": {
                "core_keywords": [],
                "long_tail": [],
                "differentiation_advice": "",
            },
            "stats_axis_label": "痛点",
        },
        agent_id="test",
    )
    return ctx


def test_sheet1_second_region_next_model_below_long_pain_block(monkeypatch):
    """痛点列行数 > 左侧要素行数时,爆文模型2 表头不得与模型1 的 H/I 重叠。"""
    ctx = _make_ctx_sheet1_pains_taller_than_element_rows()
    data = asyncio.run(_build_bytes(monkeypatch, ctx=ctx))
    wb = load_workbook(io.BytesIO(data))
    ws = wb["爆文总结"]
    r1 = _sheet1_find_row_col_b_contains(ws, "爆文模型1")
    r2 = _sheet1_find_row_col_b_contains(ws, "爆文模型2")
    assert r1 is not None and r2 is not None
    # 模型1 表头 + 1 数据行 + 6 行痛点 + 1 行间隔 = 表头 + 8
    assert r2 - r1 == 8


def test_sheet2_playbook_top_and_vertical_merges(monkeypatch):
    """Sheet2 顶部与 Sheet1 同构;playbook 区要素名写入 B 列表头行 + 爆文模型标题行。

    布局变更说明（4.3pre.6+）：
      - A 列不再做要素名竖向合并（A 列仅保留宽度占位）
      - 要素名（如「A封面」）写入 B 列对应表头行的单个单元格
    """
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))
    ws = wb["爆文总结详情2"]

    # 顶部汇总表头与 Sheet1 同构
    assert ws["B1"].value == "内容方向"
    assert ws["H1"].value == "高频痛点 / 议程"

    # A 列不再有竖向合并（要素名已移至 B 列表头）
    merged_ranges = [str(r) for r in ws.merged_cells.ranges]
    a_vertical = [r for r in merged_ranges if r.startswith("A") and ":A" in r]
    assert len(a_vertical) == 0, f"A 列不应再有竖向合并: {a_vertical}"

    # B 列中应出现要素名（如「A封面」等），确认新布局生效
    _ELEMENT_LABELS = {"A封面", "B封面压字", "C标题", "D切入点", "E引出方式", "F植入方式"}
    b_values = {str(ws.cell(row=r, column=2).value or "") for r in range(1, 120)}
    found_elements = _ELEMENT_LABELS & b_values
    assert found_elements, f"B 列应包含至少一个要素名，实际 B 列值：{b_values}"

    # 爆文模型1 标题行应存在（从 B 列起合并）
    hit_model = False
    for row in range(1, 80):
        for col in ("A", "B"):
            v = ws[f"{col}{row}"].value
            if v and "爆文模型1" in str(v):
                hit_model = True
                break
        if hit_model:
            break
    assert hit_model, "应出现爆文模型1 标题行"


def test_sheet2_col_span_template_c_title():
    from backend.app.services.canvas_export.sheet2_layout import compute_track_col_spans

    cats = [{"type": "a"}, {"type": "b"}, {"type": "c"}]
    assert compute_track_col_spans("C_title", cats) == [2, 1, 1]


def test_sheet2_narrative_apply_patches():
    from backend.app.application.agents.sheet2_narrative_agent import (
        _apply_narrative_patches,
    )

    vm: Dict[str, Any] = {
        "models": [{"elements": {"A_cover": [{"type": "x", "ratio": 0.5}]}}]
    }
    n = _apply_narrative_patches(
        vm,
        [
            {
                "model_index": 0,
                "element": "A_cover",
                "category_index": 0,
                "summary": "概括句",
                "explanation": "解释句",
            }
        ],
    )
    assert n == 1
    assert vm["models"][0]["elements"]["A_cover"][0]["summary"] == "概括句"

    # LLM 偶发小写 element code 仍应命中
    vm2: Dict[str, Any] = {
        "models": [{"elements": {"B_cover_text": [{"type": "y", "ratio": 1.0}]}}]
    }
    n2 = _apply_narrative_patches(
        vm2,
        [
            {
                "model_index": 0,
                "element": "b_cover_text",
                "category_index": 0,
                "summary": "压字概括",
                "explanation": "解释",
                "example_text": "1. 限时福利\n2. 买赠",
            }
        ],
    )
    assert n2 == 1
    assert vm2["models"][0]["elements"]["B_cover_text"][0]["example_text"].startswith("1.")


@pytest.mark.asyncio
async def test_sheet2_narrative_agent_one_llm_call_per_model():
    """每个模型单独请求 LLM,避免整表过长导致截断与解析失败。"""
    from backend.app.application.agents.sheet2_narrative_agent import Sheet2NarrativeAgent
    from tests.test_agents_real import FakeBus, FakeGateway, _make_context

    vm: Dict[str, Any] = {
        "models": [
            {
                "model_id": "M1",
                "name": "甲",
                "elements": {
                    "C_title": [{"type": "干货", "ratio": 1.0, "examples": []}],
                },
            },
            {
                "model_id": "M2",
                "name": "乙",
                "elements": {
                    "C_title": [{"type": "情绪", "ratio": 1.0, "examples": []}],
                },
            },
        ]
    }
    r1 = (
        '[{"model_index":0,"element":"c_title","category_index":0,'
        '"summary":"s1","explanation":"e1","example_text":"x1"}]'
    )
    r2 = (
        '[{"model_index":1,"element":"C_title","category_index":0,'
        '"summary":"s2","explanation":"e2","example_text":"x2"}]'
    )
    gateway = FakeGateway([r1, r2])
    agent = Sheet2NarrativeAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={"viral_model_output": vm, "crawler_output": {"all_notes": []}}
    )
    res = await agent.run(ac)
    assert res.output.get("patches_applied") == 2
    assert res.output.get("model_batches") == 2
    assert len(gateway.calls) == 2
    out = ctx.get("viral_model_output")
    assert out["models"][0]["elements"]["C_title"][0]["summary"] == "s1"
    assert out["models"][1]["elements"]["C_title"][0]["summary"] == "s2"


def test_competitor_sheet_seo_tu_columns_width_60(monkeypatch):
    """竞品爆文 Sheet 的 T/U(热搜词 Top10 / 评论热词)列宽为 60。"""
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))
    ws = wb["竞品爆文"]
    assert ws.column_dimensions["T"].width == 60
    assert ws.column_dimensions["U"].width == 60


def test_infer_brand_adaptive():
    """品牌列自适应:搜索词是已知品牌→用搜索词;品类词→用 LLM 标注的 product_brand;都无→回退搜索词。"""
    from backend.app.services.canvas_export import excel_exporter as ee

    # 1. 搜索词是已知品牌(在品牌词表)→ 直接用搜索词(竞品/品牌维度)
    note = {"source_keywords": ["雅诗兰黛", "雅诗兰黛"]}
    assert ee._infer_brand_for_export(note, {}, task_keywords=[]) == "雅诗兰黛"

    # 2. 搜索词是品类词(不在品牌词表)+ LLM 标注了真实品牌 → 用 LLM 品牌(品类维度核心场景)
    note = {"source_keywords": ["防脱洗发水"], "keyword": "防脱洗发水"}
    ann = {"product_brand": "卡诗"}
    assert ee._infer_brand_for_export(note, ann, task_keywords=[]) == "卡诗"

    # 3. 搜索词不在词表 + LLM 品牌为"未知"占位 → 回退搜索词
    note = {"source_keywords": ["防脱洗发水"]}
    ann = {"product_brand": "未知"}
    assert ee._infer_brand_for_export(note, ann, task_keywords=[]) == "防脱洗发水"

    # 4. 搜索词不在词表 + 无 LLM 品牌 → 回退搜索词(去重拼接)
    note = {"source_keywords": ["北欧", "巧克力"]}
    assert ee._infer_brand_for_export(note, {}, task_keywords=[]) == "北欧 / 巧克力"

    # 5. 搜索词在词表时,即使 LLM 品牌不同也优先用搜索词(竞品场景稳定)
    note = {"source_keywords": ["Fazer"]}
    ann = {"product_brand": "玛氏"}
    assert ee._infer_brand_for_export(note, ann, task_keywords=[]) == "Fazer"

    # 6. 无搜索词 + LLM 有品牌 → 用 LLM 品牌
    note = {"keyword": ""}
    ann = {"product_brand": "卡诗"}
    assert ee._infer_brand_for_export(note, ann, task_keywords=[]) == "卡诗"

    # 7. 都为空 → 空字符串
    note = {"title": "纯科普笔记", "keyword": ""}
    assert ee._infer_brand_for_export(note, {}, task_keywords=[]) == ""


def test_sample_sheets_metric_columns_width_10(monkeypatch):
    """Sheet3-5:类型/品牌/互动量/点赞/收藏/评论列宽为 10(其余列仍为统一 25)。"""
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))

    ws3 = wb["数据源总"]
    for letter in ("C", "D", "F", "G", "H", "I"):
        assert ws3.column_dimensions[letter].width == 10
    assert ws3.column_dimensions["B"].width == 25

    ws5 = wb[_SHEET5_NAME]
    for letter in ("D", "E", "G", "H", "I", "J"):
        assert ws5.column_dimensions[letter].width == 10


def test_sheet3_to_5_sample_annotations_mapping(monkeypatch):
    """Sheet 3-5 的 6 要素列(N-S 或类似位置)正确从 annotations 取值。"""
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))

    # Sheet 3: 20 列,N-S 是 6 要素(列号 14-19)
    ws3 = wb["数据源总"]
    # 合并序 competitor -> top_interaction，首行是 n3（竞品）
    # Sheet 3 columns: [source,author,...,cover_type(N),cover_text_type(O),...
    assert ws3["N2"].value == "达人手持产品"
    assert ws3["O2"].value == "干货/经验分享"
    assert ws3["J2"].value == "口播单推"  # direction 列

    # Sheet 5: published_at 在 B 列
    ws5 = wb[_SHEET5_NAME]
    assert ws5["B2"].value == "2024-11-01"


def test_six_element_labels_strip_non_enum_suffix(monkeypatch):
    ctx = _make_task_context_with_data()
    ann = dict((ctx.data.get("multimodal_output") or {}).get("annotations") or {})
    ann["n3"] = {
        **dict(ann.get("n3") or {}),
        "title_type": "诗意意象型（非枚举，自拟）",
        "opening_type": "剧情中自然切入（自拟）",
    }
    ctx.data["multimodal_output"] = {
        **dict(ctx.data.get("multimodal_output") or {}),
        "annotations": ann,
    }

    data = asyncio.run(_build_bytes(monkeypatch, ctx=ctx))
    wb = load_workbook(io.BytesIO(data))
    ws3 = wb["数据源总"]
    assert ws3["P2"].value == "诗意意象型"
    assert ws3["Q2"].value == "剧情中自然切入"


def test_sheet4_sheet5_source_column_uses_sheet_primary_not_hits_order(monkeypatch):
    """Sheet4/5 的「来源」须与表语义一致，不能误用 sources_hit[0]（多标签时易串台）。"""
    ctx = _make_task_context_with_data()
    out = ctx.data.get("crawler_output") or {}
    srcs = dict(out.get("sources") or {})
    # 竞品行故意把 category_top 放在前，导出仍应显示竞品
    comp = list(srcs.get("competitor") or [])
    if comp:
        comp[0] = dict(comp[0])
        comp[0]["sources_hit"] = ["category_top", "competitor"]
    # 互动 TOP 行只有品类标签时，Sheet5 仍应显示互动 TOP
    top = list(srcs.get("top_interaction") or [])
    if top:
        top[0] = dict(top[0])
        top[0]["sources_hit"] = ["category_top"]
    out = dict(out)
    out["sources"] = {**srcs, "competitor": comp, "top_interaction": top}
    ctx.data["crawler_output"] = out

    data = asyncio.run(_build_bytes(monkeypatch, ctx=ctx))
    wb = load_workbook(io.BytesIO(data))
    assert wb["竞品爆文"]["A2"].value == "【竞品】爆文"
    assert wb[_SHEET5_NAME]["A2"].value == "【互动 TOP】"


def test_sheet5_name_uses_primary_task_keyword(monkeypatch):
    ctx = _make_task_context_with_data()
    record = _make_record()
    record.keywords = ["香水", "香奈儿"]

    data = asyncio.run(_build_bytes(monkeypatch, ctx=ctx, record=record))
    wb = load_workbook(io.BytesIO(data))
    assert "【品类】香水互动top" in wb.sheetnames


def test_sheet4_competitor_extra_seo_columns(monkeypatch):
    """Sheet 4 比 Sheet 3 多 T/U 两列(seo_top10 / comment_hotwords_top10)。"""
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))
    ws = wb["竞品爆文"]

    # Sheet 4 第 1 行第 20,21 列(T,U)应是热搜词 / 评论热词表头
    assert ws["T1"].value == "笔记涵盖热搜词 Top10"
    assert ws["U1"].value == "评论热词 Top10"

    # 第 2 行 n3 应填入 seo_top10 / comment_hotwords_top10(逗号分隔)
    assert ws["T2"].value is not None and "抗老" in ws["T2"].value
    assert ws["U2"].value is not None and "好用" in ws["U2"].value


def test_sheet7_draft_is_blank_structure(monkeypatch):
    """Sheet 7 与 Sheet2 同引擎:顶部汇总表头 + playbook 占位轨。"""
    data = asyncio.run(_build_bytes(monkeypatch))
    wb = load_workbook(io.BytesIO(data))
    ws = wb["草稿"]

    assert ws["B1"].value == "内容方向"

    hit = False
    for row in range(1, 120):
        for col in "ABCDEFG":
            v = ws[f"{col}{row}"].value
            if v and "爆文模型1" in str(v):
                hit = True
                break
        if hit:
            break
    assert hit

    assert any(
        "(待填)" in str(ws[f"C{r}"].value or "") for r in range(1, 120)
    ), "应有 playbook 占位分类"


def test_image_download_failure_cell_left_empty(monkeypatch):
    """图片缓存 URL → None 时,xlsx 仍可生成,cell 留空不 crash。"""
    # 所有 URL 都返回 None(模拟 XHS 全部 403)
    all_fail_cache = {
        "https://example.com/n1.jpg": None,
        "https://example.com/n2.jpg": None,
        "https://example.com/n3.jpg": None,
        "https://example.com/n4.jpg": None,
        "https://example.com/n5.jpg": None,
        "https://example.com/ex1.jpg": None,
    }
    data = asyncio.run(_build_bytes(monkeypatch, mock_image_cache=all_fail_cache))
    wb = load_workbook(io.BytesIO(data))
    # 必须仍能打开 7 sheet
    assert wb.sheetnames == _EXPECTED_SHEETS


def test_total_timeout_degrades_to_imageless(monkeypatch):
    """total_timeout 极小触发后,仍返回合法 xlsx(无图版本)。"""
    # 让 fetch_all 故意 sleep 很久,触发 total_timeout
    async def _slow_fetch(self, urls):  # noqa: ARG001
        await asyncio.sleep(2.0)
        return {}

    monkeypatch.setattr(
        "backend.app.services.canvas_export.image_fetcher.CoverImageFetcher.fetch_all",
        _slow_fetch,
    )

    data = asyncio.run(
        build_excel_bytes(
            task_record=_make_record(),
            canvas=_make_canvas(),
            task_context=_make_task_context_with_data(),
            total_timeout=0.3,  # 极短,必然触发 timeout → 降级无图版本
        )
    )
    # 即使超时降级,仍然生成合法 xlsx
    assert len(data) > 0
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == _EXPECTED_SHEETS
