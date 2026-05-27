"""
阶段 4.3pre.3 Eval 基线(可靠性 + 结构完整度 + 导出一致性)。

这组测试不依赖真实大模型输出,只验证契约和结构,目的是:
- 4.3pre.3 回归时快速发现"结构偏移"
- 后续接入 Ragas/Promptfoo 时作为上层补丁的基线

跑法:
    pytest backend/tests/eval -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.deterministic

from backend.app.application.task_service import TaskService
from backend.app.application.agents.canvas_render_agent import CanvasRenderAgent
from backend.app.domain.task_context import task_context_store
from backend.app.infrastructure.repository.task_repository import TaskRepository

from .dataset import EVAL_SAMPLES, REQUIRED_CANVAS_MODULES, summarize_results


@pytest.fixture
def isolated_repo(tmp_path: Path, monkeypatch):
    repo = TaskRepository(storage_dir=tmp_path / "tasks")
    import backend.app.application.task_service as ts
    import backend.app.infrastructure.repository as repo_module

    monkeypatch.setattr(repo_module, "task_repository", repo)
    monkeypatch.setattr(ts, "task_repository", repo)
    return repo


@pytest.fixture
def fake_canvas_task(isolated_repo, monkeypatch):
    """同时替换全局 task_service,保证 CanvasRenderAgent 调用同一个实例。"""
    svc = TaskService()
    import backend.app.application.task_service as ts
    import backend.app.application.agents.canvas_render_agent as canvas_render

    monkeypatch.setattr(ts, "task_service", svc)
    monkeypatch.setattr(canvas_render, "task_service", svc)
    return svc


def test_keyword_search_task_creation_baseline(fake_canvas_task):
    svc: TaskService = fake_canvas_task
    results = []
    for sample in [s for s in EVAL_SAMPLES if s.scenario == "keyword_search"]:
        res = svc.create_task(
            owner_user_id="eval-user",
            raw_input=sample.raw_input,
            keywords=sample.keywords,
            idempotency_key=f"eval-{sample.sample_id}",
        )
        ok = res.created and res.record.task_id and res.record.keywords == sample.keywords
        results.append({"sample_id": sample.sample_id, "scenario": sample.scenario, "ok": ok})

    summary = summarize_results(results)
    assert summary["pass_rate"] == 1.0, summary


def test_canvas_new_modules_cover_required_nine(fake_canvas_task):
    """4.3pre.3: 画布必须覆盖 9 核心模块(8 AI + 1 静态草稿)。

    用最小上游数据投喂 CanvasRenderAgent,验证产出的 canvas.modules 覆盖全部目标 id。
    """
    svc: TaskService = fake_canvas_task
    res = svc.create_task(owner_user_id="u", raw_input="结构覆盖回归", keywords=["k"])
    tid = res.record.task_id

    ctx = task_context_store.require(tid)

    # 最小上游 seed:保证每个构造器都有 non-empty 输入
    ctx.data["crawler_output"] = {
        "source": "cache",
        "cache_source": "test",
        "sample_count": 2,
        "keywords": ["k"],
        "all_notes": [
            {
                "note_id": "n1",
                "title": "样本 1",
                "likes": 100,
                "comments": 5,
                "collects": 10,
                "media_type": "image",
                "cover_url": "https://example.com/a.jpg",
                "nickname": "作者 A",
                "url": "https://www.xiaohongshu.com/explore/n1",
                "sources_hit": ["category_top", "top_interaction"],
            },
            {
                "note_id": "n2",
                "title": "样本 2",
                "likes": 200,
                "comments": 8,
                "collects": 20,
                "media_type": "video",
                "cover_url": "https://example.com/b.jpg",
                "nickname": "作者 B",
                "url": "https://www.xiaohongshu.com/explore/n2",
                "sources_hit": ["competitor"],
            },
        ],
        "sources": {
            "category_top": [{"note_id": "n1", "title": "样本 1", "media_type": "image"}],
            "competitor": [{"note_id": "n2", "title": "样本 2", "media_type": "video"}],
            "top_interaction": [{"note_id": "n1", "title": "样本 1", "media_type": "image"}],
        },
    }
    ctx.data["multimodal_output"] = {
        "annotations": {
            "n1": {"content_direction": "口播单推", "pain_keywords": "暗沉,细纹"},
            "n2": {"content_direction": "剧情", "pain_keywords": "暗沉,泛红"},
        }
    }
    ctx.data["viral_model_output"] = {
        "models": [
            {
                "model_id": "M1",
                "name": "口播单推型",
                "description": "达人口播推荐",
                "coverage": 0.5,
                "avg_interaction": 150,
                "sample_note_ids": ["n1"],
                "elements": {
                    "A_cover": [
                        {
                            "type": "纯产品图",
                            "ratio": 1.0,
                            "count": 1,
                            "examples": [],
                            "paragraph_id": "M1-A_cover-C1",
                        }
                    ]
                },
                "paragraph_id": "M1",
            }
        ],
        "unused_directions": [],
        "total_sample_count": 2,
        "taxonomy_version": "0",
    }
    ctx.data["semantic_output"] = {
        "content_direction": {
            "top_direction": "口播单推",
            "summary_points": ["方向集中"],
            "highlight": "口播主导",
        },
        "pain_points_top": [
            {"keyword": "暗沉", "count": 2},
            {"keyword": "细纹", "count": 1},
        ],
        "seo_aggregation": {
            "core_keywords": [{"keyword": "抗老", "count": 3}],
            "long_tail": [{"keyword": "30 天见效", "count": 1}],
            "differentiation_advice": "聚焦细纹场景",
        },
        "stats_axis_label": "高频痛点 / 议程",
    }

    import asyncio

    async def _run():
        agent = CanvasRenderAgent()
        from backend.app.application.agents.base import AgentContext

        return await agent.run(AgentContext(task_id=tid, task_context=ctx))

    asyncio.run(_run())

    canvas = svc.get_canvas(tid)
    module_ids = {m.module_id for m in canvas.modules}
    missing = REQUIRED_CANVAS_MODULES - module_ids
    assert not missing, f"缺失画布模块: {missing}"
    # 旧 11 模块断言下线:绝对不应再出现
    banned = {
        "mod-title-strategy",
        "mod-product-strategy",
        "mod-cover-strategy",
        "mod-structure-strategy",
        "mod-insight-industry",
        "mod-insight-competitor",
        "mod-insight-brand",
        "mod-insight-knowledge",
        "mod-image-analysis",
        "mod-video-analysis",
        "mod-crawler-sample",
    }
    leaked = module_ids & banned
    assert not leaked, f"旧模块 id 仍出现在新画布里: {leaked}"


def test_export_snapshot_structure_consistent(fake_canvas_task, tmp_path: Path):
    svc: TaskService = fake_canvas_task
    res = svc.create_task(owner_user_id="u", raw_input="导出一致性", keywords=["x"])
    canvas = svc.get_canvas(res.record.task_id)
    body = {"task_id": res.record.task_id, "canvas": canvas.to_dict()}
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["task_id"] == res.record.task_id
    assert "modules" in reloaded["canvas"]
    assert "dimensions" in reloaded["canvas"]
    assert "themes" in reloaded["canvas"]
