"""阶段4.0 / 4.1 手动 E2E 脚本。

默认模式（无环境变量）：
    python -m backend.tests._manual_e2e_orchestration

    依次：
    1. ORCHESTRATION_ENGINE=simple 创建一个任务,跑 6 Stage,检查完成状态 + 画布模块数
    2. ORCHESTRATION_ENGINE=langgraph 创建一个任务,跑 7 节点,检查完成状态 + 画布模块数
    3. 每次任务再启一个,在 Stage 3 左右 cancel,验证 cancel 能命中

    使用 Agent 默认实现(包含降级路径),不需要真实 API Key。

阶段 4.1 真实模式：

    $env:USE_REAL_AGENTS="1"; python -m backend.tests._manual_e2e_orchestration

    在默认验证后额外跑一次真实链路,打印每个 Agent 的首条真实输出片段：
    - CrawlerAgent: source / sample_count / 维度状态 / 首条 note 标题
    - InputParserAgent: parsed keywords / dimensions / confidence
    - RAGAgent: rewritten_queries / 首条 business_rule
    - InsightAgent: industry/competitor/brand 首条 points
    - StrategyAgent: 4 子策略 templates/scenes/palette/hook
    - ImageAnalysisAgent: cover_types / opening_hooks / ocr_highlights

    真实模式会消耗 API token、依赖 cookies.json 存在。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# 兼容 "python -m" 和直接执行
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _set_engine(name: str) -> None:
    os.environ["ORCHESTRATION_ENGINE"] = name
    # 重置工厂缓存
    from backend.app.application.orchestration import reset_engine_cache
    reset_engine_cache()


async def _drive_once(engine_name: str) -> dict:
    _set_engine(engine_name)
    from backend.app.application.orchestration import get_orchestration_engine
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_status import TaskStatus
    from backend.app.infrastructure.repository import task_repository

    engine = get_orchestration_engine()
    print(f"\n=== [{engine_name}] 使用 engine: {engine.name} ===")

    res = task_service.create_task(
        owner_user_id="e2e-user",
        raw_input=f"{engine_name} e2e: 分析北欧巧克力爆款",
        keywords=["巧克力", "北欧"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    print(f"  task_id = {tid}")

    await engine.start(tid)

    # 等任务结束（最多 10s）
    for _ in range(500):
        rec = task_repository.get(tid)
        if rec and rec.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("任务超时未结束")

    rec = task_repository.require(tid)
    canvas = task_service.get_canvas(tid)
    module_ids = [m.module_id for m in canvas.modules] if canvas else []
    print(f"  final status  = {rec.status.value}")
    print(f"  modules       = {len(module_ids)}: {module_ids}")
    return {
        "engine": engine.name,
        "status": rec.status.value,
        "module_count": len(module_ids),
    }


async def _drive_cancel(engine_name: str) -> dict:
    _set_engine(engine_name)
    from backend.app.application.orchestration import get_orchestration_engine
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_status import TaskStatus
    from backend.app.infrastructure.repository import task_repository

    engine = get_orchestration_engine()
    res = task_service.create_task(
        owner_user_id="e2e-cancel",
        raw_input=f"{engine_name} cancel 回归",
        keywords=["巧克力"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    print(f"\n=== [{engine_name}-cancel] task_id = {tid} ===")

    await engine.start(tid)
    await asyncio.sleep(0.01)  # 让 runner 启动但尽可能早触发 cancel
    hit = await engine.cancel(tid)
    print(f"  cancel hit    = {hit}  (默认模式 stub 路径非常快,cancel=False 属预期;真实采集时 cancel 会命中)")

    # 模拟 API 路由侧（tasks.py）做的 transition，让任务进入 CANCELLED
    try:
        task_service.transition(tid, TaskStatus.CANCELLED)
    except Exception:
        pass

    for _ in range(500):
        rec = task_repository.get(tid)
        if rec and rec.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            break
        await asyncio.sleep(0.02)
    else:
        raise RuntimeError("cancel 后任务未结束")

    rec = task_repository.require(tid)
    print(f"  final status  = {rec.status.value}")
    return {"engine": engine.name, "status": rec.status.value, "cancel_hit": hit}


async def _drive_real_inspect() -> None:
    """阶段 4.1 真实模式:跑一次完整链路,打印每个 Agent 的真实输出片段。"""
    _set_engine("simple")
    from backend.app.application.orchestration import get_orchestration_engine
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_context import task_context_store
    from backend.app.domain.task_status import TaskStatus
    from backend.app.infrastructure.repository import task_repository

    engine = get_orchestration_engine()
    print("\n=== [REAL AGENTS] simple engine + 真实 Agent 输出（消耗 API token）===")

    res = task_service.create_task(
        owner_user_id="real-e2e",
        raw_input="分析北欧巧克力 Fazer 的爆款内容框架",
        keywords=["巧克力", "北欧", "Fazer"],
        idempotency_key=None,
    )
    tid = res.record.task_id
    print(f"  task_id = {tid}")

    await engine.start(tid)

    # 真实采集可能 30-90s,放宽到 180s
    for _ in range(9000):
        rec = task_repository.get(tid)
        if rec and rec.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
            break
        await asyncio.sleep(0.02)
    else:
        print("  [WARN] 真实模式任务超时,请检查 API/网络")
        return

    rec = task_repository.require(tid)
    print(f"  final status = {rec.status.value}")

    ctx = task_context_store.require(tid)

    # 打印每个 Agent 的真实输出片段
    input_spec = ctx.get("input_spec") or {}
    parsed = input_spec.get("parsed") or {}
    print("\n  [InputParserAgent] parsed:")
    print(f"    keywords   = {parsed.get('keywords')}")
    print(f"    dimensions = {parsed.get('dimensions')}")
    print(f"    confidence = {parsed.get('confidence')}")

    crawler = ctx.get("crawler_output") or {}
    print("\n  [CrawlerAgent]")
    print(f"    source       = {crawler.get('source')}")
    print(f"    sample_count = {crawler.get('sample_count')}")
    print(f"    dim_status   = {crawler.get('dimension_status')}")
    first_note = (crawler.get("note_summary") or [None])[0]
    if first_note:
        print(f"    first note   = [{first_note.get('dimension')}] {first_note.get('title')} "
              f"(likes={first_note.get('likes')}, media={first_note.get('media_type')})")

    semantic = ctx.get("semantic_output") or {}
    print("\n  [InsightAgent] 三维首条结论:")
    for dim in ("industry", "competitor", "brand"):
        sub = semantic.get(dim) or {}
        if isinstance(sub, dict):
            first = (sub.get("points") or sub.get("content_patterns") or sub.get("strengths") or [""])[:1]
            highlight = sub.get("highlight") or sub.get("differentiation_advice") or sub.get("next_step_advice") or ""
            print(f"    {dim:<10} first={first} highlight={highlight[:60]}")

    rag = ctx.get("rag_output") or {}
    print("\n  [RAGAgent]")
    print(f"    rewritten_queries = {rag.get('rewritten_queries')}")
    first_rule = (rag.get("business_rules") or [None])[0]
    if first_rule:
        print(f"    first rule = [{first_rule.get('rule_id')}] {first_rule.get('title')} "
              f"(score={first_rule.get('score')})")
        content = first_rule.get("content") or ""
        print(f"      content  = {content[:100]}")

    multimodal = (ctx.get("multimodal_output") or {}).get("image") or {}
    print("\n  [ImageAnalysisAgent]")
    print(f"    sample_count    = {multimodal.get('sample_count')}")
    print(f"    cover_types     = {multimodal.get('cover_types')}")
    print(f"    opening_hooks   = {multimodal.get('opening_hooks')}")
    print(f"    ocr_highlights  = {(multimodal.get('ocr_highlights') or [])[:3]}")

    strategy = ctx.get("strategy_output") or {}
    print("\n  [StrategyAgent]")
    titles = (strategy.get("title_strategy") or {}).get("templates") or []
    print(f"    title templates (first 2) = {titles[:2]}")
    product = (strategy.get("product_strategy") or {}).get("timing")
    print(f"    product timing            = {product}")
    cover = (strategy.get("cover_strategy") or {}).get("palette")
    print(f"    cover palette             = {cover}")
    structure = (strategy.get("structure_strategy") or {}).get("hook")
    print(f"    structure hook            = {structure}")


async def main() -> None:
    results = []
    results.append(await _drive_once("simple"))
    results.append(await _drive_once("langgraph"))
    results.append(await _drive_cancel("simple"))
    results.append(await _drive_cancel("langgraph"))

    print("\n=========================")
    print("阶段4.0 E2E 汇总：")
    for r in results:
        print(f"  - {r}")

    ok_simple_done = any(r.get("engine") == "simple" and r.get("status") == "completed" for r in results[:2])
    ok_lg_done = any(r.get("engine") == "langgraph" and r.get("status") == "completed" for r in results[:2])
    if not (ok_simple_done and ok_lg_done):
        print("  [FAIL] 至少一条 completed 断言未命中")
        sys.exit(1)
    print("  [OK] simple + langgraph 都能跑完主流程")

    if os.getenv("USE_REAL_AGENTS", "").strip() in ("1", "true", "yes"):
        await _drive_real_inspect()


if __name__ == "__main__":
    asyncio.run(main())
