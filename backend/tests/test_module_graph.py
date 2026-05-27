"""验收门槛:模块依赖图 + DirtyFlag 级联(4.3pre.3 新拓扑)。

新拓扑:
  mod-competitor-samples / mod-top-interaction-samples
      (CrawlerAgent 驱动,无上游)
      └─ mod-viral-model-matrix   (ViralModelAgent)
            ├─ mod-pain-points   (InsightAgent)
            └─ mod-seo-insights  (InsightAgent)
                注: SEO 不再强依赖竞品,无竞品时回退到 category_top 等来源
"""

from __future__ import annotations

from backend.app.domain.module_graph import ModuleGraph, ModuleNodeSpec
from backend.app.domain.module_status import ModuleStatus


def _build_graph() -> ModuleGraph:
    """构造 4.3pre.3 新依赖子图(只放测试关心的节点)。"""
    g = ModuleGraph()
    g.register_many(
        [
            ModuleNodeSpec(module_id="mod-competitor-samples", provides_by="CrawlerAgent"),
            ModuleNodeSpec(module_id="mod-top-interaction-samples", provides_by="CrawlerAgent"),
            ModuleNodeSpec(
                module_id="mod-viral-model-matrix",
                provides_by="ViralModelAgent",
                depends_on=[
                    "mod-competitor-samples",
                    "mod-top-interaction-samples",
                ],
            ),
            ModuleNodeSpec(
                module_id="mod-pain-points",
                provides_by="InsightAgent",
                depends_on=["mod-viral-model-matrix"],
            ),
            ModuleNodeSpec(
                module_id="mod-seo-insights",
                provides_by="InsightAgent",
                depends_on=["mod-viral-model-matrix"],
            ),
        ]
    )
    return g


def test_descendants_resolves_transitive():
    """从 mod-competitor-samples 改动后应级联影响 matrix/pain/seo 三个下游。"""
    g = _build_graph()
    descendants = g.descendants("mod-competitor-samples")
    assert descendants == {
        "mod-viral-model-matrix",
        "mod-pain-points",
        "mod-seo-insights",
    }


def test_cascade_marks_only_ready_downstream():
    g = _build_graph()
    status_map = {
        "mod-viral-model-matrix": ModuleStatus.READY,
        "mod-pain-points": ModuleStatus.GENERATING,
        "mod-seo-insights": ModuleStatus.READY,
    }
    marked = g.cascade_mark_stale(
        "mod-competitor-samples",
        lambda mid: status_map.get(mid),
    )
    # matrix 和 seo 是 READY,应被标 stale;pain-points 在 GENERATING,不被标
    assert set(marked) == {"mod-viral-model-matrix", "mod-seo-insights"}
