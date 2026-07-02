"""评论分析流水线执行可视化 —— 阶段事件断言。

回归背景:comment_pipeline.py 此前全程用单一 agent_id="CommentPipeline" 上报进度，
前端 AGENT_STEPS 目录里没有这个 id，事件进了状态机但没有任何步骤行会渲染它。
本轮改造把各阶段拆成独立 agent_id（CommentInputParser/CommentCrawler/...），
本测试锁定：
1. `_emit_progress` / `_emit_log` 两个辅助函数按约定 payload 上报，且 `_emit_progress`
   会同步把 progress 写入 task_repository（此前只发 SSE，不落库）。
2. `_step4_dim2_analysis`（维度2 类别深度分析）逐类别产生独立的 `CommentDim2` LOG，
   不再是 65% 卡住不动直到全部类别分析完。
"""
from __future__ import annotations

import pytest

from backend.app.application import comment_pipeline as cp
from backend.app.domain.events import TaskEventType


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish_event(self, **kwargs):
        self.events.append(kwargs)


class _RecordingRepo:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, task_id: str, **changes):
        self.updates.append({"task_id": task_id, **changes})


@pytest.fixture()
def fake_bus(monkeypatch):
    bus = _RecordingBus()
    monkeypatch.setattr(cp, "task_event_bus", bus)
    return bus


@pytest.fixture()
def fake_repo(monkeypatch):
    repo = _RecordingRepo()
    monkeypatch.setattr(cp, "task_repository", repo)
    return repo


@pytest.mark.asyncio
async def test_emit_progress_tags_agent_id_and_persists_progress(fake_bus, fake_repo):
    await cp._emit_progress(
        "task_1", "库中已有 87 条笔记，需再采集 113 条，开始补爬...", 10,
        agent_id="CommentCrawler",
    )

    assert len(fake_bus.events) == 1
    event = fake_bus.events[0]
    assert event["task_id"] == "task_1"
    assert event["type"] == TaskEventType.AGENT_PROGRESS
    assert event["payload"] == {
        "agent_id": "CommentCrawler",
        "message": "库中已有 87 条笔记，需再采集 113 条，开始补爬...",
        "progress": 10,
        "done": False,
    }
    # 中途进度必须同步落库，刷新页面/REST 查询才能看到接近真实的进度
    assert fake_repo.updates == [{"task_id": "task_1", "progress": 10}]


@pytest.mark.asyncio
async def test_emit_progress_default_agent_id_keeps_v1_backward_compat(fake_bus, fake_repo):
    """v1 调用点不传 agent_id 时，沿用旧的单一 "CommentPipeline" 标识，不破坏兼容性。"""
    await cp._emit_progress("task_1", "解析搜索意图...", 5)

    assert fake_bus.events[0]["payload"]["agent_id"] == "CommentPipeline"
    assert fake_bus.events[0]["payload"]["done"] is False


@pytest.mark.asyncio
async def test_emit_progress_done_marker_flips_done_flag(fake_bus, fake_repo):
    await cp._emit_progress(
        "task_1", "新采集 12 条笔记，开始全量拉取评论（含子评论）...", 25,
        agent_id="CommentCrawler", done=True,
    )
    assert fake_bus.events[0]["payload"]["done"] is True


@pytest.mark.asyncio
async def test_emit_log_publishes_log_event_with_agent_id(fake_bus):
    await cp._emit_log("task_1", "CommentFetcher", "[3/12] note_id=abc 一级=5 子=2 累计一级=17")

    assert len(fake_bus.events) == 1
    event = fake_bus.events[0]
    assert event["task_id"] == "task_1"
    assert event["type"] == TaskEventType.LOG
    assert event["payload"] == {
        "agent_id": "CommentFetcher",
        "level": "info",
        "message": "[3/12] note_id=abc 一级=5 子=2 累计一级=17",
    }


def _dim1_result_with_two_categories() -> dict:
    # 两个类别各 5 条评论，均达到 _DIM1_MIN_CATEGORY_SIZE(=5) 门槛，
    # 确保 top_cats 过滤后两个类别都会真正进入 _analyze_one 分析(而非落入
    # "综合评价" 兜底分支或被门槛淘汰)。
    return {
        "categories": [
            {"name": "价格", "count": 5, "ratio": "50.0%"},
            {"name": "效果", "count": 5, "ratio": "50.0%"},
        ],
        "core_finding": "",
        "comment_cats": [
            {"index": i, "category": "价格" if i < 5 else "效果", "sentiment": "正面"}
            for i in range(10)
        ],
        "invalid_to_other_count": 0,
    }


@pytest.mark.asyncio
async def test_step4_dim2_analysis_emits_per_category_log_and_progress(monkeypatch, fake_bus):
    """每个类别分析完成后应各发一条 CommentDim2 LOG + 一次 CommentDim2 AGENT_PROGRESS，
    而不是全部类别分析完才统一上报一次(此前 65% 会卡住直到全部类别分析完)。
    """
    async def fake_llm_chat(agent_id, system, user, max_tokens=600, json_mode=False):
        return '{"positive": {"theory": "t", "description": "d", "examples": ["e1"]}}'

    monkeypatch.setattr(cp, "_llm_chat", fake_llm_chat)

    all_comments = [{"content": f"评论{i}", "like_count": 0} for i in range(10)]
    notes = [{"note_id": "n1", "title": "笔记1"}]
    dim1_result = _dim1_result_with_two_categories()

    result = await cp._step4_dim2_analysis(
        "task_1", all_comments, dim1_result, notes, ["关键词"],
    )

    assert set(result.keys()) == {"价格", "效果"}

    dim2_logs = [
        e for e in fake_bus.events
        if e["type"] == TaskEventType.LOG and e["payload"]["agent_id"] == "CommentDim2"
    ]
    assert len(dim2_logs) == 2  # 每个有效类别各一条完成日志
    logged_categories = {log["payload"]["message"].split("」")[0].lstrip("「") for log in dim2_logs}
    assert logged_categories == {"价格", "效果"}

    dim2_progress = [
        e for e in fake_bus.events
        if e["type"] == TaskEventType.AGENT_PROGRESS and e["payload"]["agent_id"] == "CommentDim2"
    ]
    assert len(dim2_progress) == 2
    # 进度应随完成数递增，而不是每次都停在同一个值
    progress_values = [e["payload"]["progress"] for e in dim2_progress]
    assert progress_values[0] < progress_values[1]
    assert progress_values[-1] == 65  # 52 + 13 * 2/2
