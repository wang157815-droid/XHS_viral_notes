"""
视频内容质量分析结果解析器
从 video_content_prompts.py 拆分出的解析和特征提取逻辑
"""

from typing import Dict, Any, List
import json
import re


def parse_content_analysis(result: str) -> Dict[str, Any]:
    """
    解析视频内容质量分析结果

    Args:
        result: AI返回的分析结果（JSON格式）

    Returns:
        结构化的分析结果
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
            'hook_analysis', 'structure_analysis',
            'emotion_analysis', 'ending_analysis'
        ]
        for field in required_fields:
            if field not in data:
                data[field] = {}

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


def extract_content_features(analysis_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    从内容分析结果中提取关键特征

    Args:
        analysis_result: 解析后的分析结果

    Returns:
        提取的特征字典
    """
    features = {
        'hook_type': None,
        'hook_score': None,
        'has_visual_impact': False,
        'structure_type': None,
        'pacing': None,
        'emotion_curve': None,
        'has_resonance': False,
        'cta_type': None,
        'has_memorable_ending': False,
        'overall_score': None
    }

    if not analysis_result.get('success'):
        features['error'] = analysis_result.get('error')
        return features

    data = analysis_result.get('data', {})

    try:
        # 钩子特征
        hook = data.get('hook_analysis', {})
        features['hook_type'] = hook.get('hook_type')
        features['hook_score'] = hook.get('hook_score')
        features['has_visual_impact'] = hook.get('visual_impact', False)

        # 结构特征
        structure = data.get('structure_analysis', {})
        features['structure_type'] = structure.get('structure_type')
        features['pacing'] = structure.get('pacing')

        # 情感特征
        emotion = data.get('emotion_analysis', {})
        features['emotion_curve'] = emotion.get('emotion_curve')
        features['has_resonance'] = bool(emotion.get('resonance_time'))

        # 收尾特征
        ending = data.get('ending_analysis', {})
        features['cta_type'] = ending.get('cta_type')
        features['has_memorable_ending'] = ending.get('memorable_ending', False)

        # 总分
        features['overall_score'] = data.get('overall_score')

    except Exception as e:
        features['error'] = str(e)

    return features


def parse_quick_analysis(result: str) -> Dict[str, Any]:
    """解析快速分析结果"""
    try:
        result = result.strip()
        parts = result.split('|')

        if len(parts) >= 6:
            return {
                'hook_type': parts[0].strip(),
                'hook_score': int(parts[1].strip()) if parts[1].strip().isdigit() else None,
                'structure_type': parts[2].strip(),
                'pacing': parts[3].strip(),
                'cta_type': parts[4].strip(),
                'overall_score': int(parts[5].strip()) if parts[5].strip().isdigit() else None,
                'raw_result': result
            }
        else:
            return {
                'error': 'Invalid format',
                'raw_result': result
            }
    except Exception as e:
        return {
            'error': str(e),
            'raw_result': result
        }
