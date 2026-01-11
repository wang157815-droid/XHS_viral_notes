"""
音画同步分析器
将帧图片与语音内容对齐，检测关键事件
"""
from typing import List, Dict, Any, Optional
from loguru import logger

from viral_agent.models.av_sync_model import (
    VideoFrame, TranscriptSegment, SyncedMoment, TimelineSummary
)


class SyncAnalyzer:
    """音画同步分析器"""

    # 产品相关关键词
    PRODUCT_KEYWORDS = [
        '这个', '这款', '推荐', '产品', '精华', '面霜', '乳液',
        '眼霜', '水乳', '防脱', '洗发', '护发', '效果', '成分',
        '用了', '涂上', '抹上', '买了', '入手', '回购', '好用'
    ]

    # 内容开始关键词
    CONTENT_START_KEYWORDS = [
        '首先', '第一', '开始', '教大家', '告诉你', '分享',
        '今天', '方法', '技巧', '步骤', '教程', '干货', '重点'
    ]

    # 产品使用关键词
    PRODUCT_USE_KEYWORDS = [
        '用', '涂', '抹', '按摩', '敷', '喷', '挤', '取',
        '使用', '上脸', '上手', '效果', '感觉', '质地'
    ]

    def align(
        self,
        frames: List[VideoFrame],
        transcript: List[TranscriptSegment]
    ) -> List[SyncedMoment]:
        """
        将帧与语音对齐

        Args:
            frames: 视频帧列表
            transcript: 转录片段列表

        Returns:
            同步时刻列表
        """
        synced = []

        for frame in frames:
            ts = frame.timestamp

            # 找到该时刻对应的转录片段
            matching_segment = self._find_segment_at_time(transcript, ts)

            # 提取该时刻的关键词
            keywords = []
            if matching_segment:
                keywords = self._extract_keywords(matching_segment.text)

            synced.append(SyncedMoment(
                timestamp=ts,
                frame=frame,
                transcript_segment=matching_segment,
                keywords=keywords,
                event_type=""
            ))

        logger.info(f"音画对齐完成: {len(synced)} 个同步点")
        return synced

    def _find_segment_at_time(
        self,
        transcript: List[TranscriptSegment],
        timestamp: float,
        tolerance: float = 2.5
    ) -> Optional[TranscriptSegment]:
        """
        找到指定时间点的转录片段

        Args:
            transcript: 转录片段列表
            timestamp: 目标时间点
            tolerance: 容差范围（秒）
        """
        # 精确匹配
        for segment in transcript:
            if segment.start_time <= timestamp <= segment.end_time:
                return segment

        # 模糊匹配（在容差范围内）
        for segment in transcript:
            if abs(segment.start_time - timestamp) <= tolerance:
                return segment
            if abs(segment.end_time - timestamp) <= tolerance:
                return segment

        return None

    def detect_key_moments(
        self,
        synced_timeline: List[SyncedMoment],
        title: str = "",
        description: str = ""
    ) -> List[SyncedMoment]:
        """
        检测关键事件时刻

        Args:
            synced_timeline: 同步时间轴
            title: 视频标题
            description: 视频描述

        Returns:
            标记了 event_type 的关键时刻列表
        """
        key_moments = []

        for moment in synced_timeline:
            if not moment.transcript_segment:
                continue

            text = moment.transcript_segment.text

            # 检测产品提及
            if self._is_product_mention(text):
                moment.event_type = "product_mention"
                key_moments.append(moment)

            # 检测内容开始
            elif self._is_content_start(text):
                moment.event_type = "content_start"
                key_moments.append(moment)

            # 检测产品使用
            elif self._is_product_use(text):
                moment.event_type = "product_use"
                key_moments.append(moment)

        logger.info(f"检测到 {len(key_moments)} 个关键事件")
        return key_moments

    def _is_product_mention(self, text: str) -> bool:
        """检测是否是产品提及"""
        count = sum(1 for kw in self.PRODUCT_KEYWORDS if kw in text)
        return count >= 2  # 至少包含 2 个关键词

    def _is_content_start(self, text: str) -> bool:
        """检测是否是内容开始"""
        return any(kw in text for kw in self.CONTENT_START_KEYWORDS)

    def _is_product_use(self, text: str) -> bool:
        """检测是否是产品使用"""
        count = sum(1 for kw in self.PRODUCT_USE_KEYWORDS if kw in text)
        return count >= 2

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词（简单分词）"""
        # 简单的关键词提取，避免引入 jieba 依赖
        keywords = []

        # 检测产品关键词
        for kw in self.PRODUCT_KEYWORDS:
            if kw in text and kw not in keywords:
                keywords.append(kw)

        # 检测内容关键词
        for kw in self.CONTENT_START_KEYWORDS:
            if kw in text and kw not in keywords:
                keywords.append(kw)

        return keywords[:5]

    def generate_timeline_summary(
        self,
        synced_timeline: List[SyncedMoment],
        key_moments: List[SyncedMoment]
    ) -> TimelineSummary:
        """
        生成时间轴摘要

        Args:
            synced_timeline: 完整同步时间轴
            key_moments: 关键事件列表

        Returns:
            时间轴摘要
        """
        summary = TimelineSummary()

        # 计算总时长
        if synced_timeline:
            summary.total_duration = synced_timeline[-1].timestamp

        # 收集完整转录
        texts = []
        for moment in synced_timeline:
            if moment.transcript_segment:
                texts.append(moment.transcript_segment.text)
        summary.full_transcript = ' '.join(texts)

        # 提取关键事件时间
        for moment in key_moments:
            event = {
                'timestamp': moment.timestamp,
                'type': moment.event_type,
                'text': moment.transcript_segment.text if moment.transcript_segment else ""
            }
            summary.key_events.append(event)

            # 记录首次出现的时间点
            if moment.event_type == "product_mention" and summary.product_first_mention is None:
                summary.product_first_mention = moment.timestamp
            elif moment.event_type == "product_use" and summary.product_use_time is None:
                summary.product_use_time = moment.timestamp
            elif moment.event_type == "content_start" and summary.content_start_time is None:
                summary.content_start_time = moment.timestamp

        logger.info(f"生成时间轴摘要: 产品首次提及={summary.product_first_mention}s, "
                    f"产品使用={summary.product_use_time}s, "
                    f"内容开始={summary.content_start_time}s")

        return summary
