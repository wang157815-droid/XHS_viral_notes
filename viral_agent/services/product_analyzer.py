"""
产品引出分析器
分析爆款笔记中产品的引出时间、方式和营销场景

支持两种分析模式：
1. 关键词匹配（默认）：快速、免费，但准确度有限
2. AI语义分析：准确度高，需要调用AI API
"""
import re
import os
import jieba
from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING
from collections import Counter
from loguru import logger

from viral_agent.models.viral_note import ViralNote
from viral_agent.prompts.product_prompts import (
    build_batch_analysis_prompt,
    parse_batch_response,
    PRODUCT_ANALYZER_SYSTEM_PROMPT
)

if TYPE_CHECKING:
    from openai import OpenAI


class ProductAnalyzer:
    """产品引出和营销场景分析器"""

    def __init__(self, ai_client: Optional["OpenAI"] = None, model_name: str = None):
        """
        初始化分析器

        Args:
            ai_client: OpenAI客户端（可选，用于AI语义分析）
            model_name: 模型名称（如果提供client则必须提供）
        """
        self.ai_client = ai_client
        # 优先使用产品分析专用模型，未配置时回退到通用模型
        self.model_name = model_name or os.getenv("PRODUCT_MODEL_NAME") or os.getenv("MODEL_NAME", "gpt-4")
        self._init_keywords()

        if self.ai_client:
            logger.info(f"产品分析器已启用AI语义分析模式，模型: {self.model_name}")

    def _init_keywords(self):
        """初始化关键词库"""
        # 产品相关关键词
        self.product_keywords = [
            '产品', '商品', '款', '品牌', '型号', '系列',
            '这个', '这款', '这瓶', '这支', '这盒',
            '链接', '购买', '下单', '入手', '价格', '优惠'
        ]

        # 营销场景关键词
        self.scene_keywords = {
            '种草': ['种草', '安利', '推荐', '必买', '心动', '入坑'],
            '测评': ['测评', '评测', '对比', 'PK', '实测', '体验'],
            '教程': ['教程', '教学', '步骤', '方法', '技巧', '攻略'],
            '分享': ['分享', '日常', '好物', '爱用', '空瓶', '回购'],
            '避雷': ['避雷', '踩雷', '拔草', '慎买', '不推荐', '差评'],
            '开箱': ['开箱', '晒单', '收到', '快递', '包装'],
            '合集': ['合集', '盘点', '汇总', '大全', '清单', 'TOP']
        }

        # 切入方式关键词
        self.approach_keywords = {
            '痛点切入': ['困扰', '烦恼', '问题', '痛点', '难题', '苦恼'],
            '效果切入': ['效果', '改善', '提升', '变化', '前后', '对比'],
            '场景切入': ['场合', '场景', '时候', '适合', '搭配', '日常'],
            '成分切入': ['成分', '配方', '原料', '含有', '添加', '提取'],
            '价格切入': ['性价比', '平价', '便宜', '划算', '优惠', '折扣'],
            '故事切入': ['故事', '经历', '亲身', '真实', '朋友', '闺蜜']
        }

    def analyze_product_mentions(
        self,
        notes: List[ViralNote],
        use_ai: bool = True
    ) -> Dict[str, Any]:
        """
        分析产品引出特征

        Args:
            notes: 爆款笔记列表
            use_ai: 是否使用AI语义分析（默认True，需要配置ai_client）

        Returns:
            产品分析结果
        """
        logger.info(f"开始分析 {len(notes)} 篇笔记的产品引出特征...")

        # 决定使用哪种分析方法
        if use_ai and self.ai_client:
            logger.info("使用AI语义分析模式（准确度更高）")
            product_timing = self._analyze_product_timing_with_ai(notes)
        else:
            if use_ai and not self.ai_client:
                logger.warning("AI客户端未配置，回退到关键词匹配模式")
            product_timing = self._analyze_product_timing(notes)

        results = {
            'product_timing': product_timing,
            'marketing_scenes': self._analyze_marketing_scenes(notes),
            'approach_methods': self._analyze_approach_methods(notes),
            'mention_strategies': self._analyze_mention_strategies(notes)
        }

        logger.success("产品分析完成")
        return results

    def _analyze_product_timing(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        分析产品引出时间

        Returns:
            产品引出时间分析
        """
        timing_stats = {
            'title_mention': 0,  # 标题中提及
            'first_third': 0,    # 前1/3提及
            'middle_third': 0,   # 中1/3提及
            'last_third': 0,     # 后1/3提及
            'multiple_mentions': 0  # 多次提及
        }

        position_details = []

        for note in notes:
            # 检查标题
            if self._contains_product_keyword(note.title):
                timing_stats['title_mention'] += 1

            # 分析内容
            if note.desc:
                content_length = len(note.desc)
                product_positions = self._find_product_positions(note.desc)

                if product_positions:
                    # 计算首次提及位置
                    first_position = product_positions[0] / content_length

                    if first_position <= 0.33:
                        timing_stats['first_third'] += 1
                        position_details.append('开头')
                    elif first_position <= 0.66:
                        timing_stats['middle_third'] += 1
                        position_details.append('中间')
                    else:
                        timing_stats['last_third'] += 1
                        position_details.append('结尾')

                    # 检查是否多次提及
                    if len(product_positions) >= 3:
                        timing_stats['multiple_mentions'] += 1

        # 计算百分比
        total = len(notes)
        if total == 0:
            # 如果没有笔记，返回空数据
            timing_distribution = {
                'title_mention_rate': 0,
                'early_mention_rate': 0,
                'middle_mention_rate': 0,
                'late_mention_rate': 0,
                'multiple_mention_rate': 0
            }
        else:
            timing_distribution = {
                'title_mention_rate': round(timing_stats['title_mention'] / total * 100, 1),
                'early_mention_rate': round(timing_stats['first_third'] / total * 100, 1),
                'middle_mention_rate': round(timing_stats['middle_third'] / total * 100, 1),
                'late_mention_rate': round(timing_stats['last_third'] / total * 100, 1),
                'multiple_mention_rate': round(timing_stats['multiple_mentions'] / total * 100, 1)
            }

        # 推荐策略
        recommendations = []
        if timing_distribution['early_mention_rate'] > 40:
            recommendations.append("开门见山：40%+的爆款在前1/3就提及产品")
        if timing_distribution['title_mention_rate'] > 30:
            recommendations.append("标题植入：30%+的爆款在标题中提及产品")
        if timing_distribution['multiple_mention_rate'] > 50:
            recommendations.append("反复强化：50%+的爆款多次提及产品加深印象")

        return {
            'distribution': timing_distribution,
            'recommendations': recommendations,
            'optimal_strategy': self._get_optimal_timing_strategy(timing_distribution),
            'analysis_method': 'keyword'  # 标记分析方法
        }

    def _analyze_product_timing_with_ai(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        使用AI语义分析产品引出时间（准确度更高）

        通过AI理解上下文，准确识别产品提及位置，
        包括隐性指代（如"它"、代词）和品牌名等。

        Args:
            notes: 爆款笔记列表

        Returns:
            产品引出时间分析（与关键词方法格式兼容）
        """
        try:
            # 准备笔记数据
            notes_data = [
                {'title': note.title, 'desc': note.desc or ''}
                for note in notes
            ]

            # 构建提示词
            prompt = build_batch_analysis_prompt(notes_data)

            # 调用AI分析
            logger.info(f"正在使用AI分析 {len(notes)} 篇笔记的产品提及位置...")

            response = self.ai_client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": PRODUCT_ANALYZER_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # 低温度以获得更一致的结果
                max_tokens=2000
            )

            # 解析响应
            ai_response = response.choices[0].message.content

            # 调试日志：记录AI原始响应的前1000字符
            logger.debug(f"AI原始响应（前1000字符）: {ai_response[:1000]}")

            # 检查是否包含think标签
            if '<think>' in ai_response:
                logger.info("检测到DeepSeek Reasoner think标签，将自动移除")

            parsed = parse_batch_response(ai_response)

            if 'error' in parsed:
                logger.warning(f"AI响应解析失败: {parsed.get('error')}")
                logger.warning(f"原始响应（前500字符）: {ai_response[:500]}")
                logger.info("回退到关键词匹配方法")
                return self._analyze_product_timing(notes)

            # 从AI结果构建timing_distribution
            summary = parsed.get('summary', {})
            total = len(notes)

            timing_distribution = {
                'title_mention_rate': round(
                    summary.get('title_mention_count', 0) / total * 100, 1
                ) if total > 0 else 0,
                'early_mention_rate': round(
                    summary.get('early_count', 0) / total * 100, 1
                ) if total > 0 else 0,
                'middle_mention_rate': round(
                    summary.get('middle_count', 0) / total * 100, 1
                ) if total > 0 else 0,
                'late_mention_rate': round(
                    summary.get('late_count', 0) / total * 100, 1
                ) if total > 0 else 0,
                'no_mention_rate': round(
                    summary.get('no_mention_count', 0) / total * 100, 1
                ) if total > 0 else 0
            }

            # 计算多次提及（AI分析默认不提供，使用关键词方法补充）
            multiple_count = sum(
                1 for note in notes
                if len(self._find_product_positions(note.desc or '')) >= 3
            )
            timing_distribution['multiple_mention_rate'] = round(
                multiple_count / total * 100, 1
            ) if total > 0 else 0

            # 推荐策略
            recommendations = []
            if timing_distribution['early_mention_rate'] > 40:
                recommendations.append("开门见山：40%+的爆款在前1/3就提及产品")
            if timing_distribution['title_mention_rate'] > 30:
                recommendations.append("标题植入：30%+的爆款在标题中提及产品")
            if timing_distribution['multiple_mention_rate'] > 50:
                recommendations.append("反复强化：50%+的爆款多次提及产品加深印象")

            # 保存详细的AI分析结果
            ai_details = {
                'notes_analysis': parsed.get('notes_analysis', []),
                'avg_confidence': summary.get('avg_confidence', 0)
            }

            logger.success(
                f"AI分析完成，平均置信度: {ai_details['avg_confidence']:.2f}"
            )

            return {
                'distribution': timing_distribution,
                'recommendations': recommendations,
                'optimal_strategy': self._get_optimal_timing_strategy(timing_distribution),
                'analysis_method': 'ai',  # 标记分析方法
                'ai_details': ai_details  # AI分析的详细结果
            }

        except Exception as e:
            logger.error(f"AI分析失败: {e}")
            logger.info("回退到关键词匹配方法")
            return self._analyze_product_timing(notes)

    def _analyze_marketing_scenes(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        分析营销场景

        Returns:
            营销场景分析
        """
        scene_counts = Counter()
        scene_examples = {scene: [] for scene in self.scene_keywords.keys()}

        for note in notes:
            # 合并标题和内容
            full_text = f"{note.title} {note.desc}"

            # 识别场景
            identified_scenes = []
            for scene, keywords in self.scene_keywords.items():
                if any(keyword in full_text for keyword in keywords):
                    scene_counts[scene] += 1
                    identified_scenes.append(scene)

                    # 收集案例
                    if len(scene_examples[scene]) < 3:
                        scene_examples[scene].append({
                            'title': note.title[:30],
                            'interaction': note.interaction_score
                        })

            # 如果没有识别到场景，归类为"其他"
            if not identified_scenes:
                scene_counts['其他'] += 1

        # 计算分布
        total = sum(scene_counts.values())
        scene_distribution = {
            scene: {
                'count': count,
                'percentage': round(count / total * 100, 1)
            }
            for scene, count in scene_counts.most_common()
        }

        # 生成场景建议
        top_scenes = list(scene_counts.most_common(3))
        scene_recommendations = []
        for scene, count in top_scenes:
            if scene == '种草':
                scene_recommendations.append("种草类：重点展示产品效果和使用体验")
            elif scene == '测评':
                scene_recommendations.append("测评类：对比分析，数据说话，客观公正")
            elif scene == '教程':
                scene_recommendations.append("教程类：步骤清晰，实操性强，价值感足")
            elif scene == '分享':
                scene_recommendations.append("分享类：真实感受，日常融入，自然种草")

        return {
            'distribution': scene_distribution,
            'top_scenes': [s[0] for s in top_scenes],
            'examples': scene_examples,
            'recommendations': scene_recommendations
        }

    def _analyze_approach_methods(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        分析切入方式

        Returns:
            切入方式分析
        """
        approach_counts = Counter()

        for note in notes:
            content = note.desc[:300] if note.desc else ""  # 只分析开头部分

            for approach, keywords in self.approach_keywords.items():
                if any(keyword in content for keyword in keywords):
                    approach_counts[approach] += 1

        # 计算分布
        total = sum(approach_counts.values()) or 1
        approach_distribution = {
            approach: {
                'count': count,
                'percentage': round(count / total * 100, 1)
            }
            for approach, count in approach_counts.most_common()
        }

        # 生成建议（为所有切入方式提供建议，确保不为空）
        top_approaches = list(approach_counts.most_common(3))
        approach_tips = []

        # 所有切入方式的建议映射
        approach_tips_map = {
            '痛点切入': "痛点共鸣：直击用户需求，引发情感认同",
            '效果切入': "效果展示：前后对比，数据佐证，眼见为实",
            '场景切入': "场景代入：具体使用场景，增强购买欲望",
            '成分切入': "成分分析：专业背书，科学种草，理性说服",
            '价格切入': "性价比引导：突出超值感，降低决策门槛",
            '故事切入': "故事共鸣：真实经历分享，建立情感连接"
        }

        for approach, _ in top_approaches:
            if approach in approach_tips_map:
                approach_tips.append(approach_tips_map[approach])

        # 如果没有匹配到任何建议，提供默认建议
        if not approach_tips:
            approach_tips = [
                "痛点引入：先阐述用户困扰，再引出产品解决方案",
                "效果优先：用真实效果或对比图吸引注意力",
                "自然融入：在使用场景中自然提及产品"
            ]

        return {
            'distribution': approach_distribution,
            'top_approaches': [a[0] for a in top_approaches] if top_approaches else ['效果切入', '痛点切入'],
            'tips': approach_tips
        }

    def _analyze_mention_strategies(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        分析产品提及策略

        Returns:
            提及策略分析
        """
        strategies = {
            'soft_mention': 0,    # 软植入
            'hard_mention': 0,    # 硬广告
            'comparison': 0,      # 对比提及
            'story_telling': 0,   # 故事化
            'technical': 0        # 技术流
        }

        for note in notes:
            content = f"{note.title} {note.desc}"

            # 软植入：自然提及，不刻意
            if '顺便' in content or '刚好' in content or '正好' in content:
                strategies['soft_mention'] += 1

            # 硬广告：直接推荐购买
            if '链接' in content or '购买' in content or '下单' in content:
                strategies['hard_mention'] += 1

            # 对比提及
            if '对比' in content or 'PK' in content or 'VS' in content:
                strategies['comparison'] += 1

            # 故事化
            if '故事' in content or '经历' in content or '朋友' in content:
                strategies['story_telling'] += 1

            # 技术流
            if '成分' in content or '原理' in content or '技术' in content:
                strategies['technical'] += 1

        # 计算占比
        total = len(notes)
        if total == 0:
            # 避免除零错误
            strategy_distribution = {name: 0 for name in strategies}
        else:
            strategy_distribution = {
                name: round(count / total * 100, 1)
                for name, count in strategies.items()
            }

        # 推荐最佳策略组合
        best_strategies = []
        if strategy_distribution['soft_mention'] > 30:
            best_strategies.append("软植入为主，自然不突兀")
        if strategy_distribution['comparison'] > 20:
            best_strategies.append("对比分析增加可信度")
        if strategy_distribution['story_telling'] > 25:
            best_strategies.append("故事化叙述提升代入感")

        return {
            'distribution': strategy_distribution,
            'best_strategies': best_strategies,
            'balance_suggestion': self._get_balance_suggestion(strategy_distribution)
        }

    def _contains_product_keyword(self, text: str) -> bool:
        """检查文本是否包含产品关键词"""
        if not text:
            return False
        return any(keyword in text for keyword in self.product_keywords)

    def _find_product_positions(self, text: str) -> List[int]:
        """找出产品关键词在文本中的位置"""
        positions = []
        for keyword in self.product_keywords:
            index = 0
            while True:
                index = text.find(keyword, index)
                if index == -1:
                    break
                positions.append(index)
                index += len(keyword)
        return sorted(positions)

    def _get_optimal_timing_strategy(self, distribution: Dict[str, float]) -> str:
        """获取最佳时机策略（单个策略，向后兼容）"""
        strategies = self._generate_top3_timing_strategies(distribution)
        return strategies[0]['summary'] if strategies else "【均衡分布型】多次自然提及，强化产品印象"

    def _generate_top3_timing_strategies(self, distribution: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        生成3个最佳产品引出时机策略供业务选择

        Args:
            distribution: 时机分布数据

        Returns:
            3个策略列表，每个包含：name, summary, applicable_scene, operation_tips, expected_effect
        """
        strategies = []

        # 策略1: 开门见山型
        strategy1 = {
            'name': '开门见山型',
            'summary': '【开门见山型】在内容前1/3直接引入产品，快速吸引目标用户',
            'applicable_scene': '适合已有明确需求的用户群、产品知名度高的场景',
            'operation_tips': [
                '前30字内出现产品名或核心功效',
                '标题直接包含产品关键词',
                '开头用效果或数据吸引注意'
            ],
            'expected_effect': '精准触达目标用户，转化效率高，但可能流失浏览型用户',
            'data_support': f"数据显示{distribution.get('early_mention_rate', 0)}%的爆款采用此策略"
        }
        strategies.append(strategy1)

        # 策略2: 痛点切入型
        strategy2 = {
            'name': '痛点切入型',
            'summary': '【痛点切入型】先阐述问题和困扰，引发共鸣后自然引出产品作为解决方案',
            'applicable_scene': '适合解决特定问题的产品、用户有明确痛点的领域',
            'operation_tips': [
                '开头描述目标用户的常见困扰',
                '中段分析问题原因和失败经历',
                '引出产品时强调"终于找到"的惊喜感'
            ],
            'expected_effect': '用户共鸣强，信任度高，评论互动活跃',
            'data_support': f"数据显示{distribution.get('middle_mention_rate', 0)}%的爆款在中段引出产品"
        }
        strategies.append(strategy2)

        # 策略3: 效果展示型
        strategy3 = {
            'name': '效果展示型',
            'summary': '【效果展示型】通过前后对比、使用效果先吸引注意，再揭晓产品',
            'applicable_scene': '适合效果可视化的产品、护肤/美妆/健身等领域',
            'operation_tips': [
                '封面用效果对比图吸引点击',
                '先展示使用后的理想状态',
                '产品介绍时配合使用方法和时间线'
            ],
            'expected_effect': '视觉冲击力强，收藏率高，适合种草转化',
            'data_support': f"数据显示{distribution.get('title_mention_rate', 0)}%的爆款在标题提及产品"
        }
        strategies.append(strategy3)

        # 根据数据排序，将最匹配当前数据的策略排在前面
        early_rate = distribution.get('early_mention_rate', 0)
        middle_rate = distribution.get('middle_mention_rate', 0)
        late_rate = distribution.get('late_mention_rate', 0)

        # 计算每个策略的匹配分数
        scores = [
            (0, early_rate),     # 开门见山型
            (1, middle_rate),    # 痛点切入型
            (2, max(late_rate, distribution.get('title_mention_rate', 0)))  # 效果展示型
        ]
        scores.sort(key=lambda x: x[1], reverse=True)

        # 按匹配度重排策略
        sorted_strategies = [strategies[idx] for idx, _ in scores]

        # 标记推荐度
        for i, s in enumerate(sorted_strategies):
            if i == 0:
                s['recommendation'] = '⭐ 最佳推荐（基于数据分析）'
            elif i == 1:
                s['recommendation'] = '✓ 备选方案'
            else:
                s['recommendation'] = '○ 可选方案'

        return sorted_strategies

    def _get_balance_suggestion(self, distribution: Dict[str, float]) -> str:
        """获取平衡建议"""
        soft_rate = distribution.get('soft_mention', 0)
        hard_rate = distribution.get('hard_mention', 0)

        if soft_rate > hard_rate * 2:
            return "以软植入为主，营造自然分享氛围"
        elif hard_rate > soft_rate * 2:
            return "直接推荐型，适合已有信任基础的账号"
        else:
            return "软硬结合，既有自然分享又有明确推荐"

    def generate_product_strategy(self, analysis_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        生成产品引出策略

        Args:
            analysis_results: 分析结果

        Returns:
            产品策略建议
        """
        strategy = {
            'timing_guide': {
                'title': '产品引出时机指南',
                'recommendations': []
            },
            'scene_guide': {
                'title': '营销场景选择指南',
                'recommendations': []
            },
            'approach_guide': {
                'title': '切入方式指南',
                'recommendations': []
            },
            'mention_guide': {
                'title': '提及策略指南',
                'recommendations': []
            },
            # 新增：3个最佳策略供业务选择
            'top3_strategies': []
        }

        # 时机指南
        timing = analysis_results.get('product_timing', {})
        if timing.get('recommendations'):
            strategy['timing_guide']['recommendations'] = timing['recommendations']
        strategy['timing_guide']['optimal'] = timing.get('optimal_strategy', '')

        # 生成3个最佳策略
        distribution = timing.get('distribution', {})
        strategy['top3_strategies'] = self._generate_top3_timing_strategies(distribution)

        # 场景指南
        scenes = analysis_results.get('marketing_scenes', {})
        if scenes.get('recommendations'):
            strategy['scene_guide']['recommendations'] = scenes['recommendations']
        strategy['scene_guide']['top_scenes'] = scenes.get('top_scenes', [])

        # 切入指南
        approaches = analysis_results.get('approach_methods', {})
        if approaches.get('tips'):
            strategy['approach_guide']['recommendations'] = approaches['tips']
        strategy['approach_guide']['top_methods'] = approaches.get('top_approaches', [])

        # 提及指南
        mentions = analysis_results.get('mention_strategies', {})
        if mentions.get('best_strategies'):
            strategy['mention_guide']['recommendations'] = mentions['best_strategies']
        strategy['mention_guide']['balance'] = mentions.get('balance_suggestion', '')

        return strategy