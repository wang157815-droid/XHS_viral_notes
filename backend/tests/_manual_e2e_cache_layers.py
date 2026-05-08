"""阶段 4.α 手动 E2E：三层缓存命中 + 耗时递减验证。

前置要求（用户执行前请确认）：
  1. Redis 已起（本地: `docker compose up -d redis`,或 `redis-server`）
  2. PostgreSQL + pgvector 已起（`docker compose up -d postgres`）
  3. cookies.json 有效（不然 L3 采不到东西演示不了）
  4. 环境变量：
       REDIS_HOST=localhost REDIS_PORT=6379
       POSTGRES_DSN=postgresql+asyncpg://redmuse:redmuse_dev@localhost:5432/redmuse
       CRAWLER_CACHE_ENABLED=true
       CRAWLER_ENRICH_DETAILS=true

跑法（仓库根目录）：
    .venv\\Scripts\\python -m backend.tests._manual_e2e_cache_layers

会依次演示：
  1) 清空缓存 → 冷启动走 L3 (最慢,~30-60s)
  2) 同关键词再跑 → 命中 L1 (秒级,< 1s)
  3) 等 L1 过期或强制 invalidate → 命中 L2 (< 2s,因为 pgvector 查询)
  4) 打印三次耗时对比

预期结果：
  L3 ≫ L1 ≪ L2 (单位: ms)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# 兼容 python -m 和直接跑
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _warmup_connections() -> bool:
    """预热 Redis + Postgres 连接,检查基础设施是否就绪。"""
    from backend.app.infrastructure.cache.redis_client import check_redis_health
    from backend.app.infrastructure.storage.db_engine import check_postgres_health

    redis_health = await check_redis_health()
    print(f"  Redis:    {redis_health}")
    pg_health = await check_postgres_health()
    print(f"  Postgres: {pg_health}")

    ok = redis_health.get("status") == "healthy" and pg_health.get("status") == "healthy"
    if not ok:
        print("\n  [WARN] 基础设施未全部就绪,部分演示会降级")
    return ok


async def _run_one_task(keywords, title: str):
    """跑一次任务的关键步骤: input_spec + CrawlerAgent.run,计时。"""
    from backend.app.application.agents.crawler_agent import CrawlerAgent
    from backend.app.application.agents.base import AgentContext
    from backend.app.application.task_service import task_service
    from backend.app.domain.task_context import task_context_store, TaskContextWriter
    from backend.app.llm.model_gateway import model_gateway
    from backend.app.infrastructure.event_bus import task_event_bus

    print(f"\n=== [{title}] keywords={keywords} ===")

    res = task_service.create_task(
        owner_user_id="e2e-cache",
        raw_input=" ".join(keywords),
        keywords=keywords,
        idempotency_key=None,
    )
    tid = res.record.task_id
    ctx = task_context_store.require(tid)
    TaskContextWriter(ctx).write(
        "input_spec",
        {
            "parsed": {
                "keywords": keywords,
                "dimensions": {"industry": [], "competitor": [], "brand": []},
            }
        },
        agent_id="e2e",
        merge=True,
    )

    agent = CrawlerAgent(model_gateway_instance=model_gateway, event_bus=task_event_bus)
    start = time.monotonic()
    await agent.run(AgentContext(task_id=tid, task_context=ctx))
    elapsed = int((time.monotonic() - start) * 1000)

    out = ctx.get("crawler_output") or {}
    cache_source = out.get("cache_source", "?")
    count = out.get("sample_count", 0)
    print(f"  cache_source = {cache_source}")
    print(f"  sample_count = {count}")
    print(f"  耗时         = {elapsed} ms")
    return cache_source, count, elapsed


async def _invalidate_l1(keywords):
    """主动失效 L1,模拟 TTL 过期。"""
    from backend.app.infrastructure.cache.keyword_cache import get_keyword_cache

    cache = get_keyword_cache()
    ok = await cache.invalidate(keywords)
    print(f"  L1 失效: {ok}")


async def main() -> None:
    os.environ.setdefault("CRAWLER_CACHE_ENABLED", "true")
    os.environ.setdefault("CRAWLER_ENRICH_DETAILS", "true")

    print("=" * 70)
    print("阶段 4.α 三层缓存 E2E 演示")
    print("=" * 70)

    print("\n[Step 0] 基础设施连通性检查")
    healthy = await _warmup_connections()
    if not healthy:
        print("\n基础设施未就绪,仍然可继续（会降级到进程内行为）")

    keywords = ["咖啡"]

    # Step 1: 清空 L1, 首次跑 → L3
    print("\n[Step 1] 清空 L1 后首次采集,预期走 L3 实时爬")
    await _invalidate_l1(keywords)
    source1, count1, ms1 = await _run_one_task(keywords, "第一次（冷启动）")

    # 稍等一下,确保 L3 回写缓存完成
    await asyncio.sleep(2)

    # Step 2: 同关键词再跑 → L1
    print("\n[Step 2] 立即再跑相同关键词,预期命中 L1")
    source2, count2, ms2 = await _run_one_task(keywords, "第二次（热缓存）")

    # Step 3: 失效 L1 后再跑 → L2
    print("\n[Step 3] 失效 L1 后再跑,pgvector 里有数据则命中 L2")
    await _invalidate_l1(keywords)
    source3, count3, ms3 = await _run_one_task(keywords, "第三次（L1 失效）")

    # 总结
    print("\n" + "=" * 70)
    print("总结")
    print("=" * 70)
    print(f"  Step 1 (L3):  source={source1}  count={count1:3d}  耗时 {ms1:7d} ms")
    print(f"  Step 2 (L1):  source={source2}  count={count2:3d}  耗时 {ms2:7d} ms")
    print(f"  Step 3 (L2):  source={source3}  count={count3:3d}  耗时 {ms3:7d} ms")

    # 断言耗时递减（L3 应显著慢于 L1）
    passed = True
    if source2 != "L1":
        print("  [WARN] 第二次未命中 L1（应命中 Redis 热缓存）")
        passed = False
    if ms2 >= ms1:
        print(f"  [WARN] L1 耗时 {ms2}ms 应 ≪ L3 耗时 {ms1}ms")
        passed = False

    if source3 not in ("L2", "L3"):
        print(f"  [WARN] 第三次 cache_source 意外: {source3}")
        passed = False

    if passed:
        print("\n  [OK] 三层缓存工作正常,L1 命中耗时显著降低")
    else:
        print("\n  [PARTIAL] 缓存大致工作,但有异常项请检查日志")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
