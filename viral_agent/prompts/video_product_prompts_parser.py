"""
视频产品分析结果解析器
从 video_product_prompts.py 拆分出的解析、特征提取和聚合逻辑
"""

from typing import Dict, Any, List


def parse_product_analysis(result: str) -> Dict[str, Any]:
    """解析视频产品分析结果（12个数据点）"""
    try:
        # 清理结果字符串
        result = result.strip()

        # 移除Result_前缀
        if result.startswith('Result_'):
            result = result[7:]

        # 解析12个数据点
        parts = result.split(',')

        if len(parts) >= 12:
            return {
                'success': True,
                'data': {
                    # 时间维度 A-C
                    'product_appear_time': parts[0].strip(),
                    'product_use_time': parts[1].strip(),
                    'content_start_time': parts[2].strip(),
                    # 分类维度 D-G
                    'content_type': parts[3].strip(),
                    'entry_point': parts[4].strip(),
                    'product_intro_way': parts[5].strip(),
                    'product_embed_way': parts[6].strip(),
                    # 营销维度 H-L（新增）
                    'marketing_scene': parts[7].strip(),
                    'mention_count': parts[8].strip(),
                    'brand_visible': parts[9].strip(),
                    'cta_type': parts[10].strip(),
                    'cta_time': parts[11].strip()
                },
                'raw_result': result
            }
        elif len(parts) >= 7:
            # 兼容旧版7个数据点
            return {
                'success': True,
                'data': {
                    'product_appear_time': parts[0].strip(),
                    'product_use_time': parts[1].strip(),
                    'content_start_time': parts[2].strip(),
                    'content_type': parts[3].strip(),
                    'entry_point': parts[4].strip(),
                    'product_intro_way': parts[5].strip(),
                    'product_embed_way': parts[6].strip(),
                    # 新增字段设为空
                    'marketing_scene': None,
                    'mention_count': None,
                    'brand_visible': None,
                    'cta_type': None,
                    'cta_time': None
                },
                'raw_result': result,
                'warning': 'Legacy 7-point format detected'
            }
        else:
            return {
                'success': False,
                'error': 'Insufficient data points',
                'raw_result': result,
                'parsed_count': len(parts)
            }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'raw_result': result
        }


def extract_product_features(analysis_result: Dict[str, Any]) -> Dict[str, Any]:
    """从产品分析结果中提取关键特征"""
    features = {
        # 时间特征
        'has_product': False,
        'product_appear_timing': None,
        'product_use_timing': None,
        'content_start_timing': None,
        'early_product_appear': False,  # 产品是否在前30秒出现
        # 分类特征
        'content_category': None,
        'content_subcategory': None,
        'entry_strategy': None,
        'intro_strategy': None,
        'embed_strategy': None,
        # 营销特征（新增）
        'marketing_scene': None,
        'mention_count': 0,
        'brand_visible': False,
        'has_cta': False,
        'cta_type': None,
        'cta_timing': None
    }

    if not analysis_result.get('success'):
        features['error'] = analysis_result.get('error')
        return features

    data = analysis_result.get('data', {})

    try:
        # 时间特征提取
        _extract_time_features(data, features)
        # 分类特征提取
        _extract_category_features(data, features)
        # 营销特征提取
        _extract_marketing_features(data, features)

    except Exception as e:
        features['error'] = str(e)

    return features


def _extract_time_features(data: Dict[str, Any], features: Dict[str, Any]) -> None:
    """提取时间特征"""
    product_appear = data.get('product_appear_time', '/')
    if product_appear != '/':
        features['has_product'] = True
        time_str = product_appear.replace('s', '')
        if time_str.isdigit():
            features['product_appear_timing'] = int(time_str)
            features['early_product_appear'] = int(time_str) <= 30

    product_use = data.get('product_use_time', '/')
    if product_use != '/':
        time_str = product_use.replace('s', '')
        if time_str.isdigit():
            features['product_use_timing'] = int(time_str)

    content_start = data.get('content_start_time', '/')
    if content_start != '/':
        time_str = content_start.replace('s', '')
        if time_str.isdigit():
            features['content_start_timing'] = int(time_str)


def _extract_category_features(data: Dict[str, Any], features: Dict[str, Any]) -> None:
    """提取分类特征"""
    content_type = data.get('content_type', '')
    if '-' in content_type:
        parts = content_type.split('-')
        features['content_category'] = parts[0]
        features['content_subcategory'] = '-'.join(parts[1:])

    features['entry_strategy'] = data.get('entry_point')
    features['intro_strategy'] = data.get('product_intro_way')
    features['embed_strategy'] = data.get('product_embed_way')


def _extract_marketing_features(data: Dict[str, Any], features: Dict[str, Any]) -> None:
    """提取营销特征"""
    features['marketing_scene'] = data.get('marketing_scene')

    mention_count = data.get('mention_count', '0')
    if str(mention_count).isdigit():
        features['mention_count'] = int(mention_count)

    brand_visible = data.get('brand_visible', '')
    features['brand_visible'] = brand_visible in ['清晰可见', '部分可见']

    cta_type = data.get('cta_type', '')
    features['cta_type'] = cta_type
    features['has_cta'] = cta_type not in ['/', '无明确引导', None, '']

    cta_time = data.get('cta_time', '/')
    if cta_time != '/':
        time_str = cta_time.replace('s', '')
        if time_str.isdigit():
            features['cta_timing'] = int(time_str)


def aggregate_product_stats(analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合多个视频的产品分析统计"""
    stats = _init_stats(len(analyses))
    counters = _init_counters()

    for analysis in analyses:
        if not analysis.get('success'):
            continue
        features = extract_product_features(analysis)
        _update_stats_from_features(stats, counters, features)

    _calculate_averages(stats, counters)

    return stats


def _init_stats(total: int) -> Dict[str, Any]:
    """初始化统计结构"""
    return {
        'total_videos': total,
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
        'cta_rate': 0
    }


def _init_counters() -> Dict[str, Any]:
    """初始化计数器"""
    return {
        'appear_times': [],
        'mention_counts': [],
        'early_appear_count': 0,
        'brand_visible_count': 0,
        'cta_count': 0
    }


def _update_stats_from_features(
    stats: Dict[str, Any],
    counters: Dict[str, Any],
    features: Dict[str, Any]
) -> None:
    """从特征更新统计数据"""
    if features.get('has_product'):
        stats['videos_with_product'] += 1
        if features.get('product_appear_timing'):
            counters['appear_times'].append(features['product_appear_timing'])
        if features.get('early_product_appear'):
            counters['early_appear_count'] += 1

    if features.get('mention_count'):
        counters['mention_counts'].append(features['mention_count'])

    if features.get('brand_visible'):
        counters['brand_visible_count'] += 1

    if features.get('has_cta'):
        counters['cta_count'] += 1

    # 分布统计
    for field, dist_key in [
        ('marketing_scene', 'marketing_scene_distribution'),
        ('entry_strategy', 'entry_point_distribution'),
        ('intro_strategy', 'intro_way_distribution'),
        ('embed_strategy', 'embed_way_distribution'),
        ('cta_type', 'cta_type_distribution')
    ]:
        value = features.get(field)
        if value and value != '/':
            stats[dist_key][value] = stats[dist_key].get(value, 0) + 1


def _calculate_averages(stats: Dict[str, Any], counters: Dict[str, Any]) -> None:
    """计算平均值和比例"""
    appear_times = counters['appear_times']
    mention_counts = counters['mention_counts']

    if appear_times:
        stats['avg_product_appear_time'] = round(sum(appear_times) / len(appear_times), 1)

    if stats['videos_with_product'] > 0:
        stats['early_appear_rate'] = round(
            counters['early_appear_count'] / stats['videos_with_product'] * 100, 1
        )

    if mention_counts:
        stats['avg_mention_count'] = round(sum(mention_counts) / len(mention_counts), 1)

    if stats['total_videos'] > 0:
        stats['brand_visible_rate'] = round(
            counters['brand_visible_count'] / stats['total_videos'] * 100, 1
        )
        stats['cta_rate'] = round(
            counters['cta_count'] / stats['total_videos'] * 100, 1
        )
