"""
视频产品分析统计与洞察生成器
从 video_product_analyzer.py 拆分出的统计、洞察、建议生成逻辑
"""

from typing import Dict, List, Any
from collections import Counter

from viral_agent.prompts.video_product_prompts import aggregate_product_stats


class ProductStatsGenerator:
    """
    产品统计生成器

    负责：
    - 批量分析的统计计算
    - 时间维度分析
    - CTA分析
    - 营销洞察生成
    - 优化建议生成
    """

    def calculate_statistics(
        self,
        analyses: List[Dict[str, Any]],
        notes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """计算批量分析的统计数据"""
        if not analyses:
            return get_empty_stats()

        # 使用 aggregate_product_stats 进行聚合
        base_stats = aggregate_product_stats(analyses)

        # 补充额外统计
        return {
            **base_stats,
            'timing_analysis': analyze_timing(analyses),
            'marketing_insights': generate_marketing_insights(analyses),
            'cta_analysis': analyze_cta(analyses),
            'recommendations': generate_recommendations(analyses)
        }


def analyze_timing(analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """分析时间维度统计"""
    appear_times = []
    use_times = []
    content_times = []
    cta_times = []

    for analysis in analyses:
        if not analysis.get('success'):
            continue
        features = analysis.get('features', {})

        if features.get('product_appear_timing'):
            appear_times.append(features['product_appear_timing'])
        if features.get('product_use_timing'):
            use_times.append(features['product_use_timing'])
        if features.get('content_start_timing'):
            content_times.append(features['content_start_timing'])
        if features.get('cta_timing'):
            cta_times.append(features['cta_timing'])

    def calc_stats(times):
        if not times:
            return {'avg': 0, 'min': 0, 'max': 0, 'count': 0}
        return {
            'avg': round(sum(times) / len(times), 1),
            'min': min(times),
            'max': max(times),
            'count': len(times)
        }

    return {
        'product_appear': calc_stats(appear_times),
        'product_use': calc_stats(use_times),
        'content_start': calc_stats(content_times),
        'cta': calc_stats(cta_times),
        'early_appear_rate': round(
            sum(1 for t in appear_times if t <= 30) / len(appear_times) * 100, 1
        ) if appear_times else 0
    }


def analyze_cta(analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """分析CTA维度统计"""
    cta_types = Counter()
    cta_count = 0
    total = len(analyses)

    for analysis in analyses:
        if not analysis.get('success'):
            continue
        features = analysis.get('features', {})

        if features.get('has_cta'):
            cta_count += 1
        if features.get('cta_type'):
            cta_types[features['cta_type']] += 1

    return {
        'cta_rate': round(cta_count / total * 100, 1) if total else 0,
        'cta_type_distribution': dict(cta_types.most_common()),
        'most_effective_cta': cta_types.most_common(1)[0][0] if cta_types else None
    }


def generate_marketing_insights(analyses: List[Dict[str, Any]]) -> List[str]:
    """生成营销洞察"""
    insights = []

    # 统计各维度
    scenes = Counter()
    intro_ways = Counter()
    embed_ways = Counter()
    brand_visible_count = 0
    total = len(analyses)

    for analysis in analyses:
        if not analysis.get('success'):
            continue
        features = analysis.get('features', {})

        if features.get('marketing_scene'):
            scenes[features['marketing_scene']] += 1
        if features.get('intro_strategy'):
            intro_ways[features['intro_strategy']] += 1
        if features.get('embed_strategy'):
            embed_ways[features['embed_strategy']] += 1
        if features.get('brand_visible'):
            brand_visible_count += 1

    # 生成洞察
    if scenes:
        top_scene = scenes.most_common(1)[0]
        insights.append(f"最常见的营销场景是【{top_scene[0]}】（占比{top_scene[1]/total*100:.1f}%）")

    if intro_ways:
        top_intro = intro_ways.most_common(1)[0]
        insights.append(f"最有效的产品引出方式是【{top_intro[0]}】")

    if embed_ways:
        top_embed = embed_ways.most_common(1)[0]
        insights.append(f"最自然的植入方式是【{top_embed[0]}】")

    brand_rate = brand_visible_count / total * 100 if total else 0
    if brand_rate > 50:
        insights.append(f"品牌曝光率较高（{brand_rate:.1f}%），利于品牌认知")
    else:
        insights.append(f"品牌曝光率偏低（{brand_rate:.1f}%），建议增加品牌展示")

    return insights


def generate_recommendations(analyses: List[Dict[str, Any]]) -> List[str]:
    """生成产品植入建议"""
    recommendations = []

    # 分析时间维度
    timing = analyze_timing(analyses)

    if timing['product_appear']['avg'] > 0:
        avg = timing['product_appear']['avg']
        if avg <= 30:
            recommendations.append(f"产品出现时机合理（平均{avg}秒），符合PRD要求")
        else:
            recommendations.append(f"产品平均{avg}秒才出现，建议提前至30秒内")

    if timing['early_appear_rate'] < 60:
        recommendations.append("建议更多视频在前30秒展示产品，提高用户注意力转化")

    # 分析CTA
    cta = analyze_cta(analyses)

    if cta['cta_rate'] < 50:
        recommendations.append(f"CTA率仅{cta['cta_rate']}%，建议增加明确的行动号召")
    else:
        recommendations.append(f"CTA覆盖率良好（{cta['cta_rate']}%）")

    if cta['most_effective_cta']:
        recommendations.append(f"推荐使用【{cta['most_effective_cta']}】类型的CTA")

    return recommendations


def get_default_analysis(error: str = None) -> Dict[str, Any]:
    """获取默认分析结果"""
    result = {
        'success': False,
        'data': {
            'product_appear_time': '/',
            'product_use_time': '/',
            'content_start_time': '/',
            'content_type': 'unknown',
            'entry_point': 'unknown',
            'product_intro_way': 'unknown',
            'product_embed_way': 'unknown',
            'marketing_scene': None,
            'mention_count': None,
            'brand_visible': None,
            'cta_type': None,
            'cta_time': '/'
        },
        'raw_result': 'default'
    }
    if error:
        result['error'] = error
    return result


def get_empty_stats() -> Dict[str, Any]:
    """获取空统计结果"""
    return {
        'videos_with_product': 0,
        'avg_product_appear_time': 0,
        'early_appear_rate': 0,
        'marketing_scene_distribution': {},
        'entry_point_distribution': {},
        'intro_way_distribution': {},
        'embed_way_distribution': {},
        'cta_type_distribution': {},
        'avg_mention_count': 0,
        'brand_visible_rate': 0,
        'cta_rate': 0,
        'timing_analysis': {
            'product_appear': {'avg': 0, 'min': 0, 'max': 0, 'count': 0},
            'product_use': {'avg': 0, 'min': 0, 'max': 0, 'count': 0},
            'content_start': {'avg': 0, 'min': 0, 'max': 0, 'count': 0},
            'cta': {'avg': 0, 'min': 0, 'max': 0, 'count': 0},
            'early_appear_rate': 0
        },
        'marketing_insights': [],
        'cta_analysis': {
            'cta_rate': 0,
            'cta_type_distribution': {},
            'most_effective_cta': None
        },
        'recommendations': ['没有找到可分析的视频']
    }
