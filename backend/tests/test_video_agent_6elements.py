"""4.3pre.2: VideoAnalysisAgent 改为同步并发 + 6 要素标注 schema。"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from tests.test_agents_real import FakeBus, FakeGateway, _make_context


_VALID_VIDEO_ANN = (
    '{"cover_type": "前后对比", "cover_text_type": "干货/经验分享", '
    '"title_type": "干货/经验分享", "opening_type": "痛点切入", '
    '"product_intro_type": "直接带出", "product_placement_type": "融合自己使用方法/感受讲卖点", '
    '"pain_keywords": "暗沉/松垮", "content_direction": "口播单推"}'
)


@pytest.mark.asyncio
async def test_video_agent_writes_annotations_synchronously():
    """run() 同步 await,完成后 multimodal_output.annotations 已经填充。

    无后台任务,无 async_pending 字段,无 TASK_VIDEO_DONE 事件。
    """
    from backend.app.application.agents.video_analysis_agent import VideoAnalysisAgent

    gateway = FakeGateway([_VALID_VIDEO_ANN, _VALID_VIDEO_ANN])
    agent = VideoAnalysisAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "x"},
        extra={
            "crawler_output": {
                "notes_video": [
                    {"note_id": "v1", "title": "v1", "video_url": "https://x/v1.mp4", "interaction_score": 1000},
                    {"note_id": "v2", "title": "v2", "video_url": "https://x/v2.mp4", "interaction_score": 500},
                ]
            }
        },
    )
    result = await agent.run(ac)

    assert result.ok
    mm = ctx.get("multimodal_output")
    annotations = mm["annotations"]
    assert set(annotations.keys()) == {"v1", "v2"}
    # schema 字段都在
    assert annotations["v1"]["cover_type"] == "前后对比"
    assert annotations["v1"]["content_direction"] == "口播单推"
    assert annotations["v1"]["source_agent"] == "VideoAnalysisAgent"
    # 没有任何 async_pending 字段
    assert "video" not in mm or "async_pending" not in (mm.get("video") or {})


@pytest.mark.asyncio
async def test_video_agent_invalid_json_falls_back_to_failed():
    """LLM 输出乱码 → 重试 1 次,仍失败 → 该 note 不出现在 annotations,统计 failed+1。"""
    from backend.app.application.agents.video_analysis_agent import VideoAnalysisAgent

    gateway = FakeGateway(["乱码", "乱码", _VALID_VIDEO_ANN])  # v1 失败两次,v2 成功一次
    agent = VideoAnalysisAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "x"},
        extra={
            "crawler_output": {
                "notes_video": [
                    {"note_id": "v1", "title": "v1", "video_url": "https://x/v1.mp4", "interaction_score": 2000},
                    {"note_id": "v2", "title": "v2", "video_url": "https://x/v2.mp4", "interaction_score": 1000},
                ]
            }
        },
    )
    await agent.run(ac)

    mm = ctx.get("multimodal_output")
    # v1 失败被踢出,只剩 v2
    assert list(mm["annotations"].keys()) == ["v2"]
    assert mm["video_stats"]["failed"] == 1
    assert mm["video_stats"]["success"] == 1


@pytest.mark.asyncio
async def test_video_agent_disabled_toggle_skips_analysis(monkeypatch):
    """settings.video_analysis_enabled=False → run() 跳过,写空 annotations。"""
    from backend.app.application.agents.video_analysis_agent import VideoAnalysisAgent
    from backend.app.services import system_settings_store as sss_mod

    # 直接 monkey-patch get() 返回 disabled
    class _FakeStore:
        async def get(self):
            return {"video_analysis_enabled": False}

    monkeypatch.setattr(sss_mod, "get_system_settings_store", lambda: _FakeStore())

    gateway = FakeGateway([])  # 不应有任何调用
    agent = VideoAnalysisAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "x"},
        extra={
            "crawler_output": {
                "notes_video": [
                    {"note_id": "v1", "video_url": "https://x/v.mp4", "interaction_score": 1000}
                ]
            }
        },
    )
    await agent.run(ac)

    assert gateway.calls == []
    mm = ctx.get("multimodal_output")
    assert mm["annotations"] == {}
    assert mm["video_stats"]["skipped_reason"] == "admin_toggle_off"
