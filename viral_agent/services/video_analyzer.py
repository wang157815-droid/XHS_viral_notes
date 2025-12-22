"""
视频分析器
负责分析视频笔记的特征，包括封面帧、视频时长、内容结构等
"""
import os
import json
import re
import requests
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter
from loguru import logger
import subprocess
from PIL import Image
import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    logger.warning("OpenCV未安装，视频分析功能将受限")


class VideoAnalyzer:
    """视频笔记分析器"""

    def __init__(self, cache_dir: str = "datas/video_cache"):
        """
        初始化视频分析器

        Args:
            cache_dir: 视频缓存目录
        """
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

        # 检查ffmpeg是否可用
        self.ffmpeg_available = self._check_ffmpeg()

        if not self.ffmpeg_available and not CV2_AVAILABLE:
            logger.warning("FFmpeg和OpenCV均不可用，视频分析功能将大幅受限")

    def _check_ffmpeg(self) -> bool:
        """检查ffmpeg是否安装"""
        try:
            subprocess.run(['ffmpeg', '-version'],
                         capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def analyze_video_notes(self, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        分析视频笔记特征

        Args:
            notes: 包含视频信息的笔记列表

        Returns:
            视频分析结果
        """
        logger.info(f"开始分析视频笔记...")

        video_notes = [n for n in notes if n.get('note_type') == '视频']
        logger.info(f"识别到 {len(video_notes)} 个视频笔记")

        if not video_notes:
            return {
                'total_videos': 0,
                'message': '无视频笔记'
            }

        results = {
            'total_videos': len(video_notes),
            'duration_analysis': self._analyze_durations(video_notes),
            'cover_analysis': self._analyze_video_covers(video_notes),
            'content_structure': self._analyze_video_structure(video_notes),
            'opening_analysis': self._analyze_openings(video_notes),
            'engagement_patterns': self._analyze_video_engagement(video_notes)
        }

        logger.success("视频分析完成")
        return results

    def _analyze_durations(self, video_notes: List[Dict]) -> Dict[str, Any]:
        """
        分析视频时长特征

        Returns:
            时长分析结果
        """
        durations = []

        for note in video_notes:
            # 从视频信息中提取时长（如果有）
            duration = self._extract_duration(note)
            if duration:
                durations.append(duration)

        if not durations:
            return {'available': False}

        avg_duration = sum(durations) / len(durations)

        # 时长分布
        distribution = {
            '<30s': sum(1 for d in durations if d < 30),
            '30s-1min': sum(1 for d in durations if 30 <= d < 60),
            '1-3min': sum(1 for d in durations if 60 <= d < 180),
            '3-5min': sum(1 for d in durations if 180 <= d < 300),
            '>5min': sum(1 for d in durations if d >= 300)
        }

        # 找出最优时长区间
        optimal_range = max(distribution.items(), key=lambda x: x[1])[0]

        return {
            'available': True,
            'avg_duration': round(avg_duration, 1),
            'distribution': distribution,
            'optimal_range': optimal_range,
            'recommendation': self._get_duration_recommendation(avg_duration, optimal_range)
        }

    def _analyze_video_covers(self, video_notes: List[Dict]) -> Dict[str, Any]:
        """
        分析视频封面特征

        Returns:
            封面分析结果
        """
        cover_features = {
            'has_text_overlay': 0,  # 有文字覆盖
            'has_person': 0,        # 有人物出镜
            'has_product': 0,       # 有产品展示
            'high_contrast': 0,     # 高对比度
            'bright_colors': 0      # 明亮色彩
        }

        analyzed_count = 0

        for note in video_notes:
            cover_url = note.get('video_cover')
            if not cover_url:
                continue

            # 下载并分析封面
            features = self._analyze_single_cover(cover_url, note.get('note_id'))
            if features:
                analyzed_count += 1
                for key in cover_features:
                    if features.get(key):
                        cover_features[key] += 1

        if analyzed_count == 0:
            return {'available': False}

        # 转换为百分比
        cover_percentages = {
            key: round(count / analyzed_count * 100, 1)
            for key, count in cover_features.items()
        }

        # 生成封面建议
        recommendations = []
        if cover_percentages['has_text_overlay'] > 60:
            recommendations.append("文字标题覆盖能有效传达视频主题")
        if cover_percentages['has_person'] > 40:
            recommendations.append("真人出镜封面提升点击率")
        if cover_percentages['bright_colors'] > 50:
            recommendations.append("明亮色彩更吸引眼球")

        return {
            'available': True,
            'analyzed_count': analyzed_count,
            'features': cover_percentages,
            'recommendations': recommendations
        }

    def _analyze_video_structure(self, video_notes: List[Dict]) -> Dict[str, Any]:
        """
        分析视频内容结构

        Returns:
            结构分析结果
        """
        structures = {
            'has_intro': 0,      # 有片头
            'has_outro': 0,      # 有片尾
            'has_subtitle': 0,   # 有字幕
            'has_bgm': 0,        # 有背景音乐
            'has_transition': 0, # 有转场效果
            'multi_scene': 0     # 多场景切换
        }

        # 基于描述文本分析（简化版）
        for note in video_notes:
            desc = note.get('desc', '')
            title = note.get('title', '')
            full_text = f"{title} {desc}"

            # 通过关键词判断
            if any(word in full_text for word in ['开场', '片头', 'intro']):
                structures['has_intro'] += 1
            if any(word in full_text for word in ['结尾', '片尾', 'outro', '关注']):
                structures['has_outro'] += 1
            if any(word in full_text for word in ['字幕', '配文', 'subtitle']):
                structures['has_subtitle'] += 1
            if any(word in full_text for word in ['BGM', '音乐', '背景音', '配乐']):
                structures['has_bgm'] += 1
            if any(word in full_text for word in ['转场', '切换', '过渡']):
                structures['has_transition'] += 1
            if any(word in full_text for word in ['场景', '多个', '不同地点']):
                structures['multi_scene'] += 1

        total = len(video_notes)
        structure_percentages = {
            key: round(count / total * 100, 1)
            for key, count in structures.items()
        }

        # 结构建议
        structure_tips = []
        if structure_percentages['has_intro'] < 30:
            structure_tips.append("添加吸引人的开场白")
        if structure_percentages['has_outro'] > 60:
            structure_tips.append("片尾引导关注很重要")
        if structure_percentages['has_bgm'] > 70:
            structure_tips.append("合适的BGM提升观看体验")

        return {
            'features': structure_percentages,
            'tips': structure_tips
        }

    def _analyze_openings(self, video_notes: List[Dict]) -> Dict[str, Any]:
        """
        分析视频开头策略

        Returns:
            开头分析结果
        """
        opening_types = {
            'question': 0,      # 提问式
            'preview': 0,       # 预告式
            'direct': 0,        # 直接展示
            'story': 0,         # 故事式
            'problem': 0,       # 问题痛点
            'result_first': 0   # 效果前置
        }

        for note in video_notes:
            desc = note.get('desc', '')[:100]  # 只看开头部分
            title = note.get('title', '')

            # 识别开头类型
            if '?' in title or '？' in title or any(w in desc for w in ['怎么', '为什么', '如何']):
                opening_types['question'] += 1
            if any(w in desc for w in ['先看', '预告', '剧透']):
                opening_types['preview'] += 1
            if any(w in desc for w in ['直接', '马上', '立刻']):
                opening_types['direct'] += 1
            if any(w in desc for w in ['故事', '经历', '那天']):
                opening_types['story'] += 1
            if any(w in desc for w in ['问题', '困扰', '烦恼']):
                opening_types['problem'] += 1
            if any(w in desc for w in ['效果', '前后', '对比']):
                opening_types['result_first'] += 1

        total = sum(opening_types.values()) or 1
        opening_distribution = {
            key: round(count / total * 100, 1)
            for key, count in opening_types.items()
        }

        # 找出最受欢迎的开头方式
        top_openings = sorted(opening_types.items(), key=lambda x: x[1], reverse=True)[:3]

        opening_strategies = []
        for opening_type, _ in top_openings:
            if opening_type == 'question':
                opening_strategies.append("疑问开场：激发好奇心")
            elif opening_type == 'preview':
                opening_strategies.append("预告开场：展示精彩内容")
            elif opening_type == 'result_first':
                opening_strategies.append("效果前置：先展示结果")
            elif opening_type == 'problem':
                opening_strategies.append("痛点开场：引发共鸣")

        return {
            'distribution': opening_distribution,
            'top_types': [t[0] for t in top_openings],
            'strategies': opening_strategies
        }

    def _analyze_video_engagement(self, video_notes: List[Dict]) -> Dict[str, Any]:
        """
        分析视频互动特征

        Returns:
            互动分析结果
        """
        # 收集互动数据
        interactions = []
        for note in video_notes:
            interaction = {
                'liked': int(note.get('liked_count', 0)),
                'collected': int(note.get('collected_count', 0)),
                'comment': int(note.get('comment_count', 0)),
                'share': int(note.get('share_count', 0)),
                'total': 0
            }
            interaction['total'] = sum([
                interaction['liked'],
                interaction['collected'],
                interaction['comment'],
                interaction['share']
            ])
            interactions.append(interaction)

        if not interactions:
            return {'available': False}

        # 计算平均值
        avg_engagement = {
            'liked': sum(i['liked'] for i in interactions) / len(interactions),
            'collected': sum(i['collected'] for i in interactions) / len(interactions),
            'comment': sum(i['comment'] for i in interactions) / len(interactions),
            'share': sum(i['share'] for i in interactions) / len(interactions)
        }

        # 视频特有指标：完播率相关（根据评论/点赞比例估算）
        comment_rate = avg_engagement['comment'] / (avg_engagement['liked'] + 1)

        engagement_insights = []
        if comment_rate > 0.1:
            engagement_insights.append("高评论率说明内容引发讨论")
        if avg_engagement['share'] / (avg_engagement['liked'] + 1) > 0.05:
            engagement_insights.append("高分享率说明内容有传播价值")

        return {
            'available': True,
            'avg_engagement': avg_engagement,
            'comment_rate': round(comment_rate * 100, 2),
            'insights': engagement_insights
        }

    def _extract_duration(self, note: Dict) -> Optional[float]:
        """
        提取视频时长

        Args:
            note: 笔记数据

        Returns:
            时长（秒）
        """
        # 尝试从不同字段提取时长
        # 这里需要根据实际数据结构调整
        if 'video_duration' in note:
            return float(note['video_duration'])

        # 尝试从描述中提取
        desc = note.get('desc', '')
        duration_match = re.search(r'(\d+)[分:](\d+)[秒]?', desc)
        if duration_match:
            minutes = int(duration_match.group(1))
            seconds = int(duration_match.group(2))
            return minutes * 60 + seconds

        return None

    def _analyze_single_cover(self, cover_url: str, note_id: str) -> Optional[Dict]:
        """
        分析单个视频封面

        Args:
            cover_url: 封面URL
            note_id: 笔记ID

        Returns:
            封面特征
        """
        try:
            # 下载封面图片
            cache_path = os.path.join(self.cache_dir, f"{note_id}_cover.jpg")

            if not os.path.exists(cache_path):
                response = requests.get(cover_url, timeout=10)
                if response.status_code == 200:
                    with open(cache_path, 'wb') as f:
                        f.write(response.content)

            # 使用PIL分析图片
            from PIL import Image
            img = Image.open(cache_path)
            img_array = np.array(img)

            features = {
                'has_text_overlay': self._detect_text_overlay(img_array),
                'has_person': self._detect_person(img_array),
                'has_product': self._detect_product(img_array),
                'high_contrast': self._check_contrast(img_array),
                'bright_colors': self._check_brightness(img_array)
            }

            return features

        except Exception as e:
            logger.error(f"分析封面失败: {e}")
            return None

    def _detect_text_overlay(self, img_array: np.ndarray) -> bool:
        """检测是否有文字覆盖"""
        # 简化版：检测高对比度区域
        gray = np.mean(img_array, axis=2)
        edges = np.gradient(gray)
        edge_density = np.mean(np.abs(edges))
        return edge_density > 30

    def _detect_person(self, img_array: np.ndarray) -> bool:
        """检测是否有人物"""
        # 简化的肤色检测
        r = img_array[:, :, 0]
        g = img_array[:, :, 1]
        b = img_array[:, :, 2]

        skin_mask = (r > 95) & (g > 40) & (b > 20) & \
                   (r > g) & (r > b) & (np.abs(r - g) > 15)

        skin_ratio = np.sum(skin_mask) / (img_array.shape[0] * img_array.shape[1])
        return skin_ratio > 0.05

    def _detect_product(self, img_array: np.ndarray) -> bool:
        """检测是否有产品"""
        # 检测是否有清晰的物体轮廓
        gray = np.mean(img_array, axis=2)
        edges = np.gradient(gray)
        return np.max(np.abs(edges)) > 100

    def _check_contrast(self, img_array: np.ndarray) -> bool:
        """检查对比度"""
        gray = np.mean(img_array, axis=2)
        return np.std(gray) > 50

    def _check_brightness(self, img_array: np.ndarray) -> bool:
        """检查亮度"""
        avg_brightness = np.mean(img_array)
        return avg_brightness > 127

    def _get_duration_recommendation(self, avg_duration: float, optimal_range: str) -> str:
        """
        获取时长建议

        Args:
            avg_duration: 平均时长
            optimal_range: 最优区间

        Returns:
            时长建议
        """
        if optimal_range == '30s-1min':
            return "黄金时长30秒-1分钟，适合快速种草"
        elif optimal_range == '1-3min':
            return "1-3分钟最受欢迎，内容充实不冗长"
        elif optimal_range == '<30s':
            return "超短视频适合快速展示效果"
        elif optimal_range == '3-5min':
            return "3-5分钟适合深度测评和教程"
        else:
            return "根据内容调整时长，避免注水"

    def extract_key_frames(self, video_url: str, output_dir: str) -> List[str]:
        """
        提取关键帧（需要ffmpeg或opencv）

        Args:
            video_url: 视频URL
            output_dir: 输出目录

        Returns:
            关键帧图片路径列表
        """
        if not self.ffmpeg_available and not CV2_AVAILABLE:
            logger.warning("无法提取关键帧：ffmpeg和OpenCV均不可用")
            return []

        # 这里实现关键帧提取逻辑
        # 可以提取开头、中间、结尾的几个关键帧
        return []

    def analyze_video_content_by_frames(self, frames: List[str]) -> Dict[str, Any]:
        """
        通过关键帧分析视频内容

        Args:
            frames: 关键帧路径列表

        Returns:
            内容分析结果
        """
        # 分析关键帧中的内容变化
        # 识别场景切换、产品展示时机等
        return {
            'scene_changes': 0,
            'product_appearances': [],
            'visual_consistency': 0.0
        }