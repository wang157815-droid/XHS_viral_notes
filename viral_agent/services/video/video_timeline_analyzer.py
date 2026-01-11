"""
视频时间轴分析器
用于提取小红书视频笔记的7个关键时间节点和内容特征
"""
import asyncio
from typing import Dict, List, Any, Optional, Tuple
from loguru import logger
from collections import Counter, defaultdict
import re

from viral_agent.prompts.video_timeline_prompts import (
    get_timeline_analysis_prompt,
    parse_timeline_analysis,
    extract_timeline_features
)


class VideoTimelineAnalyzer:
    """视频时间轴分析器"""

    def __init__(self, ai_analyzer=None):
        """
        初始化时间轴分析器

        Args:
            ai_analyzer: AI分析器实例（可选）
        """
        self.ai_analyzer = ai_analyzer
        self.analysis_cache = {}

    async def analyze_timeline(
        self,
        video_url: str,
        title: str = None,
        description: str = None,
        video_urls: List[Dict[str, Any]] = None,
        note_id: str = None
    ) -> Dict[str, Any]:
        """
        分析单个视频的时间轴

        Args:
            video_url: 视频URL
            title: 视频标题（可选）
            description: 视频描述（可选）
            video_urls: 备选视频URL列表（P0-4新增，用于多URL兜底）
            note_id: 笔记ID（P1-download: 用于下载共享缓存键）

        Returns:
            时间轴分析结果
        """
        try:
            # 检查缓存
            cache_key = f"{video_url}_{title}"
            if cache_key in self.analysis_cache:
                return self.analysis_cache[cache_key]

            # 如果没有AI分析器，返回默认结果
            if not self.ai_analyzer:
                logger.warning("AI分析器未配置，使用默认分析")
                return self._get_default_analysis()

            # 获取基础提示词
            base_prompt = get_timeline_analysis_prompt(video_url, title)

            # 使用知识库增强提示词（RAG + JSON配置）
            if hasattr(self.ai_analyzer, 'enhance_prompt_with_knowledge'):
                prompt = self.ai_analyzer.enhance_prompt_with_knowledge(
                    base_prompt=base_prompt,
                    title=title or "",
                    description=description or "",
                    query=f"{title} 视频时间节点 产品出现时间 干货开始时间"
                )
                logger.debug("时间轴分析提示词已使用知识库增强")
            else:
                prompt = base_prompt

            # 调用AI分析视频（P0-4：透传video_urls实现多URL兜底，P1-download: 透传note_id实现下载共享）
            result = await self.ai_analyzer.analyze_video(
                video_url=video_url,
                prompt=prompt,
                title=title,
                description=description,
                video_urls=video_urls,
                note_id=note_id
            )

            # 解析结果
            analysis = parse_timeline_analysis(result)

            # 提取特征
            features = extract_timeline_features(analysis)
            analysis['features'] = features

            # 保存原始AI分析结果（重要！供导出时显示）
            analysis['raw_ai_analysis'] = result

            # 缓存结果
            self.analysis_cache[cache_key] = analysis

            return analysis

        except Exception as e:
            logger.error(f"时间轴分析失败: {e}")
            return self._get_default_analysis(error=str(e))

    def analyze_timelines_batch(self, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量分析视频时间轴

        Args:
            notes: 笔记列表，每个笔记包含video_addr字段

        Returns:
            时间轴分析统计结果
        """
        logger.info(f"开始批量分析 {len(notes)} 个视频时间轴...")

        # 收集所有视频信息
        videos = []
        for note in notes:
            if note.get('note_type') == '视频' and note.get('video_addr'):
                videos.append({
                    'url': note['video_addr'],
                    'title': note.get('title', ''),
                    'description': note.get('desc', ''),
                    'note_id': note.get('note_id'),
                    'video_urls': note.get('video_urls', [])  # P0-4: 收集备选URL
                })

        if not videos:
            logger.warning("没有找到视频URL")
            return self._get_empty_stats()

        # 批量分析（使用异步）- 兼容嵌套事件循环
        async def run_all_tasks():
            tasks = [
                self.analyze_timeline(
                    item['url'],
                    item['title'],
                    item['description'],
                    video_urls=item.get('video_urls'),  # P0-4: 透传备选URL
                    note_id=item.get('note_id')  # P1-download: 透传note_id实现下载共享
                )
                for item in videos
            ]
            return await asyncio.gather(*tasks)

        # 检查是否已有运行中的事件循环
        try:
            loop = asyncio.get_running_loop()
            # 如果已有运行中的循环，使用 nest_asyncio 支持的方式
            import nest_asyncio
            nest_asyncio.apply()
            analyses = loop.run_until_complete(run_all_tasks())
        except RuntimeError:
            # 没有运行中的循环，创建新的
            analyses = asyncio.run(run_all_tasks())

        # 筛选出视频笔记（与analyses对应）
        video_notes = [n for n in notes if n.get('note_type') == '视频' and n.get('video_addr')]

        # 统计分析结果（传入notes以计算互动数据）
        stats = self._calculate_statistics(analyses, video_notes)

        return stats

    def _calculate_statistics(
        self,
        analyses: List[Dict[str, Any]],
        notes: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        计算时间轴统计信息

        Args:
            analyses: 分析结果列表
            notes: 原始笔记列表（可选，用于计算互动数据）

        Returns:
            统计信息字典
        """
        notes = notes or []
        # 时间点统计
        product_appear_times = []
        product_use_times = []
        content_start_times = []

        # 分类统计
        content_types = Counter()
        entry_points = Counter()
        product_intro_ways = Counter()
        product_embed_ways = Counter()

        # 内容类型细分
        main_categories = Counter()
        sub_categories = Counter()

        for analysis in analyses:
            if 'error' not in analysis:
                # 提取时间点
                appear_time = self._parse_time(analysis.get('product_appear_time'))
                use_time = self._parse_time(analysis.get('product_use_time'))
                start_time = self._parse_time(analysis.get('content_start_time'))

                if appear_time is not None:
                    product_appear_times.append(appear_time)
                if use_time is not None:
                    product_use_times.append(use_time)
                if start_time is not None:
                    content_start_times.append(start_time)

                # 统计分类
                content_type = analysis.get('content_type', '')
                if '-' in content_type:
                    parts = content_type.split('-')
                    main_categories[parts[0]] += 1
                    sub_categories['-'.join(parts[1:])] += 1
                    content_types[content_type] += 1

                entry_points[analysis.get('entry_point', 'unknown')] += 1
                product_intro_ways[analysis.get('product_intro_way', 'unknown')] += 1
                product_embed_ways[analysis.get('product_embed_way', 'unknown')] += 1

        total = len(analyses)
        error_count = sum(1 for a in analyses if 'error' in a)

        # 计算时间分布
        time_distributions = self._calculate_time_distributions(
            product_appear_times,
            product_use_times,
            content_start_times
        )

        # 计算PRD合规率
        prd_compliance = self._calculate_prd_compliance(
            product_appear_times,
            content_start_times,
            product_use_times
        )

        # 计算切入方式统计（带互动数据）
        entry_point_stats = self._calculate_entry_point_stats(analyses, notes)

        # 计算植入方式统计（带互动数据）
        embed_way_stats = self._calculate_embed_way_stats(analyses, notes)

        # 生成top3植入策略
        top3_embed_strategies = self._generate_top3_embed_strategies(
            embed_way_stats,
            entry_point_stats
        )

        return {
            'total_analyzed': total,
            'success_count': total - error_count,
            'error_count': error_count,
            'time_distributions': time_distributions,
            'prd_compliance': prd_compliance,
            'content_types': {
                'main_categories': dict(main_categories.most_common()),
                'sub_categories': dict(sub_categories.most_common(10)),
                'full_types': dict(content_types.most_common(15))
            },
            'entry_points': dict(entry_points.most_common(10)),
            'entry_point_stats': entry_point_stats,
            'embed_way_stats': embed_way_stats,
            'top3_embed_strategies': top3_embed_strategies,
            'product_strategies': {
                'intro_ways': dict(product_intro_ways.most_common(10)),
                'embed_ways': dict(product_embed_ways.most_common(10))
            },
            'recommendations': self._generate_recommendations(
                time_distributions,
                main_categories,
                entry_points,
                product_intro_ways
            )
        }

    def _calculate_time_distributions(
        self,
        appear_times: List[int],
        use_times: List[int],
        start_times: List[int]
    ) -> Dict[str, Any]:
        """
        计算时间分布统计

        Args:
            appear_times: 产品出现时间列表
            use_times: 产品使用时间列表
            start_times: 内容开始时间列表

        Returns:
            时间分布统计
        """
        def get_stats(times):
            if not times:
                return {'avg': 0, 'min': 0, 'max': 0, 'ranges': {}}

            avg_time = sum(times) / len(times)
            min_time = min(times)
            max_time = max(times)

            # 时间区间分布
            ranges = {
                '0-10s': sum(1 for t in times if t <= 10),
                '11-30s': sum(1 for t in times if 11 <= t <= 30),
                '31-60s': sum(1 for t in times if 31 <= t <= 60),
                '61-90s': sum(1 for t in times if 61 <= t <= 90),
                '>90s': sum(1 for t in times if t > 90)
            }

            return {
                'avg': round(avg_time, 1),
                'min': min_time,
                'max': max_time,
                'ranges': ranges
            }

        return {
            'product_appear': get_stats(appear_times),
            'product_use': get_stats(use_times),
            'content_start': get_stats(start_times),
            'product_delay': self._calculate_delay(appear_times, use_times)
        }

    def _calculate_delay(self, appear_times: List[int], use_times: List[int]) -> Dict[str, Any]:
        """
        计算产品出现到使用的延迟

        Args:
            appear_times: 产品出现时间
            use_times: 产品使用时间

        Returns:
            延迟统计
        """
        delays = []
        for i in range(min(len(appear_times), len(use_times))):
            if appear_times[i] and use_times[i]:
                delay = use_times[i] - appear_times[i]
                if delay >= 0:
                    delays.append(delay)

        if delays:
            return {
                'avg_delay': round(sum(delays) / len(delays), 1),
                'immediate_use_rate': round(sum(1 for d in delays if d <= 5) / len(delays) * 100, 1)
            }
        return {'avg_delay': 0, 'immediate_use_rate': 0}

    def _calculate_prd_compliance(
        self,
        appear_times: List[int],
        start_times: List[int],
        use_times: List[int]
    ) -> Dict[str, Any]:
        """
        计算PRD合规率

        PRD标准：
        - 产品出现时间 ≤30s
        - 干货开始时间 20-40s
        - 产品讲解时间 40-60s
        - 视频时长 ≥60s

        Returns:
            PRD合规性统计
        """
        total_appear = len(appear_times) if appear_times else 0
        total_start = len(start_times) if start_times else 0
        total_use = len(use_times) if use_times else 0

        # 产品≤30s出现率
        product_under_30s = sum(1 for t in appear_times if t <= 30)
        product_under_30s_rate = (product_under_30s / total_appear * 100) if total_appear else 0

        # 干货20-40s开始率
        content_20_40s = sum(1 for t in start_times if 20 <= t <= 40)
        content_20_40s_rate = (content_20_40s / total_start * 100) if total_start else 0

        # 产品使用40-60s率
        explain_40_60s = sum(1 for t in use_times if 40 <= t <= 60)
        explain_40_60s_rate = (explain_40_60s / total_use * 100) if total_use else 0

        # 综合合规分数（加权平均）
        weights = [0.4, 0.35, 0.25]  # 产品出现最重要
        scores = [product_under_30s_rate, content_20_40s_rate, explain_40_60s_rate]
        overall_score = sum(w * s for w, s in zip(weights, scores))

        return {
            'product_under_30s_rate': round(product_under_30s_rate, 1),
            'product_under_30s_count': product_under_30s,
            'content_20_40s_rate': round(content_20_40s_rate, 1),
            'content_20_40s_count': content_20_40s,
            'explain_40_60s_rate': round(explain_40_60s_rate, 1),
            'explain_40_60s_count': explain_40_60s,
            'overall_compliance_score': round(overall_score, 1),
            'total_samples': {
                'appear': total_appear,
                'start': total_start,
                'use': total_use
            }
        }

    def _get_interaction_count(self, note: Dict[str, Any]) -> int:
        """
        获取笔记的互动数（点赞+收藏），兼容多种字段名格式

        Args:
            note: 笔记数据字典

        Returns:
            互动数（点赞+收藏）
        """
        # 兼容多种点赞字段名
        liked = (
            note.get('liked_count') or
            note.get('liked') or
            note.get('interact_info', {}).get('liked_count') or
            note.get('interact_info', {}).get('liked') or
            0
        )

        # 兼容多种收藏字段名
        collected = (
            note.get('collected_count') or
            note.get('collected') or
            note.get('interact_info', {}).get('collected_count') or
            note.get('interact_info', {}).get('collected') or
            0
        )

        return int(liked) + int(collected)

    def _calculate_entry_point_stats(
        self,
        analyses: List[Dict[str, Any]],
        notes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        计算切入方式统计（包含平均互动）

        Args:
            analyses: 分析结果列表
            notes: 原始笔记数据（用于获取互动数据）

        Returns:
            切入方式统计
        """
        entry_data = defaultdict(lambda: {'count': 0, 'interactions': []})

        # 建立note_id到互动数据的映射
        note_map = {n.get('note_id'): n for n in notes}

        for i, analysis in enumerate(analyses):
            if 'error' in analysis:
                continue
            entry_point = analysis.get('entry_point', 'unknown')
            if entry_point and entry_point != 'unknown':
                entry_data[entry_point]['count'] += 1
                # 获取对应笔记的互动数据
                if i < len(notes):
                    note = notes[i]
                    interaction = self._get_interaction_count(note)
                    entry_data[entry_point]['interactions'].append(interaction)

        # 计算平均互动
        result = {}
        for entry, data in entry_data.items():
            avg_interaction = (
                sum(data['interactions']) / len(data['interactions'])
                if data['interactions'] else 0
            )
            result[entry] = {
                'count': data['count'],
                'avg_interaction': round(avg_interaction, 0)
            }

        # 按数量排序
        return dict(sorted(result.items(), key=lambda x: x[1]['count'], reverse=True))

    def _calculate_embed_way_stats(
        self,
        analyses: List[Dict[str, Any]],
        notes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        计算植入方式统计（包含平均互动）

        Args:
            analyses: 分析结果列表
            notes: 原始笔记数据

        Returns:
            植入方式统计
        """
        embed_data = defaultdict(lambda: {'count': 0, 'interactions': []})

        for i, analysis in enumerate(analyses):
            if 'error' in analysis:
                continue
            embed_way = analysis.get('product_embed_way', 'unknown')
            if embed_way and embed_way != 'unknown':
                embed_data[embed_way]['count'] += 1
                if i < len(notes):
                    note = notes[i]
                    interaction = self._get_interaction_count(note)
                    embed_data[embed_way]['interactions'].append(interaction)

        result = {}
        for embed, data in embed_data.items():
            avg_interaction = (
                sum(data['interactions']) / len(data['interactions'])
                if data['interactions'] else 0
            )
            result[embed] = {
                'count': data['count'],
                'avg_interaction': round(avg_interaction, 0)
            }

        return dict(sorted(result.items(), key=lambda x: x[1]['count'], reverse=True))

    def _generate_top3_embed_strategies(
        self,
        embed_stats: Dict[str, Any],
        entry_stats: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        生成3个最佳植入策略

        Args:
            embed_stats: 植入方式统计
            entry_stats: 切入方式统计

        Returns:
            top3策略列表
        """
        strategies = []

        # 策略模板
        strategy_templates = {
            '手持口播': {
                'description': '直接手持产品进行口播介绍，真实感强',
                'applicable_scene': '适合新品推荐、功效讲解',
                'operation_tips': [
                    '展示产品包装和质地',
                    '讲解使用方法和体验',
                    '结合个人真实使用感受'
                ]
            },
            '流程中植入': {
                'description': '在护肤/使用流程中自然带入产品',
                'applicable_scene': '适合日常分享、晨间/晚间护肤',
                'operation_tips': [
                    '完整展示使用流程',
                    '产品作为流程中的一环出现',
                    '强调产品在整体流程中的作用'
                ]
            },
            '干货手法中植入': {
                'description': '在分享干货技巧时顺带介绍产品',
                'applicable_scene': '适合教程类、技巧分享类内容',
                'operation_tips': [
                    '先讲干货内容吸引用户',
                    '产品作为实现效果的工具',
                    '突出产品配合手法的效果'
                ]
            },
            '效果对比植入': {
                'description': '通过使用前后对比展示产品效果',
                'applicable_scene': '适合功效型产品、即时见效产品',
                'operation_tips': [
                    '清晰展示使用前状态',
                    '演示产品使用过程',
                    '展示使用后的明显变化'
                ]
            },
            '问题解决植入': {
                'description': '先提出问题痛点，再用产品解决',
                'applicable_scene': '适合针对特定问题的产品',
                'operation_tips': [
                    '引发用户对问题的共鸣',
                    '自然引出产品作为解决方案',
                    '展示产品解决问题的效果'
                ]
            }
        }

        # 根据统计数据排序选择top3
        if embed_stats:
            sorted_embeds = sorted(
                embed_stats.items(),
                key=lambda x: (x[1].get('avg_interaction', 0), x[1].get('count', 0)),
                reverse=True
            )

            for i, (embed_name, stats) in enumerate(sorted_embeds[:3]):
                template = strategy_templates.get(embed_name, {
                    'description': f'使用{embed_name}方式植入产品',
                    'applicable_scene': '根据内容特点灵活运用',
                    'operation_tips': ['保持自然', '与内容融合', '突出产品优势']
                })

                strategies.append({
                    'rank': i + 1,
                    'name': embed_name,
                    'summary': f"【{embed_name}】{template['description']}",
                    'applicable_scene': template['applicable_scene'],
                    'operation_tips': template['operation_tips'],
                    'data_support': f"样本数{stats.get('count', 0)}，"
                                   f"平均互动{stats.get('avg_interaction', 0):.0f}",
                    'recommendation': '⭐⭐⭐' if i == 0 else ('⭐⭐' if i == 1 else '⭐')
                })

        # 如果数据不足，补充通用策略
        while len(strategies) < 3:
            idx = len(strategies)
            default_names = ['流程中植入', '干货手法中植入', '手持口播']
            name = default_names[idx] if idx < len(default_names) else f'策略{idx+1}'
            template = strategy_templates.get(name, {
                'description': '自然植入产品',
                'applicable_scene': '通用场景',
                'operation_tips': ['保持自然']
            })
            strategies.append({
                'rank': idx + 1,
                'name': name,
                'summary': f"【{name}】{template['description']}",
                'applicable_scene': template['applicable_scene'],
                'operation_tips': template['operation_tips'],
                'data_support': '基于行业最佳实践',
                'recommendation': '⭐'
            })

        return strategies

    def _parse_time(self, time_str: str) -> Optional[int]:
        """
        解析时间字符串

        Args:
            time_str: 时间字符串（如"30s"）

        Returns:
            秒数或None
        """
        if not time_str or time_str == '/':
            return None

        try:
            # 移除's'后缀并转换为整数
            time_str = time_str.replace('s', '').strip()
            return int(time_str)
        except (ValueError, AttributeError):
            return None

    def _generate_recommendations(
        self,
        time_distributions: Dict[str, Any],
        main_categories: Counter,
        entry_points: Counter,
        product_intro_ways: Counter
    ) -> List[str]:
        """
        生成视频制作建议

        Args:
            time_distributions: 时间分布统计
            main_categories: 主分类统计
            entry_points: 切入点统计
            product_intro_ways: 产品引出方式统计

        Returns:
            建议列表
        """
        recommendations = []

        # 产品出现时间建议
        if time_distributions['product_appear']['avg'] > 0:
            avg_appear = time_distributions['product_appear']['avg']
            if avg_appear < 20:
                recommendations.append(
                    f"产品平均在{avg_appear:.0f}秒出现，前置较早有利于快速吸引注意"
                )
            elif avg_appear > 60:
                recommendations.append(
                    f"产品平均在{avg_appear:.0f}秒才出现，建议提前至30秒内"
                )
            else:
                recommendations.append(
                    f"产品出现时机合理（平均{avg_appear:.0f}秒），保持当前节奏"
                )

        # 内容开始时间建议
        if time_distributions['content_start']['avg'] > 0:
            avg_start = time_distributions['content_start']['avg']
            if avg_start > 30:
                recommendations.append(
                    f"干货内容平均{avg_start:.0f}秒才开始，建议更快进入主题"
                )

        # 产品使用延迟建议
        delay_info = time_distributions.get('product_delay', {})
        if delay_info.get('immediate_use_rate', 0) < 50:
            recommendations.append(
                "产品展示后较久才演示使用，建议缩短展示到使用的间隔"
            )

        # 内容类型建议
        if main_categories:
            top_category = main_categories.most_common(1)[0]
            recommendations.append(
                f"最受欢迎的内容类型是【{top_category[0]}】，可作为主要创作方向"
            )

        # 切入点建议
        if entry_points:
            top_entry = entry_points.most_common(1)[0]
            if '眼部' in top_entry[0]:
                recommendations.append(
                    "眼部问题是高效切入点，建议重点展示眼部改善效果"
                )
            elif '熬夜' in top_entry[0]:
                recommendations.append(
                    "熬夜场景贴近用户生活，容易产生共鸣"
                )

        # 产品引出方式建议
        if product_intro_ways:
            top_intro = product_intro_ways.most_common(1)[0]
            if '自用' in top_intro[0]:
                recommendations.append(
                    "自用分享方式更具说服力，建议展示真实使用体验"
                )

        return recommendations

    def _get_default_analysis(self, error: str = None) -> Dict[str, Any]:
        """
        获取默认分析结果

        Args:
            error: 错误信息

        Returns:
            默认分析字典
        """
        result = {
            'product_appear_time': '/',
            'product_use_time': '/',
            'content_start_time': '/',
            'content_type': 'unknown',
            'entry_point': 'unknown',
            'product_intro_way': 'unknown',
            'product_embed_way': 'unknown',
            'raw_result': 'default'
        }
        if error:
            result['error'] = error
        return result

    def _get_empty_stats(self) -> Dict[str, Any]:
        """
        获取空统计结果

        Returns:
            空统计字典
        """
        return {
            'total_analyzed': 0,
            'success_count': 0,
            'error_count': 0,
            'time_distributions': {
                'product_appear': {'avg': 0, 'min': 0, 'max': 0, 'ranges': {}},
                'product_use': {'avg': 0, 'min': 0, 'max': 0, 'ranges': {}},
                'content_start': {'avg': 0, 'min': 0, 'max': 0, 'ranges': {}},
                'product_delay': {'avg_delay': 0, 'immediate_use_rate': 0}
            },
            'prd_compliance': {
                'product_under_30s_rate': 0,
                'content_20_40s_rate': 0,
                'explain_40_60s_rate': 0,
                'overall_compliance_score': 0,
                'total_samples': {'appear': 0, 'start': 0, 'use': 0}
            },
            'content_types': {
                'main_categories': {},
                'sub_categories': {},
                'full_types': {}
            },
            'entry_points': {},
            'entry_point_stats': {},
            'embed_way_stats': {},
            'top3_embed_strategies': [],
            'product_strategies': {
                'intro_ways': {},
                'embed_ways': {}
            },
            'recommendations': ["没有找到可分析的视频"]
        }