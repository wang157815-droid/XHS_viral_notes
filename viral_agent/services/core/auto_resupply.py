"""
自动补采服务

当采集样本量不足时，自动执行补采策略：
1. 阶段1：降低筛选比例（不重新爬取，零成本）
2. 阶段2：扩大采集数量（重新爬取）
3. 阶段3：放宽时间范围（可选，仅当用户指定了时间限制）
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
    """

    # 阶段1：筛选比例调整序列（从0.5逐步放宽）
    RATIO_SEQUENCE = [0.7, 1.0]

    # 阶段2：采集数量倍率
    TARGET_MULTIPLIERS = [1.5, 2.0]

    # 阶段3：时间范围放宽序列（0=不限，最宽松）
    TIME_RANGE_SEQUENCE = [2, 3, 0]  # 一周→半年→不限
    TIME_RANGE_NAMES = {0: "不限", 1: "一天内", 2: "一周内", 3: "半年内"}

    # 采集数量硬上限（与API限制保持一致）
    MAX_TARGET_COUNT = 800

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
