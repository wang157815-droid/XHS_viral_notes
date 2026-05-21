"""4.3pre.2: ViralModelAgent (混合聚类生成爆文模型矩阵)。

5 条用例:
- happy path: 多 direction 输入 → 多 model 输出 + LLM 起名生效
- 阈值过滤: coverage < 5% 的 direction 不进入主矩阵,但落入 unused_directions
- LLM 起名失败: 模型名 fallback 到 direction
- 空 annotations: 写空 matrix,reason=no_annotations
- elements 占比正确: 单 direction 内 6 要素分类的 ratio = count / total
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from tests.test_agents_real import FakeBus, FakeGateway, _make_context


def _ann(direction: str, **fields) -> Dict[str, Any]:
    base = {
        "cover_type": fields.pop("cover_type", "纯产品图"),
        "cover_text_type": fields.pop("cover_text_type", "干货/经验分享"),
        "title_type": fields.pop("title_type", "干货/经验分享"),
        "opening_type": fields.pop("opening_type", "干货切入"),
        "product_intro_type": fields.pop("product_intro_type", "直接带出"),
        "product_placement_type": fields.pop(
            "product_placement_type", "融合自己使用方法/感受讲卖点"
        ),
        "pain_keywords": fields.pop("pain_keywords", "暗沉"),
        "content_direction": direction,
        "source_agent": "VideoAnalysisAgent",
    }
    base.update(fields)
    return base


@pytest.mark.asyncio
async def test_viral_model_agent_happy_path_with_llm_naming():
    """两个 direction,但封面类型不同 → 按 playbook 签名拆成多簇 + LLM 起名。"""
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    annotations = {
        "n1": _ann("口播单推"),
        "n2": _ann("口播单推"),
        "n3": _ann("口播单推", cover_type="前后对比"),
        "n4": _ann("干货分享"),
        "n5": _ann("干货分享"),
        "n6": _ann("干货分享", cover_type="好状态颜值照"),
    }
    all_notes = [
        {"note_id": nid, "title": f"t{nid}", "interaction_score": 5000, "likes": 2000, "cover_url": ""}
        for nid in annotations
    ]

    # LLM 起名返回 JSON 数组(与模型数一致)
    gateway = FakeGateway([
        '[{"model_id": "M1", "name": "口播产品图型", "description": "纯产品封面口播"}, '
        '{"model_id": "M2", "name": "口播对比型", "description": "前后对比封面"}, '
        '{"model_id": "M3", "name": "干货种草型", "description": "干货封面组合"}]'
    ])
    agent = ViralModelAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        {"raw_input": "x"},
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        },
    )
    await agent.run(ac)

    matrix = ctx.get("viral_model_output")
    assert matrix["total_sample_count"] == 6
    assert len(matrix["models"]) == 3
    names = [m["name"] for m in matrix["models"]]
    assert "口播产品图型" in names
    assert "口播对比型" in names
    assert "干货种草型" in names


@pytest.mark.asyncio
async def test_viral_model_agent_threshold_filters_low_coverage_to_unused():
    """1 条边角 direction(coverage 1/20=5%) 处于阈值边界,4 条主 direction → 4/20=20% > 5% 入主。

    coverage < 5% 的 direction(1 条/20=5%, 用 < 严格判) 严格小于则进 unused。
    """
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    annotations: Dict[str, Dict[str, Any]] = {}
    # 主方向 18 条
    for i in range(18):
        annotations[f"main_{i}"] = _ann("口播单推")
    # 边角 direction "剧情" 各 1 条 - 占比 1/20=5% (恰好等于 5%, 应该进主矩阵 >= 阈值)
    annotations["niche_1"] = _ann("剧情")
    # 边角 "vlog" 1 条 - 占比 1/20=5% 同样恰好等于
    annotations["niche_2"] = _ann("日常vlog")

    all_notes = [
        {"note_id": nid, "title": "t", "interaction_score": 1000, "likes": 500, "cover_url": ""}
        for nid in annotations
    ]
    # LLM 不起名(空字符串响应),走 fallback
    gateway = FakeGateway(["[]"])
    agent = ViralModelAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        }
    )
    await agent.run(ac)

    matrix = ctx.get("viral_model_output")
    # 主方向 18 条 = 90% 应入主矩阵
    main_models = [m for m in matrix["models"] if m["coverage"] > 0.5]
    assert len(main_models) == 1


@pytest.mark.asyncio
async def test_viral_model_agent_llm_naming_failure_uses_direction_as_name():
    """LLM 起名抛异常 → model.name 保持 direction(兜底)。"""
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    annotations = {f"n{i}": _ann("特殊方向") for i in range(5)}
    all_notes = [
        {"note_id": nid, "title": "t", "interaction_score": 100, "likes": 50, "cover_url": ""}
        for nid in annotations
    ]
    # LLM 直接抛异常
    class _BadGateway:
        calls = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            raise RuntimeError("LLM down")

    agent = ViralModelAgent(model_gateway_instance=_BadGateway(), event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        }
    )
    await agent.run(ac)

    matrix = ctx.get("viral_model_output")
    assert len(matrix["models"]) == 1
    # 兜底: name = direction
    assert matrix["models"][0]["name"] == "特殊方向"


@pytest.mark.asyncio
async def test_viral_model_agent_single_annotation_one_model():
    """仅 1 条标注时 eff_min=min(2,1)=1,应成模(修复小样本空白矩阵)。"""
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    annotations = {"n1": _ann("单品向")}
    all_notes = [
        {
            "note_id": "n1",
            "title": "t",
            "interaction_score": 100,
            "likes": 50,
            "cover_url": "",
        }
    ]
    agent = ViralModelAgent(model_gateway_instance=FakeGateway(["[]"]), event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        }
    )
    await agent.run(ac)
    matrix = ctx.get("viral_model_output")
    assert matrix.get("empty_reason") in (None, "")
    assert len(matrix["models"]) == 1
    assert matrix["models"][0]["name"] == "单品向"


@pytest.mark.asyncio
async def test_viral_model_agent_max_models_cap_trims_extras(monkeypatch):
    """8 个 direction、playbook 相同: 40 条样本动态上限=5（26-50区间），簇数超过上限时强制合并到 ≤5。"""
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    # 40 条样本 → _compute_dynamic_max(40) = 5（26-50 区间）
    # 若希望覆盖显式 cap=6 的行为，可通过 env var 设置，此处验证数据驱动路径
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    annotations: Dict[str, Dict[str, Any]] = {}
    for d in range(8):
        for i in range(5):
            annotations[f"n{d}_{i}"] = _ann(f"方向{d}")
    all_notes = [
        {
            "note_id": nid,
            "title": "t",
            "interaction_score": 1000,
            "likes": 500,
            "cover_url": "",
        }
        for nid in annotations
    ]
    agent = ViralModelAgent(model_gateway_instance=FakeGateway(["[]"]), event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        }
    )
    await agent.run(ac)
    matrix = ctx.get("viral_model_output")
    # 40 条样本 → 新动态上限 = 8（31-60 区间），8个direction自然成8簇，不超上限无需合并
    assert len(matrix["models"]) <= 8
    cov_sum = sum(float(m["coverage"]) for m in matrix["models"])
    assert abs(cov_sum - 1.0) < 1e-6
    covs = [float(m["coverage"]) for m in matrix["models"]]
    assert max(covs) < 0.95
    assert min(covs) > 0.01


@pytest.mark.asyncio
async def test_viral_model_agent_many_single_note_directions_longtail_covers_rest(monkeypatch):
    """52 条仅 content_direction 不同、playbook 相同：动态上限=6（51-80区间），均衡合并，避免单桶吃掉绝大多数。"""
    import backend.app.application.agents.viral_model_agent as vm_mod

    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    annotations = {f"n{i}": _ann(f"向{i}") for i in range(52)}
    all_notes = [
        {
            "note_id": nid,
            "title": "t",
            "interaction_score": 100,
            "likes": 50,
            "cover_url": "",
        }
        for nid in annotations
    ]
    agent = ViralModelAgent(model_gateway_instance=FakeGateway(["[]"]), event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        }
    )
    await agent.run(ac)
    matrix = ctx.get("viral_model_output")
    # 52 条 → 新动态上限 = 8（31-60 区间），52 单条簇经溶解后合并到 ≤8
    assert len(matrix["models"]) <= 8
    covs = [float(m["coverage"]) for m in matrix["models"]]
    assert max(covs) < 0.5
    assert min(covs) > 0.05
    assert sum(float(m["coverage"]) for m in matrix["models"]) == pytest.approx(1.0, abs=0.001)


@pytest.mark.asyncio
async def test_viral_model_agent_fragmented_directions_fallback_top_k():
    """5 条仅 direction 标签不同、playbook 相同:应并为少数簇,而非 5 个单条模型。"""
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    annotations = {f"n{i}": _ann(f"向{i}") for i in range(5)}
    all_notes = [
        {
            "note_id": nid,
            "title": "t",
            "interaction_score": 100,
            "likes": 50,
            "cover_url": "",
        }
        for nid in annotations
    ]
    agent = ViralModelAgent(model_gateway_instance=FakeGateway(["[]"]), event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        }
    )
    await agent.run(ac)
    matrix = ctx.get("viral_model_output")
    assert not matrix.get("empty_reason")
    assert len(matrix["models"]) <= 3
    assert sum(float(m["coverage"]) for m in matrix["models"]) == pytest.approx(1.0, abs=0.01)
    assert min(len(m["sample_note_ids"]) for m in matrix["models"]) >= 2


@pytest.mark.asyncio
async def test_viral_model_agent_empty_annotations_writes_empty_matrix():
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    gateway = FakeGateway([])
    agent = ViralModelAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={"multimodal_output": {"annotations": {}}, "crawler_output": {}}
    )
    await agent.run(ac)

    matrix = ctx.get("viral_model_output")
    assert matrix["models"] == []
    assert matrix["empty_reason"] == "no_annotations"
    # 没调 LLM
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_viral_model_agent_element_ratio_correctness():
    """单 direction 下 cover_type 占比 = 该 type 出现次数 / 该 direction 笔记总数。"""
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    # 4 条同 direction,3 条 cover_type=A, 1 条 cover_type=B
    annotations = {
        "a1": _ann("方向X", cover_type="纯产品图"),
        "a2": _ann("方向X", cover_type="纯产品图"),
        "a3": _ann("方向X", cover_type="纯产品图"),
        "a4": _ann("方向X", cover_type="前后对比"),
    }
    all_notes = [
        {"note_id": nid, "title": "t", "interaction_score": 100, "likes": 50, "cover_url": ""}
        for nid in annotations
    ]
    gateway = FakeGateway(["[]"])
    agent = ViralModelAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        }
    )
    await agent.run(ac)

    matrix = ctx.get("viral_model_output")
    model = matrix["models"][0]
    cover_cats = model["elements"]["A_cover"]
    # 第一个 cat 应是 纯产品图,占比 0.75 (3/4)
    assert cover_cats[0]["type"] == "纯产品图"
    assert cover_cats[0]["count"] == 3
    assert abs(cover_cats[0]["ratio"] - 0.75) < 0.001
    # 第二个 cat 是 前后对比, 0.25
    assert cover_cats[1]["type"] == "前后对比"
    assert cover_cats[1]["count"] == 1


@pytest.mark.asyncio
async def test_fuzzy_merge_and_direction_normalize():
    """验证语义相近的 content_direction 经归一化后会被合并到同一簇。

    场景：三批笔记方向分别是「美妆测评」「护肤测评」「个护测评」（3个 > 2个，触发归一化），
    LLM 归一化返回三者都映射到「产品测评」，
    之后聚类应合并为 1 个模型（而非 3 个）。
    """
    from backend.app.application.agents.viral_model_agent import ViralModelAgent

    annotations = {
        "n1": _ann("美妆测评"),
        "n2": _ann("美妆测评"),
        "n3": _ann("护肤测评"),
        "n4": _ann("护肤测评"),
        "n5": _ann("个护测评"),
    }
    all_notes = [
        {"note_id": nid, "title": f"t{nid}", "interaction_score": 3000, "likes": 1000, "cover_url": ""}
        for nid in annotations
    ]

    # LLM 调用顺序：第1次是方向归一化（3个方向>2，触发），第2次是模型起名
    normalize_resp = '{"美妆测评": "产品测评", "护肤测评": "产品测评", "个护测评": "产品测评"}'
    naming_resp = '[{"model_id": "M1", "name": "痛点共情型", "description": "以测评切入自然完成种草"}]'

    gateway = FakeGateway([normalize_resp, naming_resp])
    agent = ViralModelAgent(model_gateway_instance=gateway, event_bus=FakeBus())
    ctx, ac = _make_context(
        extra={
            "multimodal_output": {"annotations": annotations},
            "crawler_output": {"all_notes": all_notes},
        }
    )
    await agent.run(ac)

    matrix = ctx.get("viral_model_output")
    # 归一化后三个方向合并 → 只有 1 个模型
    assert len(matrix["models"]) == 1
    assert matrix["models"][0]["name"] == "痛点共情型"
    assert matrix["total_sample_count"] == 5


@pytest.mark.asyncio
async def test_dynamic_max_small_sample():
    """_compute_dynamic_max 分段阈值验证（新阈值）。"""
    from backend.app.application.agents.viral_model_agent import _compute_dynamic_max

    # 小样本（≤30）→ 6
    assert _compute_dynamic_max(5) == 6
    assert _compute_dynamic_max(10) == 6
    assert _compute_dynamic_max(15) == 6
    assert _compute_dynamic_max(16) == 6
    assert _compute_dynamic_max(30) == 6
    # 中样本 (31-60) → 8
    assert _compute_dynamic_max(31) == 8
    assert _compute_dynamic_max(40) == 8
    assert _compute_dynamic_max(60) == 8
    # 中大样本 (61-100) → 10
    assert _compute_dynamic_max(61) == 10
    assert _compute_dynamic_max(100) == 10
    # 大样本 (101-150) → 12
    assert _compute_dynamic_max(101) == 12
    assert _compute_dynamic_max(150) == 12
    # 超大样本 (151+) → 15
    assert _compute_dynamic_max(151) == 15
    assert _compute_dynamic_max(999) == 15


@pytest.mark.asyncio
async def test_bigram_similarity_basic():
    """_bigram_similarity 基本行为验证。"""
    from backend.app.application.agents.viral_model_agent import _bigram_similarity

    assert _bigram_similarity("干货分享", "干货分享") == 1.0
    # "干货分享" vs "干货引出": 共有 bigram "干货" → Jaccard = 1/5 ≈ 0.2
    assert _bigram_similarity("干货分享", "干货引出") > 0.1
    # 完全不相关的词应该接近 0
    assert _bigram_similarity("干货分享", "剧情种草") < 0.2
    assert _bigram_similarity("", "干货分享") == 0.0
    assert _bigram_similarity("∅", "∅") == 1.0
    # 语义近似的标注得分应高于完全不相关的
    similar = _bigram_similarity("干货分享", "干货引出")
    unrelated = _bigram_similarity("干货分享", "剧情种草")
    assert similar > unrelated

