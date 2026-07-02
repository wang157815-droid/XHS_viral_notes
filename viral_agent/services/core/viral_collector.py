"""
爆款笔记采集服务
负责搜索、筛选和收集爆款笔记

支持并行多维度爬取（点赞/评论/收藏），去重后按比例筛选爆款
"""
import asyncio
import math
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Dict, Any, Optional, Callable, TYPE_CHECKING
from loguru import logger
import sys
import os

# 类型检查时导入，避免循环依赖
if TYPE_CHECKING:
    from viral_agent.task import TaskControlSignal, TaskCheckpoint


class SortType(IntEnum):
    """排序方式枚举"""
    GENERAL = 0      # 综合
    TIME = 1         # 最新
    LIKES = 2        # 最多点赞
    COMMENTS = 3     # 最多评论
    COLLECTS = 4     # 最多收藏


@dataclass
class DimensionResult:
    """单维度爬取结果"""
    sort_type: SortType
    dimension_name: str
    notes: List[Dict[str, Any]]
    total_fetched: int
    success: bool
    error_message: str = ""

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import random
import re
import requests
from apis.xhs_pc_apis import XHS_Apis, CaptchaError, SoftBlockError
from viral_agent.models.viral_note import ViralNote
from viral_agent.utils import parse_chinese_number
from xhs_utils.cookie_util import trans_cookies
from xhs_utils.data_util import handle_note_info
from xhs_utils.xhs_util import xhs_web_origin, BROWSER_UA

# ── 采集节奏参数（可通过环境变量覆盖）─────────────────────────────────────────
_COLLECTOR_PAGE_SLEEP_MIN = float(os.getenv("COLLECTOR_PAGE_SLEEP_MIN", "1.5"))
_COLLECTOR_PAGE_SLEEP_MAX = float(os.getenv("COLLECTOR_PAGE_SLEEP_MAX", "4.0"))
_COLLECTOR_DIM_SLEEP = float(os.getenv("COLLECTOR_DIM_SLEEP", "5.0"))
_COLLECTOR_NOTE_SLEEP_MIN = float(os.getenv("COLLECTOR_NOTE_SLEEP_MIN", "1.0"))
_COLLECTOR_NOTE_SLEEP_MAX = float(os.getenv("COLLECTOR_NOTE_SLEEP_MAX", "2.0"))
# 重试参数
_CAPTCHA_RETRY_WAITS = [
    float(x) for x in os.getenv("COLLECTOR_RETRY_WAITS", "5").split(",")
]
# 详情接口重试等待（只重试1次，避免长时间卡在单条笔记上）
_DETAIL_RETRY_WAITS = [
    float(x) for x in os.getenv("COLLECTOR_DETAIL_RETRY_WAITS", "5").split(",")
]
# 周期刷新 cookie 间隔（秒），0 表示不启用周期刷新
_COOKIE_REFRESH_INTERVAL = float(os.getenv("COLLECTOR_COOKIE_REFRESH_INTERVAL", "360"))  # 6 分钟
# 并发信号量
_SEMAPHORE_SIZE = int(os.getenv("COLLECTOR_SEMAPHORE", "2"))


def _cdp_normalize_note(note: dict) -> None:
    """将 CDP __INITIAL_STATE__ 返回的 camelCase 字段补全为 snake_case。

    只补充下游代码会用到的关键字段，不做全量转换。原有 camelCase 字段保留，
    避免破坏其他路径。
    """
    _map = {
        "noteId":        "note_id",
        "title":         "title",         # 同名，无需映射但列出便于维护
        "desc":          "desc",
        "type":          "type",
        "userId":        "user_id",
        "nickname":      "nickname",
        "likedCount":    "liked_count",
        "commentCount":  "comment_count",
        "collectCount":  "collect_count",
        "shareCount":    "share_count",
        "videoUrl":      "video_url",
        "imageList":     "image_list",
        "tagList":       "tag_list",
        "noteUrl":       "note_url",
        "xsecToken":     "xsec_token",
    }
    for camel, snake in _map.items():
        if snake not in note and camel in note:
            note[snake] = note[camel]

    # interact_info 展开（XHS __INITIAL_STATE__ 把互动数据嵌套在 interactInfo 里）
    interact = note.get("interactInfo") or note.get("interact_info") or {}
    if interact and "liked_count" not in note:
        note["liked_count"]   = interact.get("likedCount") or interact.get("liked_count") or 0
        note["comment_count"] = interact.get("commentCount") or interact.get("comment_count") or 0
        note["collect_count"] = interact.get("collectedCount") or interact.get("collect_count") or 0
        note["share_count"]   = interact.get("shareCount") or interact.get("share_count") or 0


def _extract_initial_state_json(html: str) -> Optional[str]:
    """从 HTML 中提取 window.__INITIAL_STATE__ 的完整 JSON 字符串。

    使用栈计数器匹配嵌套括号，避免非贪婪 regex 在嵌套 JSON 时提前截断。
    参考：cloudy-sfu/MCP-rednote-assistant xhshow_contrib.py
    """
    marker = "window.__INITIAL_STATE__="
    idx = html.find(marker)
    if idx == -1:
        return None
    start = html.find("{", idx + len(marker))
    if start == -1:
        return None
    stack = []
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "{":
            stack.append(ch)
        elif ch == "}":
            stack.pop()
            if not stack:
                return html[start: i + 1]
    return None


class ViralNoteCollector:
    """爆款笔记采集器（支持暂停/恢复/取消）"""

    def __init__(self, cookies_str: str, owner_user_id: Optional[str] = None):
        """
        初始化采集器

        Args:
            cookies_str: Cookie字符串（初始值，长任务中会被 LiveCookieProvider 周期刷新）
            owner_user_id: RedMuse 用户 ID，用于 LiveCookieProvider 周期刷新
        """
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        self.client = XHS_Apis()          # HTTP 裸请求客户端（CDP 禁用时使用）
        self.collected_notes: List[ViralNote] = []
        self.search_keyword: str = ""

        # cookie 周期刷新
        self._owner_user_id: Optional[str] = owner_user_id
        self._last_cookie_refresh: float = time.time()

        # CDP 客户端（CDP_DETAIL_ENABLED=true 时替代 HTTP 客户端）
        from .playwright_detail_fetcher import cdp_detail_enabled
        if cdp_detail_enabled() and owner_user_id:
            from .xhs_cdp_client import XhsCdpClient
            self._cdp_client: Optional["XhsCdpClient"] = XhsCdpClient(owner_user_id)
            logger.info(f"[Collector] CDP 模式已启用 (user={owner_user_id!r})")
        else:
            self._cdp_client = None

        # 任务控制支持
        self._control_signal: Optional["TaskControlSignal"] = None
        self._checkpoint: Optional["TaskCheckpoint"] = None
        self._current_keyword_index: int = 0
        self._current_dimension_index: int = 0
        self._current_page_index: int = 1

        # 语义过滤（由外部注入，默认 None 表示不过滤）
        self.tier1_filter = None
        self.tier2_filter = None

        # 采集信号量（控制并发搜索维度数）
        self._semaphore = asyncio.Semaphore(_SEMAPHORE_SIZE)

    def set_control_signal(self, signal: "TaskControlSignal") -> None:
        """设置控制信号（用于暂停/取消控制）"""
        self._control_signal = signal

    def set_checkpoint(self, checkpoint: "TaskCheckpoint") -> None:
        """设置检查点（用于恢复采集）"""
        self._checkpoint = checkpoint

    def get_checkpoint(self) -> Optional["TaskCheckpoint"]:
        """
        获取当前检查点

        基于当前采集进度构建检查点，用于暂停时保存状态。
        使用独立跟踪的索引变量，确保首次暂停也能正确保存进度。
        """
        from viral_agent.task import TaskCheckpoint
        from datetime import datetime

        # 获取已采集的笔记 ID 列表
        collected_ids = [note.note_id for note in self.collected_notes]

        return TaskCheckpoint(
            keyword_index=self._current_keyword_index,
            dimension_index=self._current_dimension_index,
            page_index=self._current_page_index,  # 使用独立跟踪的页码
            collected_note_ids=collected_ids,
            timestamp=datetime.now().isoformat()
        )

    async def _maybe_refresh_cookie(self) -> None:
        """周期性从 LiveCookieProvider 取最新 cookie（仅当 LIVE_COOKIE_ENABLED=true）。

        每 _COOKIE_REFRESH_INTERVAL 秒刷新一次；失败时保留当前 cookie 继续运行。
        """
        if _COOKIE_REFRESH_INTERVAL <= 0 or not self._owner_user_id:
            return
        if time.time() - self._last_cookie_refresh < _COOKIE_REFRESH_INTERVAL:
            return

        try:
            from backend.app.services.xhs_auth.credential_resolver import get_credential_resolver
            resolved = get_credential_resolver().resolve(self._owner_user_id)
            if resolved.found and resolved.source == "live_browser":
                self.cookies_str = resolved.cookies_str
                self.cookies = trans_cookies(self.cookies_str)
                self._last_cookie_refresh = time.time()
                logger.info(
                    "[collector] cookie 周期刷新成功 source=live_browser cookie_len={}",
                    len(self.cookies_str),
                )
            else:
                logger.debug("[collector] cookie 周期刷新：LiveCookie 未启用，跳过")
        except Exception as exc:
            logger.warning("[collector] cookie 周期刷新失败，保持当前 cookie: {}", exc)

    async def _search_with_captcha_retry(
        self,
        query: str,
        page: int,
        sort: int,
        **kwargs,
    ):
        """带退避重试的 search_note 包装器。

        遇到 CaptchaError / SoftBlockError 时：
        1. 先尝试刷新 cookie（LiveCookieProvider）
        2. 指数退避等待（5s / 15s / 45s）
        3. 超过最大重试次数后 raise，由上层决策
        """
        loop = asyncio.get_event_loop()
        last_exc: Optional[Exception] = None

        for attempt, wait_sec in enumerate([0.0] + _CAPTCHA_RETRY_WAITS):
            if attempt > 0:
                logger.warning(
                    "[collector] search_note 风控重试 attempt={} wait={:.0f}s query={!r} page={}",
                    attempt, wait_sec, query, page,
                )
                await self._maybe_refresh_cookie()
                await asyncio.sleep(wait_sec)

            try:
                if self._cdp_client is not None:
                    # ── CDP 模式：真实 Chrome 发请求，浏览器指纹完整 ──
                    result = await self._cdp_client.search_note(
                        query=query,
                        page=page,
                        sort_type_choice=sort,
                        note_type=kwargs.get("note_type", 0),
                        note_time=kwargs.get("time_range", 0),
                    )
                    # 虚拟号/SMS 登录用户无 browser_data 目录，CDP context 不可用时自动降级 HTTP
                    if (
                        isinstance(result, tuple)
                        and len(result) == 3
                        and not result[0]
                        and result[1] == "CDP context unavailable"
                    ):
                        logger.warning(
                            "[collector] CDP context 不可用（虚拟号登录/无持久化浏览器配置文件），"
                            "本次任务自动降级为 HTTP 模式并禁用 CDP"
                        )
                        self._cdp_client = None
                        result = await loop.run_in_executor(
                            None,
                            lambda: self.client.search_note(
                                query=query,
                                cookies_str=self.cookies_str,
                                page=page,
                                sort_type_choice=sort,
                                note_type=kwargs.get("note_type", 0),
                                note_time=kwargs.get("time_range", 0),
                                note_range=0,
                                pos_distance=0,
                                geo="",
                            ),
                        )
                else:
                    # ── HTTP 模式（回退）──
                    result = await loop.run_in_executor(
                        None,
                        lambda: self.client.search_note(
                            query=query,
                            cookies_str=self.cookies_str,
                            page=page,
                            sort_type_choice=sort,
                            note_type=kwargs.get("note_type", 0),
                            note_time=kwargs.get("time_range", 0),
                            note_range=0,
                            pos_distance=0,
                            geo="",
                        ),
                    )
                return result
            except (CaptchaError, SoftBlockError) as exc:
                last_exc = exc
                logger.warning(
                    "[collector] {} attempt={} query={!r} page={}: {}",
                    type(exc).__name__, attempt, query, page, exc,
                )
                continue
            except Exception:
                raise  # 非风控异常直接上抛

        # 重试耗尽
        logger.error(
            "[collector] search_note 重试耗尽 query={!r} page={} last_exc={}",
            query, page, last_exc,
        )
        raise RuntimeError(
            f"CRAWLER_CAPTCHA: 重试 {len(_CAPTCHA_RETRY_WAITS)} 次后仍触发风控: {last_exc}"
        ) from last_exc

    async def _preflight_cookie_check(self) -> None:
        """采集前 Cookie 有效性预检。

        调用 get_user_self_info 接口，失败则直接抛出 RuntimeError，
        让 Agent 层将 AUTH_COOKIE_EXPIRED / CRAWLER_CAPTCHA 错误码传给前端。
        """
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.client.get_user_self_info(self.cookies_str)
            )
            success = result[0] if isinstance(result, tuple) else (result or {}).get("success")
            if not success:
                logger.warning("⚠️ Cookie 预检失败（get_user_self_info 返回 success=false）")
                raise RuntimeError("AUTH_COOKIE_EXPIRED: Cookie 已失效，请前往「设置→数据源授权」重新授权")
        except CaptchaError as e:
            # 预检阶段触发 CAPTCHA：先刷新 cookie 再重试一次
            logger.warning("[collector] Cookie 预检触发 CAPTCHA，尝试刷新 cookie 后重试: {}", e)
            await self._maybe_refresh_cookie()
            await asyncio.sleep(_CAPTCHA_RETRY_WAITS[0])
            try:
                result2 = await loop.run_in_executor(
                    None, lambda: self.client.get_user_self_info(self.cookies_str)
                )
                success2 = result2[0] if isinstance(result2, tuple) else (result2 or {}).get("success")
                if not success2:
                    raise RuntimeError("AUTH_COOKIE_EXPIRED: Cookie 预检刷新后仍失败")
            except CaptchaError as e2:
                raise RuntimeError(f"CRAWLER_CAPTCHA: 触发人机验证，请稍后重试或重新授权") from e2
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("[collector] Cookie 预检异常（跳过预检，继续采集）: {}", e)

    async def _check_pause_point(self) -> bool:
        """
        检查暂停点 - 在循环关键位置调用

        Returns:
            True: 可以继续执行
            False: 需要退出（收到取消信号）
        """
        if self._control_signal is None:
            return True
        return await self._control_signal.check_pause_point()

    def calculate_interaction_score(self, note_info: Dict[str, Any]) -> int:
        """
        计算笔记互动分数

        Args:
            note_info: 笔记信息字典

        Returns:
            互动总分数
        """
        # 处理笔记类型（兼容中文和英文）
        note_type = note_info.get('note_type', '') or note_info.get('type', '')
        # 将英文类型转换为中文（用于判断）
        if note_type == 'video':
            note_type = '视频'
        elif note_type == 'normal':
            note_type = '图集'

        # 优先从interact_info获取数据
        interact_info = note_info.get('interact_info', {})

        if interact_info:
            # interact_info中的数据可能是字符串或中文格式（如"2.6万"），需要转换
            liked = parse_chinese_number(interact_info.get('liked_count', '0'))
            collected = parse_chinese_number(interact_info.get('collected_count', '0'))
            comment = parse_chinese_number(interact_info.get('comment_count', '0'))
            # 注意是shared_count而不是share_count
            share = parse_chinese_number(interact_info.get('shared_count', '0'))
        else:
            # 如果没有interact_info，尝试从顶层获取
            liked = parse_chinese_number(note_info.get('liked_count', 0))
            collected = parse_chinese_number(note_info.get('collected_count', 0))
            comment = parse_chinese_number(note_info.get('comment_count', 0))
            share = parse_chinese_number(note_info.get('shared_count', 0))

        total_score = liked + collected + comment

        # 记录调试信息
        if total_score > 0:
            logger.debug(f"互动分数: 点赞{liked} 收藏{collected} 评论{comment} = {total_score}")

        if note_type == "视频" or note_type == "video":
            return total_score + share
        else:
            return total_score

    # [已废弃] 新版本使用并行多维度爬取 + 比例筛选，不再需要阈值判断
    # def is_viral_note(self, note_info: Dict[str, Any], threshold: int) -> bool:
    #     """判断是否为爆款笔记（已废弃，保留供参考）"""
    #     pass

    async def search_viral_notes_multi_keywords(
        self,
        keywords: List[str],
        target_count: int = 100,
        note_type: int = 0,
        time_range: int = 0,
        min_sample_count: int = 50,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> List[ViralNote]:
        """
        多关键词并行采集爆款笔记

        策略：
        1. 遍历每个关键词，执行三维度采集
        2. 所有关键词的结果合并去重（记录来源关键词）
        3. 按互动分数排序
        4. 应用爆款比例筛选
        5. 校验最终样本量

        Args:
            keywords: 搜索关键词列表（数量不设上限，由调用方按维度控制）
            target_count: 目标爬取总数量
            note_type: 笔记类型
            time_range: 时间范围
            min_sample_count: 最低样本量要求
            progress_callback: 进度回调函数

        Returns:
            按互动分数排序并截取的爆款笔记列表
        """
        self.search_keywords = keywords  # 保存多关键词列表

        # Cookie 有效性预检（失败直接抛出，前端展示授权引导）
        await self._preflight_cookie_check()

        # 智能调整参数：确保 min_sample_count 不超过合理范围
        original_min_sample = min_sample_count

        if min_sample_count > target_count:
            min_sample_count = target_count
            logger.info(f"📊 智能调整: 最低样本量 {original_min_sample} → {min_sample_count} (不超过目标数量)")

        logger.info(f"开始多关键词采集: {keywords}")
        logger.info(f"目标数量: {target_count}, 最低样本量: {min_sample_count}")

        # 计算每个关键词的目标数量
        target_per_keyword = self._calculate_target_per_keyword(
            len(keywords), target_count, min_sample_count
        )

        # 存储所有笔记（按 note_id 去重，记录来源关键词）
        all_notes: Dict[str, Dict[str, Any]] = {}
        keyword_stats: Dict[str, int] = {}  # 每个关键词的采集数量

        total_keywords = len(keywords)

        # 从检查点恢复时，跳过已完成的关键词
        start_index = 0
        is_resuming = False  # 标记是否正在恢复中（只对恢复的第一个关键词有效）
        if self._checkpoint is not None:
            start_index = self._checkpoint.keyword_index
            is_resuming = True
            logger.info(f"从检查点恢复: 从关键词索引 {start_index} 开始")

        for i, keyword in enumerate(keywords[start_index:], start=start_index):
            # 更新当前关键词索引（在检查点之前更新，确保暂停时保存正确的位置）
            self._current_keyword_index = i

            # 只有恢复的第一个关键词保留检查点，后续关键词从头开始
            if is_resuming and i == start_index:
                # 恢复中：同步当前索引到检查点值，确保"刚恢复就暂停"也能保存正确位置
                self._current_dimension_index = self._checkpoint.dimension_index
                self._current_page_index = self._checkpoint.page_index
                logger.info(f"恢复关键词 {i}: 从维度 {self._checkpoint.dimension_index}, 页 {self._checkpoint.page_index} 继续")
            else:
                # 非恢复或后续关键词：清空检查点，从头开始
                self._checkpoint = None
                self._current_dimension_index = 0
                self._current_page_index = 1

            # === 检查点 1: 关键词循环开始 ===
            if not await self._check_pause_point():
                logger.info(f"任务被取消/暂停，停止在关键词 {i}: {keyword}")
                break

            if progress_callback:
                progress_callback(
                    int((i / total_keywords) * 80),
                    f"正在采集关键词 [{i+1}/{total_keywords}]: {keyword}"
                )

            logger.info(f"[关键词 {i+1}/{total_keywords}] 开始采集: {keyword}")

            # 调用单关键词采集（内部执行三维度爬取）
            notes = await self._search_single_keyword(
                query=keyword,
                target_per_keyword=target_per_keyword,
                note_type=note_type,
                time_range=time_range
            )

            keyword_stats[keyword] = len(notes)

            # 合并去重，记录来源关键词
            for note in notes:
                note_id = note.get('note_id')
                if not note_id:
                    continue

                if note_id not in all_notes:
                    # 新笔记，添加来源关键词
                    note['source_keywords'] = [keyword]
                    all_notes[note_id] = note
                else:
                    # 已存在的笔记，追加来源关键词
                    existing_keywords = all_notes[note_id].get('source_keywords', [])
                    if keyword not in existing_keywords:
                        existing_keywords.append(keyword)
                        all_notes[note_id]['source_keywords'] = existing_keywords

            logger.info(f"[关键词 {i+1}/{total_keywords}] 采集完成: {len(notes)} 条")

            # 关键词之间等待，避免请求过快
            if i < total_keywords - 1:
                # === 检查点 2: 关键词间隔 ===
                if not await self._check_pause_point():
                    logger.info(f"任务被取消/暂停，停止在关键词 {i+1} 后")
                    break
                logger.info("等待 {:.1f} 秒后采集下一个关键词...", _COLLECTOR_DIM_SLEEP)
                await asyncio.sleep(_COLLECTOR_DIM_SLEEP + random.uniform(0, 2))

        # 统计多关键词匹配数量
        multi_match_count = sum(
            1 for note in all_notes.values()
            if len(note.get('source_keywords', [])) > 1
        )

        logger.info(f"多关键词采集完成: 共 {len(all_notes)} 篇去重后笔记")
        logger.info(f"各关键词采集数量: {keyword_stats}")
        logger.info(f"多关键词匹配笔记: {multi_match_count} 篇")

        if progress_callback:
            progress_callback(85, f"采集完成，去重后 {len(all_notes)} 篇，正在排序筛选...")

        # 转换为 ViralNote 对象
        viral_notes = [ViralNote.from_spider_data(note) for note in all_notes.values()]

        # 按互动分数降序排序
        viral_notes.sort(key=lambda x: x.interaction_score, reverse=True)

        # 保留筛选前的全部笔记（供自动补采阶段1使用）
        self._all_notes_before_filter = viral_notes.copy()

        result = viral_notes

        # 校验样本量
        if len(result) < min_sample_count:
            logger.warning(
                f"⚠️ 样本量不足！当前 {len(result)} 篇，最低要求 {min_sample_count} 篇。"
                f"建议增加更多关键词。"
            )

        if progress_callback:
            progress_callback(100, f"完成！共 {len(viral_notes)} 篇，筛选出 {len(result)} 篇爆款")

        # 保存统计信息
        self.keyword_stats = keyword_stats
        self.multi_match_count = multi_match_count
        self.collected_notes = result

        return result

    def _calculate_target_per_keyword(
        self,
        keyword_count: int,
        total_target: int,
        min_sample_count: int
    ) -> int:
        """
        计算每个关键词应采集的目标数量

        策略：
        1. 优先保证不超过用户设定的 total_target
        2. 在此基础上尽量满足 min_sample_count 需求
        3. 如果 target_count 不足以满足 min_sample_count，记录警告但不强制超出
        """
        # 基础分配：总目标 / 关键词数
        base_per_keyword = total_target // keyword_count

        # 需要的原始数量（考虑去重损耗）
        safety_factor = 1.3  # 考虑30%去重损耗
        ideal_per_keyword = int((min_sample_count * safety_factor) / keyword_count)

        # 计算上限（不超过用户设定总目标的1.5倍分摊）
        upper_limit = max(int(base_per_keyword * 1.5), base_per_keyword)

        # 取两者较大值，但不超过上限
        result = min(max(base_per_keyword, ideal_per_keyword), upper_limit)

        # 确保至少有意义的数量，但最小值也不能突破上限
        result = max(result, min(10, upper_limit))

        # 如果计算结果仍不足以满足样本量需求，记录警告
        estimated_total = result * keyword_count
        estimated_after_dedup = int(estimated_total * 0.7)  # 考虑去重
        if estimated_after_dedup < min_sample_count:
            logger.warning(
                f"⚠️ 当前配置预估样本量({estimated_after_dedup})不足{min_sample_count}，"
                f"建议增加目标数量或减少关键词数量"
            )

        return result

    async def _search_single_keyword(
        self,
        query: str,
        target_per_keyword: int,
        note_type: int,
        time_range: int
    ) -> List[Dict[str, Any]]:
        """
        单关键词三维度采集

        Args:
            query: 搜索关键词
            target_per_keyword: 该关键词目标数量
            note_type: 笔记类型
            time_range: 时间范围

        Returns:
            该关键词采集到的笔记列表（原始字典格式）
        """
        dimensions = [
            (SortType.LIKES, "点赞"),
            (SortType.COMMENTS, "评论"),
            (SortType.COLLECTS, "收藏")
        ]

        target_per_dimension = math.ceil(target_per_keyword / len(dimensions))
        all_notes: Dict[str, Dict[str, Any]] = {}

        # 从检查点恢复时，跳过已完成的维度
        start_dim_index = 0
        is_dim_resuming = False  # 标记是否正在恢复维度
        if self._checkpoint is not None:
            start_dim_index = self._checkpoint.dimension_index
            is_dim_resuming = True
            logger.debug(f"从检查点恢复: 从维度索引 {start_dim_index} 开始")

        for dim_idx, (sort_type, name) in enumerate(dimensions[start_dim_index:], start=start_dim_index):
            # 更新当前维度索引（在检查点之前更新）
            self._current_dimension_index = dim_idx

            # 只有恢复的第一个维度保留页码检查点，后续维度从第 1 页开始
            if is_dim_resuming and dim_idx == start_dim_index and self._checkpoint:
                # 恢复中：保留 _checkpoint.page_index
                self._current_page_index = self._checkpoint.page_index
                logger.debug(f"恢复维度 {name}: 从第 {self._current_page_index} 页继续")
                # 完成这个维度后清空检查点（一次性恢复）
            else:
                # 非恢复或后续维度：从第 1 页开始
                self._current_page_index = 1

            # === 检查点 3: 维度循环开始 ===
            if not await self._check_pause_point():
                logger.info(f"任务被取消/暂停，停止在维度: {name}")
                break

            result = await self._fetch_dimension(
                query=query,
                sort_type=sort_type,
                dimension_name=name,
                target_per_dimension=target_per_dimension,
                note_type=note_type,
                time_range=time_range
            )

            # 完成恢复的维度后，清空检查点（后续维度从头开始）
            if is_dim_resuming and dim_idx == start_dim_index:
                self._checkpoint = None
                is_dim_resuming = False

            if result.success:
                for note in result.notes:
                    note_id = note.get('note_id')
                    if note_id and note_id not in all_notes:
                        all_notes[note_id] = note

            # 维度间等待（参数化 + jitter）
            if not await self._check_pause_point():
                break
            await asyncio.sleep(_COLLECTOR_DIM_SLEEP + random.uniform(0, 2))

        # 完成当前关键词后重置维度索引
        self._current_dimension_index = 0

        return list(all_notes.values())

    async def search_viral_notes(
        self,
        query: str,
        target_count: int = 100,
        note_type: int = 0,  # 0:不限 1:视频 2:图文
        time_range: int = 0,  # 0:不限 1:一天内 2:一周内 3:半年内
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> List[ViralNote]:
        """
        并行多维度搜索爆款笔记

        同时按"最多点赞"、"最多评论"、"最多收藏"三个维度爬取，
        去重后按互动分数排序，按比例截取爆款。

        Args:
            query: 搜索关键词
            target_count: 目标爬取总数量（三维度总和）
            note_type: 笔记类型
            time_range: 时间范围
            progress_callback: 进度回调函数

        Returns:
            按互动分数排序并截取的爆款笔记列表
        """
        self.search_keyword = query

        logger.info(f"开始并行多维度爬取: {query}")
        logger.info(f"目标数量: {target_count}")

        # 三个爬取维度
        dimensions = [
            (SortType.LIKES, "点赞"),
            (SortType.COMMENTS, "评论"),
            (SortType.COLLECTS, "收藏")
        ]

        # 每个维度的目标数量（向上取整确保总数足够）
        target_per_dimension = math.ceil(target_count / len(dimensions))

        if progress_callback:
            progress_callback(5, "开始依次爬取点赞/评论/收藏三个维度...")

        # 从检查点恢复时，跳过已完成的维度
        start_dim_index = 0
        is_dim_resuming = False
        if self._checkpoint is not None:
            start_dim_index = self._checkpoint.dimension_index
            is_dim_resuming = True
            logger.info(f"从检查点恢复: 从维度索引 {start_dim_index} 开始")

        # 串行执行三个维度的爬取（避免触发反爬机制）
        results = []
        for i, (sort_type, name) in enumerate(dimensions[start_dim_index:], start=start_dim_index):
            # 更新当前维度索引
            self._current_dimension_index = i

            # 只有恢复的第一个维度保留页码检查点
            if is_dim_resuming and i == start_dim_index and self._checkpoint:
                self._current_page_index = self._checkpoint.page_index
                logger.debug(f"恢复维度 {name}: 从第 {self._current_page_index} 页继续")
            else:
                self._current_page_index = 1

            # 检查点检查
            if not await self._check_pause_point():
                logger.info(f"任务被取消/暂停，停止在维度: {name}")
                break

            if progress_callback:
                progress_callback(
                    10 + i * 25,
                    f"正在爬取【{name}】维度 ({i+1}/3)..."
                )

            result = await self._fetch_dimension(
                query=query,
                sort_type=sort_type,
                dimension_name=name,
                target_per_dimension=target_per_dimension,
                note_type=note_type,
                time_range=time_range
            )
            results.append(result)

            # 完成恢复的维度后，清空检查点
            if is_dim_resuming and i == start_dim_index:
                self._checkpoint = None
                is_dim_resuming = False

            # 维度之间等待一段时间，避免请求过快
            if i < len(dimensions) - 1:
                if not await self._check_pause_point():
                    break
                logger.info("等待 {:.1f} 秒后爬取下一个维度...", _COLLECTOR_DIM_SLEEP)
                await asyncio.sleep(_COLLECTOR_DIM_SLEEP + random.uniform(0, 2))

        # 合并结果并去重（使用 note_id 作为键）
        all_notes: Dict[str, Dict[str, Any]] = {}
        dimension_stats = {"点赞": 0, "评论": 0, "收藏": 0}

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"维度爬取异常: {result}")
                continue

            if isinstance(result, DimensionResult):
                dimension_stats[result.dimension_name] = result.total_fetched

                for note in result.notes:
                    note_id = note.get('note_id')
                    if note_id and note_id not in all_notes:
                        all_notes[note_id] = note

        logger.info(
            f"三维度爬取完成: 点赞{dimension_stats['点赞']} "
            f"评论{dimension_stats['评论']} 收藏{dimension_stats['收藏']}"
        )
        logger.info(f"去重后笔记数量: {len(all_notes)}")

        if progress_callback:
            progress_callback(80, f"爬取完成，去重后{len(all_notes)}篇，正在排序筛选...")

        # 转换为 ViralNote 对象
        viral_notes = [ViralNote.from_spider_data(note) for note in all_notes.values()]

        # 按互动分数降序排序
        viral_notes.sort(key=lambda x: x.interaction_score, reverse=True)

        # 保留筛选前的全部笔记（供自动补采阶段1使用）
        self._all_notes_before_filter = viral_notes.copy()

        if progress_callback:
            progress_callback(100, f"完成！共采集 {len(viral_notes)} 篇笔记")

        self.collected_notes = viral_notes[:viral_count]
        return self.collected_notes

    async def _search_notes_async(self, **kwargs) -> List[Dict]:
        """异步搜索笔记（带风控退避重试）"""
        _dbg_query = kwargs['query']
        _dbg_page = kwargs['page']
        _dbg_sort = kwargs.get('sort_type', 2)
        logger.info(
            "[search_note] query={!r} page={} sort={} cookie_len={}",
            _dbg_query, _dbg_page, _dbg_sort, len(self.cookies_str),
        )

        # 周期刷新 cookie（每 _COOKIE_REFRESH_INTERVAL 秒）
        await self._maybe_refresh_cookie()

        # 带退避重试的 search_note 调用
        result = await self._search_with_captcha_retry(
            query=_dbg_query,
            page=_dbg_page,
            sort=_dbg_sort,
            **{k: v for k, v in kwargs.items() if k not in ("query", "page", "sort_type")},
        )

        # 解析返回值（返回的是元组：success, msg, res_json）
        if isinstance(result, tuple) and len(result) == 3:
            success, msg, res_json = result
            logger.info(f"搜索API返回: success={success}, msg={msg[:100] if msg else 'None'}")
            # 诊断：打印完整顶层结构
            if res_json:
                _top_keys = list(res_json.keys())
                logger.info(f"搜索响应顶层keys={_top_keys} success={success}")
                # 如有 code / subCode 等字段也打印
                for _k in ("code", "subCode", "errorCode", "error", "loginRequired"):
                    if _k in res_json:
                        logger.warning(f"  └ {_k}={res_json[_k]!r}")
            if success and res_json and 'data' in res_json:
                # 诊断：打印 data 层级的 key 和 items 原始数量
                _raw_data = res_json.get('data') or {}
                _raw_items = _raw_data.get('items', [])
                logger.info(
                    f"搜索data结构: keys={list(_raw_data.keys())} "
                    f"items原始数量={len(_raw_items)} "
                    f"has_more={_raw_data.get('has_more')} "
                    + (f"首条keys={list(_raw_items[0].keys())}" if _raw_items else "items为空")
                )
                if not _raw_data:
                    logger.warning(
                        "⚠️ XHS 返回 data:{} (软封禁/soft-block)。"
                        "常见原因: cookie 缺少 web_session 字段、web_session 已过期、"
                        "或当前 IP 被 XHS 临时限流。"
                        "解决方案: 到「设置→数据源授权」重新粘贴包含 web_session 的完整 cookie。"
                    )
                # 返回笔记列表
                items = _raw_items
                notes = []
                non_note_count = 0
                for item in items:
                    if 'note_card' in item:
                        note = item['note_card']
                        # ID和xsec_token都在item顶层
                        note_id = item.get('id')
                        xsec_token = item.get('xsec_token', '')

                        if note_id:
                            # 构造完整的URL（国内 xiaohongshu / 国际 rednote 与 XHS_WEB_ORIGIN 一致，避免与 webapi 域 Cookie 错配）
                            _origin = xhs_web_origin().rstrip("/")
                            if xsec_token:
                                note['note_url'] = (
                                    f"{_origin}/explore/{note_id}"
                                    f"?xsec_token={xsec_token}&xsec_source=pc_search"
                                )
                            else:
                                note['note_url'] = (
                                    f"{_origin}/explore/{note_id}?xsec_source=pc_search"
                                )

                            note['note_id'] = note_id  # 添加ID到note对象
                            notes.append(note)
                        else:
                            # 如果还是没有ID，打印调试信息
                            logger.debug(f"无法获取笔记ID，item字段: {list(item.keys())}")
                    else:
                        # model_type=rec_query / hot_query 等平台推荐项,非笔记
                        non_note_count += 1
                logger.info(
                    f"搜索返回：真实笔记 {len(notes)} 条"
                    + (f"，过滤掉 {non_note_count} 条平台推荐" if non_note_count else "")
                )
                return notes
            else:
                logger.warning(f"搜索失败: {msg}")
                return []
        else:
            logger.error(f"意外的返回格式: {type(result)}")
            return []

    async def _get_note_detail_async(self, note_url: str) -> Optional[Dict]:
        """异步获取笔记详情，带指数退避重试（与搜索接口保持一致）。

        优先级：
          1. CDP 模式（CDP_DETAIL_ENABLED=true）：Playwright Chrome，浏览器指纹完整
          2. HTTP 模式（回退）：签名 API 请求，可能被风控
        """
        loop = asyncio.get_event_loop()
        last_exc: Optional[Exception] = None
        result = None

        for attempt, wait_sec in enumerate([0.0] + _CAPTCHA_RETRY_WAITS):
            if attempt > 0:
                logger.warning(
                    f"⚠️ 获取详情触发风控，第 {attempt} 次重试（等待 {wait_sec}s）: {note_url}"
                )
                await self._maybe_refresh_cookie()
                await asyncio.sleep(wait_sec)

            try:
                if self._cdp_client is not None:
                    # ── CDP 模式：真实 Chrome 导航笔记页，浏览器指纹完整 ──
                    result = await self._cdp_client.get_note_info(note_url)
                    # 虚拟号/SMS 登录用户无 browser_data 目录，CDP context 不可用时自动降级 HTTP
                    if (
                        isinstance(result, tuple)
                        and len(result) == 3
                        and not result[0]
                        and result[1] == "CDP context unavailable"
                    ):
                        logger.warning(
                            "[collector] CDP context 不可用（虚拟号登录/无持久化浏览器配置文件），"
                            "笔记详情自动降级为 HTTP 模式并禁用 CDP"
                        )
                        self._cdp_client = None
                        result = await loop.run_in_executor(
                            None,
                            lambda: self.client.get_note_info(note_url, self.cookies_str)
                        )
                else:
                    # ── HTTP 模式（回退）──
                    result = await loop.run_in_executor(
                        None,
                        lambda: self.client.get_note_info(note_url, self.cookies_str)
                    )
                last_exc = None
                break
            except (CaptchaError, SoftBlockError) as e:
                last_exc = e
                continue
            except Exception as e:
                logger.warning(f"获取详情异常（非风控）: {e}")
                return None

        if last_exc is not None:
            # 重试耗尽，走 HTML 降级
            logger.warning(f"⚠️ 获取详情重试 {len(_CAPTCHA_RETRY_WAITS)} 次仍触发风控，尝试 HTML 降级: {note_url}")
            try:
                html_detail = await self._html_fallback(note_url)
                if html_detail:
                    logger.info(f"✅ HTML 降级成功: url={note_url}")
                    return html_detail
            except Exception as fallback_exc:
                logger.debug(f"HTML 降级异常: {fallback_exc}")
            logger.error(f"⚠️ HTML 降级也失败，跳过该笔记")
            return None

        # 解析返回值（返回的是元组：success, msg, res_json）
        if isinstance(result, tuple) and len(result) == 3:
            success, msg, res_json = result
            # CDP 直接返回路径：__INITIAL_STATE__ 的 note（camelCase），跳过 handle_note_info
            if success and isinstance(res_json, dict) and "_direct_note" in res_json:
                note = res_json["_direct_note"]
                # 补全 snake_case 字段，确保下游 note.get('note_id') 等能正常取值
                _cdp_normalize_note(note)
                logger.info(f"CDP 详情直接返回: note_id={note.get('note_id', '?')}")
                return note
            if success and res_json and 'data' in res_json:
                # 获取笔记详情
                items = res_json.get('data', {}).get('items', [])
                if items and len(items) > 0:
                    # 使用handle_note_info处理原始数据，正确提取视频URL等字段
                    try:
                        item = items[0]
                        item['url'] = note_url
                        processed_data = handle_note_info(item)
                        return processed_data
                    except Exception as e:
                        logger.warning(f"处理笔记数据失败 {note_url}: {e}，返回原始note_card")
                        return items[0].get('note_card', {})
                else:
                    # items为空，可能是笔记被删除、xsec_token过期或风控
                    # 尝试 HTML 降级解析（P2 兜底）
                    logger.warning(f"笔记详情items为空，尝试 HTML 降级: url={note_url}, msg={msg}")
                    html_detail = await self._html_fallback(note_url)
                    if html_detail:
                        return html_detail
            else:
                logger.warning(f"获取笔记详情失败: success={success}, msg={msg}, url={note_url}")
        else:
            logger.error(f"意外的返回格式: {type(result)}")

        return None

    async def _html_fallback(self, note_url: str) -> Optional[Dict]:
        """CAPTCHA 或 items 为空时，从笔记页面提取 window.__INITIAL_STATE__ 数据。

        优先级：
          1. CDP 模式（CDP_DETAIL_ENABLED=true）：Playwright Chrome 真实浏览器，
             浏览器指纹完整，成功率最高。
          2. 裸 HTTP 降级：直接 GET 笔记页面 HTML，仅在 XHS SSR 时有效。
        """
        # ── 层级 1：CDP Playwright 模式 ─────────────────────────────────────
        from .playwright_detail_fetcher import cdp_detail_enabled, PlaywrightDetailFetcher
        if cdp_detail_enabled() and self._owner_user_id:
            try:
                fetcher = await PlaywrightDetailFetcher.get_for_identity(self._owner_user_id)
                result = await fetcher.fetch(note_url)
                if result:
                    logger.info(f"[CDP] 详情获取成功: {note_url}")
                    return result
                logger.debug(f"[CDP] 未返回数据，回退裸 HTTP: {note_url}")
            except Exception as exc:
                logger.debug(f"[CDP] 异常，回退裸 HTTP: {exc}")

        # ── 层级 2：裸 HTTP 降级 ────────────────────────────────────────────
        loop = asyncio.get_event_loop()
        web_origin = xhs_web_origin()
        headers = {
            "User-Agent": BROWSER_UA,
            "Cookie": self.cookies_str,
            "Referer": f"{web_origin}/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            page_url = note_url
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(page_url, headers=headers, timeout=15)
            )
            if resp.status_code in (471, 461):
                raise CaptchaError(f"HTML 降级触发 CAPTCHA, status={resp.status_code}")

            html = resp.text

            has_initial_state = "window.__INITIAL_STATE__" in html
            has_note_detail = "noteDetailMap" in html
            logger.debug(
                f"HTML 降级响应: status={resp.status_code} len={len(html)} "
                f"has_initial_state={has_initial_state} has_noteDetailMap={has_note_detail} "
                f"url={page_url}"
            )
            if not has_initial_state:
                logger.warning(
                    f"HTML 降级：页面无 __INITIAL_STATE__，"
                    f"响应前200字符: {html[:200]!r}, url={page_url}"
                )
                return None
            if not has_note_detail:
                logger.debug(f"HTML 降级：__INITIAL_STATE__ 存在但无 noteDetailMap，url={page_url}")
                return None

            js_str = _extract_initial_state_json(html)
            if not js_str:
                logger.debug(f"HTML 降级：提取 __INITIAL_STATE__ JSON 失败，url={page_url}")
                return None

            state = json.loads(
                js_str
                .replace(":undefined", ":null")
                .replace(":Undefined", ":null")
            )
            note_id = note_url.split("?")[0].rstrip("/").split("/")[-1]
            note = (
                state.get("note", {})
                .get("noteDetailMap", {})
                .get(note_id, {})
                .get("note")
            )
            if note:
                logger.info(f"HTML 降级成功: note_id={note_id}")
            else:
                logger.debug(
                    f"HTML 降级：state 中无 note_id={note_id}，"
                    f"available keys: {list(state.get('note', {}).get('noteDetailMap', {}).keys())}"
                )
            return note
        except CaptchaError:
            raise
        except json.JSONDecodeError as exc:
            logger.warning(f"HTML 降级 JSON 解析失败: {exc}, url={note_url}")
            return None
        except Exception as exc:
            logger.debug(f"HTML 降级失败: {exc}, url={note_url}")
            return None

    async def _fetch_dimension(
        self,
        query: str,
        sort_type: SortType,
        dimension_name: str,
        target_per_dimension: int,
        note_type: int,
        time_range: int
    ) -> DimensionResult:
        """
        单维度爬取实现

        Args:
            query: 搜索关键词
            sort_type: 排序方式
            dimension_name: 维度名称（用于日志）
            target_per_dimension: 该维度目标数量
            note_type: 笔记类型
            time_range: 时间范围

        Returns:
            DimensionResult: 该维度的爬取结果
        """
        notes = []
        max_pages = 30  # 每个维度最多30页

        # 使用调用方设置的 _current_page_index（恢复时由上层设置，正常时为 1）
        page = self._current_page_index
        if page > 1:
            logger.debug(f"[{dimension_name}] 从第 {page} 页继续")

        logger.info(f"[{dimension_name}] 开始爬取，目标 {target_per_dimension} 条")

        try:
            while len(notes) < target_per_dimension and page <= max_pages:
                # 更新页码跟踪（在检查点之前更新，确保暂停时能保存当前页）
                self._current_page_index = page

                # === 检查点 4: 分页循环开始 ===
                if not await self._check_pause_point():
                    logger.info(f"[{dimension_name}] 任务被取消/暂停，停止在第 {page} 页")
                    break

                # 兼容旧检查点（如果存在）
                if self._checkpoint:
                    self._checkpoint.page_index = page

                # 搜索当前页
                search_results = await self._search_notes_async(
                    query=query,
                    page=page,
                    note_type=note_type,
                    time_range=time_range,
                    sort_type=int(sort_type)
                )

                if not search_results:
                    logger.debug(f"[{dimension_name}] 第 {page} 页无结果，停止")
                    break

                # 诊断：打印第1页第1条 note_url，便于排查 xsec_token 是否缺失
                if page == 1 and search_results:
                    first_url = search_results[0].get('note_url', 'NO_URL')
                    has_token = 'xsec_token=' in first_url and 'xsec_token=&' not in first_url
                    logger.info(
                        f"[{dimension_name}] 首条URL样本: "
                        f"{'有xsec_token' if has_token else '⚠️ 无/空xsec_token'} "
                        f"| {first_url[:100]}"
                    )

                # Tier1 粗过滤：批量过滤整页 briefs，减少无效详情请求
                if self.tier1_filter and search_results:
                    try:
                        before_t1 = len(search_results)
                        search_results = await self.tier1_filter(search_results)
                        logger.debug(
                            f"[{dimension_name}] Tier1 过滤: {before_t1} → {len(search_results)} 条"
                        )
                    except Exception as _e:
                        logger.warning(f"[{dimension_name}] Tier1 过滤异常，跳过: {_e}")

                # 获取笔记详情
                page_success = 0
                page_fail = 0
                for note_brief in search_results:
                    note_url = note_brief.get('note_url', '')
                    if not note_url:
                        page_fail += 1
                        continue

                    try:
                        note_detail = await self._get_note_detail_async(note_url)
                        if note_detail:
                            # Tier2 精判：逐条判断详情语义是否符合用户意图
                            if self.tier2_filter:
                                try:
                                    if not await self.tier2_filter(note_detail):
                                        logger.debug(
                                            f"[{dimension_name}] Tier2 淘汰: "
                                            f"{note_detail.get('note_id', '?')}"
                                        )
                                        page_fail += 1
                                        continue  # 不 append，不计 page_success，不触发 break
                                except Exception as _e:
                                    logger.warning(
                                        f"[{dimension_name}] Tier2 过滤异常，fail-open: {_e}"
                                    )
                            notes.append(note_detail)
                            page_success += 1
                            if len(notes) >= target_per_dimension:
                                break
                        else:
                            page_fail += 1
                    except Exception as e:
                        logger.debug(f"[{dimension_name}] 获取详情失败: {e}")
                        page_fail += 1
                        continue

                    await asyncio.sleep(random.uniform(_COLLECTOR_NOTE_SLEEP_MIN, _COLLECTOR_NOTE_SLEEP_MAX))

                logger.info(
                    f"[{dimension_name}] 第{page}页: 搜索{len(search_results)}条 "
                    f"→ 详情成功{page_success}条, 失败{page_fail}条, 累计{len(notes)}条"
                )
                page += 1
                await asyncio.sleep(random.uniform(_COLLECTOR_PAGE_SLEEP_MIN, _COLLECTOR_PAGE_SLEEP_MAX))

            logger.info(f"[{dimension_name}] 爬取完成: {len(notes)} 条")

            return DimensionResult(
                sort_type=sort_type,
                dimension_name=dimension_name,
                notes=notes,
                total_fetched=len(notes),
                success=True
            )

        except Exception as e:
            logger.error(f"[{dimension_name}] 爬取失败: {e}")
            return DimensionResult(
                sort_type=sort_type,
                dimension_name=dimension_name,
                notes=notes,
                total_fetched=len(notes),
                success=False,
                error_message=str(e)
            )
        finally:
            # 维度结束后关闭 CDP 搜索会话（释放浏览器 Page）
            if self._cdp_client is not None:
                await self._cdp_client.close_session(
                    query=query,
                    sort_type_choice=int(sort_type),
                    note_type=note_type,
                    time_range=time_range,
                )

    def filter_by_interaction(
        self,
        notes: List[Dict[str, Any]],
        min_threshold: int = 1000,
        max_threshold: Optional[int] = None
    ) -> List[ViralNote]:
        """
        按互动量范围筛选笔记

        Args:
            notes: 原始笔记数据列表
            min_threshold: 最小互动阈值
            max_threshold: 最大互动阈值（可选）

        Returns:
            筛选后的爆款笔记列表
        """
        filtered_notes = []

        for note_data in notes:
            score = self.calculate_interaction_score(note_data)

            # 检查是否在范围内
            if score >= min_threshold:
                if max_threshold is None or score <= max_threshold:
                    viral_note = ViralNote.from_spider_data(note_data)
                    filtered_notes.append(viral_note)

        # 按互动分数降序排序
        filtered_notes.sort(key=lambda x: x.interaction_score, reverse=True)

        return filtered_notes

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取采集统计信息

        Returns:
            统计信息字典
        """
        if not self.collected_notes:
            return {
                'total_count': 0,
                'avg_interaction': 0,
                'video_count': 0,
                'image_count': 0,
                'max_interaction': 0,
                'min_interaction': 0
            }

        interactions = [note.interaction_score for note in self.collected_notes]
        video_count = sum(1 for note in self.collected_notes if note.note_type == "视频")

        return {
            'total_count': len(self.collected_notes),
            'avg_interaction': sum(interactions) // len(interactions),
            'video_count': video_count,
            'image_count': len(self.collected_notes) - video_count,
            'max_interaction': max(interactions),
            'min_interaction': min(interactions),
            'top_notes': [
                {
                    'title': note.title[:30],
                    'interaction': note.interaction_score,
                    'type': note.note_type
                }
                for note in self.collected_notes[:5]
            ]
        }

    def save_collected_notes(self, output_dir: str = "datas/viral_analysis"):
        """
        保存采集的笔记数据（支持多关键词统计）

        Args:
            output_dir: 输出目录
        """
        import json
        from datetime import datetime

        os.makedirs(output_dir, exist_ok=True)

        # 生成文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"viral_notes_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)

        # 构建保存数据
        data = {
            'collection_time': timestamp,
            'total_notes': len(self.collected_notes),
            'statistics': self.get_statistics(),
            'notes': [note.to_dict() for note in self.collected_notes]
        }

        # 支持多关键词模式
        if hasattr(self, 'search_keywords') and self.search_keywords:
            data['search_keywords'] = self.search_keywords
            data['search_keyword'] = ', '.join(self.search_keywords)  # 兼容旧格式
            data['keyword_statistics'] = {
                'keyword_count': len(self.search_keywords),
                'by_keyword': getattr(self, 'keyword_stats', {}),
                'multi_match_count': getattr(self, 'multi_match_count', 0)
            }
        else:
            # 单关键词模式（向后兼容）
            data['search_keyword'] = self.search_keyword
            data['search_keywords'] = [self.search_keyword] if self.search_keyword else []

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.success(f"爆款笔记已保存到: {filepath}")
        return filepath

    # ==================== 阈值模式采集（新增） ====================

    async def search_viral_notes_by_threshold(
        self,
        keywords: List[str],
        min_interaction: int = 5000,
        max_collect_count: int = 500,
        note_type: int = 0,
        time_range: int = 0,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> List[ViralNote]:
        """
        按互动量阈值采集爆款笔记（阈值模式）

        策略：
        1. 遍历每个关键词，执行三维度采集
        2. 实时过滤：只保留互动分 >= min_interaction 的笔记
        3. 连续 10 条低于阈值时提前终止（优化效率）
        4. 达到 max_collect_count 时停止

        Args:
            keywords: 搜索关键词列表（最多 5 个）
            min_interaction: 互动量最低阈值
            max_collect_count: 最大采集数量
            note_type: 笔记类型
            time_range: 时间范围
            progress_callback: 进度回调函数

        Returns:
            符合阈值的爆款笔记列表
        """
        keywords = keywords[:5]
        self.search_keywords = keywords

        logger.info(f"🎯 阈值模式采集: {keywords}")
        logger.info(f"   互动阈值: >= {min_interaction}, 最大数量: {max_collect_count}")

        # 存储所有符合阈值的笔记（按 note_id 去重）
        all_notes: Dict[str, Dict[str, Any]] = {}
        keyword_stats: Dict[str, int] = {}

        total_keywords = len(keywords)

        for i, keyword in enumerate(keywords):
            # 更新当前关键词索引
            self._current_keyword_index = i

            # 检查点检查
            if not await self._check_pause_point():
                logger.info(f"任务被取消/暂停，停止在关键词 {i}: {keyword}")
                break

            if progress_callback:
                progress_callback(
                    int((i / total_keywords) * 80),
                    f"阈值采集 关键词 [{i+1}/{total_keywords}]: {keyword}"
                )

            logger.info(f"[关键词 {i+1}/{total_keywords}] 开始阈值采集: {keyword}")

            # 调用单关键词阈值采集
            notes = await self._search_single_keyword_threshold(
                query=keyword,
                min_interaction=min_interaction,
                max_per_keyword=max_collect_count // total_keywords + 50,
                note_type=note_type,
                time_range=time_range
            )

            keyword_stats[keyword] = len(notes)

            # 合并去重
            for note in notes:
                note_id = note.get('note_id')
                if not note_id:
                    continue
                if note_id not in all_notes:
                    note['source_keywords'] = [keyword]
                    all_notes[note_id] = note
                else:
                    existing_keywords = all_notes[note_id].get('source_keywords', [])
                    if keyword not in existing_keywords:
                        existing_keywords.append(keyword)

            logger.info(f"[关键词 {i+1}/{total_keywords}] 完成: {len(notes)} 条符合阈值")

            # 检查是否达到上限
            if len(all_notes) >= max_collect_count:
                logger.info(f"已达最大数量 {max_collect_count}，停止采集")
                break

            # 关键词间隔
            if i < total_keywords - 1:
                if not await self._check_pause_point():
                    break
                await asyncio.sleep(_COLLECTOR_DIM_SLEEP + random.uniform(0, 2))

        logger.info(f"阈值采集完成: 共 {len(all_notes)} 篇符合条件")
        logger.info(f"各关键词采集数量: {keyword_stats}")

        if progress_callback:
            progress_callback(90, f"阈值采集完成，共 {len(all_notes)} 篇")

        # 转换为 ViralNote 对象
        viral_notes = [ViralNote.from_spider_data(note) for note in all_notes.values()]

        # 按互动分数降序排序
        viral_notes.sort(key=lambda x: x.interaction_score, reverse=True)

        # 限制最大数量
        result = viral_notes[:max_collect_count]

        if progress_callback:
            progress_callback(100, f"完成！共 {len(result)} 篇爆款（阈值 >= {min_interaction}）")

        # 保存统计信息
        self.keyword_stats = keyword_stats
        self.collected_notes = result

        return result

    async def _search_single_keyword_threshold(
        self,
        query: str,
        min_interaction: int,
        max_per_keyword: int,
        note_type: int,
        time_range: int
    ) -> List[Dict[str, Any]]:
        """
        单关键词阈值采集（三维度）

        Args:
            query: 搜索关键词
            min_interaction: 互动量阈值
            max_per_keyword: 该关键词最大采集数
            note_type: 笔记类型
            time_range: 时间范围

        Returns:
            符合阈值的笔记列表（原始字典格式）
        """
        dimensions = [
            (SortType.LIKES, "点赞"),
            (SortType.COMMENTS, "评论"),
            (SortType.COLLECTS, "收藏")
        ]

        all_notes: Dict[str, Dict[str, Any]] = {}

        for dim_idx, (sort_type, name) in enumerate(dimensions):
            self._current_dimension_index = dim_idx

            if not await self._check_pause_point():
                break

            result = await self._fetch_dimension_threshold(
                query=query,
                sort_type=sort_type,
                dimension_name=name,
                min_interaction=min_interaction,
                max_count=max_per_keyword // len(dimensions) + 20,
                note_type=note_type,
                time_range=time_range
            )

            if result.success:
                for note in result.notes:
                    note_id = note.get('note_id')
                    if note_id and note_id not in all_notes:
                        all_notes[note_id] = note

            # 检查是否达到上限
            if len(all_notes) >= max_per_keyword:
                break

            if not await self._check_pause_point():
                break
            await asyncio.sleep(_COLLECTOR_DIM_SLEEP + random.uniform(0, 2))

        return list(all_notes.values())

    async def _fetch_dimension_threshold(
        self,
        query: str,
        sort_type: SortType,
        dimension_name: str,
        min_interaction: int,
        max_count: int,
        note_type: int,
        time_range: int
    ) -> DimensionResult:
        """
        单维度阈值采集（实时过滤 + 智能终止）

        Args:
            query: 搜索关键词
            sort_type: 排序方式
            dimension_name: 维度名称
            min_interaction: 互动量阈值
            max_count: 最大采集数
            note_type: 笔记类型
            time_range: 时间范围

        Returns:
            采集结果
        """
        notes = []
        max_pages = 30
        page = 1
        consecutive_below_threshold = 0  # 连续低于阈值的计数

        logger.debug(f"[{dimension_name}] 阈值采集开始，阈值={min_interaction}")

        try:
            while len(notes) < max_count and page <= max_pages:
                self._current_page_index = page

                if not await self._check_pause_point():
                    break

                # 搜索当前页
                search_results = await self._search_notes_async(
                    query=query,
                    page=page,
                    note_type=note_type,
                    time_range=time_range,
                    sort_type=int(sort_type)
                )

                if not search_results:
                    logger.debug(f"[{dimension_name}] 第 {page} 页无结果，停止")
                    break

                # 逐条获取详情并过滤
                for note_brief in search_results:
                    note_url = note_brief.get('note_url', '')
                    if not note_url:
                        continue

                    try:
                        note_detail = await self._get_note_detail_async(note_url)
                        if not note_detail:
                            continue

                        # 计算互动分数
                        score = self.calculate_interaction_score(note_detail)

                        if score >= min_interaction:
                            notes.append(note_detail)
                            consecutive_below_threshold = 0  # 重置计数
                            if len(notes) >= max_count:
                                break
                        else:
                            consecutive_below_threshold += 1
                            # 连续 10 条低于阈值，智能终止
                            if consecutive_below_threshold >= 10:
                                logger.debug(
                                    f"[{dimension_name}] 连续 10 条低于阈值，提前终止"
                                )
                                break

                    except Exception as e:
                        logger.debug(f"[{dimension_name}] 获取详情失败: {e}")
                        continue

                    await asyncio.sleep(random.uniform(_COLLECTOR_NOTE_SLEEP_MIN, _COLLECTOR_NOTE_SLEEP_MAX))

                # 如果连续低于阈值，退出分页循环
                if consecutive_below_threshold >= 10:
                    break

                page += 1
                await asyncio.sleep(random.uniform(_COLLECTOR_PAGE_SLEEP_MIN, _COLLECTOR_PAGE_SLEEP_MAX))

            logger.info(f"[{dimension_name}] 阈值采集完成: {len(notes)} 条")

            return DimensionResult(
                sort_type=sort_type,
                dimension_name=dimension_name,
                notes=notes,
                total_fetched=len(notes),
                success=True
            )

        except Exception as e:
            logger.error(f"[{dimension_name}] 阈值采集失败: {e}")
            return DimensionResult(
                sort_type=sort_type,
                dimension_name=dimension_name,
                notes=notes,
                total_fetched=len(notes),
                success=False,
                error_message=str(e)
            )
        finally:
            if self._cdp_client is not None:
                await self._cdp_client.close_session(
                    query=query,
                    sort_type_choice=int(sort_type),
                    note_type=note_type,
                    time_range=time_range,
                )

    async def search_viral_notes_unified(
        self,
        keywords: List[str],
        search_mode: str = "ratio",
        # 比例模式参数
        target_count: int = 100,
        min_sample_count: int = 50,
        # 阈值模式参数
        min_interaction: int = 5000,
        max_collect_count: int = 500,
        # 公共参数
        note_type: int = 0,
        time_range: int = 0,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> List[ViralNote]:
        """
        统一采集入口（支持两种模式）

        根据 search_mode 参数分发到对应的采集方法：
        - ratio: 比例筛选模式（现有）
        - threshold: 阈值筛选模式（新增）

        Args:
            keywords: 搜索关键词列表
            search_mode: 搜索模式（ratio/threshold）
            target_count: 目标采集数量（比例模式）
            min_sample_count: 最低样本量（比例模式）
            min_interaction: 互动量阈值（阈值模式）
            max_collect_count: 最大采集数量（阈值模式）
            note_type: 笔记类型
            time_range: 时间范围
            progress_callback: 进度回调函数

        Returns:
            采集到的爆款笔记列表
        """
        if search_mode == "threshold":
            logger.info("📊 使用阈值筛选模式")
            return await self.search_viral_notes_by_threshold(
                keywords=keywords,
                min_interaction=min_interaction,
                max_collect_count=max_collect_count,
                note_type=note_type,
                time_range=time_range,
                progress_callback=progress_callback
            )
        else:
            logger.info("📊 使用比例筛选模式")
            return await self.search_viral_notes_multi_keywords(
                keywords=keywords,
                target_count=target_count,
                note_type=note_type,
                time_range=time_range,
                min_sample_count=min_sample_count,
                progress_callback=progress_callback
            )