"""
自动补采服务

当采集样本量不足时，自动执行补采策略：
1. 阶段1：降低筛选比例（不重新爬取，零成本）
2. 阶段2：扩大采集数量（重新爬取）
3. 阶段3：放宽时间范围（可选，仅当用户指定了时间限制）
4. 阶段4：AI 关键词扩展（新增，生成更宽泛的关键词进行补采）
"""

from dataclasses import dataclass
from typing import List, Optional, Callable, Tuple, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from viral_agent.models.viral_note import ViralNote
    from viral_agent.services.core.viral_collector import ViralCollector


@dataclass
class ResupplyResult:
    """补采结果"""
    success: bool              # 是否满足最低样本量
    final_count: int           # 最终笔记数量
    phases_executed: List[str] # 执行的补采阶段
    final_viral_ratio: float   # 最终使用的筛选比例
    extra_collected: int       # 额外采集的数量（阶段2/3）
    message: str               # 结果描述


class AutoResupplyService:
    """
    自动补采服务

    策略说明：
    - 阶段1：降低筛选比例（0.5 → 0.7 → 1.0），不重新爬取
    - 阶段2：扩大采集数量（×1.5 → ×2.0），需要重新爬取
    - 阶段3：放宽时间范围（一周→半年→不限），仅当用户限制了时间
    - 阶段4：AI 关键词扩展（生成更宽泛的关键词进行补采）
    """

    # 阶段1：筛选比例调整序列（从0.5逐步放宽）
    RATIO_SEQUENCE = [0.7, 1.0]

    # 阶段2：采集数量倍率
    TARGET_MULTIPLIERS = [1.5, 2.0]

    # 阶段3：时间范围放宽序列（0=不限，最宽松）
    TIME_RANGE_SEQUENCE = [2, 3, 0]  # 一周→半年→不限
    TIME_RANGE_NAMES = {0: "不限", 1: "一天内", 2: "一周内", 3: "半年内"}

    # 采集数量硬上限（与API限制保持一致，阈值模式可达1000）
    MAX_TARGET_COUNT = 1000

    # 阶段4：AI 关键词扩展配置
    MAX_EXPANSION_ROUNDS = 2      # 最多扩展 2 轮
    EXPANSION_BATCH_SIZE = 3      # 每轮扩展 3 个关键词
    EXPANSION_TARGET_PER_KW = 50  # 每个扩展关键词采集 50 条

    def __init__(self, max_resupply_rounds: int = 3):
        """
        Args:
            max_resupply_rounds: 最大补采轮数（防止无限循环）
        """
        self.max_rounds = max_resupply_rounds

    async def execute_resupply(
        self,
        all_notes_before_filter: List["ViralNote"],
        current_viral_ratio: float,
        min_sample_count: int,
        collector: "ViralCollector",
        keywords: List[str],
        original_target_count: int,
        note_type: int,
        time_range: int,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Tuple[List["ViralNote"], ResupplyResult]:
        """
        执行自动补采（全流程在单一方法中，避免数据不同步问题）
        """
        phases_executed: List[str] = []
        extra_collected = 0
        current_notes = list(all_notes_before_filter)
        current_ratio = current_viral_ratio
        current_time_range = time_range
        original_count = len(current_notes)

        # ===== 阶段1：降低筛选比例（零成本） =====
        for new_ratio in self.RATIO_SEQUENCE:
            if new_ratio <= current_ratio:
                continue

            phases_executed.append(f"阶段1: 筛选比例 {current_ratio} → {new_ratio}")
            logger.info(f"🔄 补采阶段1: 降低筛选比例 {current_ratio} → {new_ratio}")

            if progress_callback:
                progress_callback(88, f"补采中: 调整筛选比例至 {int(new_ratio*100)}%")

            current_ratio = new_ratio
            filtered = self._apply_filter(current_notes, current_ratio)

            if len(filtered) >= min_sample_count:
                logger.success(f"✅ 阶段1成功: {len(filtered)} 篇 >= {min_sample_count}")
                return filtered, ResupplyResult(
                    success=True,
                    final_count=len(filtered),
                    phases_executed=phases_executed,
                    final_viral_ratio=current_ratio,
                    extra_collected=0,
                    message=f"通过降低筛选比例至 {int(current_ratio*100)}%，满足样本量要求"
                )

        # 阶段1完成后，current_ratio 已经是 1.0
        current_ratio = 1.0

        # ===== 阶段2：扩大采集数量 =====
        for multiplier in self.TARGET_MULTIPLIERS:
            # 计算新目标，但不超过上限
            raw_target = int(original_target_count * multiplier)
            new_target = min(raw_target, self.MAX_TARGET_COUNT)

            # 如果已达上限且之前已尝试过，跳过
            if new_target <= original_target_count:
                continue

            phases_executed.append(f"阶段2: 采集目标 {original_target_count} → {new_target}")
            logger.info(f"🔄 补采阶段2: 扩大采集目标 → {new_target}")

            if progress_callback:
                progress_callback(90, f"补采中: 扩大采集至 {new_target} 条")

            # 重新采集（不筛选，后面统一筛选）
            new_notes = await collector.search_viral_notes_multi_keywords(
                keywords=keywords,
                target_count=new_target,
                viral_ratio=1.0,
                note_type=note_type,
                time_range=current_time_range,
                min_sample_count=min_sample_count,
                progress_callback=None
            )

            # 更新状态（关键：确保数据同步）
            added = max(0, len(new_notes) - len(current_notes))
            extra_collected += added
            current_notes = new_notes
            filtered = self._apply_filter(current_notes, current_ratio)

            logger.info(f"   阶段2本轮: 新增 {added} 条，当前共 {len(current_notes)} 条")

            if len(filtered) >= min_sample_count:
                logger.success(f"✅ 阶段2成功: {len(filtered)} 篇 >= {min_sample_count}")
                return filtered, ResupplyResult(
                    success=True,
                    final_count=len(filtered),
                    phases_executed=phases_executed,
                    final_viral_ratio=current_ratio,
                    extra_collected=extra_collected,
                    message=f"通过扩大采集至 {new_target} 条，满足样本量要求"
                )

            # 如果已达上限，不再尝试更大倍率
            if new_target >= self.MAX_TARGET_COUNT:
                logger.info(f"   已达采集上限 {self.MAX_TARGET_COUNT}，跳过更高倍率")
                break

        # ===== 阶段3：放宽时间范围（仅当用户指定了时间限制） =====
        if time_range > 0:
            for new_time_range in self.TIME_RANGE_SEQUENCE:
                if new_time_range <= current_time_range and new_time_range != 0:
                    continue

                old_name = self.TIME_RANGE_NAMES.get(current_time_range, "?")
                new_name = self.TIME_RANGE_NAMES.get(new_time_range, "?")
                phases_executed.append(f"阶段3: 时间范围 {old_name} → {new_name}")
                logger.info(f"🔄 补采阶段3: 放宽时间范围 → {new_name}")

                if progress_callback:
                    progress_callback(92, f"补采中: 放宽时间范围至 {new_name}")

                current_time_range = new_time_range
                # 阶段3也使用上限约束
                new_target = min(
                    int(original_target_count * 2.0),
                    self.MAX_TARGET_COUNT
                )

                new_notes = await collector.search_viral_notes_multi_keywords(
                    keywords=keywords,
                    target_count=new_target,
                    viral_ratio=1.0,
                    note_type=note_type,
                    time_range=current_time_range,
                    min_sample_count=min_sample_count,
                    progress_callback=None
                )

                # 更新状态（关键：确保数据同步）
                added = max(0, len(new_notes) - len(current_notes))
                extra_collected += added
                current_notes = new_notes
                filtered = self._apply_filter(current_notes, current_ratio)

                logger.info(f"   阶段3本轮: 新增 {added} 条，当前共 {len(current_notes)} 条")

                if len(filtered) >= min_sample_count:
                    logger.success(f"✅ 阶段3成功: {len(filtered)} 篇 >= {min_sample_count}")
                    return filtered, ResupplyResult(
                        success=True,
                        final_count=len(filtered),
                        phases_executed=phases_executed,
                        final_viral_ratio=current_ratio,
                        extra_collected=extra_collected,
                        message=f"通过放宽时间范围至{new_name}，满足样本量要求"
                    )

                if new_time_range == 0:  # 已是最宽松，退出
                    break

        # 所有阶段都未能满足，返回当前最佳结果
        filtered = self._apply_filter(current_notes, current_ratio)
        logger.warning(
            f"⚠️ 补采完成但仍不足: {len(filtered)} 篇 < {min_sample_count} "
            f"(额外采集 {extra_collected} 条)"
        )

        return filtered, ResupplyResult(
            success=False,
            final_count=len(filtered),
            phases_executed=phases_executed,
            final_viral_ratio=current_ratio,
            extra_collected=extra_collected,
            message=f"已执行所有补采策略，最终 {len(filtered)} 篇，仍低于 {min_sample_count} 篇要求"
        )

    def _apply_filter(
        self,
        notes: List["ViralNote"],
        viral_ratio: float
    ) -> List["ViralNote"]:
        """应用筛选比例，返回互动分数最高的前N%笔记"""
        if not notes:
            return []
        notes_sorted = sorted(notes, key=lambda x: x.interaction_score, reverse=True)
        count = max(1, int(len(notes_sorted) * viral_ratio))
        return notes_sorted[:count]

    # ==================== 阶段4：AI 关键词扩展（新增） ====================

    async def execute_resupply_with_expansion(
        self,
        all_notes_before_filter: List["ViralNote"],
        current_viral_ratio: float,
        min_sample_count: int,
        collector: "ViralCollector",
        keywords: List[str],
        original_target_count: int,
        note_type: int,
        time_range: int,
        search_mode: str = "ratio",
        min_interaction: int = 5000,
        enable_ai_expansion: bool = True,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Tuple[List["ViralNote"], ResupplyResult]:
        """
        执行自动补采（含 AI 关键词扩展）

        先执行原有阶段 1-3，如果仍不满足且启用了 AI 扩展，
        则进入阶段 4 使用 AI 生成扩展关键词进行补采。

        Args:
            all_notes_before_filter: 筛选前的全部笔记
            current_viral_ratio: 当前筛选比例
            min_sample_count: 最低样本量要求
            collector: 采集器实例
            keywords: 原始关键词列表
            original_target_count: 原始目标数量
            note_type: 笔记类型
            time_range: 时间范围
            search_mode: 搜索模式（ratio/threshold）
            min_interaction: 互动量阈值（阈值模式）
            enable_ai_expansion: 是否启用 AI 关键词扩展
            progress_callback: 进度回调函数

        Returns:
            (笔记列表, 补采结果)
        """
        # 先执行原有阶段 1-3
        notes, result = await self.execute_resupply(
            all_notes_before_filter=all_notes_before_filter,
            current_viral_ratio=current_viral_ratio,
            min_sample_count=min_sample_count,
            collector=collector,
            keywords=keywords,
            original_target_count=original_target_count,
            note_type=note_type,
            time_range=time_range,
            progress_callback=progress_callback
        )

        # 【关键】阈值模式：过滤掉低于阈值的笔记
        # 因为阶段1-3使用比例模式采集，可能混入低于阈值的笔记
        if search_mode == "threshold" and min_interaction:
            before_filter = len(notes)
            notes = [n for n in notes if n.interaction_score >= min_interaction]
            filtered_count = before_filter - len(notes)
            if filtered_count > 0:
                logger.info(
                    f"🔥 阈值过滤：移除 {filtered_count} 篇低于 {min_interaction} 的笔记，"
                    f"剩余 {len(notes)} 篇"
                )
            # 更新结果中的最终数量
            result.final_count = len(notes)
            result.success = len(notes) >= min_sample_count

        # 如果已满足要求或未启用 AI 扩展，直接返回
        if result.success or not enable_ai_expansion:
            return notes, result

        # 阶段 4：AI 关键词扩展
        logger.info("🤖 阶段 1-3 未能满足样本量，启动阶段 4: AI 关键词扩展")

        return await self._execute_keyword_expansion(
            current_notes=notes,
            original_keywords=keywords,
            min_sample_count=min_sample_count,
            collector=collector,
            note_type=note_type,
            time_range=time_range,
            search_mode=search_mode,
            min_interaction=min_interaction,
            phases_executed=result.phases_executed.copy(),
            extra_collected=result.extra_collected,
            progress_callback=progress_callback
        )

    async def _execute_keyword_expansion(
        self,
        current_notes: List["ViralNote"],
        original_keywords: List[str],
        min_sample_count: int,
        collector: "ViralCollector",
        note_type: int,
        time_range: int,
        search_mode: str,
        min_interaction: int,
        phases_executed: List[str],
        extra_collected: int,
        progress_callback: Optional[Callable[[int, str], None]]
    ) -> Tuple[List["ViralNote"], ResupplyResult]:
        """
        阶段 4：AI 关键词扩展补采

        流程：
        1. 调用 KeywordExpander 生成扩展关键词
        2. 用扩展关键词采集新笔记
        3. 合并去重
        4. 判断是否满足样本量，否则继续下一轮
        """
        try:
            from viral_agent.services.keyword import KeywordExpander
        except ImportError:
            logger.warning("KeywordExpander 服务不可用，跳过阶段 4")
            return current_notes, ResupplyResult(
                success=False,
                final_count=len(current_notes),
                phases_executed=phases_executed,
                final_viral_ratio=1.0,
                extra_collected=extra_collected,
                message="AI 关键词扩展服务不可用"
            )

        expander = KeywordExpander()
        if not expander.client:
            logger.warning("AI 服务未配置，跳过阶段 4")
            return current_notes, ResupplyResult(
                success=False,
                final_count=len(current_notes),
                phases_executed=phases_executed,
                final_viral_ratio=1.0,
                extra_collected=extra_collected,
                message="AI 服务未配置，无法执行关键词扩展"
            )

        # 已尝试过的关键词
        tried_keywords = set(original_keywords)
        all_notes = list(current_notes)
        existing_ids = {n.note_id for n in all_notes}
        expansion_round = 0

        while len(all_notes) < min_sample_count and expansion_round < self.MAX_EXPANSION_ROUNDS:
            expansion_round += 1
            phases_executed.append(f"阶段4: AI关键词扩展 (第{expansion_round}轮)")
            logger.info(f"🔄 补采阶段4: AI关键词扩展 (第{expansion_round}轮)")

            if progress_callback:
                progress_callback(
                    93 + expansion_round,
                    f"AI关键词扩展中... (第{expansion_round}轮)"
                )

            # 生成上下文（使用已采集笔记的标题）
            context = self._build_expansion_context(all_notes[:10])

            # 调用 AI 扩展
            expansion_result = await expander.expand_keywords(
                original_keywords=original_keywords,
                context=context,
                max_expansion=self.EXPANSION_BATCH_SIZE,
                exclude_keywords=list(tried_keywords)
            )

            if not expansion_result.success:
                logger.warning(f"AI 扩展失败: {expansion_result.error_message}")
                break

            # 提取新关键词
            new_keywords = [
                ek.keyword for ek in expansion_result.expanded_keywords
                if ek.keyword not in tried_keywords
            ]

            if not new_keywords:
                logger.info("没有新的扩展关键词，停止扩展")
                break

            logger.info(f"   扩展关键词: {new_keywords}")
            tried_keywords.update(new_keywords)

            # 用扩展关键词采集
            new_notes = await collector.search_viral_notes_unified(
                keywords=new_keywords,
                search_mode=search_mode,
                target_count=self.EXPANSION_TARGET_PER_KW * len(new_keywords),
                viral_ratio=1.0,
                min_sample_count=1,  # 不限制最低样本
                min_interaction=min_interaction,
                max_collect_count=self.EXPANSION_TARGET_PER_KW * len(new_keywords),
                note_type=note_type,
                time_range=time_range,
                progress_callback=None
            )

            # 合并去重（阈值模式下额外检查互动分）
            added_count = 0
            for note in new_notes:
                if note.note_id not in existing_ids:
                    # 阈值模式：确保笔记符合阈值要求
                    if search_mode == "threshold" and min_interaction:
                        if note.interaction_score < min_interaction:
                            continue  # 跳过低于阈值的笔记
                    all_notes.append(note)
                    existing_ids.add(note.note_id)
                    added_count += 1

            extra_collected += added_count
            logger.info(
                f"   扩展关键词新增 {added_count} 条，当前共 {len(all_notes)} 条"
            )

            # 检查是否满足
            if len(all_notes) >= min_sample_count:
                logger.success(
                    f"✅ 阶段4成功: {len(all_notes)} 篇 >= {min_sample_count}"
                )
                return all_notes, ResupplyResult(
                    success=True,
                    final_count=len(all_notes),
                    phases_executed=phases_executed,
                    final_viral_ratio=1.0,
                    extra_collected=extra_collected,
                    message=f"通过 AI 关键词扩展（{expansion_round}轮），满足样本量要求"
                )

        # 所有扩展轮次完成但仍不足
        logger.warning(
            f"⚠️ 阶段4完成但仍不足: {len(all_notes)} 篇 < {min_sample_count}"
        )
        return all_notes, ResupplyResult(
            success=False,
            final_count=len(all_notes),
            phases_executed=phases_executed,
            final_viral_ratio=1.0,
            extra_collected=extra_collected,
            message=f"已执行 AI 扩展{expansion_round}轮，最终 {len(all_notes)} 篇"
        )

    def _build_expansion_context(self, notes: List["ViralNote"]) -> str:
        """
        构建关键词扩展的上下文（使用已采集笔记的标题）

        Args:
            notes: 已采集的笔记列表

        Returns:
            上下文字符串
        """
        if not notes:
            return ""

        titles = [note.title for note in notes[:10] if note.title]
        if not titles:
            return ""

        return f"已采集的爆款笔记标题示例：\n" + "\n".join(f"- {t}" for t in titles)
