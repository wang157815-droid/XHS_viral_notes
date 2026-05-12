"""
视频产品深度分析器
扩展版的产品分析服务，支持12个数据点（A-L）
与原有的video_timeline_analyzer并行工作，提供更细致的产品植入分析
"""

import asyncio
from typing import Dict, List, Any, Optional
from loguru import logger

from viral_agent.prompts.video_product_prompts import (
    get_product_analysis_prompt,
    parse_product_analysis,
    extract_product_features
)
from viral_agent.services.video.video_product_stats import (
    ProductStatsGenerator,
    get_default_analysis,
    get_empty_stats
)


class VideoProductAnalyzer:
    """
    视频产品深度分析器

    支持12个数据点的完整产品分析：
    - 时间维度 A-C：产品出现时间、使用时间、干货开始时间
    - 分类维度 D-G：内容类型、切入点、引出方式、植入方式
    - 营销维度 H-L：营销场景、提及次数、品牌可见性、CTA类型、CTA时间
    """

    def __init__(self, ai_analyzer=None):
        """
        初始化产品分析器

        Args:
            ai_analyzer: AI分析器实例（需要支持视频分析）
        """
        self.ai_analyzer = ai_analyzer
        self.analysis_cache = {}
        self.stats_generator = ProductStatsGenerator()

    async def analyze_product(
        self,
        video_url: str,
        title: str = None,
        description: str = None,
        duration: int = None
    ) -> Dict[str, Any]:
        """
        分析单个视频的产品植入策略

        Args:
            video_url: 视频URL
            title: 视频标题
            description: 视频描述
            duration: 视频时长（秒）

        Returns:
            产品分析结果（12个数据点）
        """
        try:
            # 检查缓存
            cache_key = f"product_{video_url}_{title}"
            if cache_key in self.analysis_cache:
                return self.analysis_cache[cache_key]

            # 如果没有AI分析器，返回默认结果
            if not self.ai_analyzer:
                logger.warning("AI分析器未配置，使用默认产品分析")
                return get_default_analysis()

            # 获取基础提示词
            base_prompt = get_product_analysis_prompt(
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
                    query=f"{title} 产品植入方式 产品引出技巧 营销场景 品牌露出"
                )
                logger.debug("产品分析提示词已使用知识库增强")
            else:
                prompt = base_prompt

            # 调用AI分析视频
            result = await self.ai_analyzer.analyze_video(
                video_url=video_url,
                prompt=prompt,
                title=title,
                description=description
            )

            # 解析结果（12个数据点）
            analysis = parse_product_analysis(result)

            # 提取特征
            if analysis.get('success'):
                features = extract_product_features(analysis)
                analysis['features'] = features

            # 保存原始AI分析结果
            analysis['raw_ai_result'] = result

            # 缓存结果
            self.analysis_cache[cache_key] = analysis

            return analysis

        except Exception as e:
            logger.error(f"视频产品分析失败: {e}")
            return get_default_analysis(error=str(e))

    async def analyze_batch(
        self,
        notes: List[Dict[str, Any]],
        max_concurrent: int = 3
    ) -> Dict[str, Any]:
        """
        批量分析视频产品植入策略

        Args:
            notes: 视频笔记列表
            max_concurrent: 最大并发数

        Returns:
            批量分析结果和统计
        """
        logger.info(f"开始批量分析 {len(notes)} 个视频的产品策略...")

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
                return await self.analyze_product(
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

        logger.success(f"产品分析完成：成功 {len(valid_analyses)}/{len(video_notes)} 个")

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
