"""
4.3pre.3 CanvasRenderAgent 新画布模块契约测试(品类 TOP 无独立样本卡)。

覆盖:
- 每个新模块的最小合法 content 契约(字段存在 + 类型正确)
- paragraph_id 全局唯一(同一画布里不重复)
- paragraph_id 格式符合规则表(P{i} / S-core-{i} / S-long-{i} / {module_id}-note-{note_id} / M{i} / M{i}-{CODE}-C{j})
- stats_axis_label 默认值 "高频痛点 / 议程",且能被 semantic_output 覆盖
- 空输入兜底:crawler / semantic / viral_matrix 都为空时仍产出全部固定模块 + Layer3 六分卡(不崩)
- 不再出现旧 11 模块 id(断链保护)
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from backend.app.application.agents.base import AgentContext
from backend.app.application.agents.canvas_render_agent import CanvasRenderAgent
from backend.app.application.task_service import TaskService
from backend.app.domain.task_context import task_context_store
from backend.app.infrastructure.repository.task_repository import TaskRepository


_BANNED_OLD_MODULE_IDS = {
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

_BASE_CANVAS_MODULE_IDS = {
    "mod-overview-stats",
    "mod-viral-model-matrix",
    "mod-seo-insights",
    "mod-pain-points",
    "mod-draft-workbench",
}

# advanced_config.note_type 为「不限」或未设置时:Layer3 为 6 张分载体样本卡
_LAYER3_SPLIT_SAMPLE_IDS = {
    "mod-competitor-samples-image",
    "mod-competitor-samples-video",
    "mod-top-interaction-samples-image",
    "mod-top-interaction-samples-video",
    "mod-serp-top-samples-image",
    "mod-serp-top-samples-video",
}

_LAYER3_CLASSIC_SAMPLE_IDS = {
    "mod-competitor-samples",
    "mod-top-interaction-samples",
    "mod-serp-top-samples",
}

_REQUIRED_NEW_MODULE_IDS_UNLIMITED = _BASE_CANVAS_MODULE_IDS | _LAYER3_SPLIT_SAMPLE_IDS


@pytest.fixture
def isolated_task_service(tmp_path: Path, monkeypatch):
    """隔离 TaskRepository + TaskService 单例,避免 CanvasRenderAgent 写到真实数据。"""
    repo = TaskRepository(storage_dir=tmp_path / "tasks")
    svc = TaskService()

    import backend.app.application.task_service as ts
    import backend.app.infrastructure.repository as repo_module
    import backend.app.application.agents.canvas_render_agent as canvas_render

    monkeypatch.setattr(repo_module, "task_repository", repo)
    monkeypatch.setattr(ts, "task_repository", repo)
    monkeypatch.setattr(ts, "task_service", svc)
    monkeypatch.setattr(canvas_render, "task_service", svc)
    return svc


def _seed_full_context(tid: str) -> None:
    """投喂完整上游数据(viral_matrix / semantic / crawler / multimodal)。"""
    ctx = task_context_store.require(tid)
    ctx.data["crawler_output"] = {
        "source": "cache",
        "cache_source": "test",
        "sample_count": 3,
        "keywords": ["抗老精华"],
        "all_notes": [
            {
                "note_id": f"n{i}",
                "title": f"样本 {i}",
                "likes": 100 * i,
                "comments": 5 * i,
                "collects": 10 * i,
                "media_type": "image" if i % 2 else "video",
                "cover_url": f"https://example.com/{i}.jpg",
                "nickname": f"作者 {i}",
                "url": f"https://www.xiaohongshu.com/explore/n{i}",
                "sources_hit": ["category_top"],
            }
            for i in range(1, 4)
        ],
        "sources": {
            "category_top": [
                {"note_id": "n1", "title": "样本 1", "nickname": "A",
                 "likes": 100, "comments": 5, "collects": 10,
                 "media_type": "image", "cover_url": "u1",
                 "url": "https://xhs/n1", "sources_hit": ["category_top"]},
            ],
            "competitor": [
                {"note_id": "n2", "title": "样本 2", "nickname": "B",
                 "likes": 200, "comments": 10, "collects": 20,
                 "media_type": "video", "cover_url": "u2",
                 "url": "https://xhs/n2",
                 "sources_hit": ["competitor"],
                 "seo_top10": ["抗老", "精华"],
                 "comment_hotwords_top10": ["好用"]},
            ],
            "top_interaction": [
                {"note_id": "n3", "title": "样本 3", "nickname": "C",
                 "likes": 300, "comments": 15, "collects": 30,
                 "media_type": "image", "cover_url": "u3",
                 "url": "https://xhs/n3",
                 "sources_hit": ["top_interaction"]},
            ],
            "serp_top": [
                {"note_id": "n1", "title": "样本 1", "nickname": "A",
                 "likes": 100, "comments": 5, "collects": 10,
                 "media_type": "image", "cover_url": "u1",
                 "url": "https://xhs/n1",
                 "sources_hit": ["category_top", "serp_top"]},
            ],
        },
    }
    ctx.data["multimodal_output"] = {
        "annotations": {
            "n1": {"content_direction": "口播单推", "pain_keywords": "暗沉,细纹"},
            "n2": {"content_direction": "剧情", "pain_keywords": "暗沉,泛红"},
            "n3": {"content_direction": "知识科普", "pain_keywords": "法令纹"},
        }
    }
    ctx.data["viral_model_output"] = {
        "models": [
            {
                "model_id": "M1",
                "name": "口播单推型",
                "description": "达人口播推荐",
                "coverage": 0.4,
                "avg_interaction": 150,
                "sample_note_ids": ["n1"],
                "elements": {
                    "A_cover": [
                        {"type": "纯产品图", "ratio": 0.6, "count": 6,
                         "examples": [], "paragraph_id": "M1-A_cover-C1"},
                        {"type": "前后对比", "ratio": 0.4, "count": 4,
                         "examples": [], "paragraph_id": "M1-A_cover-C2"},
                    ],
                    "C_title": [
                        {"type": "痛点+解决方案", "ratio": 1.0, "count": 10,
                         "examples": [], "paragraph_id": "M1-C_title-C1"},
                    ],
                },
                "paragraph_id": "M1",
            },
            {
                "model_id": "M2",
                "name": "干货分享型",
                "description": "教程演示",
                "coverage": 0.3,
                "avg_interaction": 120,
                "sample_note_ids": ["n3"],
                "elements": {
                    "D_opening": [
                        {"type": "干货切入", "ratio": 1.0, "count": 5,
                         "examples": [], "paragraph_id": "M2-D_opening-C1"},
                    ],
                },
                "paragraph_id": "M2",
            },
        ],
        "unused_directions": [
            {"direction": "日常 vlog", "ratio": 0.05, "avg_interaction": 20000,
             "reason": "闭环验证差"},
        ],
        "total_sample_count": 20,
        "taxonomy_version": "v2",
    }
    ctx.data["semantic_output"] = {
        "content_direction": {
            "top_direction": "口播单推",
            "summary_points": ["方向集中", "热度上升"],
            "highlight": "口播主导",
        },
        "pain_points_top": [
            {"keyword": "暗沉", "count": 12},
            {"keyword": "细纹", "count": 8},
            {"keyword": "法令纹", "count": 5},
        ],
        "seo_aggregation": {
            "core_keywords": [
                {"keyword": "抗老", "count": 15},
                {"keyword": "精华", "count": 10},
            ],
            "long_tail": [
                {"keyword": "30 天见效", "count": 3},
            ],
            "differentiation_advice": "聚焦细纹场景",
        },
        "stats_axis_label": "高频痛点 / 议程",
    }


def _run_canvas_agent(tid: str) -> None:
    ctx = task_context_store.require(tid)
    agent = CanvasRenderAgent()
    asyncio.run(agent.run(AgentContext(task_id=tid, task_context=ctx)))


# ----------------------------------------------------------------------
# 主测试
# ----------------------------------------------------------------------


def test_canvas_contains_all_new_modules_and_no_legacy(isolated_task_service):
    svc: TaskService = isolated_task_service
    res = svc.create_task(owner_user_id="u", raw_input="抗老精华", keywords=["抗老"])
    _seed_full_context(res.record.task_id)
    _run_canvas_agent(res.record.task_id)

    canvas = svc.get_canvas(res.record.task_id)
    ids = {m.module_id for m in canvas.modules}

    assert ids >= _REQUIRED_NEW_MODULE_IDS_UNLIMITED, (
        f"缺失新模块: {_REQUIRED_NEW_MODULE_IDS_UNLIMITED - ids}"
    )
    leaked = ids & _BANNED_OLD_MODULE_IDS
    assert not leaked, f"旧模块 id 不应再出现: {leaked}"


def test_canvas_modules_have_minimal_contracts(isolated_task_service):
    svc: TaskService = isolated_task_service
    res = svc.create_task(owner_user_id="u", raw_input="契约校验", keywords=["k"])
    _seed_full_context(res.record.task_id)
    _run_canvas_agent(res.record.task_id)

    canvas = svc.get_canvas(res.record.task_id)
    modules = {m.module_id: m for m in canvas.modules}

    # overview
    ov = modules["mod-overview-stats"].content
    for f in (
        "total_notes",
        "image_count",
        "video_count",
        "source_breakdown",
        "direction_breakdown",
        "keyword",
    ):
        assert f in ov, f"mod-overview-stats 缺字段: {f}"
    assert isinstance(ov["source_breakdown"], list)
    assert isinstance(ov["direction_breakdown"], list)

    # viral matrix
    vm = modules["mod-viral-model-matrix"].content
    assert "matrix" in vm
    mx = vm["matrix"]
    for f in ("models", "unused_directions", "total_sample_count", "stats_axis_label"):
        assert f in mx
    assert len(mx["models"]) == 2

    # Layer3 分载体样本表统一契约(不限=6 卡)
    for mid in _LAYER3_SPLIT_SAMPLE_IDS:
        c = modules[mid].content
        assert "source_type" in c
        assert "sample_count" in c
        assert c.get("media_kind") in ("image", "video")
        assert isinstance(c["notes"], list)
        for row in c["notes"]:
            for f in ("note_id", "title", "likes", "cover_url"):
                assert f in row

    # pain points
    pp = modules["mod-pain-points"].content
    assert "stats_axis_label" in pp
    assert isinstance(pp["items"], list)
    for item in pp["items"]:
        assert {"keyword", "count", "paragraph_id"} <= set(item.keys())

    # seo
    seo = modules["mod-seo-insights"].content
    for f in ("core_keywords", "long_tail", "differentiation_advice"):
        assert f in seo
    for item in seo["core_keywords"]:
        assert {"keyword", "count", "paragraph_id"} <= set(item.keys())

    # draft
    dw = modules["mod-draft-workbench"].content
    assert dw["template"] == "blank_skeleton"
    assert set(dw["fields"].keys()) == {
        "title", "cover_concept", "hook", "structure", "product_intro", "cta",
    }


def test_paragraph_ids_are_globally_unique(isolated_task_service):
    svc: TaskService = isolated_task_service
    res = svc.create_task(owner_user_id="u", raw_input="pid 唯一性", keywords=["k"])
    _seed_full_context(res.record.task_id)
    _run_canvas_agent(res.record.task_id)

    canvas = svc.get_canvas(res.record.task_id)
    pids = _collect_paragraph_ids(canvas.to_dict())
    assert pids, "画布应产出至少一批 paragraph_id"
    dup = [p for p in pids if pids.count(p) > 1]
    assert not dup, f"paragraph_id 重复: {set(dup)}"


def test_paragraph_id_formats_follow_spec(isolated_task_service):
    svc: TaskService = isolated_task_service
    res = svc.create_task(owner_user_id="u", raw_input="pid 格式", keywords=["k"])
    _seed_full_context(res.record.task_id)
    _run_canvas_agent(res.record.task_id)

    canvas = svc.get_canvas(res.record.task_id)
    modules = {m.module_id: m for m in canvas.modules}

    # Viral matrix: 两级 paragraph_id
    model_pids: list[str] = []
    cat_pids: list[str] = []
    for model in modules["mod-viral-model-matrix"].content["matrix"]["models"]:
        pid = model.get("paragraph_id")
        assert pid is not None and re.fullmatch(r"M\d+", pid)
        model_pids.append(pid)
        for cats in model["elements"].values():
            for cat in cats:
                cpid = cat.get("paragraph_id")
                assert cpid is not None
                assert re.fullmatch(
                    r"M\d+-[A-F]_[a-z_]+-C\d+", cpid
                ), f"ElementCategory paragraph_id 格式不匹配: {cpid}"
                cat_pids.append(cpid)

    assert len(model_pids) == len(set(model_pids))  # 模型级唯一
    assert len(cat_pids) == len(set(cat_pids))      # 分类级唯一

    # Pain points: P{i}
    for i, item in enumerate(modules["mod-pain-points"].content["items"], start=1):
        assert item["paragraph_id"] == f"P{i}"

    # SEO: S-core-{i} / S-long-{i}
    for i, item in enumerate(
        modules["mod-seo-insights"].content["core_keywords"], start=1
    ):
        assert item["paragraph_id"] == f"S-core-{i}"
    for i, item in enumerate(
        modules["mod-seo-insights"].content["long_tail"], start=1
    ):
        assert item["paragraph_id"] == f"S-long-{i}"

    # 样本类:{module_id}-note-{note_id}
    for mid in _LAYER3_SPLIT_SAMPLE_IDS:
        for row in modules[mid].content["notes"]:
            assert row["paragraph_id"] == f"{mid}-note-{row['note_id']}"


def test_stats_axis_label_default_fallback(isolated_task_service):
    svc: TaskService = isolated_task_service
    res = svc.create_task(owner_user_id="u", raw_input="默认轴名", keywords=["k"])
    ctx = task_context_store.require(res.record.task_id)
    # 完全不提供 semantic_output.stats_axis_label 字段
    ctx.data["crawler_output"] = {"sample_count": 0, "keywords": ["k"],
                                   "all_notes": [], "sources": {}}
    ctx.data["multimodal_output"] = {"annotations": {}}
    ctx.data["viral_model_output"] = {"models": [], "unused_directions": [],
                                       "total_sample_count": 0}
    ctx.data["semantic_output"] = {
        "pain_points_top": [{"keyword": "暗沉", "count": 3}],
        # 故意不写 stats_axis_label
    }
    _run_canvas_agent(res.record.task_id)

    canvas = svc.get_canvas(res.record.task_id)
    pp = next(m for m in canvas.modules if m.module_id == "mod-pain-points")
    assert pp.content["stats_axis_label"] == "高频痛点 / 议程"
    assert "高频痛点 / 议程" in pp.title


def test_stats_axis_label_overridable_by_semantic(isolated_task_service):
    svc: TaskService = isolated_task_service
    res = svc.create_task(owner_user_id="u", raw_input="覆盖轴名", keywords=["k"])
    ctx = task_context_store.require(res.record.task_id)
    ctx.data["crawler_output"] = {"sample_count": 0, "keywords": ["k"],
                                   "all_notes": [], "sources": {}}
    ctx.data["multimodal_output"] = {"annotations": {}}
    ctx.data["viral_model_output"] = {"models": [], "unused_directions": [],
                                       "total_sample_count": 0}
    ctx.data["semantic_output"] = {
        "pain_points_top": [{"keyword": "黑眼圈", "count": 8}],
        "stats_axis_label": "皮肤问题",
    }
    _run_canvas_agent(res.record.task_id)

    canvas = svc.get_canvas(res.record.task_id)
    pp = next(m for m in canvas.modules if m.module_id == "mod-pain-points")
    assert pp.content["stats_axis_label"] == "皮肤问题"
    assert "皮肤问题" in pp.title


def test_canvas_layer3_classic_when_note_type_image(isolated_task_service):
    """高级配置仅图文时 Layer3 仍为 3 张经典样本卡(过滤为图文行)。"""
    svc: TaskService = isolated_task_service
    res = svc.create_task(owner_user_id="u", raw_input="仅图文", keywords=["k"])
    _seed_full_context(res.record.task_id)
    ctx = task_context_store.require(res.record.task_id)
    ctx.data["input_spec"] = {
        "raw_input": "仅图文",
        "advanced_config": {"note_type": "图文"},
    }
    _run_canvas_agent(res.record.task_id)
    canvas = svc.get_canvas(res.record.task_id)
    ids = {m.module_id for m in canvas.modules}
    expected = _BASE_CANVAS_MODULE_IDS | _LAYER3_CLASSIC_SAMPLE_IDS
    assert ids >= expected
    assert not (ids & _LAYER3_SPLIT_SAMPLE_IDS)
    for mid in _LAYER3_CLASSIC_SAMPLE_IDS:
        mod = canvas.find_module(mid)
        assert mod is not None
        assert mod.content.get("media_kind") == "image"


def test_empty_inputs_still_produce_all_eight_modules(isolated_task_service):
    svc: TaskService = isolated_task_service
    res = svc.create_task(owner_user_id="u", raw_input="空输入", keywords=[])
    ctx = task_context_store.require(res.record.task_id)
    # 所有分区都是空 dict
    ctx.data["crawler_output"] = {}
    ctx.data["multimodal_output"] = {}
    ctx.data["viral_model_output"] = {}
    ctx.data["semantic_output"] = {}
    _run_canvas_agent(res.record.task_id)

    canvas = svc.get_canvas(res.record.task_id)
    ids = {m.module_id for m in canvas.modules}
    assert ids >= _REQUIRED_NEW_MODULE_IDS_UNLIMITED

    # 整个 canvas.to_dict() 可以被 json.dumps(无异常)
    import json
    json.dumps(canvas.to_dict(), ensure_ascii=False)


# ----------------------------------------------------------------------
# helper
# ----------------------------------------------------------------------


def _collect_paragraph_ids(node) -> list[str]:
    """深度遍历 canvas.to_dict(),收集所有 paragraph_id 值。"""
    out: list[str] = []
    if isinstance(node, dict):
        pid = node.get("paragraph_id")
        if isinstance(pid, str) and pid:
            out.append(pid)
        for v in node.values():
            out.extend(_collect_paragraph_ids(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_collect_paragraph_ids(item))
    return out
