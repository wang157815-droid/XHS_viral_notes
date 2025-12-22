"""
视频综合推理结果解析器
从 video_synthesis_prompts.py 拆分出的解析和提取逻辑
"""

from typing import Dict, Any, List
import json
import re


def parse_synthesis_result(result: str) -> Dict[str, Any]:
    """
    解析视频综合推理结果

    Args:
        result: AI返回的分析结果（JSON格式）

    Returns:
        结构化的推理结果
    """
    try:
        # 清理结果字符串
        result = result.strip()

        # 尝试提取JSON部分
        json_match = re.search(r'```json\s*(.*?)\s*```', result, re.DOTALL)
        if json_match:
            result = json_match.group(1)
        else:
            # 尝试直接解析
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                result = json_match.group(0)

        # 解析JSON
        data = json.loads(result)

        # 验证必要字段
        required_fields = [
            'hook_strategy', 'structure_strategy', 'timing_strategy',
            'product_strategy', 'cta_strategy', 'best_practices'
        ]
        missing_fields = [f for f in required_fields if f not in data]

        if missing_fields:
            return {
                'success': True,
                'data': data,
                'warning': f'Missing fields: {missing_fields}'
            }

        return {
            'success': True,
            'data': data
        }

    except json.JSONDecodeError as e:
        return {
            'success': False,
            'error': f'JSON解析失败: {str(e)}',
            'raw_result': result
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'raw_result': result
        }


def extract_key_recommendations(synthesis_result: Dict[str, Any]) -> List[str]:
    """
    从综合推理结果中提取关键建议

    Args:
        synthesis_result: 解析后的推理结果

    Returns:
        关键建议列表
    """
    recommendations = []

    if not synthesis_result.get('success'):
        return recommendations

    data = synthesis_result.get('data', {})

    try:
        # 提取钩子策略的关键点
        _extract_hook_recommendations(data, recommendations)
        # 提取时间策略
        _extract_timing_recommendations(data, recommendations)
        # 提取产品策略
        _extract_product_recommendations(data, recommendations)
        # 提取CTA策略
        _extract_cta_recommendations(data, recommendations)
        # 提取最佳实践
        _extract_best_practices(data, recommendations)

    except Exception:
        pass

    return recommendations


def _extract_hook_recommendations(
    data: Dict[str, Any],
    recommendations: List[str]
) -> None:
    """提取钩子策略建议"""
    hook_strategy = data.get('hook_strategy', {})
    if hook_strategy.get('best_hook_types'):
        types = '/'.join(hook_strategy['best_hook_types'][:2])
        recommendations.append(f"开场钩子：优先使用{types}类型")


def _extract_timing_recommendations(
    data: Dict[str, Any],
    recommendations: List[str]
) -> None:
    """提取时间策略建议"""
    timing_strategy = data.get('timing_strategy', {})
    if timing_strategy.get('product_appear_timing'):
        recommendations.append(f"产品出现时机：{timing_strategy['product_appear_timing']}")


def _extract_product_recommendations(
    data: Dict[str, Any],
    recommendations: List[str]
) -> None:
    """提取产品策略建议"""
    product_strategy = data.get('product_strategy', {})
    if product_strategy.get('best_intro_ways'):
        ways = '/'.join(product_strategy['best_intro_ways'][:2])
        recommendations.append(f"产品引出方式：{ways}")


def _extract_cta_recommendations(
    data: Dict[str, Any],
    recommendations: List[str]
) -> None:
    """提取CTA策略建议"""
    cta_strategy = data.get('cta_strategy', {})
    if cta_strategy.get('best_cta_types'):
        types = '/'.join(cta_strategy['best_cta_types'][:2])
        recommendations.append(f"行动号召：{types}")


def _extract_best_practices(
    data: Dict[str, Any],
    recommendations: List[str]
) -> None:
    """提取最佳实践"""
    best_practices = data.get('best_practices', [])
    recommendations.extend(best_practices[:5])


def parse_quick_synthesis(result: str) -> List[str]:
    """解析快速综合推理结果"""
    recommendations = []
    try:
        lines = result.strip().split('\n')
        for line in lines:
            # 移除序号前缀
            line = re.sub(r'^[\d]+[\.\、\)]\s*', '', line.strip())
            if line:
                recommendations.append(line)
    except Exception:
        pass
    return recommendations[:5]


def parse_checklist(result: str) -> Dict[str, List[str]]:
    """解析检查清单结果"""
    checklist = {
        'opening': [],
        'content': [],
        'product': [],
        'closing': [],
        'technical': []
    }

    section_map = {
        '开场': 'opening',
        '内容': 'content',
        '产品': 'product',
        '收尾': 'closing',
        '技术': 'technical'
    }

    current_section = None

    try:
        lines = result.strip().split('\n')
        for line in lines:
            line = line.strip()

            # 检测段落标题
            current_section = _detect_section(line, section_map, current_section)

            # 检测检查项
            _parse_checklist_item(line, current_section, checklist)

    except Exception:
        pass

    return checklist


def _detect_section(
    line: str,
    section_map: Dict[str, str],
    current_section: str
) -> str:
    """检测当前行是否为段落标题"""
    for key, section in section_map.items():
        if key in line and ('检查' in line or '：' in line or ':' in line):
            return section
    return current_section


def _parse_checklist_item(
    line: str,
    current_section: str,
    checklist: Dict[str, List[str]]
) -> None:
    """解析检查项"""
    if line.startswith('□') or line.startswith('☐') or line.startswith('-'):
        item = re.sub(r'^[□☐\-]\s*', '', line)
        if current_section and item:
            checklist[current_section].append(item)
