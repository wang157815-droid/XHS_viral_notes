"""
视频内容质量分析器
对标图文笔记的4维分析：开场钩子/结构/情感/收尾
需要AI视觉模型（如qwen-vl、glm-4v）观看视频进行分析
"""

import asyncio
from typing import Dict, List, Any, Optional
from loguru import logger

from viral_agent.prompts.video_content_prompts import (
    get_content_analysis_prompt,
    parse_content_analysis,
    extract_content_features,
    get_quick_analysis_prompt,
    parse_quick_analysis
)
from viral_agent.services.video.video_content_stats import (
    ContentStatsGenerator,
    get_default_analysis,
    get_empty_stats
)


class VideoContentAnalyzer:
    """
    视频内容质量分析器

    分析4个核心维度：
    1. 开场钩子：类型、强度、视觉冲击
    2. 内容结构：结构类型、节奏、转场
    3. 情感节奏：情感曲线、共鸣点、信任建立
    4. 收尾引导：CTA类型、时机、记忆点
    """

    def __init__(self, ai_analyzer=None, quick_mode: bool = False):
        """
        初始化内容质量分析器

        Args:
            ai_analyzer: AI视觉分析器实例（需要支持视频分析）
            quick_mode: 是否使用快速分析模式（降低成本但精度略低）
        """
        self.ai_analyzer = ai_analyzer
        self.quick_mode = quick_mode
        self.analysis_cache = {}
        self.stats_generator = ContentStatsGenerator()

    async def analyze_content(
        self,
        video_url: str,
        title: str = None,
        description: str = None,
        duration: int = None
    ) -> Dict[str, Any]:
        """
        分析单个视频的内容质量

        Args:
            video_url: 视频URL
            title: 视频标题
            description: 视频描述
            duration: 视频时长（秒）

        Returns:
            内容质量分析结果
        """
        try:
            # 检查缓存
            cache_key = f"content_{video_url}_{title}"
            if cache_key in self.analysis_cache:
                return self.analysis_cache[cache_key]

            # 如果没有AI分析器，返回默认结果
            if not self.ai_analyzer:
                logger.warning("AI视觉分析器未配置，使用默认分析")
                return get_default_analysis()

            # 根据模式选择基础提示词
            if self.quick_mode:
                base_prompt = get_quick_analysis_prompt(title)
            else:
                base_prompt = get_content_analysis_prompt(
                    video_url=video_url,
                    title=title,
                    description=description,
                    duration=duration
                )

            # 使用知识库增强提示词（RAG）
            if hasattr(self.ai_analyzer, 'enhance_prompt_with_knowledge'):
                prompt = self.ai_analyzer.enhance_prompt_with_knowledge(
                    base_prompt=base_prompt,
                    title=title or "",
                    description=description or "",
                    query=f"{title} 视频开场钩子 内容结构 情感节奏 收尾引导"
                )
                logger.debug("内容分析提示词已使用知识库增强")
            else:
                prompt = base_prompt

            # 调用AI分析视频
            result = await self.ai_analyzer.analyze_video(
                video_url=video_url,
                prompt=prompt,
                title=title,
                description=description
            )

            # 解析结果
            if self.quick_mode:
                analysis = parse_quick_analysis(result)
                analysis = {'success': True, 'data': analysis, 'mode': 'quick'}
            else:
                analysis = parse_content_analysis(result)

            # 提取特征
            if analysis.get('success'):
                features = extract_content_features(analysis)
                analysis['features'] = features

            # 保存原始AI分析结果
            analysis['raw_ai_result'] = result

            # 缓存结果
            self.analysis_cache[cache_key] = analysis

            return analysis

        except Exception as e:
            logger.error(f"视频内容分析失败: {e}")
            return get_default_analysis(error=str(e))

    async def analyze_batch(
        self,
        notes: List[Dict[str, Any]],
        max_concurrent: int = 3
    ) -> Dict[str, Any]:
        """
        批量分析视频内容质量

        Args:
            notes: 视频笔记列表
            max_concurrent: 最大并发数

        Returns:
            批量分析结果和统计
        """
        logger.info(f"开始批量分析 {len(notes)} 个视频的内容质量...")

        # 筛选视频笔记
        video_notes = [
            n for n in notes
            if n.get('note_type') == '视频' and n.get('video_addr')
        ]

        if not video_notes:
            logger.warning("没有找到视频笔记")
            return self._get_empty_batch_result()

        logger.info(f"找到 {len(video_notes)} 个视频笔记")

        # 准备分析任务
        semaphore = asyncio.Semaphore(max_concurrent)

        async def analyze_with_semaphore(note):
            async with semaphore:
                return await self.analyze_content(
                    video_url=note.get('video_addr'),
                    title=note.get('title'),
                    description=note.get('desc'),
                    duration=note.get('duration')
                )

        # 执行批量分析（使用安全包装器，兼容 uvloop）
        from viral_agent.utils.async_utils import safe_nest_asyncio_apply
        safe_nest_asyncio_apply()
        tasks = [analyze_with_semaphore(note) for note in video_notes]
        analyses = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        valid_analyses = []
        error_count = 0

        for i, result in enumerate(analyses):
            if isinstance(result, Exception):
                logger.error(f"分析第{i+1}个视频失败: {result}")
                error_count += 1
            elif result.get('success', False):
                valid_analyses.append(result)
            else:
                error_count += 1

        # 使用统计生成器计算统计
        stats = self.stats_generator.calculate_statistics(valid_analyses, video_notes)
        stats['total_videos'] = len(video_notes)
        stats['success_count'] = len(valid_analyses)
        stats['error_count'] = error_count

        # 添加详细分析结果
        stats['individual_analyses'] = [
            {
                'note_id': video_notes[i].get('note_id'),
                'title': video_notes[i].get('title', '')[:50],
                'analysis': analyses[i] if not isinstance(analyses[i], Exception) else {
                    'error': str(analyses[i])
                }
            }
            for i in range(len(video_notes))
        ]

        logger.success(f"内容分析完成：成功 {len(valid_analyses)}/{len(video_notes)} 个")

        return stats

    def _get_empty_batch_result(self) -> Dict[str, Any]:
        """获取空批量结果"""
        return {
            'total_videos': 0,
            'success_count': 0,
            'error_count': 0,
            'individual_analyses': [],
            **get_empty_stats()
        }
