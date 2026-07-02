"""4.3pre.2: ImageAnalysisAgent 输出 schema 对齐 VideoAgent。"""

from __future__ import annotations

import pytest

from tests.test_agents_real import FakeBus, FakeGateway, _make_context


_VALID_IMAGE_ANN = (
    '{"cover_type": "好状态颜值照", "cover_text_type": "效果", '
    '"title_type": "笔记主题", "opening_type": "干货切入", '
    '"product_intro_type": "融入到干货/经验分享中", '
    '"product_placement_type": "融合自己使用方法/感受讲卖点", '
    '"pain_keywords": "暗沉", "content_direction": "干货分享", '
    '"product_brand": "雅诗兰黛"}'
)


@pytest.mark.asyncio
async def test_image_agent_merges_annotations_into_shared_dict():
    """Image 写 multimodal_output.annotations,merge 模式 → 与 VideoAgent 共享。"""
    from backend.app.application.agents.image_analysis_agent import ImageAnalysisAgent
    from backend.app.domain.task_context import TaskContextWriter

    gateway = FakeGateway([_VALID_IMAGE_ANN])
    agent = ImageAnalysisAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "x"},
        extra={
            "crawler_output": {
                "notes_image": [
                    {"note_id": "i1", "title": "i1", "cover_url": "https://x/c1", "interaction_score": 1000}
                ]
            }
        },
    )
    # 预先写入 VideoAgent 已经放的一个 annotation,模拟 merge 共享
    TaskContextWriter(ctx).write(
        "multimodal_output",
        {"annotations": {"v_pre": {"source_agent": "VideoAnalysisAgent"}}},
        agent_id="seed",
        merge=True,
    )

    await agent.run(ac)

    mm = ctx.get("multimodal_output")
    annotations = mm["annotations"]
    # merge 保留先前的 + 加新的
    assert "v_pre" in annotations
    assert "i1" in annotations
    assert annotations["i1"]["source_agent"] == "ImageAnalysisAgent"


def test_image_valid_annotation_accepts_custom_product_labels():
    from backend.app.application.agents.image_analysis_agent import ImageAnalysisAgent

    p = {
        "cover_type": "场景摆拍",
        "cover_text_type": "干货/经验分享",
        "title_type": "笔记主题",
        "opening_type": "干货切入",
        "product_intro_type": "先讲使用场景再带出设备型号",
        "product_placement_type": "口播参数配合画面字幕",
        "pain_keywords": "续航",
        "content_direction": "数码测评",
    }
    assert ImageAnalysisAgent._is_valid_annotation(p)


@pytest.mark.asyncio
async def test_image_agent_rejects_none_product_fields():
    """E/F 为 _NONE 时校验失败,本条不写入 annotations。"""
    from backend.app.application.agents.image_analysis_agent import ImageAnalysisAgent

    bad_ann = (
        '{"cover_type": "好状态颜值照", "cover_text_type": "效果", '
        '"title_type": "笔记主题", "opening_type": "干货切入", '
        '"product_intro_type": "_NONE", "product_placement_type": "_NONE", '
        '"pain_keywords": "暗沉", "content_direction": "干货分享"}'
    )
    gateway = FakeGateway([bad_ann, bad_ann])
    agent = ImageAnalysisAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "x"},
        extra={
            "crawler_output": {
                "notes_image": [
                    {"note_id": "i1", "title": "纯科普笔记", "cover_url": "https://x/c", "interaction_score": 100}
                ]
            }
        },
    )
    await agent.run(ac)

    mm = ctx.get("multimodal_output")
    assert "i1" not in (mm.get("annotations") or {})
    assert mm["image_stats"]["success"] == 0
    assert mm["image_stats"]["failed"] == 1


def test_coerce_annotation_keeps_product_brand_drops_unknown():
    """_coerce_annotation 保留 product_brand 等白名单字段,丢弃未知字段。"""
    from backend.app.application.agents.image_analysis_agent import (
        ImageAnalysisAgent,
        _ANNOTATION_KEYS,
    )

    parsed = {
        "cover_type": "场景摆拍",
        "cover_text_type": "干货/经验分享",
        "title_type": "笔记主题",
        "opening_type": "干货切入",
        "product_intro_type": "直接带出",
        "product_placement_type": "口播参数",
        "pain_keywords": "续航",
        "content_direction": "数码测评",
        "product_brand": "大疆",
        "unknown_field": "应被丢弃",
    }
    out = ImageAnalysisAgent._coerce_annotation(parsed)
    assert out["product_brand"] == "大疆"
    assert "unknown_field" not in out
    assert set(out.keys()) == set(_ANNOTATION_KEYS)
