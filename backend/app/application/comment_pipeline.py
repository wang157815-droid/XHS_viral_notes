"""
CommentAnalysisSkill 流水线。

v2（当前默认）— 三维度舆情分析：
  0. 解析输入（保留）
  1. 爬取笔记（保留）
  2. 全量评论采集（全翻页一级 + 子评论展开）
  3. 维度1：产品舆情分类 + 核心发现
  4. 维度2：每类别深度分析（正/中/负 + 理论依据 + 示例）
  5. 维度3：总体 Bullet-point 洞察
  6. 生成 MD 报告
  7. 写入 TaskContext["comment_output"]，通过 SSE 推送完成事件
  输出：2-Sheet Excel（评论数据源 / 笔记数据源）+ MD 文档

v1（切换 _ANALYSIS_VERSION = "v1" 可恢复）— 旧逻辑保留，不删除：
  2. 首屏 Top K 评论
  3-7. 笔记类别×评论类型矩阵分析 → 3-Sheet Excel
"""

from __future__ import annotations

import asyncio
import io
import json
import random
import re
from collections import Counter, defaultdict
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

# ── 版本开关 ─────────────────────────────────────────────────────────────────
_ANALYSIS_VERSION: str = "v2"   # "v1" 可切回旧逻辑

# ── 采集参数默认值 ─────────────────────────────────────────────────────────────
_DEFAULT_TOP_NOTES = 0           # 0 = 不限，爬到多少用多少
_DEFAULT_TOP_COMMENTS = 5        # v1 专用：每条笔记取点赞 Top K 条评论
_CRAWL_TARGET_PER_KW = 50        # 每关键词采集目标数

# v1 速率控制
_V1_INTER_NOTE_SLEEP = 1.0
_V1_MAX_CONCURRENT = 5

# v2 速率控制（参考 MediaCrawler 策略：页间睡眠 + 串行 + 总量预算）
#
# MediaCrawler 默认：每页 sleep 2s，并发=1，每笔记上限 10 条一级评论
# 我们需要更多评论做分析，但要避免连续请求触发风控（~1900 条即中招）：
#   - 页间 sleep: 2.5~4.5s 随机 jitter，打破等间隔 bot 特征
#   - 每笔记最多 3 页（每页 ~20 条 → 上限 ~60 条一级评论）
#   - 串行处理笔记（并发=1），笔记间 sleep 4~8s
#   - 全局总一级评论软上限 1000 条，超限停止采集
#   - CaptchaError 触发后全局暂停 90s，跳过该笔记剩余页
_V2_PAGE_SLEEP_MIN = 2.5        # 页间最小 sleep（秒）
_V2_PAGE_SLEEP_MAX = 4.5        # 页间最大 sleep（秒）
_V2_INTER_NOTE_SLEEP_MIN = 4.0  # 笔记间最小 sleep（秒）
_V2_INTER_NOTE_SLEEP_MAX = 8.0  # 笔记间最大 sleep（秒）
_V2_MAX_PAGES_PER_NOTE = 5      # 每笔记最多请求页数（约 100 条一级评论）
_V2_MAX_PARENT_PER_NOTE = 100   # 每笔记一级评论硬上限
_V2_TOTAL_PARENT_BUDGET = 1000  # 全局总一级评论软上限，超过则停止继续采集
_V2_CAPTCHA_PAUSE = 90.0        # CAPTCHA 熔断后全局暂停时长（秒）
_V2_MAX_CONCURRENT = 1          # 串行处理，减少并发压力

# 保留旧名称兼容（v1 pipeline 不使用页间 sleep）
_V2_INTER_NOTE_SLEEP = 4.0
_V2_INTER_SUB_SLEEP = 0.5

# ── v1 LLM Prompt（旧版，保留不删） ────────────────────────────────────────────

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


# ── v2 LLM Prompt ────────────────────────────────────────────────────────────

_DIM1_CLASSIFY_SYSTEM = """你是产品舆情分析专家。请对小红书评论进行产品维度分类，识别用户在哪些产品特征维度上发表了意见。

分类规则：
- 每条评论只对应一个最相关的产品维度，常见维度包括（不限于）：功效/成分、使用体验/肤感、气味/味道、外观/包装、价格/性价比、服务/物流、适用人群、品牌/口碑、副作用/刺激
- 情感标签：正面（认可/满意/推荐）、负面（批评/失望/吐槽）、中性（描述/疑问/观望）
- 仅当评论与产品完全无关（纯闲聊、纯表情、无实质内容）时，类别填"其他"

输出格式（严格 JSON，不含任何其他文字）：
{"results": [{"index": 0, "category": "功效/成分", "sentiment": "正面"}, ...]}"""

_DIM1_FINDING_SYSTEM = """你是小红书产品舆情分析师。根据以下评论维度统计数据和评论样本，写一段核心发现。

要求：
- 2-4 句话，信息密度高，避免废话
- 点出最突出的 1-2 个用户关注维度及其情感倾向
- 指出正负情感的关键差异或矛盾点
- 语气客观，结合具体数字，避免模糊表述"""

_DIM2_ANALYSIS_SYSTEM = """你是资深用户行为分析师，熟悉消费者心理学和社会学理论。请对某一产品评论维度下的评论进行深度分析，分别从正面评价、中性/模糊评价、负面评价三个角度解析用户心理动因。

对每个角度输出：
- theory：一个具体的心理学或社会学理论名称（如"期望确认理论"、"认知失调理论"、"社会认同理论"、"信息瀑布效应"、"损失厌恶"、"从众效应"等，必须是真实理论）
- description：2-3 句话，解释该理论如何体现在这批评论中，结合具体评论内容说明
- examples：从提供的评论样本中选取 2-3 条最典型的原文（直接引用，不修改）

如果某个情感类型评论数为 0，examples 留空列表，description 说明"该类评论暂无样本"。

输出格式（严格 JSON，不含任何其他文字）：
{"positive": {"theory": "...", "description": "...", "examples": ["评论原文1", "评论原文2"]}, "neutral": {"theory": "...", "description": "...", "examples": ["评论原文1"]}, "negative": {"theory": "...", "description": "...", "examples": ["评论原文1", "评论原文2"]}}"""

_DIM3_SUMMARY_SYSTEM = """你是资深小红书内容策划，擅长从用户评论中提炼内容创作洞察。

请阅读以下真实用户评论样本，从**内容创作者视角**提炼行动洞察。关注：
- 我们发布相关笔记时应该注意什么、做什么
- 评论区呈现出哪些用户行为特征或互动规律
- 哪些话题/痛点/场景值得在内容中强化或规避
- 从这批评论中能学到哪些经验教训（Learnings / Insights）

要求：
- 5-8 条，每条一句完整的行动导向洞察（20-50 字）
- 每条聚焦一个独立视角，不重复，覆盖：内容角度、受众特征、互动策略、风险点等
- 结合评论中的具体细节，避免空话套话
- 语气务实，像给品牌团队/博主提建议

输出格式（严格 JSON，不含任何其他文字）：
{"insights": ["洞察1", "洞察2", "洞察3"]}"""


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
        overrides: dict = {
            "temperature": 0.3,
            "max_tokens": max_tokens,
            # DeepSeek 思考模式关闭（正确参数见官方文档）：
            #   extra_body={"thinking": {"type": "disabled"}}
            # 理由：
            #   1. 思考 tokens 会消耗 max_tokens 预算，导致 content 被截断或为空
            #   2. 思考模式下 temperature/top_p 等参数不生效
            #   3. 评论分析任务不需要深度推理，关闭可加快响应、节省成本
            "extra_body": {"thinking": {"type": "disabled"}},
        }
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


# ── 步骤实现（共用 Step 0-1） ─────────────────────────────────────────────────

async def _step0_parse_input(
    raw_input: str,
    hint_keywords: List[str],
) -> Dict[str, Any]:
    """Step 0：LLM 解析自然语言输入，提取干净的搜索关键词和配置参数。"""
    _NOISE_WORDS = re.compile(
        r"笔记|评论区|评论|互动量|点赞|热门|爆款|小红书|帮我|分析|采集|整理|汇总|查看|看看|高赞|留言"
    )

    def _is_clean(kw: str) -> bool:
        return len(kw) <= 10 and not _NOISE_WORDS.search(kw)

    if hint_keywords and all(_is_clean(k) for k in hint_keywords):
        return {
            "keywords": hint_keywords,
            "time_range": 0,
            "min_interaction": 0,
            "top_notes": _DEFAULT_TOP_NOTES,
            "top_comments_per_note": _DEFAULT_TOP_COMMENTS,
        }

    user_msg = raw_input.strip()
    if hint_keywords:
        user_msg = f"用户输入：{raw_input}\n参考关键词提示（可能不准确）：{'、'.join(hint_keywords)}"

    raw = await _llm_chat("CommentPipeline.InputParser", _INPUT_PARSER_SYSTEM, user_msg, max_tokens=200)
    parsed = _llm_parse_json(raw)

    if not parsed:
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
    """步骤 1：爬取笔记，返回 dict 列表，按互动量降序。"""
    from ..application.agents.crawler_agent import _collect_one_dimension

    runtime_cfg: Dict[str, Any] = {
        "target_count": target_count,
        "note_type": 0,
        "time_range": time_range,
        "min_interaction": min_interaction,
    }
    if user_query:
        _uq = user_query
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


# ── v1 专用步骤（保留原样，不删除） ──────────────────────────────────────────

async def _step2_fetch_comments_for_note(
    note: Dict[str, Any],
    cookies_str: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    """[v1] 步骤 2+3：拉取单条笔记的首屏评论，按 like_count 降序取 Top K。"""
    note_id = str(note.get("note_id") or "").strip()
    if not note_id or note_id.startswith("stub_"):
        return []

    xsec = str(note.get("xsec_token") or "").strip()
    if not xsec:
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
        return result if top_k <= 0 else result[:top_k]

    except Exception as exc:
        logger.warning(f"[comment_pipeline] 拉取评论异常 note_id={note_id}: {exc}")
        return []


async def _classify_single_note(note: Dict[str, Any]) -> str:
    """[v1] 对单条笔记独立调用 LLM 分类。"""
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
    return "种草帖" if title else "笔记分享"


async def _step4_classify_notes(notes: List[Dict[str, Any]]) -> List[str]:
    """[v1] 步骤 4：LLM 批量分类笔记，返回与 notes 等长的分类列表。"""
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
    """[v1] 步骤 5：LLM 批量分类评论，返回与 comments 等长的类型列表。"""
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
    """[v1] 步骤 6：LLM 总结单条笔记评论区话题。"""
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
    """[v1] 步骤 7：聚合分组 + LLM 写摘要和 AI 评论，返回 Sheet1 行数据列表。"""
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for c in all_comments:
        key = (str(c.get("note_category") or "其他"), str(c.get("comment_type") or "其他"))
        groups[key].append(c)

    sorted_groups = sorted(
        groups.items(),
        key=lambda kv: sum(x["like_count"] for x in kv[1]),
        reverse=True,
    )

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
        if not ai_comments_raw:
            single = (parsed or {}).get("ai_comment") or ""
            ai_comments_raw = [single] if single else []
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


# ── v2 专用步骤 ───────────────────────────────────────────────────────────────

async def _step2v2_fetch_all_comments_for_note(
    note: Dict[str, Any],
    cookies_str: str,
) -> List[Dict[str, Any]]:
    """[v2] 步骤 2：分页采集单篇笔记评论，含页间睡眠和 CaptchaError 熔断。

    策略（参考 MediaCrawler）：
    - 手动分页调用 get_note_out_comment（单页），每页后随机 sleep
    - 最多 _V2_MAX_PAGES_PER_NOTE 页 / _V2_MAX_PARENT_PER_NOTE 条一级评论
    - 子评论只使用每页返回时已附带的 sub_comments，不额外翻页
    - CaptchaError 向上层抛出，由 _run_v2_pipeline 做全局熔断

    Returns:
        扁平评论列表，每条含 is_sub_comment: bool 和 parent_comment_id: str。
    """
    note_id = str(note.get("note_id") or "").strip()
    if not note_id or note_id.startswith("stub_"):
        return []

    xsec = str(note.get("xsec_token") or "").strip()
    if not xsec:
        url = str(note.get("url") or "")
        m = re.search(r'xsec_token=([^&]+)', url)
        if m:
            xsec = m.group(1)

    if not xsec:
        logger.debug(f"[comment v2] note_id={note_id} 无 xsec_token，跳过")
        return []

    def _parse_comment(c: dict, *, is_sub: bool, parent_id: str) -> Optional[Dict]:
        if not isinstance(c, dict):
            return None
        content_raw = c.get("content") or {}
        text = str(
            content_raw.get("text") if isinstance(content_raw, dict) else content_raw or ""
        ).strip()
        if not text:
            return None
        return {
            "comment_id": str(c.get("id") or ""),
            "content": text,
            "like_count": int(c.get("like_count") or 0),
            "author": str((c.get("user_info") or {}).get("nickname") or ""),
            "note_id": note_id,
            "note_url": str(note.get("url") or ""),
            "note_title": str(note.get("title") or ""),
            "is_sub_comment": is_sub,
            "parent_comment_id": parent_id,
        }

    from apis.xhs_pc_apis import XHS_Apis, CaptchaError as _CaptchaError  # noqa: PLC0415
    client = XHS_Apis()

    result: List[Dict] = []
    cursor = ""
    parent_count = 0

    for page_idx in range(_V2_MAX_PAGES_PER_NOTE):
        # 调用单页接口（同步 → to_thread 转异步）
        try:
            success, msg, res_json = await asyncio.to_thread(
                client.get_note_out_comment, note_id, cursor, xsec, cookies_str
            )
        except _CaptchaError:
            # CaptchaError 向上冒泡，交由 _run_v2_pipeline 做全局熔断
            logger.warning(f"[comment v2] CAPTCHA note_id={note_id} page={page_idx+1}，向上抛出熔断")
            raise
        except Exception as exc:
            logger.warning(f"[comment v2] note_id={note_id} page={page_idx+1} 请求异常: {exc}")
            break

        if not success or not isinstance(res_json, dict):
            logger.debug(f"[comment v2] note_id={note_id} 接口失败: {msg}")
            break

        data = res_json.get("data") or {}
        comments_raw = data.get("comments") or []
        has_more = bool(data.get("has_more", False))
        cursor = str(data.get("cursor", ""))

        # 从第一页第一条提取笔记时间（如果还没有）
        if page_idx == 0 and comments_raw and not note.get("publish_time"):
            first = comments_raw[0] if isinstance(comments_raw[0], dict) else {}
            note_time_raw = (
                (first.get("note_info") or {}).get("time")
                or (first.get("note") or {}).get("time")
            )
            if note_time_raw:
                try:
                    import datetime as _dt  # noqa: PLC0415
                    ts = int(note_time_raw) // 1000 if int(note_time_raw) > 1e10 else int(note_time_raw)
                    note["publish_time"] = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                except Exception:
                    pass

        # 解析本页评论
        for parent in comments_raw:
            if parent_count >= _V2_MAX_PARENT_PER_NOTE:
                break
            if not isinstance(parent, dict):
                continue
            parent_id = str(parent.get("id") or "")
            p_parsed = _parse_comment(parent, is_sub=False, parent_id="")
            if p_parsed:
                result.append(p_parsed)
                parent_count += 1

            # 只使用页面已附带的 sub_comments，不额外翻页（子评论翻页接口 /comment/sub/page 风控极敏感）
            for sub in (parent.get("sub_comments") or []):
                s_parsed = _parse_comment(sub, is_sub=True, parent_id=parent_id)
                if s_parsed:
                    result.append(s_parsed)

        logger.debug(
            f"[comment v2] note_id={note_id} page={page_idx+1} "
            f"本页={len(comments_raw)} 累计一级={parent_count} has_more={has_more}"
        )

        # 已达每笔记上限 或 没有更多页
        if parent_count >= _V2_MAX_PARENT_PER_NOTE or not has_more:
            break

        # 页间随机 sleep（打破等间隔 bot 特征）
        sleep_sec = random.uniform(_V2_PAGE_SLEEP_MIN, _V2_PAGE_SLEEP_MAX)
        logger.debug(f"[comment v2] 页间 sleep {sleep_sec:.1f}s ...")
        await asyncio.sleep(sleep_sec)

    return result


async def _step3_dim1_classify(
    all_comments: List[Dict[str, Any]],
    keywords: List[str],
) -> Dict[str, Any]:
    """[v2] Step 3：维度1 — 批量分类评论（产品舆情维度+情感）+ 核心发现。

    Returns:
        {
            "categories": [{"name", "count", "ratio", "sentiment_breakdown"}],
            "core_finding": str,
            "comment_cats": [{"index", "category", "sentiment"}, ...],
        }
    """
    if not all_comments:
        return {"categories": [], "core_finding": "", "comment_cats": []}

    logger.info(
        f"[v2 Dim1] 开始分类，评论总量={len(all_comments)} 条，关键词={keywords}"
    )

    # batch_size=20：每批约 20×22≈440 tokens 输出，给思考型模型（deepseek-v4-flash 等）
    # 留出足够的思考链预算，避免 JSON 被截断。
    batch_size = 20
    comment_cats: List[Dict] = [{}] * len(all_comments)
    kw_hint = f"（产品：{'、'.join(keywords)}）" if keywords else ""

    for start in range(0, len(all_comments), batch_size):
        batch = all_comments[start:start + batch_size]
        lines = [f"{i}. {c.get('content', '')[:100]}" for i, c in enumerate(batch)]
        user_msg = (
            f"以下是 {len(batch)} 条小红书评论{kw_hint}（index 从 0 开始），"
            f"请对全部 {len(batch)} 条进行产品舆情维度分类：\n"
            + "\n".join(lines)
        )
        raw = await _llm_chat(
            "CommentPipeline.Dim1Classifier",
            _DIM1_CLASSIFY_SYSTEM,
            user_msg,
            max_tokens=2500,
            json_mode=True,
        )
        parsed = _llm_parse_json_robust(raw) or {}
        for item in (parsed.get("results") or []):
            idx = item.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(batch)):
                continue
            comment_cats[start + idx] = {
                "index": start + idx,
                "category": (item.get("category") or "其他").strip(),
                "sentiment": (item.get("sentiment") or "中性").strip(),
            }

    # 填充未分类条目
    for i, cat in enumerate(comment_cats):
        if not cat:
            comment_cats[i] = {"index": i, "category": "其他", "sentiment": "中性"}

    # 聚合统计
    cat_counts = Counter(c["category"] for c in comment_cats)
    total = len(comment_cats) or 1

    def _sentiment_breakdown(name: str) -> Dict[str, int]:
        return {
            s: sum(1 for c in comment_cats if c["category"] == name and c.get("sentiment") == s)
            for s in ("正面", "中性", "负面")
        }

    categories = [
        {
            "name": name,
            "count": count,
            "ratio": f"{count / total * 100:.1f}%",
            "sentiment_breakdown": _sentiment_breakdown(name),
        }
        for name, count in cat_counts.most_common()
        if name != "其他"
    ]
    if "其他" in cat_counts:
        categories.append({
            "name": "其他",
            "count": cat_counts["其他"],
            "ratio": f"{cat_counts['其他'] / total * 100:.1f}%",
            "sentiment_breakdown": _sentiment_breakdown("其他"),
        })

    # 诊断日志：显示各类别分布，方便排查
    cat_summary = ", ".join(f"{c['name']}({c['count']})" for c in categories[:8])
    other_count = cat_counts.get("其他", 0)
    logger.info(
        f"[v2 Dim1] 分类完成：有效类别={len([c for c in categories if c['name']!='其他'])} 个，"
        f"其他={other_count} 条，分布: {cat_summary}"
    )

    # 核心发现：取 top 5 有效类别生成摘要
    top_cats = [c for c in categories if c["name"] != "其他"][:5]
    summary_lines = [
        f"- {c['name']}：{c['count']}条（{c['ratio']}）"
        f" 正面{c['sentiment_breakdown']['正面']}/中性{c['sentiment_breakdown']['中性']}/负面{c['sentiment_breakdown']['负面']}"
        for c in top_cats
    ]
    sample_parts = []
    for cat_info in top_cats[:3]:
        samples = [
            all_comments[i].get("content", "")[:60]
            for i, cc in enumerate(comment_cats)
            if cc.get("category") == cat_info["name"]
        ][:3]
        if samples:
            sample_parts.append(f"【{cat_info['name']}典型评论】" + " | ".join(samples))

    finding_user = (
        f"产品：{'、'.join(keywords)}\n"
        f"评论总量：{len(all_comments)} 条\n\n"
        f"维度分布：\n" + "\n".join(summary_lines)
        + ("\n\n" + "\n".join(sample_parts) if sample_parts else "")
    )
    # DeepSeek 推理模型的 max_tokens 同时包含思考链 + 正文，给 800 确保
    # 思考完后仍有足够配额写出核心发现。
    core_finding = await _llm_chat(
        "CommentPipeline.Dim1Finding",
        _DIM1_FINDING_SYSTEM,
        finding_user,
        max_tokens=800,
    )

    return {
        "categories": categories,
        "core_finding": core_finding,
        "comment_cats": comment_cats,
    }


async def _step4_dim2_analysis(
    all_comments: List[Dict[str, Any]],
    dim1_result: Dict[str, Any],
    notes: List[Dict[str, Any]],
    keywords: List[str],
) -> Dict[str, Dict]:
    """[v2] Step 4：维度2 — 每个舆情类别的深度分析（正/中/负 + 理论依据 + 示例）。

    Returns:
        Dict keyed by category name:
        {"category", "count", "ratio", "sentiment_breakdown",
         "positive"/"neutral"/"negative": {"theory", "description", "examples"}}
    """
    comment_cats = dim1_result.get("comment_cats") or []
    categories = dim1_result.get("categories") or []

    top_valid = [c["name"] for c in categories if c["name"] != "其他"]
    logger.info(
        f"[v2 Dim2] 收到 {len(all_comments)} 条评论，"
        f"有效类别 {len(top_valid)} 个: {top_valid[:8]}"
    )

    cat_by_idx: Dict[int, Dict] = {c["index"]: c for c in comment_cats}
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for i, comment in enumerate(all_comments):
        cc = cat_by_idx.get(i, {})
        cat = cc.get("category", "其他")
        groups[cat].append({**comment, "_sentiment": cc.get("sentiment", "中性")})

    note_titles = "、".join(n.get("title", "") for n in notes[:5] if n.get("title"))
    kw_str = "、".join(keywords)

    results: Dict[str, Dict] = {}

    async def _analyze_one(cat_info: Dict) -> Tuple[str, Dict]:
        cat_name = cat_info["name"]
        cat_comments = groups.get(cat_name, [])

        if not cat_comments:
            empty_slot = {"theory": "", "description": "该类别下暂无评论样本", "examples": []}
            return cat_name, {
                "category": cat_name,
                "count": 0,
                "ratio": "0%",
                "sentiment_breakdown": {"正面": 0, "中性": 0, "负面": 0},
                "positive": empty_slot,
                "neutral": empty_slot,
                "negative": empty_slot,
            }

        sent_counts: Dict[str, int] = {"正面": 0, "中性": 0, "负面": 0}
        by_sent: Dict[str, List[Dict]] = {"正面": [], "中性": [], "负面": []}
        for c in cat_comments:
            s = c.get("_sentiment", "中性")
            if s in sent_counts:
                sent_counts[s] += 1
                by_sent[s].append(c)

        def top_n(lst: List[Dict], n: int = 3) -> List[str]:
            # 每个情感桶最多取 3 条点赞最高的，截 60 字，控制 user_msg 长度
            return [
                c.get("content", "")[:60]
                for c in sorted(lst, key=lambda x: x.get("like_count", 0), reverse=True)[:n]
            ]

        user_msg = (
            f"产品：{kw_str}\n"
            f"相关笔记背景：{note_titles}\n\n"
            f"当前分析维度：{cat_name}\n"
            f"该维度评论总量：{len(cat_comments)} 条\n"
            f"正面 {sent_counts['正面']} 条 | 中性 {sent_counts['中性']} 条 | 负面 {sent_counts['负面']} 条\n"
        )
        for label, key in [("正面", "正面"), ("中性", "中性"), ("负面", "负面")]:
            samples = top_n(by_sent[key])
            if samples:
                user_msg += f"\n【{label}评论样本】\n" + "\n".join(f"- {s}" for s in samples) + "\n"

        raw = await _llm_chat(
            "CommentPipeline.Dim2Analysis",
            _DIM2_ANALYSIS_SYSTEM,
            user_msg,
            max_tokens=1600,
            json_mode=True,
        )
        parsed = _llm_parse_json(raw) or {}

        def _safe_slot(key: str) -> Dict:
            val = parsed.get(key) or {}
            return {
                "theory": str(val.get("theory") or ""),
                "description": str(val.get("description") or ""),
                "examples": [str(e) for e in (val.get("examples") or []) if str(e).strip()][:3],
            }

        return cat_name, {
            "category": cat_name,
            "count": len(cat_comments),
            "ratio": cat_info.get("ratio", ""),
            "sentiment_breakdown": sent_counts,
            "positive": _safe_slot("positive"),
            "neutral": _safe_slot("neutral"),
            "negative": _safe_slot("negative"),
        }

    # 只分析非"其他"的 top 8 类别
    top_cats = [c for c in categories if c["name"] != "其他"][:8]
    if not top_cats:
        # Dim1 全部归入"其他"（通常因模型分类失败），创建兜底虚拟类别保证后续分析不为空
        logger.warning(
            "[v2 Dim2] 有效类别为空，Dim1 分类可能失败，将所有评论作为'综合评价'类别继续分析"
        )
        top_cats = [
            {
                "name": "综合评价",
                "count": len(all_comments),
                "ratio": "100.0%",
                "sentiment_breakdown": {
                    "正面": sum(1 for c in comment_cats if c.get("sentiment") == "正面"),
                    "中性": sum(1 for c in comment_cats if c.get("sentiment") == "中性"),
                    "负面": sum(1 for c in comment_cats if c.get("sentiment") == "负面"),
                },
            }
        ]

    analysis_results = await asyncio.gather(*[_analyze_one(c) for c in top_cats])
    for cat_name, analysis in analysis_results:
        results[cat_name] = analysis

    return results


async def _step5_dim3_summary(
    keywords: List[str],
    all_comments: List[Dict[str, Any]],
    dim1_result: Dict[str, Any],
) -> List[str]:
    """[v2] Step 5：维度3 — 从实际评论中提炼内容创作行动洞察。

    直接向大模型提供原始评论样本（随机抽取 + 高赞优先），让其从整体视角
    提炼创作建议、受众特征、互动规律等行动导向的洞察，而非对 Dim1/Dim2
    进行二次归纳（那样会产生脱离评论内容的空洞结论）。
    """
    if not all_comments:
        return ["暂无足够评论数据生成洞察"]

    # 样本构建：高赞评论优先 + 随机补充，总量上限 120 条，单条截 80 字
    sorted_by_like = sorted(all_comments, key=lambda c: c.get("like_count", 0), reverse=True)
    top_liked = sorted_by_like[:60]
    rest = sorted_by_like[60:]
    random.shuffle(rest)
    sample = top_liked + rest[:60]  # 最多 120 条

    comment_lines = [
        f"{i+1}. {c.get('content', '')[:80]}"
        for i, c in enumerate(sample)
    ]

    core_finding = dim1_result.get("core_finding") or ""
    cat_summary = "、".join(
        f"{c['name']}({c['count']}条)"
        for c in (dim1_result.get("categories") or [])
        if c["name"] != "其他"
    )[:200]

    user_msg = (
        f"产品/关键词：{'、'.join(keywords)}\n"
        + (f"舆情维度分布：{cat_summary}\n" if cat_summary else "")
        + (f"核心发现：{core_finding}\n" if core_finding else "")
        + f"\n以下是 {len(sample)} 条真实用户评论样本：\n"
        + "\n".join(comment_lines)
    )

    raw = await _llm_chat(
        "CommentPipeline.Dim3Summary",
        _DIM3_SUMMARY_SYSTEM,
        user_msg,
        max_tokens=800,
        json_mode=True,
    )
    parsed = _llm_parse_json(raw) or {}
    insights = [str(i).strip() for i in (parsed.get("insights") or []) if str(i).strip()]
    return insights or ["评论分析完成，详细维度分析请参见报告"]


def _render_md(comment_output: Dict[str, Any]) -> str:
    """[v2] Step 6：生成三维度 Markdown 舆情报告。"""
    keywords = comment_output.get("keywords") or []
    total_notes = comment_output.get("total_notes", 0)
    total_comments = comment_output.get("total_comments", 0)
    generated_at = (comment_output.get("generated_at") or "")[:10]

    dim1: Dict = comment_output.get("dim1") or {}
    dim2: Dict = comment_output.get("dim2") or {}
    dim3: List = comment_output.get("dim3") or []

    kw_str = "、".join(keywords)
    lines: List[str] = []

    lines += [
        f"# {kw_str} 评论舆情分析报告",
        "",
        f"> 分析笔记：**{total_notes}** 条 | 评论总量：**{total_comments}** 条 | 生成时间：{generated_at}",
        "",
    ]

    # Section 1: Dim1
    lines += ["## 1. 舆情分类统计", ""]
    categories = dim1.get("categories") or []
    if categories:
        lines += [
            "| 产品舆情维度 | 评论数 | 占比 | 正面 | 中性 | 负面 |",
            "|------------|--------|------|------|------|------|",
        ]
        for cat in categories:
            sb = cat.get("sentiment_breakdown") or {}
            lines.append(
                f"| {cat['name']} | {cat['count']} | {cat['ratio']} "
                f"| {sb.get('正面', 0)} | {sb.get('中性', 0)} | {sb.get('负面', 0)} |"
            )
        lines.append("")

    core_finding = dim1.get("core_finding") or ""
    if core_finding:
        lines += ["**核心发现**", "", core_finding, ""]

    # Section 2: Dim2
    lines += ["---", "", "## 2. 舆情分类分析", ""]
    for cat_name, analysis in dim2.items():
        ratio = analysis.get("ratio", "")
        count = analysis.get("count", 0)
        lines += [f"### {cat_name}（{ratio}，共 {count} 条评论）", ""]

        for sent_key, label in [
            ("positive", "正面评价"),
            ("neutral", "中性/模糊评价"),
            ("negative", "负面评价"),
        ]:
            data = analysis.get(sent_key) or {}
            sb = analysis.get("sentiment_breakdown") or {}
            sent_map = {"positive": "正面", "neutral": "中性", "negative": "负面"}
            sent_count = sb.get(sent_map[sent_key], 0)

            lines += [f"#### {label}（{sent_count} 条）", ""]
            theory = data.get("theory") or ""
            desc = data.get("description") or ""
            examples = data.get("examples") or []

            if theory:
                lines += [f"**理论依据**：{theory}", ""]
            if desc:
                lines += [desc, ""]
            for ex in examples:
                lines.append(f"> {ex}")
            if examples:
                lines.append("")

    # Section 3: Dim3
    lines += ["---", "", "## 3. 舆情分析总结", ""]
    for i, insight in enumerate(dim3, 1):
        lines.append(f"{i}. {insight}")
    lines.append("")

    return "\n".join(lines)


# ── v1 / v2 流水线封装 ────────────────────────────────────────────────────────

async def _run_v1_pipeline(
    task_id: str,
    keywords: List[str],
    cookies_str: str,
    *,
    top_notes: int,
    top_comments_per_note: int,
    time_range: int,
    min_interaction: int,
    raw_input: str,
    owner_user_id: str,
    start_ts: datetime,
) -> Dict[str, Any]:
    """[v1] 旧版流水线：首屏 Top K 评论 + 笔记类别×评论类型矩阵分析。"""
    notes_with_comments: List[Tuple[Dict, List]] = []
    total_comment_count = 0

    cached = await comment_cache_store.get(keywords, time_range, min_interaction)
    if cached:
        cached_notes = cached["notes"]
        notes_with_comments = [(n, n.pop("_cached_comments", [])) for n in cached_notes]
        total_comment_count = sum(len(c) for _, c in notes_with_comments)
        logger.info(
            f"[v1] task={task_id} 缓存命中 notes={len(notes_with_comments)} "
            f"comments={total_comment_count} age={cached.get('cache_age_hours')}h"
        )
        await _emit_progress(task_id, f"命中缓存，已有 {len(notes_with_comments)} 条笔记数据，开始 AI 分析...", 40)
    else:
        crawl_target = max(top_notes * 3, _CRAWL_TARGET_PER_KW) if top_notes > 0 else _CRAWL_TARGET_PER_KW
        notes_raw = await _step1_crawl_notes(
            keywords, cookies_str, crawl_target,
            time_range=time_range, min_interaction=min_interaction,
            user_query=raw_input, owner_user_id=owner_user_id,
        )
        top_notes_list = notes_raw[:top_notes] if top_notes > 0 else notes_raw
        if not top_notes_list:
            raise ValueError(f"关键词「{'、'.join(keywords)}」未采集到任何笔记，请检查 Cookie 或关键词")

        await _emit_progress(task_id, f"已采集 {len(top_notes_list)} 条笔记，开始拉取评论...", 25)

        semaphore = asyncio.Semaphore(_V1_MAX_CONCURRENT)

        async def _fetch_v1(note: Dict) -> Tuple[Dict, List]:
            async with semaphore:
                comments = await _step2_fetch_comments_for_note(note, cookies_str, top_comments_per_note)
            await asyncio.sleep(_V1_INTER_NOTE_SLEEP)
            return note, comments

        fetch_results = await asyncio.gather(*[_fetch_v1(n) for n in top_notes_list])
        notes_with_comments = [(n, c) for n, c in fetch_results if c]
        total_comment_count = sum(len(c) for _, c in notes_with_comments)

        if notes_with_comments:
            cache_notes = [{**n, "_cached_comments": c} for n, c in notes_with_comments]
            asyncio.create_task(comment_cache_store.set(
                keywords=keywords, time_range=time_range, min_interaction=min_interaction,
                notes=cache_notes, comment_count=total_comment_count,
            ))

    await _emit_progress(task_id, f"已获取 {total_comment_count} 条评论，开始 AI 分析...", 45)

    await _emit_progress(task_id, "AI 分类笔记类型...", 50)
    note_categories = await _step4_classify_notes([n for n, _ in notes_with_comments])

    await _emit_progress(task_id, "AI 分类评论类型...", 60)
    all_comments_flat: List[Dict] = []
    for (note, comments), cat in zip(notes_with_comments, note_categories):
        for c in comments:
            c["note_category"] = cat
        all_comments_flat.extend(comments)

    comment_types = await _step5_classify_comments(all_comments_flat, keywords=keywords)
    for i, ctype in enumerate(comment_types):
        if i < len(all_comments_flat):
            all_comments_flat[i]["comment_type"] = ctype

    await _emit_progress(task_id, "AI 总结评论话题...", 70)
    note_topic_tasks = []
    for (note, comments), cat in zip(notes_with_comments, note_categories):
        note["note_category"] = cat
        note_topic_tasks.append(_step6_summarize_note_topics(note, comments))
    note_topics = await asyncio.gather(*note_topic_tasks)

    await _emit_progress(task_id, "AI 聚合分析，生成评论洞察报告...", 80)
    total_likes = sum(c["like_count"] for c in all_comments_flat) or 1
    sheet1_rows = await _step7_aggregate_and_summarize(all_comments_flat, total_likes)

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

    elapsed = int((datetime.now(timezone.utc) - start_ts).total_seconds())
    return {
        "version": "v1",
        "keywords": keywords,
        "total_notes": len(notes_with_comments),
        "total_comments": total_comment_count,
        "elapsed_seconds": elapsed,
        "sheet1_analysis": sheet1_rows,
        "sheet2_comments": all_comments_flat,
        "sheet3_notes": sheet3_rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _run_v2_pipeline(
    task_id: str,
    keywords: List[str],
    cookies_str: str,
    *,
    top_notes: int,
    time_range: int,
    min_interaction: int,
    raw_input: str,
    owner_user_id: str,
    start_ts: datetime,
) -> Dict[str, Any]:
    """[v2] 新版三维度舆情分析流水线。"""
    notes_with_comments: List[Tuple[Dict, List]] = []
    total_parent_comments = 0
    total_sub_comments = 0

    # 先查 v2 缓存（cache_store 已在 key 中包含版本号）
    cached = await comment_cache_store.get(keywords, time_range, min_interaction)
    if cached:
        cached_notes = cached["notes"]
        notes_with_comments = [(n, n.pop("_cached_comments", [])) for n in cached_notes]
        total_parent_comments = cached.get("comment_count", 0)
        logger.info(
            f"[v2] task={task_id} 缓存命中 notes={len(notes_with_comments)} "
            f"comments={total_parent_comments} age={cached.get('cache_age_hours')}h"
        )
        await _emit_progress(
            task_id,
            f"命中缓存（{cached.get('cache_age_hours', 0)} 小时前），"
            f"已有 {len(notes_with_comments)} 条笔记数据，开始 AI 分析...",
            40,
        )
    else:
        crawl_target = max(top_notes * 3, _CRAWL_TARGET_PER_KW) if top_notes > 0 else _CRAWL_TARGET_PER_KW
        await _emit_progress(task_id, f"正在采集关键词「{'、'.join(keywords)}」的笔记...", 10)

        notes_raw = await _step1_crawl_notes(
            keywords, cookies_str, crawl_target,
            time_range=time_range, min_interaction=min_interaction,
            user_query=raw_input, owner_user_id=owner_user_id,
        )
        top_notes_list = notes_raw[:top_notes] if top_notes > 0 else notes_raw
        if not top_notes_list:
            raise ValueError(f"关键词「{'、'.join(keywords)}」未采集到任何笔记，请检查 Cookie 或关键词")

        await _emit_progress(
            task_id,
            f"已采集 {len(top_notes_list)} 条笔记，开始全量拉取评论（含子评论）...",
            25,
        )
        logger.info(
            f"[v2] task={task_id} 采集到 {len(top_notes_list)} 条笔记，开始串行评论拉取 "
            f"(上限: 每笔记 {_V2_MAX_PAGES_PER_NOTE} 页/{_V2_MAX_PARENT_PER_NOTE} 条，"
            f"全局预算 {_V2_TOTAL_PARENT_BUDGET} 条)"
        )

        from apis.xhs_pc_apis import CaptchaError as _CaptchaError  # noqa: PLC0415
        budget_exhausted = False

        for note_idx, note in enumerate(top_notes_list):
            if budget_exhausted:
                break
            if total_parent_comments >= _V2_TOTAL_PARENT_BUDGET:
                logger.info(
                    f"[v2] task={task_id} 已达总评论预算 {_V2_TOTAL_PARENT_BUDGET}，"
                    f"停止继续采集（已处理 {note_idx}/{len(top_notes_list)} 篇笔记）"
                )
                budget_exhausted = True
                break

            try:
                comments = await _step2v2_fetch_all_comments_for_note(note, cookies_str)
            except _CaptchaError:
                logger.warning(
                    f"[v2] task={task_id} CAPTCHA 熔断！全局暂停 {_V2_CAPTCHA_PAUSE}s，"
                    f"跳过剩余笔记"
                )
                await asyncio.sleep(_V2_CAPTCHA_PAUSE)
                # 保留已采集到的部分，停止继续采集
                budget_exhausted = True
                break
            except Exception as exc:
                logger.warning(f"[v2] task={task_id} note_id={note.get('note_id')} 评论采集异常: {exc}")
                comments = []

            notes_with_comments.append((note, comments))
            note_parent = sum(1 for c in comments if not c.get("is_sub_comment"))
            note_sub = sum(1 for c in comments if c.get("is_sub_comment"))
            total_parent_comments += note_parent
            total_sub_comments += note_sub

            logger.info(
                f"[v2] task={task_id} [{note_idx+1}/{len(top_notes_list)}] "
                f"note_id={note.get('note_id')} "
                f"一级={note_parent} 子={note_sub} "
                f"累计一级={total_parent_comments}/{_V2_TOTAL_PARENT_BUDGET}"
            )

            # 笔记间随机 sleep（最后一篇不需要等待）
            if note_idx < len(top_notes_list) - 1 and not budget_exhausted:
                sleep_sec = random.uniform(_V2_INTER_NOTE_SLEEP_MIN, _V2_INTER_NOTE_SLEEP_MAX)
                logger.debug(f"[v2] 笔记间 sleep {sleep_sec:.1f}s ...")
                await asyncio.sleep(sleep_sec)

        # 写入 v2 缓存（后台）
        if notes_with_comments:
            total_all = total_parent_comments + total_sub_comments
            cache_notes = [{**n, "_cached_comments": c} for n, c in notes_with_comments]
            asyncio.create_task(comment_cache_store.set(
                keywords=keywords,
                time_range=time_range,
                min_interaction=min_interaction,
                notes=cache_notes,
                comment_count=total_all,
            ))

    total_comments = total_parent_comments + total_sub_comments
    all_comments_flat: List[Dict] = [c for _, comments in notes_with_comments for c in comments]

    await _emit_progress(
        task_id,
        f"已获取 {total_comments} 条评论（一级 {total_parent_comments} / 子评论 {total_sub_comments}），开始维度分析...",
        45,
    )

    # Step 3: Dim1
    await _emit_progress(task_id, "维度1：产品舆情分类中...", 52)
    dim1_result = await _step3_dim1_classify(all_comments_flat, keywords)
    logger.info(
        f"[v2] task={task_id} 维度1完成，识别 {len(dim1_result['categories'])} 个舆情类别"
    )

    # Step 4: Dim2
    await _emit_progress(task_id, "维度2：深度分析各舆情类别...", 65)
    dim2_result = await _step4_dim2_analysis(
        all_comments_flat, dim1_result, [n for n, _ in notes_with_comments], keywords
    )
    logger.info(f"[v2] task={task_id} 维度2完成，分析了 {len(dim2_result)} 个类别")

    # Step 5: Dim3
    await _emit_progress(task_id, "维度3：生成总体洞察...", 80)
    dim3_insights = await _step5_dim3_summary(keywords, all_comments_flat, dim1_result)

    # Step 6: MD
    await _emit_progress(task_id, "生成舆情分析 Markdown 报告...", 88)

    # 组装 Excel Sheet 数据
    sheet1_comments = []
    for comment in all_comments_flat:
        sheet1_comments.append({
            "content": comment.get("content") or "",
            "like_count": comment.get("like_count") or 0,
            "author": comment.get("author") or "",
            "is_sub_comment": comment.get("is_sub_comment", False),
            "note_title": comment.get("note_title") or "",
            "note_url": comment.get("note_url") or "",
        })

    sheet2_notes = []
    for note, comments in notes_with_comments:
        sheet2_notes.append({
            "url": note.get("url") or note.get("note_id") or "",
            "publish_time": note.get("publish_time") or "",
            "title": note.get("title") or "",
            "interaction_score": note.get("interaction_score") or 0,
            "fetched_comment_count": len(comments),
        })

    elapsed = int((datetime.now(timezone.utc) - start_ts).total_seconds())
    comment_output: Dict[str, Any] = {
        "version": "v2",
        "keywords": keywords,
        "total_notes": len(notes_with_comments),
        "total_comments": total_comments,
        "total_parent_comments": total_parent_comments,
        "total_sub_comments": total_sub_comments,
        "elapsed_seconds": elapsed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dim1": {
            "categories": dim1_result["categories"],
            "core_finding": dim1_result["core_finding"],
        },
        "dim2": dim2_result,
        "dim3": dim3_insights,
        "sheet1_comments": sheet1_comments,
        "sheet2_notes": sheet2_notes,
    }

    # 渲染 MD（存入 comment_output）
    comment_output["md_content"] = _render_md(comment_output)

    return comment_output


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
    """评论分析主流水线入口，由 tool_executor 通过 asyncio.create_task 调起。"""
    start_ts = datetime.now(timezone.utc)
    logger.info(
        f"[comment_pipeline] 启动 task={task_id} version={_ANALYSIS_VERSION} keywords={keywords}"
    )

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

        # Step 0：解析输入
        await _emit_progress(task_id, "解析搜索意图...", 5)
        parsed = await _step0_parse_input(
            raw_input=raw_input or " ".join(keywords),
            hint_keywords=keywords,
        )
        keywords = parsed["keywords"]
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

        # 根据版本分发
        if _ANALYSIS_VERSION == "v2":
            comment_output = await _run_v2_pipeline(
                task_id, keywords, cookies_str,
                top_notes=top_notes,
                time_range=time_range,
                min_interaction=min_interaction,
                raw_input=raw_input,
                owner_user_id=owner_user_id,
                start_ts=start_ts,
            )
        else:
            comment_output = await _run_v1_pipeline(
                task_id, keywords, cookies_str,
                top_notes=top_notes,
                top_comments_per_note=top_comments_per_note,
                time_range=time_range,
                min_interaction=min_interaction,
                raw_input=raw_input,
                owner_user_id=owner_user_id,
                start_ts=start_ts,
            )

        # Step N：写入 TaskContext
        ctx = task_context_store.require(task_id)
        from ..domain.task_context import TaskContextWriter
        writer = TaskContextWriter(ctx)
        writer.write("comment_output", comment_output, agent_id="CommentPipeline")

        elapsed = comment_output.get("elapsed_seconds", 0)
        total_notes_count = comment_output.get("total_notes", 0)
        total_comment_count = comment_output.get("total_comments", 0)

        task_repository.update(
            task_id,
            status=TaskStatus.COMPLETED.value,
            progress=100,
            collected_count=total_comment_count,
            duration_seconds=elapsed,
        )

        # 构建完成摘要文本
        export_url = f"/api/v1/tasks/{task_id}/export/comment_excel"
        if _ANALYSIS_VERSION == "v2":
            md_url = f"/api/v1/tasks/{task_id}/export/comment_md"
            dim3 = comment_output.get("dim3") or []
            findings = "\n".join(f"- {i}" for i in dim3[:3]) if dim3 else ""
            summary_text = (
                f"**评论舆情分析完成** ✓\n\n"
                f"关键词：{'、'.join(keywords)} | 笔记：{total_notes_count} 条 | "
                f"评论：{total_comment_count} 条（含子评论）| 耗时：{elapsed}s\n\n"
                + (f"**主要洞察：**\n{findings}\n\n" if findings else "")
                + f"[点击下载评论数据表 Excel]({export_url})  |  [点击下载舆情分析报告 MD]({md_url})"
            )
        else:
            sheet1_rows = comment_output.get("sheet1_analysis") or []
            top3 = sheet1_rows[:3]
            findings_v1 = "\n".join(
                f"- **{r['comment_type']}**（占比 {r['ratio']}）：{r['summary']}"
                for r in top3 if r.get("summary")
            )
            summary_text = (
                f"**评论分析完成** ✓\n\n"
                f"关键词：{'、'.join(keywords)} | 笔记：{total_notes_count} 条 | 评论：{total_comment_count} 条 | 耗时：{elapsed}s\n\n"
                + (f"**主要发现：**\n{findings_v1}\n\n" if findings_v1 else "")
                + f"[点击下载评论分析报告 Excel]({export_url})"
            )

        await _emit(task_id, TaskEventType.CONVERSATION_MESSAGE, {
            "role": "assistant",
            "content": summary_text,
            "intent": "comment_analysis",
            "linked_task_id": task_id,
        })

        _append_completion_message(task_id, summary_text)

        done_payload: Dict[str, Any] = {
            "task_type": "comment_analysis",
            "export_url": export_url,
            "total_notes": total_notes_count,
            "total_comments": total_comment_count,
        }
        if _ANALYSIS_VERSION == "v2":
            done_payload["md_export_url"] = f"/api/v1/tasks/{task_id}/export/comment_md"

        await _emit(task_id, TaskEventType.DONE, done_payload)

        logger.info(
            f"[comment_pipeline] 完成 task={task_id} version={_ANALYSIS_VERSION} "
            f"notes={total_notes_count} comments={total_comment_count} elapsed={elapsed}s"
        )

    except asyncio.CancelledError:
        logger.info(f"[comment_pipeline] task={task_id} 已被取消，pipeline 中止")
        raise

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
