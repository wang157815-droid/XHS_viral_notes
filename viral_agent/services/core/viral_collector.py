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

from apis.xhs_pc_apis import XHS_Apis
from viral_agent.models.viral_note import ViralNote
from viral_agent.utils import parse_chinese_number
from xhs_utils.cookie_util import trans_cookies
from xhs_utils.data_util import handle_note_info


class ViralNoteCollector:
    """爆款笔记采集器（支持暂停/恢复/取消）"""

    def __init__(self, cookies_str: str):
        """
        初始化采集器

        Args:
            cookies_str: Cookie字符串
        """
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        self.client = XHS_Apis()  # XHS_Apis 不需要参数
        self.collected_notes: List[ViralNote] = []
        self.search_keyword: str = ""  # 存储搜索关键词

        # 任务控制支持
        self._control_signal: Optional["TaskControlSignal"] = None
        self._checkpoint: Optional["TaskCheckpoint"] = None
        self._current_keyword_index: int = 0
        self._current_dimension_index: int = 0
        self._current_page_index: int = 1  # 当前页码（独立跟踪，确保首次暂停也能保存）

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
        viral_ratio: float = 0.5,
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
            keywords: 搜索关键词列表（最多5个）
            target_count: 目标爬取总数量
            viral_ratio: 爆款比例
            note_type: 笔记类型
            time_range: 时间范围
            min_sample_count: 最低样本量要求
            progress_callback: 进度回调函数

        Returns:
            按互动分数排序并截取的爆款笔记列表
        """
        # 限制关键词数量
        keywords = keywords[:5]
        self.search_keywords = keywords  # 保存多关键词列表

        # 智能调整参数：确保 min_sample_count 不超过合理范围
        expected_analysis = int(target_count * viral_ratio)
        original_min_sample = min_sample_count

        if min_sample_count > target_count:
            min_sample_count = target_count
            logger.info(f"📊 智能调整: 最低样本量 {original_min_sample} → {min_sample_count} (不超过目标数量)")
        elif min_sample_count > expected_analysis:
            min_sample_count = max(expected_analysis, 10)  # 至少保留10条
            logger.info(f"📊 智能调整: 最低样本量 {original_min_sample} → {min_sample_count} (适配预估分析量)")

        logger.info(f"开始多关键词采集: {keywords}")
        logger.info(f"目标数量: {target_count}, 爆款比例: {viral_ratio}, 最低样本量: {min_sample_count}")

        # 计算每个关键词的目标数量
        target_per_keyword = self._calculate_target_per_keyword(
            len(keywords), target_count, viral_ratio, min_sample_count
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
                logger.info("等待 5 秒后采集下一个关键词...")
                await asyncio.sleep(5)

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

        # 应用爆款比例筛选
        viral_count = max(1, int(len(viral_notes) * viral_ratio))
        result = viral_notes[:viral_count]

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
        viral_ratio: float,
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

        # 需要的原始数量（考虑筛选比例和去重损耗）
        raw_needed = min_sample_count / viral_ratio
        safety_factor = 1.3  # 考虑30%去重损耗
        ideal_per_keyword = int((raw_needed * safety_factor) / keyword_count)

        # 计算上限（不超过用户设定总目标的1.5倍分摊）
        upper_limit = max(int(base_per_keyword * 1.5), base_per_keyword)

        # 取两者较大值，但不超过上限
        result = min(max(base_per_keyword, ideal_per_keyword), upper_limit)

        # 确保至少有意义的数量，但最小值也不能突破上限
        result = max(result, min(10, upper_limit))

        # 如果计算结果仍不足以满足样本量需求，记录警告
        estimated_total = result * keyword_count
        estimated_after_filter = int(estimated_total * viral_ratio * 0.7)  # 考虑去重
        if estimated_after_filter < min_sample_count:
            logger.warning(
                f"⚠️ 当前配置预估样本量({estimated_after_filter})不足{min_sample_count}，"
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

            # 维度间等待
            if not await self._check_pause_point():
                break
            await asyncio.sleep(2)

        # 完成当前关键词后重置维度索引
        self._current_dimension_index = 0

        return list(all_notes.values())

    async def search_viral_notes(
        self,
        query: str,
        target_count: int = 100,
        viral_ratio: float = 0.5,
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
            viral_ratio: 爆款比例 (0.5=前1/2, 0.33=前1/3, 0.25=前1/4)
            note_type: 笔记类型
            time_range: 时间范围
            progress_callback: 进度回调函数

        Returns:
            按互动分数排序并截取的爆款笔记列表
        """
        self.search_keyword = query

        logger.info(f"开始并行多维度爬取: {query}")
        logger.info(f"目标数量: {target_count}, 爆款比例: {viral_ratio}")

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
                logger.info(f"等待 3 秒后爬取下一个维度...")
                await asyncio.sleep(3)

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

        # 按比例截取爆款
        viral_count = max(1, int(len(viral_notes) * viral_ratio))

        logger.info(f"按比例 {viral_ratio} 筛选出 {viral_count} 篇爆款笔记")

        if progress_callback:
            progress_callback(100, f"完成！共{len(viral_notes)}篇，筛选出{viral_count}篇爆款")

        self.collected_notes = viral_notes[:viral_count]
        return self.collected_notes

    async def _search_notes_async(self, **kwargs) -> List[Dict]:
        """异步搜索笔记（封装同步方法）"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.client.search_note(
                query=kwargs['query'],
                cookies_str=self.cookies_str,  # 添加 cookies_str 参数
                page=kwargs['page'],
                sort_type_choice=kwargs.get('sort_type', 2),
                note_type=kwargs.get('note_type', 0),
                note_time=kwargs.get('time_range', 0),
                note_range=0,
                pos_distance=0,
                geo=""
            )
        )

        # 解析返回值（返回的是元组：success, msg, res_json）
        if isinstance(result, tuple) and len(result) == 3:
            success, msg, res_json = result
            logger.info(f"搜索API返回: success={success}, msg={msg[:100] if msg else 'None'}")
            if success and res_json and 'data' in res_json:
                # 返回笔记列表
                items = res_json.get('data', {}).get('items', [])
                logger.info(f"搜索返回 {len(items)} 条结果")
                notes = []
                for item in items:
                    if 'note_card' in item:
                        note = item['note_card']
                        # ID和xsec_token都在item顶层
                        note_id = item.get('id')
                        xsec_token = item.get('xsec_token', '')

                        if note_id:
                            # 构造完整的URL，包含必要的参数
                            if xsec_token:
                                note['note_url'] = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_search"
                            else:
                                # 如果没有token，至少加上xsec_source
                                note['note_url'] = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_source=pc_search"

                            note['note_id'] = note_id  # 添加ID到note对象
                            notes.append(note)
                        else:
                            # 如果还是没有ID，打印调试信息
                            logger.debug(f"无法获取笔记ID，item字段: {list(item.keys())}")
                return notes
            else:
                logger.warning(f"搜索失败: {msg}")
                return []
        else:
            logger.error(f"意外的返回格式: {type(result)}")
            return []

    async def _get_note_detail_async(self, note_url: str) -> Optional[Dict]:
        """异步获取笔记详情（封装同步方法）"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.client.get_note_info(note_url, self.cookies_str)
        )

        # 解析返回值（返回的是元组：success, msg, res_json）
        if isinstance(result, tuple) and len(result) == 3:
            success, msg, res_json = result
            if success and res_json and 'data' in res_json:
                # 获取笔记详情
                items = res_json.get('data', {}).get('items', [])
                if items and len(items) > 0:
                    # 使用handle_note_info处理原始数据，正确提取视频URL等字段
                    try:
                        item = items[0]
                        # handle_note_info函数期望数据中有url字段，需要先添加
                        item['url'] = note_url
                        processed_data = handle_note_info(item)
                        return processed_data
                    except Exception as e:
                        logger.warning(f"处理笔记数据失败 {note_url}: {e}，返回原始note_card")
                        # 如果处理失败，返回原始note_card
                        return items[0].get('note_card', {})
                else:
                    # items为空，可能是笔记被删除或不存在
                    logger.debug(f"笔记详情items为空: {note_url}")
            else:
                logger.debug(f"获取笔记详情失败: {msg}")
        else:
            logger.error(f"意外的返回格式: {type(result)}")

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

                # 获取笔记详情
                for note_brief in search_results:
                    note_url = note_brief.get('note_url', '')
                    if not note_url:
                        continue

                    try:
                        note_detail = await self._get_note_detail_async(note_url)
                        if note_detail:
                            notes.append(note_detail)
                            if len(notes) >= target_per_dimension:
                                break
                    except Exception as e:
                        logger.debug(f"[{dimension_name}] 获取详情失败: {e}")
                        continue

                    await asyncio.sleep(1)  # 防止请求过快

                page += 1
                await asyncio.sleep(2)  # 页面间隔

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
                await asyncio.sleep(5)

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
            await asyncio.sleep(2)

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

                    await asyncio.sleep(1)

                # 如果连续低于阈值，退出分页循环
                if consecutive_below_threshold >= 10:
                    break

                page += 1
                await asyncio.sleep(2)

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

    async def search_viral_notes_unified(
        self,
        keywords: List[str],
        search_mode: str = "ratio",
        # 比例模式参数
        target_count: int = 100,
        viral_ratio: float = 0.5,
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
            viral_ratio: 爆款比例（比例模式）
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
                viral_ratio=viral_ratio,
                note_type=note_type,
                time_range=time_range,
                min_sample_count=min_sample_count,
                progress_callback=progress_callback
            )