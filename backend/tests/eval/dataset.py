"""
最小评估样本集（阶段 4.3pre.3 更新基线模块清单）。

样本场景分层：
- keyword_search: 关键词 → 任务创建成功率
- export_consistency: 导出结构与 CanvasSchema 一致

4.3pre.3 重构后不再覆盖旧 `mod-title/product/cover/structure-strategy` 4 模块,
改为覆盖新画布模块清单(品类 TOP 无独立样本卡;见 docs/canvas_restructure_spec.md v1.2.2)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class EvalSample:
    sample_id: str
    scenario: str
    raw_input: str
    keywords: List[str]


EVAL_SAMPLES: List[EvalSample] = [
    EvalSample("s-choco-01", "keyword_search", "分析奶油巧克力爆款笔记", ["巧克力", "奶油"]),
    EvalSample("s-fazer-01", "keyword_search", "北欧 Fazer 产品植入策略", ["北欧", "Fazer"]),
    EvalSample("s-haircare-01", "keyword_search", "防脱精华推荐内容框架", ["防脱", "精华"]),
    EvalSample("s-export-01", "export_consistency", "导出结构一致性回归", ["巧克力"]),
]


# 默认高级配置「不限」时 Layer3 为三源×图文/视频 6 卡 + 其余固定模块。
REQUIRED_CANVAS_MODULES = {
    "mod-overview-stats",
    "mod-viral-model-matrix",
    "mod-competitor-samples-image",
    "mod-competitor-samples-video",
    "mod-top-interaction-samples-image",
    "mod-top-interaction-samples-video",
    "mod-serp-top-samples-image",
    "mod-serp-top-samples-video",
    "mod-seo-insights",
    "mod-pain-points",
    "mod-draft-workbench",
}


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.get("ok"))
    coverage = {}
    for scenario in {r.get("scenario") for r in results}:
        scenario_total = sum(1 for r in results if r.get("scenario") == scenario)
        scenario_pass = sum(
            1 for r in results if r.get("scenario") == scenario and r.get("ok")
        )
        coverage[scenario] = {"total": scenario_total, "passed": scenario_pass}
    return {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
        "coverage": coverage,
    }
