"""
定时预热 & 按需预热任务。

`scheduled_warmup(ctx, force=False)` —— 同一入口,两种触发来源：
- **ARQ cron 触发**（每小时探针,force=False）：
  - 门控 1: `crawler_schedule.enabled = False` → skip
  - 门控 2: 当前时间未到 `crawler:next_run_at`（Redis）→ skip
  - 调度策略：每次执行完后随机生成 12~20h 后的下次执行时间，
    保证每天至少执行一次且时间点随机（防规律性访问）
- **用户"立即采集"触发**（API 层 enqueue 时 force=True）：
  - 门控 1: 开关关着仍然 skip（尊重管理员总开关）
  - 门控 2: **跳过**（穿透时间节流,立即执行）
  - 执行完后同样重新抽签写入下次时间

核心任务流程：
- 合并 focus_keywords ∪ Redis 热词 Top N → 去重
- 逐个跑 CrawlerAgent 的核心采集函数 → 写 L1 Redis + L2 pgvector
- 每个关键词间隔 30s,避免触发 XHS 反爬
- 结果写 Redis `crawler:last_run`（供设置页 `/crawler-status` 显示）
- 写 Redis `crawler:next_run_at`（下次随机执行时间）

`warmup_keyword_on_demand`（带关键词的按需预热,保留给其他场景）：
- 不走焦点关键词列表,而是按调用方传入的关键词列表跑
- 不受开关/interval 限制(调用方自己决策)
"""

from __future__ import annotations

import asyncio
import json
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

# xhshow 可用性检查：ARQ worker 必须使用虚拟环境的 Python 启动，否则此库不存在
# 老签名算法（execjs）会让 XHS 搜索返回 data:{} 空结果，无法采集任何数据
try:
    import xhshow as _xhshow_check  # noqa: F401
except ImportError:
    logger.error(
        "[warmup] ❌ xhshow 未安装！当前 Python 环境缺少此签名库，"
        "所有采集请求将使用旧算法，XHS 会返回空数据。\n"
        "解决方法：用虚拟环境的 Python 启动 ARQ worker：\n"
        "  .venv\\Scripts\\python -m backend.app.infrastructure.queue.runner"
    )


_DEFAULT_MAX_KEYWORDS = 30  # hot_keywords_top_n 未配置时的兜底
_INTER_KEYWORD_SLEEP_SEC = 30.0
_PER_KEYWORD_TARGET = 200  # 每关键词采集目标数

# ── 品类词横向语义扩词 Prompt ──────────────────────────────────────────────
_CATEGORY_EXPANSION_SYSTEM = """你是小红书内容营销数据分析专家，专门负责"品类词横向语义扩词"任务。

## 核心原则：只做横向扩充，绝不替换品类

你的任务是在保持核心品类词不变的前提下，从场景、人群、功效、使用方式四个维度各生成 1 个复合搜索词。

【为什么不能替换品类】
品类替换会造成严重的受众偏移，导致后续 AI 分析产出错误的营销策略。
反例：用"大米"替代"燕麦米"是错误的——燕麦米的核心受众是减脂/控糖人群，
大米的受众是追求碳水饱腹的普通消费者，两者购买动机完全不同；
若把大米笔记混入燕麦米分析样本，AI 提取出的用户画像和卖点将严重失真。

## 四类扩词的生成标准

【场景词】
定义：该品类被真实使用的具体生活场景。
关键判断：目标用户在这个场景下会主动搜索并使用该品类吗？需要有直接因果关联，而非强行拼凑。
  ✅ "燕麦米 减脂餐" — 减脂人群规划饮食时确实会搜燕麦米，因为燕麦米低GI、适合减脂
  ✅ "燕麦米 代餐" — 代餐场景下燕麦米是常见替代主食选择
  ❌ "燕麦米 火锅" — 燕麦米与火锅无实际关联，没有用户会这样搜索
  ❌ "燕麦米 旅游" — 场景与品类之间缺乏消费逻辑

【人群词】
定义：有强烈且明确需求、会专门搜索该品类的特定人群。
关键判断：这类人群是否有清晰的、区别于普通人的理由去搜索该品类？人群特征要具体，不能泛化。
  ✅ "燕麦米 糖尿病" — 糖尿病患者需要低GI主食，燕麦米是医学上推荐的选择，需求强烈
  ✅ "燕麦米 健身" — 健身人群关注血糖控制和蛋白质，是燕麦米的精准受众
  ❌ "燕麦米 老人" — 过于泛化，老年人并非专门搜索燕麦米的人群，关联性不足
  ❌ "燕麦米 孕妇" — 孕妇不是燕麦米的核心诉求人群

【功效词】
定义：该品类能真实兑现的、有科学/常识支撑的健康或功能利益点。
关键判断：这个功效是该品类公认的真实属性，不是夸大、捏造或无关的功能描述？
  ✅ "燕麦米 低GI" — 燕麦米是公认的低升糖指数食品，有营养学依据
  ✅ "燕麦米 饱腹" — 燕麦米富含膳食纤维，饱腹感是其真实、可验证的特性
  ❌ "燕麦米 美白" — 与燕麦米无关的功效，无科学依据
  ❌ "燕麦米 助眠" — 与该品类的实际功能毫无关联

【使用方式词】
定义：用户实际烹饪或使用该品类的具体方式，用户会主动搜索学习的真实做法。
关键判断：这种用法是真实可行的、符合该品类物理特性的，用户购买后确实需要搜索学习吗？
  ✅ "燕麦米 怎么煮" — 用户购买后确实会搜索烹饪方法，有明确学习需求
  ✅ "燕麦米 食谱" — 用户希望了解更多花样吃法，搜索需求真实存在
  ❌ "燕麦米 油炸" — 不符合燕麦米的真实使用场景，几乎无人会这样烹饪

## 输出要求
- 严格生成 4 个词，每类 1 个，格式为：品类词+空格+扩展词
- 每个词总长 2–8 个字，使用小红书用户真实会搜索的口语化表达
- 禁止出现任何品牌名、竞品名、替代品类名"""

_CATEGORY_EXPANSION_USER = """品类词：「{keyword}」

请为该品类词按以下四类各生成 1 个小红书扩展搜索词：
- 场景词：该品类真实被使用的生活场景（用户在此场景下确有动机搜索该品类）
- 人群词：有强烈且明确需求的特定目标人群（该人群有具体理由专门搜索该品类）
- 功效词：该品类公认的、可验证的真实健康或功能利益点
- 使用方式词：用户会主动学习的真实烹饪/使用方法

生成前请逐类自问：「持有这个需求的用户，真的会在小红书上这样搜索吗？这个关联有常识依据吗？」

严格输出 JSON，不要添加任何其他内容：
{{"keywords": ["场景词结果","人群词结果","功效词结果","使用方式词结果"]}}"""


async def scheduled_warmup(ctx: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """定时 / 立即预热任务（共用入口）。

    Args:
        ctx: ARQ 任务上下文
        force: True=用户"立即采集"触发,跳过时间节流(但仍受开关限制);
               False=cron 探针触发,受双门控限制(开关 + next_run_at)

    门控规则：
    - 开关(enabled) = False: 无论 force 都 skip
    - force=False 且当前时间 < crawler:next_run_at: skip
    - 其他情况: 执行采集，完成后随机生成 12~20h 后的下次时间
    """
    trigger = "manual" if force else "scheduled"
    start = datetime.now(timezone.utc)
    logger.info(f"[arq.scheduled_warmup] 开始,trigger={trigger} time={start.isoformat()}")

    try:
        from ....services.system_settings_store import get_system_settings_store

        schedule_cfg = await get_system_settings_store().get_crawler_schedule()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.scheduled_warmup] 读取设置失败({exc}),按默认启用处理")
        schedule_cfg = {"enabled": True}

    # ----- 门控 1: 开关 (无条件检查,force 也不能绕过) -----
    if not schedule_cfg.get("enabled", True):
        logger.info("[arq.scheduled_warmup] 定时预热已在设置页禁用,跳过本次")
        return {
            "status": "skipped",
            "reason": "admin_disabled",
            "trigger": trigger,
            "started_at": start.isoformat(),
        }

    # ----- 门控 2: next_run_at (仅 force=False 时检查,立即采集穿透) -----
    if not force:
        if not await _next_run_at_reached(start):
            logger.info("[arq.scheduled_warmup] 未到计划执行时间，跳过本次 (cron 节流)")
            return {
                "status": "skipped",
                "reason": "not_yet",
                "trigger": trigger,
                "started_at": start.isoformat(),
            }
    else:
        logger.info("[arq.scheduled_warmup] 立即采集模式(force=True),穿透时间节流")

    max_keywords = _DEFAULT_MAX_KEYWORDS
    logger.info(f"[arq.scheduled_warmup] 门控通过,top_n={max_keywords}")

    try:
        keywords = await _collect_warmup_keywords()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.scheduled_warmup] 收集关键词失败: {exc}")
        keywords = []

    if not keywords:
        logger.info("[arq.scheduled_warmup] 无可预热关键词,跳过")
        await _write_last_run(
            {
                "status": "skipped",
                "reason": "no_keywords",
                "trigger": trigger,
                "started_at": start.isoformat(),
            }
        )
        # 即使无关键词也要写下次执行时间，否则每小时探针都会触发本次逻辑
        await _write_next_run_at(start)
        return {"status": "skipped", "reason": "no_keywords", "trigger": trigger}

    keywords = keywords[:max_keywords]
    logger.info(f"[arq.scheduled_warmup] 预热 {len(keywords)} 个关键词: {keywords}")

    # 门控全部通过，写入 running 状态——进程意外中断时前端能看到正确提示
    await _write_last_run(
        {
            "status": "running",
            "trigger": trigger,
            "started_at": start.isoformat(),
            "total_keywords": len(keywords),
        }
    )

    results = {"ok": 0, "failed": 0, "skipped": 0, "per_keyword": []}
    _completed_normally = False
    try:
        for idx, kw in enumerate(keywords):
            try:
                crawled = await _warmup_one_keyword(kw)
                if crawled:
                    results["ok"] += 1
                    results["per_keyword"].append({"keyword": kw, "count": crawled, "status": "ok"})
                else:
                    results["skipped"] += 1
                    results["per_keyword"].append({"keyword": kw, "count": 0, "status": "empty"})
            except Exception as exc:  # noqa: BLE001
                results["failed"] += 1
                results["per_keyword"].append({"keyword": kw, "status": "error", "error": str(exc)})
                logger.warning(f"[arq.scheduled_warmup] 预热 {kw} 失败: {exc}")

            if idx < len(keywords) - 1:
                await asyncio.sleep(_INTER_KEYWORD_SLEEP_SEC)

        _completed_normally = True
    finally:
        end = datetime.now(timezone.utc)
        if _completed_normally:
            summary = {
                "status": "ok" if results["ok"] > 0 else "empty",
                "trigger": trigger,
                "started_at": start.isoformat(),
                "finished_at": end.isoformat(),
                "duration_sec": (end - start).total_seconds(),
                "total_keywords": len(keywords),
                "ok_count": results["ok"],
                "failed_count": results["failed"],
                "skipped_count": results["skipped"],
                "per_keyword": results["per_keyword"],
            }
        else:
            # 进程被 kill / asyncio.CancelledError / job_timeout 等中断
            summary = {
                "status": "interrupted",
                "trigger": trigger,
                "started_at": start.isoformat(),
                "finished_at": end.isoformat(),
                "duration_sec": (end - start).total_seconds(),
                "total_keywords": len(keywords),
                "ok_count": results["ok"],
                "failed_count": results["failed"],
                "skipped_count": results["skipped"],
                "per_keyword": results["per_keyword"],
                "reason": "process_interrupted",
            }
            logger.warning(
                f"[arq.scheduled_warmup] 进程中断，写入 interrupted 终态 "
                f"(ok={results['ok']} failed={results['failed']})"
            )
        await _write_last_run(summary)
        await _write_next_run_at(end)

    logger.info(f"[arq.scheduled_warmup] 完成: trigger={trigger} ok={results['ok']} failed={results['failed']}")
    return summary


async def warmup_keyword_on_demand(
    ctx: Dict[str, Any],
    keywords: List[str],
    target_count: int = 30,
) -> Dict[str, Any]:
    """管理员"立即采集"按钮触发：为一组关键词预热。"""
    start = datetime.now(timezone.utc)
    logger.info(f"[arq.warmup_on_demand] keywords={keywords} target={target_count}")

    crawled = 0
    try:
        crawled = await _warmup_one_keyword(" ".join(keywords), target=target_count)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[arq.warmup_on_demand] 失败: {exc}")
        return {"status": "error", "error": str(exc)}

    end = datetime.now(timezone.utc)
    return {
        "status": "ok",
        "keywords": keywords,
        "crawled_count": crawled,
        "duration_sec": (end - start).total_seconds(),
    }


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------


async def _collect_warmup_keywords() -> List[str]:
    """合并 focus_keywords ∪ top queries,去重去空白。

    关键词来源优先级：
    1. `FocusKeywordsStore`(datas/config/focus_keywords.json):
       前端"设置 → 焦点关键词"写入的真实用户配置（本阶段主渠道)
    2. `viral_agent.config.knowledge_loader.load_focus_keywords()`(兼容老路径):
       若老项目部署有该函数,继续吸收
    3. Redis `hot:queries`：近期 Top 查询关键词（由 rebuild_hot_queries cron 填充）

    三路合并后大小写不敏感去重,保持 `focus` 在 `top_queries` 之前。
    """
    focus: List[str] = []

    # 主渠道: FocusKeywordsStore
    try:
        from ....services.focus_keywords_store import get_focus_keywords_store

        focus = list(await get_focus_keywords_store().get())
        if focus:
            logger.info(f"[warmup] 从 FocusKeywordsStore 读取 {len(focus)} 个关键词")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] FocusKeywordsStore 读取失败: {exc}")
        focus = []

    # 兼容：老 knowledge_loader（若存在）
    if not focus:
        try:
            from viral_agent.config.knowledge_loader import (  # type: ignore[attr-defined]
                load_focus_keywords,
            )

            legacy = list(load_focus_keywords() or [])
            if legacy:
                logger.info(f"[warmup] 从 legacy knowledge_loader 补充 {len(legacy)} 个关键词")
                focus = legacy
        except Exception:
            pass

    top_queries: List[str] = []
    try:
        top_queries = await _load_top_queries_from_redis()
    except Exception:
        top_queries = []

    # 合并 + trim + 大小写不敏感去重(保留先后顺序：focus 优先)
    seen_lower: set[str] = set()
    merged: List[str] = []
    for kw in list(focus) + list(top_queries):
        s = str(kw).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        merged.append(s)
    return merged


async def _load_top_queries_from_redis() -> List[str]:
    """从 Redis `hot:queries` 拿 Top N 历史关键词。"""
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("hot:queries")
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data if x]
        if isinstance(data, dict) and "keywords" in data:
            return [str(x) for x in data["keywords"]]
    except Exception:  # noqa: BLE001
        pass
    return []


def _find_warmup_user_id() -> str:
    """找一个可用的 owner_user_id，与正常爆文任务的 cookie 解析路径保持一致。

    策略（优先级从高到低）：
    1. 从任务仓库找最近一条任务的 owner_user_id —— 与正常爆文任务完全相同路径，
       保证 warmup 使用的 cookie 和用户手动跑任务时用的完全一致。
    2. 从 XhsCredentialStore 找最近验证过的 active 凭据（credential store 路径）。
    3. 都没有时返回空字符串，resolver 会尝试 .env COOKIES 兜底。
    """
    # ---- 路径 1: 从最近任务获取 owner_user_id，并验证该 ID 确实有可用凭据 ----
    try:
        from ....infrastructure.repository.task_repository import task_repository
        from ....services.xhs_auth import get_credential_store

        cred_store = get_credential_store()
        all_tasks = task_repository.list_for_user("", include_all=True, limit=20)
        for task in all_tasks:
            uid = getattr(task, "owner_user_id", None) or ""
            if not uid:
                continue
            # 验证该 owner_user_id 在 XhsCredentialStore 中有对应凭据，避免使用无效 ID
            cred = cred_store.get_by_redmuse_user_id(uid)
            if cred and cred.cookies_path:
                logger.info(f"[warmup] 使用最近任务的 owner_user_id: {uid!r} (task={task.task_id})")
                return uid
            else:
                logger.debug(f"[warmup] 跳过任务 owner {uid!r}：无匹配凭据，继续查找")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[warmup] 从任务仓库获取 owner 失败: {exc}")

    # ---- 路径 2: XhsCredentialStore active 凭据 ----
    try:
        from ....services.xhs_auth import get_credential_store

        credentials = get_credential_store().list_credentials()

        def _sort_key(c: Any) -> str:
            return str(c.last_validated_at or c.updated_at or "")

        active = [
            c for c in credentials
            if c.redmuse_user_id and c.cookies_path and c.status == "active"
        ]
        if active:
            best = max(active, key=_sort_key)
            logger.info(
                f"[warmup] 使用 active 凭据: {best.redmuse_user_id} "
                f"(xhs={best.xhs_nickname}, validated={best.last_validated_at})"
            )
            return best.redmuse_user_id

        fallback = [
            c for c in credentials
            if c.redmuse_user_id and c.cookies_path and c.status != "unbound"
        ]
        if fallback:
            best = max(fallback, key=_sort_key)
            logger.warning(
                f"[warmup] 无 active 凭据，降级使用: {best.redmuse_user_id} status={best.status}"
            )
            return best.redmuse_user_id
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] 查找凭据失败: {exc}")

    # ---- 路径 3: 空字符串 → resolver 会尝试 .env COOKIES ----
    logger.warning("[warmup] 未找到任何 owner_user_id，将依赖 .env COOKIES 兜底")
    return ""


async def _warmup_one_keyword(keyword: str, target: int = _PER_KEYWORD_TARGET) -> int:
    """为单个关键词跑一次 L3 → 写回 L1/L2,返回真实采到的条数。

    流程：
    1. 主采集（品类词）
    2. 若主采集结果 < target，调用 LLM 生成 4 个横向扩展词补采
    3. L0 in-memory 去重（seen_note_ids），合并全量结果
    4. 写回 L1 Redis + L2 pgvector
    """
    try:
        from ....application.agents.crawler_agent import (
            _collect_one_dimension,
            _normalize_note,
            _resolve_cookies_str,
        )
        from ...cache.keyword_cache import get_keyword_cache
        from ...storage.notes_vector_store import get_notes_vector_store
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] 依赖加载失败: {exc}")
        return 0

    # 优先使用 XhsCredentialStore 中绑定的账号（管理员优先）
    owner_user_id = _find_warmup_user_id()
    # 解析 cookie 并记录 cookies_path，方便对比正常任务用的是同一份文件
    try:
        from ....services.xhs_auth import get_credential_resolver, get_credential_store
        _cred = get_credential_store().get_by_redmuse_user_id(owner_user_id) if owner_user_id else None
        logger.info(
            f"[warmup] 凭据查找: owner={owner_user_id or '(empty)'} "
            f"cred_path={getattr(_cred, 'cookies_path', None)} "
            f"cred_status={getattr(_cred, 'status', None)}"
        )
    except Exception:
        pass
    cookies_str = _resolve_cookies_str(owner_user_id)
    logger.info(
        f"[warmup] cookie 解析: owner={owner_user_id or '(empty)'} "
        f"found={bool(cookies_str)} len={len(cookies_str)} "
        f"has_web_session={'web_session=' in cookies_str} "
        f"has_a1={'a1=' in cookies_str}"
    )
    if not cookies_str:
        logger.warning("[warmup] 未找到可用 cookies（请在设置→数据源授权中绑定小红书账号），跳过")
        return 0
    if "web_session=" not in cookies_str:
        logger.warning("[warmup] ⚠️ cookie 缺少 web_session 字段，搜索将返回空结果，跳过")
        return 0

    runtime_cfg = {
        "target_count": target,
        "note_type": 0,
        "time_range": 3,    # 半年内
        "min_interaction": 1000,
    }

    # ── 步骤 1：主采集（品类词，dimension="warmup"）──
    try:
        primary_raw = await _collect_one_dimension(
            cookies_str, [keyword], target, runtime_cfg, owner_user_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] 主采集 {keyword} 失败: {exc}")
        return 0

    normalized: List[Any] = [_normalize_note(n, "warmup", keyword) for n in (primary_raw or [])]
    seen_ids: set = {n["note_id"] for n in normalized if n.get("note_id")}

    logger.info(f"[warmup] '{keyword}' 主采集: {len(normalized)}/{target} 条")

    # ── 步骤 2：不足时，品类词横向语义扩词补采（dimension="warmup_expanded"）──
    remaining = target - len(normalized)
    if remaining > 0:
        logger.info(f"[warmup] '{keyword}' 不足 target，开始扩词补采（还差 {remaining} 条）")
        expanded_kws = await _expand_category_keywords(keyword)
        if expanded_kws:
            per_target = math.ceil(remaining / len(expanded_kws))
            exp_runtime_cfg = {**runtime_cfg, "target_count": per_target}
            for exp_kw in expanded_kws:
                await asyncio.sleep(5.0)  # 扩词间隔，避免触发反爬
                try:
                    exp_raw = await _collect_one_dimension(
                        cookies_str, [exp_kw], per_target, exp_runtime_cfg, owner_user_id
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[warmup] 扩词采集 '{exp_kw}' 失败: {exc}")
                    continue
                added = 0
                for n in (exp_raw or []):
                    nd = _normalize_note(n, "warmup_expanded", exp_kw)
                    nid = nd.get("note_id")
                    if nid and nid not in seen_ids:
                        seen_ids.add(nid)
                        normalized.append(nd)
                        added += 1
                logger.info(f"[warmup] 扩词 '{exp_kw}' 补采: +{added} 条（去重后）")
        else:
            logger.info(f"[warmup] '{keyword}' LLM 扩词失败或返回空，跳过补采")

    if not normalized:
        return 0

    # ── 步骤 3：写回 L1 Redis ──
    try:
        await get_keyword_cache().set([keyword], normalized)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] L1 回填失败: {exc}")
    # ── 步骤 4：写回 L2 pgvector ──
    try:
        await get_notes_vector_store().add_notes(normalized)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] L2 回填失败: {exc}")

    logger.info(f"[warmup] '{keyword}' 最终写回: {len(normalized)} 条（含扩词补采）")
    return len(normalized)


async def _expand_category_keywords(primary_kw: str) -> List[str]:
    """调用 LLM，按「场景/人群/功效/使用方式」四类各生成 1 个横向语义扩展词，共 4 个。

    失败时返回空列表，不阻断主采集流程。
    """
    try:
        from ....llm.model_gateway import ModelGateway

        gw = ModelGateway()
        messages = [
            {"role": "system", "content": _CATEGORY_EXPANSION_SYSTEM},
            {"role": "user", "content": _CATEGORY_EXPANSION_USER.format(keyword=primary_kw)},
        ]
        result = await gw.chat(
            "WarmupKeywordExpander",
            messages,
            overrides={
                "temperature": 0.7,
                # 关闭思考模式：避免 thinking tokens 耗尽 max_tokens 导致 content 为空
                "extra_body": {"enable_thinking": False},
                "response_format": {"type": "json_object"},
            },
        )
        raw_text = ""
        if isinstance(result, str):
            raw_text = result
        elif isinstance(result, dict):
            raw_text = result.get("content") or result.get("text") or ""
        elif hasattr(result, "content"):
            raw_text = str(result.content)

        # 提取 JSON
        import re as _re
        json_match = _re.search(r'\{.*"keywords".*\}', raw_text, _re.DOTALL)
        if not json_match:
            logger.warning(f"[warmup._expand] LLM 返回无 JSON: {raw_text[:200]!r}")
            return []
        parsed = json.loads(json_match.group())
        kws = parsed.get("keywords") or []
        if not isinstance(kws, list):
            return []
        # 过滤空字符串，最多取 4 个
        clean = [str(k).strip() for k in kws if str(k).strip()][:4]
        if len(clean) < 4:
            logger.warning(f"[warmup._expand] LLM 返回词数不足 4: {clean}")
        logger.info(f"[warmup._expand] '{primary_kw}' 扩展词: {clean}")
        return clean
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup._expand] LLM 调用失败: {type(exc).__name__}: {exc}")
        return []


async def _next_run_at_reached(now: datetime) -> bool:
    """读取 Redis crawler:next_run_at，判断是否到了执行时间。

    - 不存在（首次部署/key 已过期）→ True（立即执行）
    - now >= next_run_at  → True
    - now < next_run_at   → False
    - Redis 故障          → False（保守跳过，避免 Redis 抖动造成反复触发）
    """
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("crawler:next_run_at")
        if not raw:
            # key 不存在 → 首次部署或上一次 skip 路径遗漏写入，放行执行
            logger.info("[warmup] crawler:next_run_at 不存在，视为首次执行，放行")
            return True
        raw_str = raw.decode() if isinstance(raw, bytes) else str(raw)
        next_run = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        if now >= next_run:
            return True
        remaining_h = (next_run - now).total_seconds() / 3600
        logger.info(f"[warmup] 距下次执行还有 {remaining_h:.1f}h（{next_run.isoformat()}）")
        return False
    except Exception as exc:  # noqa: BLE001
        # Redis 故障时保守跳过，避免每小时探针因连接抖动而反复触发采集
        logger.warning(f"[warmup] 读取 next_run_at 失败，本次跳过: {exc}")
        return False


async def _write_next_run_at(now: datetime) -> None:
    """随机生成 12~20h 后的时间戳写入 Redis，供下次探针判定。"""
    offset_sec = random.uniform(12 * 3600, 20 * 3600)
    next_run = now + timedelta(seconds=offset_sec)
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        await client.setex("crawler:next_run_at", 86400 * 2, next_run.isoformat())
        logger.info(f"[warmup] 下次计划执行: {next_run.isoformat()} (距今 {offset_sec/3600:.1f}h)")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[warmup] 写入 next_run_at 失败: {exc}")


async def _write_last_run(summary: Dict[str, Any]) -> None:
    """写最近一次预热结果到 Redis,供设置页读取。"""
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        await client.setex(
            "crawler:last_run",
            86400,  # 保留 1 天
            json.dumps(summary, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[warmup._write_last_run] 写 Redis 失败: {exc}")


async def read_last_run() -> Optional[Dict[str, Any]]:
    """读最近一次预热结果（供 `/settings/crawler-status` 用）。"""
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("crawler:last_run")
        if not raw:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


async def read_next_run_at() -> Optional[str]:
    """读取下次计划执行时间（ISO 字符串），供 `/settings/crawler-status` 用。"""
    try:
        from ...cache.redis_client import get_redis

        client = await get_redis()
        raw = await client.get("crawler:next_run_at")
        if not raw:
            return None
        return raw.decode() if isinstance(raw, bytes) else str(raw)
    except Exception:  # noqa: BLE001
        return None
