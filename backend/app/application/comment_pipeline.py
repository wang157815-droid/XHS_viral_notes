"""
CommentAnalysisSkill 流水线。

流程（8 步）：
  1. 爬取笔记（复用 crawler_agent._collect_one_dimension）
  2. 为每条笔记拉取首屏评论（get_note_out_comment）
  3. 按 like_count 取 Top K 评论
  4. LLM 批量分类笔记（求助帖/吐槽帖/测评类/干货类/其他）
  5. LLM 批量分类评论（亲测有效型/选择纠结型/细节提问型/情绪型/其他）
  6. LLM 总结每条笔记的评论区话题（→ Sheet3 最后一列）
  7. 按「笔记类别×评论类型」聚合，计算占比，LLM 写摘要 + 生成 AI 评论（→ Sheet1）
  8. 写入 TaskContext["comment_output"]，通过 SSE 推送完成事件

不走 Canvas，不走 AgentOrchestrator，结果直接存 TaskContext。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ..domain.events import TaskEventType
from ..domain.task_context import task_context_store
from ..domain.task_status import TaskStatus
from ..infrastructure.event_bus import task_event_bus
from ..infrastructure.repository import task_repository
from ..infrastructure.storage.comment_cache_store import comment_cache_store
from ..llm.model_gateway import model_gateway

# ── 采集参数默认值 ─────────────────────────────────────────────────────────────
_DEFAULT_TOP_NOTES = 0           # 0 = 不限，爬到多少用多少
_DEFAULT_TOP_COMMENTS = 5        # 每条笔记取点赞 Top K 条评论
_CRAWL_TARGET_PER_KW = 50        # 每关键词采集目标数
_INTER_NOTE_SLEEP = 1.0          # 拉评论接口间隔（秒），防限频
_MAX_CONCURRENT_COMMENTS = 5     # 并发拉评论最大数

# ── LLM Prompt ──────────────────────────────────────────────────────────────

_NOTE_CLASSIFY_SYSTEM = """你是小红书内容分析专家。请根据笔记的标题、描述和话题标签，为每条笔记打一个类型标签。

【标准类型】内容匹配时必须使用以下原名，禁止改写：
- 求助帖：用户遇到问题（脱发/选品/皮肤等）向网友求助
- 测评类：多产品对比测试、功效评测
- 吐槽帖：吐槽某款产品踩雷、不好用
- 单品推荐：专注推荐一款产品的种草内容
- 伪合集：形式像合集但核心在推某款特定产品
- vlog：日常打卡、生活记录形式
- 干货类：实用技巧、选购攻略、使用方法分享
- 科普类：成分解析、科学原理、知识科普

【不匹配标准类型时】根据笔记实际内容自由生成 2~4 字标签，风格参考上面（加"帖/类"等后缀），绝对禁止输出"其他"：
- 开箱帖、亲测帖、探店类、情感类、问答类、日记帖、合集类、攻略类……
- 实在信息极少时，根据标题主题词造标签，如"防脱日记"、"洗发分享"

⚠️ 严禁：禁止输出"其他"或空字符串，每一条都必须有有意义的标签。

输出格式（严格 JSON，不含任何其他文字）：
{"results": [{"index": 0, "category": "求助帖"}, {"index": 1, "category": "单品推荐"}, ...]}"""

_COMMENT_CLASSIFY_SYSTEM = """你是小红书用户评论分析专家。请根据评论内容，为每条评论打一个类型标签。

【标准类型】内容匹配时必须使用以下原名，禁止改写：
- 亲测有效型：亲身使用有效果（产品＋使用时长＋效果描述）
- 温和推荐型：使用体验正向、温和安利（产品＋使用感＋效果）
- 差异突出型：强调比竞品更好的独特优势（产品名＋对比下的优点＋适合人群）
- 选择纠结型：在两款及以上产品之间纠结选哪个
- 效果加码型：在竞品帖下分享本品效果给本品背书（人称＋产品名＋强效果）
- 细节提问型：询问质地/气味/肤感/适合人群等使用细节
- 产品提问型：询问使用方法、使用顺序、见效时间等操作类问题

【不匹配标准类型时】根据评论实际内容自由生成 3~6 字标签，以"型"结尾，绝对禁止输出"其他"：
- 负面吐槽型、情绪互动型、购买表达型、长期打卡型、成分质疑型、价格咨询型……
- 纯表情/"哈哈哈"类评论 → 情绪互动型
- 表达已购/想买 → 购买表达型
- 对效果或成分持怀疑态度 → 效果质疑型

⚠️ 严禁：禁止输出"其他"或空字符串，每一条都必须有有意义的标签。

输出格式（严格 JSON，不含任何其他文字）：
{"results": [{"index": 0, "comment_type": "亲测有效型"}, {"index": 1, "comment_type": "细节提问型"}, ...]}"""

_NOTE_TOPIC_SYSTEM = """你是小红书评论区分析专家。请根据以下评论内容，总结该笔记评论区的核心话题和用户关注点。

要求：
- 用 1-2 句话简洁概括
- 聚焦用户最关心的问题或讨论热点
- 不要重复评论原文，提炼核心语义"""

_GROUP_SUMMARY_SYSTEM = """你是小红书内容营销分析师。根据以下同一类型的用户评论，完成两个任务：

任务1：用 1-2 句话总结这类评论的核心关注点（评论内容核心摘要）
任务2：生成 5 条代表这类用户需求的典型评论文案，要求：
- 每条 20~50 字，口语化，贴近真实用户语气
- 5 条之间内容要有差异，不要重复同一说法
- 禁止使用"作为一个XX"开头，禁止过于书面化

请严格输出 JSON，格式为：
{"summary": "核心摘要内容", "ai_comments": ["评论1", "评论2", "评论3", "评论4", "评论5"]}

不要输出多余内容。"""

_INPUT_PARSER_SYSTEM = """你是搜索意图解析助手。请从用户的自然语言描述中提取小红书笔记搜索所需的结构化参数。

提取规则：
- keywords：核心搜索词，去掉「笔记」「评论区」「互动量」「近一周」等修饰词，只保留产品/品类名称，最多5个
- time_range：时间范围（0=不限 1=一天内 2=一周内 3=半年内），默认0
- min_interaction：互动量下限（数字，如1000），未提及则为0
- top_notes：分析笔记数量上限，用户未明确指定则为0（表示不限，爬到多少用多少）
- top_comments_per_note：每条笔记取评论数量，未提及则为5

示例：
输入："近一周防脱精华、防脱洗发水互动量比较高的笔记评论区"
输出：{"keywords":["防脱精华","防脱洗发水"],"time_range":2,"min_interaction":0,"top_notes":0,"top_comments_per_note":5}

输入："帮我采集格力空调的高赞评论，只要最近半年的，取前30条笔记"
输出：{"keywords":["格力空调"],"time_range":3,"min_interaction":0,"top_notes":30,"top_comments_per_note":5}

严格输出 JSON，不要任何解释。"""


# ── 工具函数 ─────────────────────────────────────────────────────────────────

async def _emit(task_id: str, event_type: TaskEventType, payload: Dict[str, Any]) -> None:
    try:
        await task_event_bus.publish_event(task_id=task_id, type=event_type, payload=payload)
    except Exception as exc:
        logger.debug(f"[comment_pipeline] SSE 推送失败 task={task_id}: {exc}")


async def _emit_progress(task_id: str, message: str, progress: int) -> None:
    await _emit(task_id, TaskEventType.AGENT_PROGRESS, {
        "agent_id": "CommentPipeline",
        "message": message,
        "progress": progress,
    })


def _resolve_cookies(owner_user_id: str) -> str:
    from ..application.agents.crawler_agent import _resolve_cookies_str
    return _resolve_cookies_str(owner_user_id)


def _interaction_score(note: Any) -> int:
    if isinstance(note, dict):
        return int(note.get("interaction_score") or 0)
    return int(getattr(note, "interaction_score", 0) or 0)


def _note_to_dict(note: Any) -> Dict[str, Any]:
    """ViralNote 对象或 dict 统一转成 dict。"""
    if isinstance(note, dict):
        return note
    tags_raw = getattr(note, "tags", None) or []
    tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
    return {
        "note_id": str(getattr(note, "note_id", "") or ""),
        "title": str(getattr(note, "title", "") or ""),
        "desc": str(getattr(note, "desc", "") or ""),
        "tags": tags,
        "url": str(getattr(note, "note_url", "") or ""),
        "likes": int(getattr(note, "liked_count", 0) or 0),
        "collects": int(getattr(note, "collected_count", 0) or 0),
        "comments": int(getattr(note, "comment_count", 0) or 0),
        "interaction_score": int(getattr(note, "interaction_score", 0) or 0),
        "publish_time": str(getattr(note, "upload_time", "") or getattr(note, "publish_time", "") or ""),
        "xsec_token": str(getattr(note, "xsec_token", "") or ""),
        "note_type": str(getattr(note, "note_type", "") or ""),
    }


def _llm_parse_json(text: str) -> Optional[Dict]:
    """从 LLM 响应中提取 JSON 对象，容错处理代码块包裹等情况。"""
    text = text.strip()
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


def _llm_parse_json_robust(text: str) -> Optional[Dict]:
    """更健壮的 JSON 解析：先尝试标准解析，失败时用正则逐条提取已完成的 results 条目。"""
    result = _llm_parse_json(text)
    if result is not None:
        return result

    # 标准解析失败（可能被截断）：逐条抢救 results 数组里已完整的条目
    items = re.findall(r'\{[^{}]+\}', text)
    rescued: list = []
    for item_str in items:
        try:
            obj = json.loads(item_str)
            if "index" in obj:
                rescued.append(obj)
        except Exception:
            pass
    if rescued:
        logger.debug(f"[comment_pipeline] JSON 截断，逐条抢救到 {len(rescued)} 条分类结果")
        return {"results": rescued}
    return None


async def _llm_chat(
    agent_id: str, system: str, user: str,
    max_tokens: int = 600, json_mode: bool = False,
) -> str:
    """调用 LLM，返回文本内容。失败时返回空字符串。"""
    try:
        overrides: dict = {"temperature": 0.3, "max_tokens": max_tokens}
        if json_mode:
            overrides["response_format"] = {"type": "json_object"}
        result = await model_gateway.chat(
            agent_id,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            modality="text",
            overrides=overrides,
        )
        if isinstance(result, dict):
            return str(result.get("content") or "").strip()
        return str(result or "").strip()
    except Exception as exc:
        logger.warning(f"[comment_pipeline] LLM 调用失败 agent={agent_id}: {exc}")
        return ""


async def _tier1_brief_filter(user_query: str, briefs: list) -> list:
    """Tier1 粗过滤：每页 briefs 批量一次 LLM 调用，宽松淘汰明显不相关条目。

    fail-open：LLM 或解析失败时原样返回 briefs，不丢弃任何条目。
    """
    if not briefs:
        return briefs
    try:
        id_map = {b["note_id"]: b for b in briefs if b.get("note_id")}
        if not id_map:
            return briefs
        items_text = "\n".join(
            "note_id={} 标题={} 标签={}".format(
                b["note_id"],
                b.get("display_title") or b.get("title", ""),
                "|".join(t.get("name", "") for t in (b.get("tag_list") or [])),
            )
            for b in briefs if b.get("note_id")
        )
        system = (
            "你是小红书笔记相关性初筛专家。你的任务是：根据用户的搜索意图，对一批笔记做「粗粒度」相关性判断。\n\n"
            "## 判断标准\n"
            "只需排除「核心品类/话题完全不符」的笔记，允许通过以下类型：\n"
            "- 同品类的不同角度：评测、种草、使用心得、教程、避坑、好物推荐等\n"
            "- 同品类的横向延伸：相关场景、人群、功效、使用方式\n"
            "- 标题/标签信息不足、无法确定时：默认通过（宁可放过不误杀）\n\n"
            "## 必须淘汰的情况\n"
            "- 关键词相同但品类完全不同（例：用户搜「燕麦奶」食品，笔记是「燕麦奶色系穿搭」）\n"
            "- 关键词仅作为修饰词出现，笔记主体是另一品类（例：用户搜「助眠精油」，笔记核心是「助眠冥想音频」）\n\n"
            "## 重要原则\n"
            "本轮是粗筛，判断依据仅为标题和标签，信息有限，请保持宽松，遇到模糊情况一律通过。\n\n"
            '输出合法 JSON，格式：{"results": [{"note_id": "...", "pass": true}]}'
        )
        user_msg = f"{user_query}\n\n待判断笔记：\n{items_text}"
        raw = await _llm_chat(
            "CommentPipeline.Tier1Filter", system, user_msg,
            max_tokens=800, json_mode=True,
        )
        parsed = _llm_parse_json(raw) or {}
        results = parsed.get("results", [])
        all_seen_ids = {r["note_id"] for r in results if "note_id" in r}
        pass_ids = {r["note_id"] for r in results if r.get("pass", True)}
        # 未出现在 LLM 结果中的 note_id 默认通过，避免误杀
        pass_ids |= (set(id_map) - all_seen_ids)
        filtered = [b for b in briefs if not b.get("note_id") or b["note_id"] in pass_ids]
        logger.info(
            f"[Tier1Filter] 过滤前 {len(briefs)} 条 → 过滤后 {len(filtered)} 条"
            f"（淘汰 {len(briefs) - len(filtered)} 条）"
        )
        return filtered
    except Exception as exc:
        logger.warning(f"[Tier1Filter] 过滤失败，fail-open 返回原始 briefs: {exc}")
        return briefs


async def _tier2_detail_filter(user_query: str, note: dict) -> bool:
    """Tier2 精判：对单条 note 详情判断核心主题是否符合用户意图。

    fail-open：LLM 或解析失败时返回 True（通过），不误杀。
    兼容 handle_note_info 正常返回（tags: List[str]）和降级 note_card（tag_list）两种格式。
    """
    try:
        title = note.get("title") or note.get("display_title", "")
        desc = note.get("desc", "")
        # 兼容两种格式：正常为 tags: List[str]，降级为 tag_list: [{"name": "..."}]
        tags = note.get("tags") or [
            t.get("name", "") for t in (note.get("tag_list") or [])
        ]
        tags_str = " | ".join(str(t) for t in tags if t)
        system = (
            "你是小红书笔记相关性精判专家。你的任务是：根据用户的搜索意图，对单条笔记的完整内容做「精细」相关性判断。\n\n"
            "## 判断标准\n"
            "笔记的核心话题必须与用户意图属于同一品类/领域，以下情况判定为通过：\n"
            "- 内容角度不同但品类相同：评测/种草/使用教程/成分分析/对比/避坑/推荐清单等\n"
            "- 场景/人群/功效/使用方式不同，但核心品类一致\n"
            "- 笔记提及多个产品，但主要讨论的品类与用户意图一致\n\n"
            "## 必须淘汰的情况\n"
            "- 关键词相同但实际品类不同（例：用户搜「燕麦奶」食品类，笔记讲的是「燕麦奶色系穿搭/家居」）\n"
            "- 用户意图有明确约束（如「个护产品」），而笔记属于完全不同领域（如「音乐/影视/穿搭」）\n"
            "- 笔记仅在标签中带有关键词，但正文核心内容与用户意图无关\n\n"
            "## 判断要点\n"
            "重点看笔记的「核心受众」和「核心诉求」是否与用户意图匹配，而不是字面关键词是否出现。\n\n"
            '输出合法 JSON，格式：{"pass": true, "reason": "一句话说明判断依据"}'
        )
        user_msg = (
            f"{user_query}\n\n"
            f"笔记内容：\n标题：{title}\n描述：{desc[:300]}\n标签：{tags_str}"
        )
        raw = await _llm_chat(
            "CommentPipeline.Tier2Filter", system, user_msg,
            max_tokens=200, json_mode=True,
        )
        parsed = _llm_parse_json(raw) or {}
        result = bool(parsed.get("pass", True))
        if not result:
            logger.debug(
                f"[Tier2Filter] 淘汰 note_id={note.get('note_id','?')} "
                f"reason={parsed.get('reason','')}"
            )
        return result
    except Exception as exc:
        logger.warning(f"[Tier2Filter] 过滤失败，fail-open 返回 True: {exc}")
        return True


# ── 步骤实现 ─────────────────────────────────────────────────────────────────

async def _step0_parse_input(
    raw_input: str,
    hint_keywords: List[str],
) -> Dict[str, Any]:
    """Step 0：LLM 解析自然语言输入，提取干净的搜索关键词和配置参数。

    如果 hint_keywords 已经是干净的短词（每个词 ≤ 10 字且不含「笔记」「评论区」等修饰），
    则直接复用，只做轻量去噪；否则调用 LLM 解析。

    Returns:
        {
            "keywords": [...],
            "time_range": 0,
            "min_interaction": 0,
            "top_notes": 20,
            "top_comments_per_note": 5,
        }
    """
    _NOISE_WORDS = re.compile(
        r"笔记|评论区|评论|互动量|点赞|热门|爆款|小红书|帮我|分析|采集|整理|汇总|查看|看看|高赞|留言"
    )

    def _is_clean(kw: str) -> bool:
        return len(kw) <= 10 and not _NOISE_WORDS.search(kw)

    # 如果所有 hint 关键词都是干净的短词，直接用
    if hint_keywords and all(_is_clean(k) for k in hint_keywords):
        return {
            "keywords": hint_keywords,
            "time_range": 0,
            "min_interaction": 0,
            "top_notes": _DEFAULT_TOP_NOTES,
            "top_comments_per_note": _DEFAULT_TOP_COMMENTS,
        }

    # 否则调 LLM 解析（用 raw_input，hint_keywords 作为补充提示）
    user_msg = raw_input.strip()
    if hint_keywords:
        user_msg = f"用户输入：{raw_input}\n参考关键词提示（可能不准确）：{'、'.join(hint_keywords)}"

    raw = await _llm_chat("CommentPipeline.InputParser", _INPUT_PARSER_SYSTEM, user_msg, max_tokens=200)
    parsed = _llm_parse_json(raw)

    if not parsed:
        # LLM 解析失败：最后兜底，直接对 hint_keywords 去噪
        fallback_kw = [_NOISE_WORDS.sub("", k).strip() for k in hint_keywords]
        fallback_kw = [k for k in fallback_kw if len(k) >= 2]
        if not fallback_kw:
            fallback_kw = hint_keywords[:3]
        logger.warning(f"[comment_pipeline] InputParser LLM 失败，兜底 keywords={fallback_kw}")
        return {
            "keywords": fallback_kw,
            "time_range": 0,
            "min_interaction": 0,
            "top_notes": _DEFAULT_TOP_NOTES,
            "top_comments_per_note": _DEFAULT_TOP_COMMENTS,
        }

    kw_list = [str(k).strip() for k in (parsed.get("keywords") or []) if str(k).strip()]
    if not kw_list:
        kw_list = hint_keywords[:3] or [""]

    return {
        "keywords": kw_list[:5],
        "time_range": int(parsed.get("time_range") or 0),
        "min_interaction": int(parsed.get("min_interaction") or 0),
        "top_notes": int(parsed.get("top_notes") if parsed.get("top_notes") is not None else _DEFAULT_TOP_NOTES),
        "top_comments_per_note": int(parsed.get("top_comments_per_note") or _DEFAULT_TOP_COMMENTS),
    }


async def _step1_crawl_notes(
    keywords: List[str],
    cookies_str: str,
    target_count: int,
    *,
    time_range: int = 0,
    min_interaction: int = 0,
    user_query: str = "",
    owner_user_id: str = "",
) -> List[Dict[str, Any]]:
    """步骤 1：爬取笔记，返回 dict 列表，按互动量降序。

    user_query 非空时，将两级语义过滤闭包注入 runtime_cfg，
    由 _collect_one_dimension → ViralNoteCollector 在采集过程中调用。
    过滤函数定义在本文件（comment_pipeline.py），可直接访问 model_gateway。
    """
    from ..application.agents.crawler_agent import _collect_one_dimension

    runtime_cfg: Dict[str, Any] = {
        "target_count": target_count,
        "note_type": 0,
        "time_range": time_range,
        "min_interaction": min_interaction,
    }
    if user_query:
        _uq = user_query  # 显式捕获，避免闭包引用变量被覆盖
        runtime_cfg["tier1_filter"] = lambda b: _tier1_brief_filter(_uq, b)
        runtime_cfg["tier2_filter"] = lambda n: _tier2_detail_filter(_uq, n)

    try:
        raw = await _collect_one_dimension(cookies_str, keywords, target_count, runtime_cfg, owner_user_id or None)
        notes = [_note_to_dict(n) for n in (raw or [])]
        notes.sort(key=_interaction_score, reverse=True)
        return notes
    except Exception as exc:
        logger.warning(f"[comment_pipeline] 爬取笔记失败: {exc}")
        return []


async def _step2_fetch_comments_for_note(
    note: Dict[str, Any],
    cookies_str: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    """步骤 2+3：拉取单条笔记的首屏评论，按 like_count 降序取 Top K。"""
    note_id = str(note.get("note_id") or "").strip()
    if not note_id or note_id.startswith("stub_"):
        return []

    xsec = str(note.get("xsec_token") or "").strip()
    if not xsec:
        # 尝试从 url 中解析 xsec_token
        url = str(note.get("url") or "")
        m = re.search(r'xsec_token=([^&]+)', url)
        if m:
            xsec = m.group(1)

    if not xsec:
        logger.debug(f"[comment_pipeline] note_id={note_id} 无 xsec_token，跳过评论拉取")
        return []

    try:
        from apis.xhs_pc_apis import XHS_Apis
        client = XHS_Apis()
        success, msg, res_json = await asyncio.to_thread(
            client.get_note_out_comment, note_id, "", xsec, cookies_str
        )
        if not success or not isinstance(res_json, dict):
            logger.debug(f"[comment_pipeline] note_id={note_id} 评论接口失败: {msg}")
            return []

        data = res_json.get("data") or {}

        # 尝试从评论 API 响应里顺带提取笔记发布时间
        note_time_raw = (
            (data.get("note_info") or {}).get("time")
            or (data.get("note") or {}).get("time")
            or (data.get("note") or {}).get("create_time")
        )
        if note_time_raw and not note.get("publish_time"):
            try:
                import datetime as _dt
                ts = int(note_time_raw) // 1000 if int(note_time_raw) > 1e10 else int(note_time_raw)
                note["publish_time"] = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except Exception:
                pass

        raw_comments: List[Any] = []
        for key in ("comments", "comment_list", "list", "items"):
            if isinstance(data.get(key), list):
                raw_comments = data[key]
                break

        result = []
        for c in raw_comments:
            if not isinstance(c, dict):
                continue
            content = c.get("content") or {}
            text = str(content.get("text") if isinstance(content, dict) else content or "").strip()
            if not text:
                continue
            result.append({
                "comment_id": str(c.get("id") or ""),
                "content": text,
                "like_count": int(c.get("like_count") or 0),
                "author": str((c.get("user_info") or {}).get("nickname") or ""),
                "note_id": note_id,
                "note_url": str(note.get("url") or ""),
            })

        result.sort(key=lambda x: x["like_count"], reverse=True)
        # top_k=0 表示不限，返回全部；否则截取前 top_k 条
        return result if top_k <= 0 else result[:top_k]

    except Exception as exc:
        logger.warning(f"[comment_pipeline] 拉取评论异常 note_id={note_id}: {exc}")
        return []


async def _classify_single_note(note: Dict[str, Any]) -> str:
    """对单条笔记独立调用 LLM 分类。单条调用 max_tokens 给大一点，确保能返回完整 JSON。"""
    title = (note.get("title") or "").strip()
    desc = (note.get("desc") or "")[:80].strip()
    tags = "、".join(str(t) for t in (note.get("tags") or [])[:5])
    info = title
    if desc:
        info += f"｜{desc}"
    if tags:
        info += f"｜#{tags}"
    raw = await _llm_chat(
        "CommentPipeline.NoteClassifier",
        _NOTE_CLASSIFY_SYSTEM,
        f"请为以下1条笔记分类（index=0）：\n0. {info[:300]}",
        max_tokens=80,
    )
    parsed = _llm_parse_json_robust(raw)
    if parsed and isinstance(parsed.get("results"), list):
        for item in parsed["results"]:
            cat = (item.get("category") or "").strip()
            if cat and cat != "其他":
                return cat
    # 单条重试也拿不到结果说明模型异常或内容为空，返回通用类别
    return "种草帖" if title else "笔记分享"


async def _step4_classify_notes(notes: List[Dict[str, Any]]) -> List[str]:
    """步骤 4：LLM 批量分类笔记，返回与 notes 等长的分类列表。分批处理（每批 10 条）。
    批次失败时对未分类条目逐条重试，确保每条都有基于内容的有效标签。"""
    if not notes:
        return []
    categories: List[Optional[str]] = [None] * len(notes)
    batch_size = 10
    for start in range(0, len(notes), batch_size):
        batch = notes[start: start + batch_size]
        lines = []
        for i, n in enumerate(batch):
            title = (n.get("title") or "").strip()
            desc = (n.get("desc") or "")[:60].strip()
            tags = "、".join(str(t) for t in (n.get("tags") or [])[:5])
            info = title
            if desc:
                info += f"｜{desc}"
            if tags:
                info += f"｜#{tags}"
            lines.append(f"{i}. {info[:150]}")
        user_msg = (
            f"以下是 {len(batch)} 条小红书笔记（index 从 0 开始），"
            f"请对全部 {len(batch)} 条逐一分类，index 必须与输入一一对应：\n"
            + "\n".join(lines)
        )
        raw = await _llm_chat(
            "CommentPipeline.NoteClassifier", _NOTE_CLASSIFY_SYSTEM, user_msg, max_tokens=800
        )
        parsed = _llm_parse_json_robust(raw)
        if parsed and isinstance(parsed.get("results"), list):
            for item in parsed["results"]:
                idx = item.get("index")
                cat = (item.get("category") or "").strip()
                if not cat or cat == "其他":
                    continue
                global_idx = start + (idx if isinstance(idx, int) else 0)
                if 0 <= global_idx < len(categories):
                    categories[global_idx] = cat

    # 对批次中未分类的条目逐条重试
    retry_indices = [i for i, c in enumerate(categories) if c is None]
    if retry_indices:
        logger.info(f"[NoteClassifier] {len(retry_indices)} 条批次未分类，逐条重试...")
        tasks = [_classify_single_note(notes[i]) for i in retry_indices]
        results = await asyncio.gather(*tasks)
        for i, cat in zip(retry_indices, results):
            categories[i] = cat

    return [c or "笔记分享" for c in categories]


async def _step5_classify_comments(
    comments: List[Dict[str, Any]],
    keywords: Optional[List[str]] = None,
) -> List[str]:
    """步骤 5：LLM 批量分类评论，返回与 comments 等长的类型列表。分批处理（每批 25 条）。"""
    if not comments:
        return []
    result: List[Optional[str]] = [None] * len(comments)
    batch_size = 15
    kw_hint = f"（关于：{'/'.join(keywords)}）" if keywords else ""
    for start in range(0, len(comments), batch_size):
        batch = comments[start: start + batch_size]
        lines = [f"{i}. {c.get('content', '')[:120]}" for i, c in enumerate(batch)]
        user_msg = (
            f"以下是 {len(batch)} 条小红书评论{kw_hint}（index 从 0 开始），"
            f"请对全部 {len(batch)} 条逐一分类，index 必须与输入一一对应：\n"
            + "\n".join(lines)
        )
        raw = await _llm_chat(
            "CommentPipeline.CommentClassifier", _COMMENT_CLASSIFY_SYSTEM, user_msg, max_tokens=900
        )
        parsed = _llm_parse_json_robust(raw)
        if parsed and isinstance(parsed.get("results"), list):
            for item in parsed["results"]:
                batch_idx = item.get("index")
                if not isinstance(batch_idx, int) or not (0 <= batch_idx < len(batch)):
                    continue
                ctype = (item.get("comment_type") or "").strip()
                if not ctype or ctype == "其他":
                    continue
                result[start + batch_idx] = ctype

    # 对批次中未分类的评论逐条重试
    retry_indices = [i for i, c in enumerate(result) if c is None]
    if retry_indices:
        logger.info(f"[CommentClassifier] {len(retry_indices)} 条批次未分类，逐条重试...")
        async def _classify_single_comment(comment: Dict[str, Any]) -> str:
            content = (comment.get("content") or "")[:200]
            raw = await _llm_chat(
                "CommentPipeline.CommentClassifier",
                _COMMENT_CLASSIFY_SYSTEM,
                f"请为以下1条评论分类{kw_hint}（index=0）：\n0. {content}",
                max_tokens=60,
            )
            parsed = _llm_parse_json_robust(raw)
            if parsed and isinstance(parsed.get("results"), list):
                for item in parsed["results"]:
                    ctype = (item.get("comment_type") or "").strip()
                    if ctype and ctype != "其他":
                        return ctype
            # 最终兜底：从评论内容本身提炼
            content_short = content.strip()
            if not content_short or len(content_short) <= 3:
                return "情绪互动型"
            if any(w in content_short for w in ["？", "?", "吗", "呢", "啊", "怎么", "哪个", "如何"]):
                return "产品提问型"
            if any(w in content_short for w in ["踩雷", "不好", "失望", "差", "骗"]):
                return "负面吐槽型"
            if any(w in content_short for w in ["哈哈", "哈", "😂", "lol", "hhh"]):
                return "情绪互动型"
            return "用户反馈型"
        tasks = [_classify_single_comment(comments[i]) for i in retry_indices]
        retry_results = await asyncio.gather(*tasks)
        for i, ctype in zip(retry_indices, retry_results):
            result[i] = ctype

    return [c or "用户反馈型" for c in result]


async def _step6_summarize_note_topics(
    note: Dict[str, Any],
    comments: List[Dict[str, Any]],
) -> str:
    """步骤 6：LLM 总结单条笔记评论区话题。"""
    if not comments:
        return ""
    comment_texts = "\n".join(
        f"- {c['content'][:80]}（点赞{c['like_count']}）" for c in comments[:15]
    )
    user_msg = f"笔记标题：{note.get('title', '')}\n\n评论内容：\n{comment_texts}"
    return await _llm_chat("CommentPipeline.TopicSummarizer", _NOTE_TOPIC_SYSTEM, user_msg, max_tokens=200)


async def _step7_aggregate_and_summarize(
    all_comments: List[Dict[str, Any]],
    total_likes: int,
) -> List[Dict[str, Any]]:
    """步骤 7：聚合分组 + LLM 写摘要和 AI 评论，返回 Sheet1 行数据列表。"""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for c in all_comments:
        key = (str(c.get("note_category") or "其他"), str(c.get("comment_type") or "其他"))
        groups[key].append(c)

    # 按分组点赞总数降序排列
    sorted_groups = sorted(
        groups.items(),
        key=lambda kv: sum(x["like_count"] for x in kv[1]),
        reverse=True,
    )

    sheet1_rows = []
    tasks_llm = []

    async def _process_group(seq: int, key: Tuple[str, str], group_comments: List[Dict]) -> Dict:
        note_cat, comment_type = key
        group_likes = sum(c["like_count"] for c in group_comments)
        ratio = f"{group_likes / total_likes * 100:.0f}%" if total_likes > 0 else "0%"
        top_comment = max(group_comments, key=lambda x: x["like_count"])

        texts = "\n".join(f"- {c['content'][:80]}（点赞{c['like_count']}）" for c in group_comments[:20])
        user_msg = (
            f"笔记类别：{note_cat}\n评论类型：{comment_type}\n\n"
            f"评论样本（共 {len(group_comments)} 条）：\n{texts}"
        )
        raw = await _llm_chat("CommentPipeline.GroupSummarizer", _GROUP_SUMMARY_SYSTEM, user_msg, max_tokens=600)
        parsed = _llm_parse_json(raw)
        summary = (parsed or {}).get("summary") or ""
        ai_comments_raw = (parsed or {}).get("ai_comments") or []
        # 兼容旧格式（单条 ai_comment）
        if not ai_comments_raw:
            single = (parsed or {}).get("ai_comment") or ""
            ai_comments_raw = [single] if single else []
        # 取前5条，过滤空字符串，带序号拼成多行文本
        ai_comments_list = [c.strip() for c in ai_comments_raw if str(c).strip()][:5]
        ai_comment = "\n".join(f"{i+1}. {c}" for i, c in enumerate(ai_comments_list))

        return {
            "seq": seq,
            "note_category": note_cat,
            "comment_type": comment_type,
            "ratio": ratio,
            "summary": summary,
            "top_comment": top_comment["content"],
            "ai_comment": ai_comment,
            "ai_comment_count": len(ai_comments_list),
            "group_like_total": group_likes,
        }

    coros = [_process_group(i + 1, key, grp) for i, (key, grp) in enumerate(sorted_groups)]
    sheet1_rows = await asyncio.gather(*coros)
    return list(sheet1_rows)


# ── 对话回写 ──────────────────────────────────────────────────────────────────

def _append_completion_message(task_id: str, content: str) -> None:
    """将流水线完成消息写回到对应的对话中，让下载链接显示在聊天里。"""
    try:
        from ..domain.conversation import ChatMessage, new_id
        from ..services.conversation_store import get_conversation_store
        conversation_store = get_conversation_store()

        record = task_repository.get(task_id)
        if not record:
            return

        # 从 input_spec 取 conversation_id
        conversation_id = str(
            (record.input_spec or {}).get("advanced_config", {}).get("conversation_id") or ""
        ).strip()
        if not conversation_id:
            logger.debug(f"[comment_pipeline] task={task_id} 无 conversation_id，跳过回写消息")
            return

        msg = ChatMessage(
            message_id=new_id("msg"),
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            intent="comment_analysis",
            linked_task_id=task_id,
        )
        conversation_store.append_message(conversation_id, msg)
        logger.info(f"[comment_pipeline] task={task_id} 完成消息已写回对话 {conversation_id}")
    except Exception as exc:
        logger.warning(f"[comment_pipeline] 回写对话消息失败（忽略）: {exc}")


# ── 主入口 ────────────────────────────────────────────────────────────────────

async def run_comment_pipeline(
    task_id: str,
    keywords: List[str],
    raw_input: str = "",
    top_notes: int = _DEFAULT_TOP_NOTES,
    top_comments_per_note: int = _DEFAULT_TOP_COMMENTS,
) -> None:
    """评论分析主流水线入口，由 tool_executor 通过 asyncio.create_task 调起。

    Args:
        task_id: 任务 ID
        keywords: 意图层提取的关键词（可能含修饰词，Step 0 会清洗）
        raw_input: 用户原始输入（用于 LLM 解析）
        top_notes: 分析笔记数量
        top_comments_per_note: 每条笔记取评论数量
    """
    start_ts = datetime.now(timezone.utc)
    logger.info(f"[comment_pipeline] 启动 task={task_id} keywords={keywords}")

    # ── 任务状态：running ──
    try:
        task_repository.update(task_id, status=TaskStatus.RUNNING.value, progress=5)
        await _emit(task_id, TaskEventType.TASK_STATUS, {"status": TaskStatus.RUNNING.value, "progress": 5})
    except Exception:
        pass

    try:
        record = task_repository.get(task_id)
        owner_user_id = str(record.owner_user_id) if record else ""
        cookies_str = _resolve_cookies(owner_user_id)
        if not cookies_str:
            raise ValueError("未找到可用的 XHS Cookie，请在设置→数据源授权中绑定小红书账号")

        # ── Step 0：解析自然语言输入，提取干净关键词和配置 ──
        await _emit_progress(task_id, "解析搜索意图...", 5)
        parsed = await _step0_parse_input(
            raw_input=raw_input or " ".join(keywords),
            hint_keywords=keywords,
        )
        keywords = parsed["keywords"]
        # 用 None 判断，避免 0（不限）被 or 运算符视为假值而被覆盖
        _tn = parsed.get("top_notes")
        top_notes = _tn if _tn is not None else top_notes
        _tc = parsed.get("top_comments_per_note")
        top_comments_per_note = _tc if (_tc is not None and _tc > 0) else top_comments_per_note
        time_range = parsed.get("time_range") or 0
        min_interaction = parsed.get("min_interaction") or 0

        logger.info(
            f"[comment_pipeline] task={task_id} 解析后 keywords={keywords} "
            f"time_range={time_range} min_interaction={min_interaction}"
        )

        if not keywords or not any(k.strip() for k in keywords):
            raise ValueError("无法从输入中提取有效的搜索关键词，请直接说明你想分析的产品名称，例如：防脱精华")

        # ── Step 1：爬取笔记（先查缓存）──
        await _emit_progress(task_id, f"正在采集关键词「{'、'.join(keywords)}」的笔记...", 10)

        notes_with_comments: List[tuple] = []
        total_comment_count = 0
        from_cache = False

        cached = await comment_cache_store.get(keywords, time_range, min_interaction)
        if cached:
            # 缓存命中：直接还原 notes_with_comments
            cached_notes = cached["notes"]
            notes_with_comments = [
                (n, n.pop("_cached_comments", [])) for n in cached_notes
            ]
            total_comment_count = sum(len(c) for _, c in notes_with_comments)
            from_cache = True
            logger.info(
                f"[comment_pipeline] task={task_id} 缓存命中，"
                f"跳过爬取 notes={len(notes_with_comments)} comments={total_comment_count} "
                f"age={cached.get('cache_age_hours')}h"
            )
            await _emit_progress(
                task_id,
                f"命中缓存（{cached.get('cache_age_hours', 0)} 小时前），"
                f"已有 {len(notes_with_comments)} 条笔记数据，开始 AI 分析...",
                40,
            )
        else:
            # 缓存未命中：正常爬取
            crawl_target = max(top_notes * 3, _CRAWL_TARGET_PER_KW) if top_notes > 0 else _CRAWL_TARGET_PER_KW
            notes_raw = await _step1_crawl_notes(
                keywords, cookies_str, crawl_target,
                time_range=time_range, min_interaction=min_interaction,
                user_query=raw_input,
                owner_user_id=owner_user_id,
            )
            # top_notes=0 表示不限，直接用全部爬取结果
            top_notes_list = notes_raw[:top_notes] if top_notes > 0 else notes_raw

            if not top_notes_list:
                raise ValueError(
                    f"关键词「{'、'.join(keywords)}」未采集到任何笔记，请检查 Cookie 或关键词"
                )

            await _emit_progress(task_id, f"已采集 {len(top_notes_list)} 条笔记，开始拉取评论...", 25)
            logger.info(f"[comment_pipeline] task={task_id} 采集到 {len(top_notes_list)} 条笔记")

            # ── Step 2+3：并发拉评论（限并发数） ──
            semaphore = asyncio.Semaphore(_MAX_CONCURRENT_COMMENTS)

            async def _fetch_with_sem(note: Dict) -> Tuple[Dict, List[Dict]]:
                async with semaphore:
                    comments = await _step2_fetch_comments_for_note(note, cookies_str, top_comments_per_note)
                    await asyncio.sleep(_INTER_NOTE_SLEEP)
                    return note, comments

            fetch_results = await asyncio.gather(*[_fetch_with_sem(n) for n in top_notes_list])
            notes_with_comments = [(n, c) for n, c in fetch_results if c]
            total_comment_count = sum(len(c) for _, c in notes_with_comments)

            # ── 写入缓存（后台，不阻塞流水线） ──
            if notes_with_comments:
                cache_notes = [
                    {**n, "_cached_comments": c} for n, c in notes_with_comments
                ]
                asyncio.create_task(
                    comment_cache_store.set(
                        keywords=keywords,
                        time_range=time_range,
                        min_interaction=min_interaction,
                        notes=cache_notes,
                        comment_count=total_comment_count,
                    )
                )

        await _emit_progress(task_id, f"已获取 {total_comment_count} 条高赞评论，开始 AI 分析...", 45)

        # ── Step 4：LLM 分类笔记 ──
        await _emit_progress(task_id, "AI 分类笔记类型...", 50)
        note_categories = await _step4_classify_notes([n for n, _ in notes_with_comments])

        # ── Step 5：LLM 分类评论（扁平化所有评论一起批处理） ──
        await _emit_progress(task_id, "AI 分类评论类型...", 60)
        all_comments_flat: List[Dict[str, Any]] = []
        note_comment_offsets: List[Tuple[int, int]] = []  # (start, end) in all_comments_flat
        for (note, comments), cat in zip(notes_with_comments, note_categories):
            start = len(all_comments_flat)
            for c in comments:
                c["note_category"] = cat
            all_comments_flat.extend(comments)
            note_comment_offsets.append((start, len(all_comments_flat)))

        comment_types = await _step5_classify_comments(all_comments_flat, keywords=keywords)
        for i, ctype in enumerate(comment_types):
            if i < len(all_comments_flat):
                all_comments_flat[i]["comment_type"] = ctype

        # ── Step 6：LLM 总结每条笔记评论区话题 ──
        await _emit_progress(task_id, "AI 总结评论话题...", 70)
        note_topic_tasks = []
        for (note, comments), cat in zip(notes_with_comments, note_categories):
            note["note_category"] = cat
            note_topic_tasks.append(_step6_summarize_note_topics(note, comments))
        note_topics = await asyncio.gather(*note_topic_tasks)

        # ── Step 7：聚合分组 + LLM 写摘要 ──
        await _emit_progress(task_id, "AI 聚合分析，生成评论洞察报告...", 80)
        total_likes = sum(c["like_count"] for c in all_comments_flat) or 1
        sheet1_rows = await _step7_aggregate_and_summarize(all_comments_flat, total_likes)

        # ── 组装 Sheet3 笔记数据 ──
        sheet3_rows = []
        for (note, _comments), cat, topic in zip(notes_with_comments, note_categories, note_topics):
            sheet3_rows.append({
                "url": note.get("url") or note.get("note_id") or "",
                "publish_time": note.get("publish_time") or "",
                "title": note.get("title") or "",
                "interaction_score": note.get("interaction_score") or 0,
                "comment_count": note.get("comments") or 0,
                "note_category": cat,
                "topic_summary": topic,
            })

        # ── 组装输出 ──
        elapsed = int((datetime.now(timezone.utc) - start_ts).total_seconds())
        comment_output = {
            "keywords": keywords,
            "total_notes": len(notes_with_comments),
            "total_comments": total_comment_count,
            "elapsed_seconds": elapsed,
            "sheet1_analysis": sheet1_rows,
            "sheet2_comments": all_comments_flat,
            "sheet3_notes": sheet3_rows,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # ── Step 8：写入 TaskContext ──
        ctx = task_context_store.require(task_id)
        from ..domain.task_context import TaskContextWriter
        writer = TaskContextWriter(ctx)
        writer.write("comment_output", comment_output, agent_id="CommentPipeline")

        # 更新任务状态为完成
        task_repository.update(
            task_id,
            status=TaskStatus.COMPLETED.value,
            progress=100,
            collected_count=total_comment_count,
            duration_seconds=elapsed,
        )

        # 构建结果摘要
        top3 = sheet1_rows[:3]
        findings = "\n".join(
            f"- **{r['comment_type']}**（占比 {r['ratio']}）：{r['summary']}"
            for r in top3 if r.get("summary")
        )
        export_url = f"/api/v1/tasks/{task_id}/export/comment_excel"
        summary_text = (
            f"**评论分析完成** ✓\n\n"
            f"关键词：{'、'.join(keywords)} | 笔记：{len(notes_with_comments)} 条 | 评论：{total_comment_count} 条 | 耗时：{elapsed}s\n\n"
            + (f"**主要发现：**\n{findings}\n\n" if findings else "")
            + f"[点击下载评论分析报告 Excel]({export_url})"
        )

        # 先推送 conversation_message（必须在 DONE 之前！SSE 客户端收到 done 后立即关闭连接）
        await _emit(task_id, TaskEventType.CONVERSATION_MESSAGE, {
            "role": "assistant",
            "content": summary_text,
            "intent": "comment_analysis",
            "linked_task_id": task_id,
        })

        # 写回对话持久化存储（断线重连时可从历史中恢复）
        _append_completion_message(task_id, summary_text)

        # 最后推送 DONE 事件（SSE 客户端收到后立即关闭连接，必须放最后）
        await _emit(task_id, TaskEventType.DONE, {
            "task_type": "comment_analysis",
            "export_url": export_url,
            "total_notes": len(notes_with_comments),
            "total_comments": total_comment_count,
        })

        logger.info(
            f"[comment_pipeline] 完成 task={task_id} "
            f"notes={len(notes_with_comments)} comments={total_comment_count} elapsed={elapsed}s"
        )

    except asyncio.CancelledError:
        # 任务被 execution_coordinator.cancel() 取消（用户点击终止按钮）。
        # cancel_task API 已将状态写为 CANCELLED 并推送 DONE 事件，此处只记录日志，不重复写状态。
        logger.info(f"[comment_pipeline] task={task_id} 已被取消，pipeline 中止")
        raise  # 必须 re-raise，让 execution_coordinator 感知 asyncio.Task 已结束

    except Exception as exc:
        logger.error(f"[comment_pipeline] 失败 task={task_id}: {type(exc).__name__}: {exc}")
        try:
            task_repository.update(
                task_id,
                status=TaskStatus.FAILED.value,
                last_error=str(exc)[:500],
            )
        except Exception:
            pass
        await _emit(task_id, TaskEventType.ERROR, {
            "code": "COMMENT_PIPELINE_FAILED",
            "message": f"评论分析失败：{exc}",
        })
