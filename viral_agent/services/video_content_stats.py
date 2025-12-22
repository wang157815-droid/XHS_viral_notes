"""
视频内容统计与洞察生成器
从 video_content_analyzer.py 拆分出的统计、洞察、建议生成逻辑
"""

from typing import Dict, List, Any
from collections import Counter


class ContentStatsGenerator:
    """
    内容统计生成器

    负责：
    - 批量分析的统计计算
    - 内容洞察生成
    - 优化建议生成
    """

    def calculate_statistics(
        self,
        analyses: List[Dict[str, Any]],
        notes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        计算批量分析的统计数据

        Args:
            analyses: 有效的分析结果列表
            notes: 原始笔记数据

        Returns:
            统计数据字典
        """
        if not analyses:
            return get_empty_stats()

        # 统计各维度
        hook_types = Counter()
        hook_scores = []
        structure_types = Counter()
        pacing_types = Counter()
        emotion_curves = Counter()
        cta_types = Counter()
        visual_impact_count = 0
        memorable_ending_count = 0
        overall_scores = []

        for analysis in analyses:
            if not analysis.get('success'):
                continue

            features = analysis.get('features', {})

            if features.get('hook_type'):
                hook_types[features['hook_type']] += 1
            if features.get('hook_score'):
                hook_scores.append(features['hook_score'])
            if features.get('structure_type'):
                structure_types[features['structure_type']] += 1
            if features.get('pacing'):
                pacing_types[features['pacing']] += 1
            if features.get('emotion_curve'):
                emotion_curves[features['emotion_curve']] += 1
            if features.get('cta_type'):
                cta_types[features['cta_type']] += 1
            if features.get('has_visual_impact'):
                visual_impact_count += 1
            if features.get('has_memorable_ending'):
                memorable_ending_count += 1
            if features.get('overall_score'):
                overall_scores.append(features['overall_score'])

        total = len(analyses)

        return {
            'hook_analysis': {
                'type_distribution': dict(hook_types.most_common()),
                'avg_score': round(sum(hook_scores) / len(hook_scores), 1) if hook_scores else 0,
                'best_hook_type': hook_types.most_common(1)[0][0] if hook_types else None,
                'visual_impact_rate': round(visual_impact_count / total * 100, 1) if total else 0
            },
            'structure_analysis': {
                'type_distribution': dict(structure_types.most_common()),
                'pacing_distribution': dict(pacing_types.most_common()),
                'best_structure': structure_types.most_common(1)[0][0] if structure_types else None
            },
            'emotion_analysis': {
                'curve_distribution': dict(emotion_curves.most_common()),
                'most_common_curve': emotion_curves.most_common(1)[0][0] if emotion_curves else None
            },
            'ending_analysis': {
                'cta_type_distribution': dict(cta_types.most_common()),
                'memorable_ending_rate': round(memorable_ending_count / total * 100, 1) if total else 0,
                'best_cta_type': cta_types.most_common(1)[0][0] if cta_types else None
            },
            'overall': {
                'avg_score': round(sum(overall_scores) / len(overall_scores), 1) if overall_scores else 0,
                'high_quality_rate': round(
                    sum(1 for s in overall_scores if s >= 7) / len(overall_scores) * 100, 1
                ) if overall_scores else 0
            },
            'insights': generate_insights(
                hook_types, structure_types, emotion_curves, cta_types,
                hook_scores, visual_impact_count, total
            ),
            'recommendations': generate_recommendations(
                hook_types, structure_types, hook_scores, cta_types, total
            )
        }


def generate_insights(
    hook_types: Counter,
    structure_types: Counter,
    emotion_curves: Counter,
    cta_types: Counter,
    hook_scores: List[int],
    visual_impact_count: int,
    total: int
) -> List[str]:
    """生成内容洞察"""
    insights = []

    # 钩子洞察
    if hook_types:
        top_hook = hook_types.most_common(1)[0]
        insights.append(f"最受欢迎的开场钩子是【{top_hook[0]}】（占比{top_hook[1]/total*100:.1f}%）")

    if hook_scores:
        avg_score = sum(hook_scores) / len(hook_scores)
        if avg_score >= 4:
            insights.append(f"整体开场钩子质量较高（平均{avg_score:.1f}分）")
        elif avg_score < 3:
            insights.append(f"开场钩子质量有待提升（平均仅{avg_score:.1f}分）")

    # 结构洞察
    if structure_types:
        top_structure = structure_types.most_common(1)[0]
        insights.append(f"最常用的内容结构是【{top_structure[0]}】")

    # 情感洞察
    if emotion_curves:
        top_curve = emotion_curves.most_common(1)[0]
        insights.append(f"主流的情感曲线是【{top_curve[0]}】")

    # 视觉冲击洞察
    if total > 0:
        impact_rate = visual_impact_count / total * 100
        if impact_rate > 60:
            insights.append(f"视觉冲击运用较多（{impact_rate:.1f}%），有助于留住用户")
        elif impact_rate < 30:
            insights.append(f"视觉冲击较少（{impact_rate:.1f}%），建议增加视觉元素")

    # CTA洞察
    if cta_types:
        insights.append(f"最常见的CTA类型是【{cta_types.most_common(1)[0][0]}】")

    return insights


def generate_recommendations(
    hook_types: Counter,
    structure_types: Counter,
    hook_scores: List[int],
    cta_types: Counter,
    total: int
) -> List[str]:
    """生成内容建议"""
    recommendations = []

    # 钩子建议
    if hook_types:
        top_hooks = [h[0] for h in hook_types.most_common(2)]
        recommendations.append(f"开场钩子推荐使用：{'/'.join(top_hooks)}")

    if hook_scores and sum(hook_scores) / len(hook_scores) < 3.5:
        recommendations.append("建议加强开场设计，前3秒要有明确的吸引点")

    # 结构建议
    if structure_types:
        top_structure = structure_types.most_common(1)[0][0]
        recommendations.append(f"推荐采用【{top_structure}】结构，更容易获得用户认可")

    # CTA建议
    if cta_types:
        no_cta = cta_types.get('无明确CTA', 0)
        if no_cta > total * 0.3:
            recommendations.append("较多视频缺少CTA，建议在结尾添加明确的行动引导")
        else:
            recommendations.append(f"CTA设置良好，推荐使用【{cta_types.most_common(1)[0][0]}】")

    # 通用建议
    recommendations.extend([
        "开场3秒内必须抓住用户注意力",
        "内容节奏张弛有度，避免过于平淡",
        "结尾要有记忆点或行动引导"
    ])

    return recommendations[:8]  # 最多8条建议


def get_default_analysis(error: str = None) -> Dict[str, Any]:
    """获取默认分析结果"""
    result = {
        'success': False,
        'data': {
            'hook_analysis': {
                'hook_type': 'unknown',
                'hook_score': 0,
                'visual_impact': False,
                'hook_description': ''
            },
            'structure_analysis': {
                'structure_type': 'unknown',
                'pacing': 'unknown',
                'transition_quality': 'unknown',
                'structure_description': ''
            },
            'emotion_analysis': {
                'emotion_curve': 'unknown',
                'resonance_time': '',
                'resonance_type': '',
                'trust_building': ''
            },
            'ending_analysis': {
                'cta_type': 'unknown',
                'cta_position': '',
                'memorable_ending': False,
                'ending_description': ''
            },
            'overall_score': 0,
            'key_insights': []
        },
        'raw_result': 'default'
    }
    if error:
        result['error'] = error
    return result


def get_empty_stats() -> Dict[str, Any]:
    """获取空统计结果"""
    return {
        'hook_analysis': {
            'type_distribution': {},
            'avg_score': 0,
            'best_hook_type': None,
            'visual_impact_rate': 0
        },
        'structure_analysis': {
            'type_distribution': {},
            'pacing_distribution': {},
            'best_structure': None
        },
        'emotion_analysis': {
            'curve_distribution': {},
            'most_common_curve': None
        },
        'ending_analysis': {
            'cta_type_distribution': {},
            'memorable_ending_rate': 0,
            'best_cta_type': None
        },
        'overall': {
            'avg_score': 0,
            'high_quality_rate': 0
        },
        'insights': ['没有找到可分析的视频'],
        'recommendations': ['请先采集视频数据后再进行分析']
    }
