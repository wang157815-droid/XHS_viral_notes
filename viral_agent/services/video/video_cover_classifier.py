"""
视频封面分类器
用于分析小红书视频笔记的封面图片类型和特征
"""
import asyncio
from typing import Dict, List, Any, Optional
from loguru import logger
from collections import Counter
import os

from viral_agent.prompts.video_cover_prompts import (
    get_cover_classification_prompt,
    parse_cover_classification,
    get_cover_detail_analysis_prompt,
    parse_cover_detail_analysis,
    extract_cover_detail_features
)


class VideoCoverClassifier:
    """视频封面分类器"""

    def __init__(self, ai_analyzer=None):
        """
        初始化封面分类器

        Args:
            ai_analyzer: AI分析器实例（可选）
        """
        self.ai_analyzer = ai_analyzer
        self.classification_cache = {}

    async def classify_cover(self, cover_url: str, title: str = None) -> Dict[str, Any]:
        """
        分类单个视频封面

        Args:
            cover_url: 封面图片URL
            title: 视频标题（可选，用于辅助分析）

        Returns:
            分类结果字典
        """
        try:
            # 检查缓存
            if cover_url in self.classification_cache:
                return self.classification_cache[cover_url]

            # 如果没有AI分析器，返回默认结果
            if not self.ai_analyzer:
                logger.warning("AI分析器未配置，使用默认分类")
                return self._get_default_classification()

            # 获取提示词
            prompt = get_cover_classification_prompt(cover_url)

            # 调用AI分析
            result = await self.ai_analyzer.analyze_image(
                image_url=cover_url,
                prompt=prompt,
                title=title
            )

            # 解析结果
            classification = parse_cover_classification(result)

            # 缓存结果
            self.classification_cache[cover_url] = classification

            return classification

        except Exception as e:
            logger.error(f"封面分类失败: {e}")
            return self._get_default_classification(error=str(e))

    async def analyze_cover_detail(
        self,
        cover_url: str,
        title: str = None
    ) -> Dict[str, Any]:
        """
        深度分析单个视频封面（40+字段，对标图文封面分析）

        Args:
            cover_url: 封面图片URL
            title: 视频标题（可选）

        Returns:
            详细分析结果，包含文字/颜色/布局/视觉风格4个维度
        """
        try:
            # 检查缓存
            cache_key = f"detail_{cover_url}"
            if cache_key in self.classification_cache:
                return self.classification_cache[cache_key]

            # 如果没有AI分析器，返回默认结果
            if not self.ai_analyzer:
                logger.warning("AI分析器未配置，使用默认详细分析")
                return self._get_default_detail_analysis()

            # 获取详细分析提示词
            prompt = get_cover_detail_analysis_prompt(cover_url)

            # 调用AI分析
            result = await self.ai_analyzer.analyze_image(
                image_url=cover_url,
                prompt=prompt,
                title=title
            )

            # 解析结果
            analysis = parse_cover_detail_analysis(result)

            # 提取特征
            if analysis.get('success'):
                features = extract_cover_detail_features(analysis)
                analysis['features'] = features

            # 保存原始AI结果
            analysis['raw_ai_result'] = result

            # 缓存结果
            self.classification_cache[cache_key] = analysis

            return analysis

        except Exception as e:
            logger.error(f"封面详细分析失败: {e}")
            return self._get_default_detail_analysis(error=str(e))

    async def analyze_covers_detail_batch(
        self,
        notes: List[Dict[str, Any]],
        max_concurrent: int = 3
    ) -> Dict[str, Any]:
        """
        批量深度分析视频封面

        Args:
            notes: 笔记列表
            max_concurrent: 最大并发数

        Returns:
            详细分析统计结果
        """
        logger.info(f"开始批量深度分析 {len(notes)} 个视频封面...")

        # 筛选视频笔记
        video_notes = [
            n for n in notes
            if n.get('note_type') == '视频' and n.get('video_cover')
        ]

        if not video_notes:
            logger.warning("没有找到视频封面")
            return self._get_empty_detail_stats()

        logger.info(f"找到 {len(video_notes)} 个视频封面")

        # 准备分析任务
        semaphore = asyncio.Semaphore(max_concurrent)

        async def analyze_with_semaphore(note):
            async with semaphore:
                return await self.analyze_cover_detail(
                    cover_url=note.get('video_cover'),
                    title=note.get('title')
                )

        # 执行批量分析
        try:
            loop = asyncio.get_running_loop()
            import nest_asyncio
            nest_asyncio.apply()
            tasks = [analyze_with_semaphore(note) for note in video_notes]
            analyses = await asyncio.gather(*tasks, return_exceptions=True)
        except RuntimeError:
            tasks = [analyze_with_semaphore(note) for note in video_notes]
            analyses = asyncio.run(asyncio.gather(*tasks, return_exceptions=True))

        # 处理结果
        valid_analyses = []
        error_count = 0

        for i, result in enumerate(analyses):
            if isinstance(result, Exception):
                logger.error(f"分析第{i+1}个封面失败: {result}")
                error_count += 1
            elif result.get('success', False):
                valid_analyses.append(result)
            else:
                error_count += 1

        # 生成详细统计
        stats = self._calculate_detail_statistics(valid_analyses, video_notes)
        stats['total_covers'] = len(video_notes)
        stats['success_count'] = len(valid_analyses)
        stats['error_count'] = error_count

        logger.success(f"封面深度分析完成：成功 {len(valid_analyses)}/{len(video_notes)} 个")

        return stats

    def _calculate_detail_statistics(
        self,
        analyses: List[Dict[str, Any]],
        notes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """计算详细分析统计"""
        if not analyses:
            return self._get_empty_detail_stats()

        # 文字分析统计
        text_coverage_dist = Counter()
        text_keyword_dist = Counter()
        text_position_dist = Counter()
        has_text_count = 0
        has_number_count = 0
        has_question_count = 0
        has_emotion_count = 0

        # 颜色分析统计
        color_dist = Counter()
        brightness_dist = Counter()
        saturation_dist = Counter()

        # 布局分析统计
        orientation_dist = Counter()
        collage_count = 0
        person_type_dist = Counter()
        product_type_dist = Counter()
        has_person_count = 0
        has_product_count = 0

        # 风格分析统计
        style_dist = Counter()
        complexity_dist = Counter()

        for analysis in analyses:
            if not analysis.get('success'):
                continue

            features = analysis.get('features', {})

            # 文字特征统计
            if features.get('text_coverage'):
                text_coverage_dist[features['text_coverage']] += 1
            if features.get('has_text'):
                has_text_count += 1
            if features.get('has_number'):
                has_number_count += 1
            if features.get('has_question'):
                has_question_count += 1
            if features.get('has_emotion_word'):
                has_emotion_count += 1
            if features.get('text_position'):
                text_position_dist[features['text_position']] += 1

            # 颜色特征统计
            if features.get('dominant_color'):
                color_dist[features['dominant_color']] += 1
            if features.get('is_bright'):
                brightness_dist['亮'] += 1
            else:
                brightness_dist['暗/中'] += 1
            if features.get('is_saturated'):
                saturation_dist['高'] += 1
            else:
                saturation_dist['低/中'] += 1

            # 布局特征统计
            if features.get('orientation'):
                orientation_dist[features['orientation']] += 1
            if features.get('is_collage'):
                collage_count += 1
            if features.get('has_person'):
                has_person_count += 1
            if features.get('person_type'):
                person_type_dist[features['person_type']] += 1
            if features.get('has_product'):
                has_product_count += 1
            if features.get('product_type'):
                product_type_dist[features['product_type']] += 1

            # 风格特征统计
            if features.get('style'):
                style_dist[features['style']] += 1
            if features.get('complexity'):
                complexity_dist[features['complexity']] += 1

        total = len(analyses)

        return {
            'text_analysis': {
                'coverage_distribution': dict(text_coverage_dist.most_common()),
                'position_distribution': dict(text_position_dist.most_common()),
                'has_text_rate': round(has_text_count / total * 100, 1) if total else 0,
                'has_number_rate': round(has_number_count / total * 100, 1) if total else 0,
                'has_question_rate': round(has_question_count / total * 100, 1) if total else 0,
                'has_emotion_rate': round(has_emotion_count / total * 100, 1) if total else 0
            },
            'color_analysis': {
                'dominant_color_distribution': dict(color_dist.most_common()),
                'brightness_distribution': dict(brightness_dist.most_common()),
                'saturation_distribution': dict(saturation_dist.most_common()),
                'bright_rate': round(brightness_dist.get('亮', 0) / total * 100, 1) if total else 0
            },
            'layout_analysis': {
                'orientation_distribution': dict(orientation_dist.most_common()),
                'collage_rate': round(collage_count / total * 100, 1) if total else 0,
                'person_presence_distribution': dict(person_type_dist.most_common()),
                'product_display_distribution': dict(product_type_dist.most_common()),
                'has_person_rate': round(has_person_count / total * 100, 1) if total else 0,
                'has_product_rate': round(has_product_count / total * 100, 1) if total else 0
            },
            'visual_style': {
                'style_distribution': dict(style_dist.most_common()),
                'complexity_distribution': dict(complexity_dist.most_common()),
                'most_common_style': style_dist.most_common(1)[0][0] if style_dist else None
            },
            'insights': self._generate_detail_insights(
                text_coverage_dist, color_dist, person_type_dist,
                style_dist, has_text_count, has_person_count, total
            ),
            'recommendations': self._generate_detail_recommendations(
                text_coverage_dist, color_dist, orientation_dist,
                style_dist, collage_count, total
            )
        }

    def _generate_detail_insights(
        self,
        text_coverage: Counter,
        color_dist: Counter,
        person_type: Counter,
        style_dist: Counter,
        has_text_count: int,
        has_person_count: int,
        total: int
    ) -> List[str]:
        """生成详细分析洞察"""
        insights = []

        # 文字洞察
        text_rate = has_text_count / total * 100 if total else 0
        if text_rate > 70:
            insights.append(f"封面文字使用率很高（{text_rate:.1f}%），文字是吸引点击的关键元素")
        elif text_rate < 30:
            insights.append(f"封面文字使用率较低（{text_rate:.1f}%），视觉元素是主要吸引力")

        # 颜色洞察
        if color_dist:
            top_color = color_dist.most_common(1)[0]
            insights.append(f"主流色调是【{top_color[0]}】（占比{top_color[1]/total*100:.1f}%）")

        # 人物洞察
        person_rate = has_person_count / total * 100 if total else 0
        if person_rate > 60:
            insights.append(f"人物出镜率高（{person_rate:.1f}%），真人形象增强信任感")
        if person_type:
            top_person = person_type.most_common(1)[0]
            insights.append(f"最常见的人物出镜方式是【{top_person[0]}】")

        # 风格洞察
        if style_dist:
            top_style = style_dist.most_common(1)[0]
            insights.append(f"封面风格以【{top_style[0]}】为主")

        return insights

    def _generate_detail_recommendations(
        self,
        text_coverage: Counter,
        color_dist: Counter,
        orientation: Counter,
        style_dist: Counter,
        collage_count: int,
        total: int
    ) -> List[str]:
        """生成详细分析建议"""
        recommendations = []

        # 文字建议
        if text_coverage:
            top_coverage = text_coverage.most_common(1)[0][0]
            if top_coverage in ['中', '多']:
                recommendations.append("封面文字适量，突出核心卖点，避免过于杂乱")
            else:
                recommendations.append("可适当增加文字引导，提升点击率")

        # 颜色建议
        if color_dist:
            top_color = color_dist.most_common(1)[0][0]
            recommendations.append(f"主色调建议使用【{top_color}】，与爆款风格保持一致")

        # 布局建议
        if orientation:
            top_orient = orientation.most_common(1)[0][0]
            recommendations.append(f"封面方向建议【{top_orient}】，符合用户浏览习惯")

        collage_rate = collage_count / total * 100 if total else 0
        if collage_rate > 40:
            recommendations.append("拼图/对比图效果好，适合展示前后效果对比")
        else:
            recommendations.append("单图封面更简洁，突出单一重点")

        # 风格建议
        if style_dist:
            top_style = style_dist.most_common(1)[0][0]
            recommendations.append(f"封面风格推荐【{top_style}】，更容易获得用户信任")

        return recommendations[:6]

    def _get_default_detail_analysis(self, error: str = None) -> Dict[str, Any]:
        """获取默认详细分析结果"""
        result = {
            'success': False,
            'data': {
                'text_analysis': {},
                'color_analysis': {},
                'layout_analysis': {},
                'visual_style': {}
            },
            'features': {},
            'raw_result': 'default'
        }
        if error:
            result['error'] = error
        return result

    def _get_empty_detail_stats(self) -> Dict[str, Any]:
        """获取空详细统计结果"""
        return {
            'total_covers': 0,
            'success_count': 0,
            'error_count': 0,
            'text_analysis': {
                'coverage_distribution': {},
                'position_distribution': {},
                'has_text_rate': 0,
                'has_number_rate': 0,
                'has_question_rate': 0,
                'has_emotion_rate': 0
            },
            'color_analysis': {
                'dominant_color_distribution': {},
                'brightness_distribution': {},
                'saturation_distribution': {},
                'bright_rate': 0
            },
            'layout_analysis': {
                'orientation_distribution': {},
                'collage_rate': 0,
                'person_presence_distribution': {},
                'product_display_distribution': {},
                'has_person_rate': 0,
                'has_product_rate': 0
            },
            'visual_style': {
                'style_distribution': {},
                'complexity_distribution': {},
                'most_common_style': None
            },
            'insights': ['没有找到可分析的视频封面'],
            'recommendations': ['请先采集视频数据后再进行分析']
        }

    def classify_covers_batch(self, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量分类视频封面

        Args:
            notes: 笔记列表，每个笔记包含video_cover字段

        Returns:
            封面分类统计结果
        """
        logger.info(f"开始批量分类 {len(notes)} 个视频封面...")

        # 收集所有封面URL
        cover_urls = []
        for note in notes:
            if note.get('note_type') == '视频' and note.get('video_cover'):
                cover_urls.append({
                    'url': note['video_cover'],
                    'title': note.get('title', ''),
                    'note_id': note.get('note_id')
                })

        if not cover_urls:
            logger.warning("没有找到视频封面")
            return self._get_empty_stats()

        # 批量分类（使用异步）- 兼容嵌套事件循环
        async def run_all_tasks():
            tasks = [
                self.classify_cover(item['url'], item['title'])
                for item in cover_urls
            ]
            return await asyncio.gather(*tasks)

        # 检查是否已有运行中的事件循环
        try:
            loop = asyncio.get_running_loop()
            # 如果已有运行中的循环，使用 nest_asyncio 支持的方式
            import nest_asyncio
            nest_asyncio.apply()
            classifications = loop.run_until_complete(run_all_tasks())
        except RuntimeError:
            # 没有运行中的循环，创建新的
            classifications = asyncio.run(run_all_tasks())

        # 统计分类结果
        stats = self._calculate_statistics(classifications)

        return stats

    def _calculate_statistics(self, classifications: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        计算分类统计信息

        Args:
            classifications: 分类结果列表

        Returns:
            统计信息字典
        """
        # 主分类统计
        main_categories = Counter()
        sub_categories = Counter()
        image_types = Counter()
        category_combinations = Counter()

        # 新增：内容形式统计
        content_types = Counter()  # 摆拍/过程/产品图等
        text_overlay_count = 0     # 有文字压图的数量
        person_appear_count = 0    # 有人物出镜的数量

        for cls in classifications:
            if 'error' not in cls:
                main = cls.get('main_category', 'unknown')
                sub = cls.get('sub_category', 'unknown')
                img_type = cls.get('image_type', 'unknown')

                main_categories[main] += 1
                sub_categories[sub] += 1
                image_types[img_type] += 1
                category_combinations[f"{main}-{sub}"] += 1

                # 推断内容形式
                content_type = self._infer_content_type(main, sub)
                content_types[content_type] += 1

                # 推断是否有文字压图（基于raw_result）
                raw = cls.get('raw_result', '')
                if self._has_text_overlay(raw, main, sub):
                    text_overlay_count += 1

                # 推断是否有人物出镜
                if self._has_person(main, sub, raw):
                    person_appear_count += 1

        total = len(classifications)
        error_count = sum(1 for cls in classifications if 'error' in cls)
        success_count = total - error_count

        # 计算比率
        text_overlay_rate = (text_overlay_count / success_count * 100) if success_count else 0
        person_appear_rate = (person_appear_count / success_count * 100) if success_count else 0

        return {
            'total_analyzed': total,
            'success_count': success_count,
            'error_count': error_count,
            'main_categories': dict(main_categories.most_common()),
            'sub_categories': dict(sub_categories.most_common(10)),
            'image_types': dict(image_types.most_common()),
            'top_combinations': dict(category_combinations.most_common(10)),
            # 新增统计
            'image_type_distribution': {
                '单图': image_types.get('单图', 0),
                '拼图': image_types.get('拼图', 0),
                '截图': image_types.get('截图', 0)
            },
            'content_type_distribution': dict(content_types.most_common()),
            'text_overlay_rate': round(text_overlay_rate, 1),
            'text_overlay_count': text_overlay_count,
            'person_appear_rate': round(person_appear_rate, 1),
            'person_appear_count': person_appear_count,
            'recommendations': self._generate_recommendations(
                main_categories, sub_categories, image_types
            )
        }

    def _infer_content_type(self, main_category: str, sub_category: str) -> str:
        """
        推断封面内容形式

        Args:
            main_category: 主分类
            sub_category: 子分类

        Returns:
            内容形式：摆拍/过程/产品图/效果图/其他
        """
        # 基于分类推断内容形式
        if '摆拍' in sub_category or '静态' in sub_category:
            return '摆拍'
        if '过程' in sub_category or '使用' in sub_category or '流程' in sub_category:
            return '过程'
        if '产品' in main_category or '产品' in sub_category:
            return '产品图'
        if '效果' in sub_category or '对比' in sub_category:
            return '效果图'
        if '个人' in main_category or '真人' in sub_category:
            return '摆拍'  # 个人形象类归为摆拍
        if '内容' in main_category or '干货' in sub_category:
            return '内容截图'
        return '其他'

    def _has_text_overlay(self, raw_result: str, main: str, sub: str) -> bool:
        """
        判断封面是否有文字压图

        Args:
            raw_result: AI分析原始结果
            main: 主分类
            sub: 子分类

        Returns:
            是否有文字压图
        """
        # 基于关键词判断
        text_keywords = ['文字', '标题', '压图', '字幕', '文案', '内容呈现']
        combined = f"{raw_result} {main} {sub}".lower()
        return any(kw in combined for kw in text_keywords)

    def _has_person(self, main: str, sub: str, raw_result: str) -> bool:
        """
        判断封面是否有人物出镜

        Args:
            main: 主分类
            sub: 子分类
            raw_result: AI分析原始结果

        Returns:
            是否有人物出镜
        """
        person_keywords = ['个人', '真人', '人物', '博主', '出镜', '自拍', '人像']
        combined = f"{main} {sub} {raw_result}"
        return any(kw in combined for kw in person_keywords)

    def _generate_recommendations(
        self,
        main_categories: Counter,
        sub_categories: Counter,
        image_types: Counter
    ) -> List[str]:
        """
        生成封面设计建议

        Args:
            main_categories: 主分类统计
            sub_categories: 子分类统计
            image_types: 图片类型统计

        Returns:
            建议列表
        """
        recommendations = []

        # 最受欢迎的主分类
        if main_categories:
            top_main = main_categories.most_common(1)[0]
            recommendations.append(
                f"最受欢迎的封面类型是【{top_main[0]}】，"
                f"占比{top_main[1]/sum(main_categories.values())*100:.1f}%"
            )

        # 最受欢迎的子分类
        if sub_categories:
            top_sub = sub_categories.most_common(1)[0]
            recommendations.append(
                f"具体展示方式以【{top_sub[0]}】最为常见"
            )

        # 图片类型建议
        if image_types:
            top_type = image_types.most_common(1)[0]
            if top_type[0] == '拼图':
                recommendations.append(
                    "拼图形式的封面更容易吸引注意力，建议使用多元素组合"
                )
            elif top_type[0] == '单图':
                recommendations.append(
                    "单图封面更加简洁直接，建议突出核心卖点"
                )

        # 根据分类分布给出建议
        if main_categories:
            if '产品展示类' in main_categories and main_categories['产品展示类'] > 0:
                recommendations.append(
                    "产品展示类封面效果好，建议清晰展示产品特点和使用场景"
                )
            if '个人形象类' in main_categories and main_categories['个人形象类'] > 0:
                recommendations.append(
                    "个人形象能增加信任度，建议展示真实使用效果"
                )
            if '内容呈现类' in main_categories and main_categories['内容呈现类'] > 0:
                recommendations.append(
                    "干货内容型封面转化率高，建议突出价值点和步骤数"
                )

        return recommendations

    def _get_default_classification(self, error: str = None) -> Dict[str, Any]:
        """
        获取默认分类结果

        Args:
            error: 错误信息

        Returns:
            默认分类字典
        """
        result = {
            'main_category': 'unknown',
            'sub_category': 'unknown',
            'image_type': 'unknown',
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
            'main_categories': {},
            'sub_categories': {},
            'image_types': {},
            'top_combinations': {},
            'image_type_distribution': {'单图': 0, '拼图': 0, '截图': 0},
            'content_type_distribution': {},
            'text_overlay_rate': 0,
            'text_overlay_count': 0,
            'person_appear_rate': 0,
            'person_appear_count': 0,
            'recommendations': ["没有找到可分析的视频封面"]
        }