"""
爆款笔记特征提取器
负责从笔记中提取关键特征用于分析
"""
import re
import jieba
import json
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter
from datetime import datetime
from loguru import logger

from viral_agent.models.viral_note import ViralNote
from viral_agent.services.cover_analyzer import CoverAnalyzer
from viral_agent.services.video_analyzer import VideoAnalyzer


class ViralFeatureExtractor:
    """爆款笔记特征提取器"""

    def __init__(self, enable_cover_analysis: bool = True):
        """初始化特征提取器

        Args:
            enable_cover_analysis: 是否启用封面分析
        """
        # 初始化jieba分词
        self._init_jieba()

        # 初始化封面分析器
        self.enable_cover_analysis = enable_cover_analysis
        if enable_cover_analysis:
            try:
                self.cover_analyzer = CoverAnalyzer()
                logger.info("封面分析器初始化成功")
            except Exception as e:
                logger.warning(f"封面分析器初始化失败: {e}，将跳过封面分析")
                self.cover_analyzer = None
                self.enable_cover_analysis = False
        else:
            self.cover_analyzer = None

        # 初始化视频分析器
        try:
            self.video_analyzer = VideoAnalyzer()
            logger.info("视频分析器初始化成功")
        except Exception as e:
            logger.warning(f"视频分析器初始化失败: {e}，将跳过视频特征分析")
            self.video_analyzer = None

    def _init_jieba(self):
        """初始化jieba分词器，添加自定义词典"""
        # 添加常见的小红书词汇
        custom_words = [
            '姐妹们', 'OOTD', '平替', '测评', '种草', '拔草',
            '踩雷', '避雷', '绝绝子', '码住', '蹲一个', '冲冲冲',
            '宝子们', '家人们', '集美们', 'yyds', '绝了'
        ]
        for word in custom_words:
            jieba.add_word(word)

    def extract_all_features(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        提取所有笔记的特征

        Args:
            notes: 爆款笔记列表

        Returns:
            特征字典
        """
        logger.info(f"开始提取 {len(notes)} 篇笔记的特征...")

        features = {
            'title_features': self.extract_title_features(notes),
            'content_features': self.extract_content_features(notes),
            'time_features': self.extract_time_features(notes),
            'user_features': self.extract_user_features(notes),
            'interaction_features': self.extract_interaction_features(notes)
        }

        # 封面分析（如果启用）
        if self.enable_cover_analysis and self.cover_analyzer:
            logger.info("开始分析封面特征...")
            notes_data = [note.to_dict() for note in notes]
            features['cover_features'] = self.cover_analyzer.analyze_covers(notes_data)

            # 所有图片OCR分析（扩展功能）
            logger.info("开始分析所有图片（OCR文字提取）...")
            features['all_images_ocr'] = self.cover_analyzer.analyze_all_images(notes_data, max_images_per_note=9)
        else:
            features['cover_features'] = {
                'enabled': False,
                'message': 'Cover analysis disabled or not available'
            }
            features['all_images_ocr'] = {
                'enabled': False,
                'message': 'OCR analysis disabled'
            }

        # 视频特征分析
        if self.video_analyzer:
            logger.info("开始分析视频特征...")
            notes_data = [note.to_dict() for note in notes]
            features['video_features'] = self.video_analyzer.analyze_video_notes(notes_data)
        else:
            features['video_features'] = {
                'enabled': False,
                'message': 'Video analysis not available'
            }

        logger.success("特征提取完成")
        return features

    def extract_title_features(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        提取标题特征

        Returns:
            标题特征字典
        """
        titles = [note.title for note in notes if note.title]

        # 标题长度分析
        title_lengths = [len(title) for title in titles]
        avg_length = sum(title_lengths) / len(title_lengths) if title_lengths else 0

        # 标题关键词提取
        all_words = []
        for title in titles:
            words = list(jieba.cut(title))
            all_words.extend([w for w in words if len(w) > 1])  # 过滤单字

        word_freq = Counter(all_words)
        top_keywords = word_freq.most_common(20)

        # 标题模式分析
        patterns = self._analyze_title_patterns(titles)

        # Emoji使用分析
        emoji_stats = self._analyze_emoji_usage(titles)

        return {
            'avg_length': round(avg_length, 1),
            'length_distribution': {
                '<10': sum(1 for l in title_lengths if l < 10),
                '10-20': sum(1 for l in title_lengths if 10 <= l < 20),
                '20-30': sum(1 for l in title_lengths if 20 <= l < 30),
                '>30': sum(1 for l in title_lengths if l >= 30)
            },
            'top_keywords': [{'word': w, 'count': c} for w, c in top_keywords],
            'common_patterns': patterns,
            'emoji_usage': emoji_stats
        }

    def extract_content_features(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        提取内容特征

        Returns:
            内容特征字典
        """
        contents = [note.desc for note in notes if note.desc]

        # 内容长度分析
        content_lengths = [len(content) for content in contents]
        avg_length = sum(content_lengths) / len(content_lengths) if content_lengths else 0

        # 内容关键词提取
        all_words = []
        for content in contents:
            words = list(jieba.cut(content))
            all_words.extend([w for w in words if len(w) > 1])

        word_freq = Counter(all_words)
        top_keywords = word_freq.most_common(30)

        # 标签分析
        all_tags = []
        for note in notes:
            if note.tags:
                all_tags.extend(note.tags)

        tag_freq = Counter(all_tags)
        top_tags = tag_freq.most_common(15)

        # 内容结构分析
        structure_patterns = self._analyze_content_structure(contents)

        return {
            'avg_length': round(avg_length, 1),
            'length_distribution': {
                '<100': sum(1 for l in content_lengths if l < 100),
                '100-300': sum(1 for l in content_lengths if 100 <= l < 300),
                '300-500': sum(1 for l in content_lengths if 300 <= l < 500),
                '>500': sum(1 for l in content_lengths if l >= 500)
            },
            'top_keywords': [{'word': w, 'count': c} for w, c in top_keywords[:20]],
            'top_tags': [{'tag': t, 'count': c} for t, c in top_tags],
            'structure_patterns': structure_patterns
        }

    def extract_time_features(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        提取时间特征

        Returns:
            时间特征字典
        """
        # 发布时间分析
        upload_times = []
        for note in notes:
            if note.upload_time:
                try:
                    # 尝试解析时间
                    if '天前' in note.upload_time:
                        days = int(note.upload_time.replace('天前', ''))
                        upload_times.append(('recent', days))
                    elif '昨天' in note.upload_time:
                        upload_times.append(('recent', 1))
                    elif '今天' in note.upload_time:
                        upload_times.append(('recent', 0))
                    else:
                        upload_times.append(('date', note.upload_time))
                except:
                    continue

        # 统计发布时间分布
        recent_distribution = {
            '今天': sum(1 for t, d in upload_times if t == 'recent' and d == 0),
            '1-3天': sum(1 for t, d in upload_times if t == 'recent' and 1 <= d <= 3),
            '4-7天': sum(1 for t, d in upload_times if t == 'recent' and 4 <= d <= 7),
            '7天以上': sum(1 for t, d in upload_times if t == 'recent' and d > 7)
        }

        return {
            'upload_distribution': recent_distribution,
            'total_analyzed': len(upload_times)
        }

    def extract_user_features(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        提取用户特征

        Returns:
            用户特征字典
        """
        # 统计不同用户数量
        unique_users = set(note.user_id for note in notes)

        # 统计用户爆款数量分布
        user_note_counts = Counter(note.user_id for note in notes)

        # 找出高产爆款用户
        top_users = user_note_counts.most_common(10)
        top_user_info = []
        for user_id, count in top_users:
            # 找到该用户的笔记
            user_notes = [n for n in notes if n.user_id == user_id]
            if user_notes:
                top_user_info.append({
                    'nickname': user_notes[0].nickname,
                    'viral_count': count,
                    'avg_interaction': sum(n.interaction_score for n in user_notes) // len(user_notes)
                })

        # IP归属地分析
        locations = [note.ip_location for note in notes if note.ip_location]
        location_freq = Counter(locations)
        top_locations = location_freq.most_common(10)

        return {
            'unique_users': len(unique_users),
            'avg_notes_per_user': round(len(notes) / len(unique_users), 2) if unique_users else 0,
            'top_viral_creators': top_user_info[:5],
            'top_locations': [{'location': l, 'count': c} for l, c in top_locations]
        }

    def extract_interaction_features(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """
        提取互动特征

        Returns:
            互动特征字典
        """
        # 互动数据统计
        liked_counts = [note.liked_count for note in notes]
        collected_counts = [note.collected_count for note in notes]
        comment_counts = [note.comment_count for note in notes]
        interaction_scores = [note.interaction_score for note in notes]

        # 计算各项平均值
        avg_liked = sum(liked_counts) / len(liked_counts) if liked_counts else 0
        avg_collected = sum(collected_counts) / len(collected_counts) if collected_counts else 0
        avg_comment = sum(comment_counts) / len(comment_counts) if comment_counts else 0

        # 收藏/点赞比例分析
        collection_rates = []
        for note in notes:
            if note.liked_count > 0:
                rate = note.collected_count / note.liked_count
                collection_rates.append(rate)

        avg_collection_rate = sum(collection_rates) / len(collection_rates) if collection_rates else 0

        # 互动分布
        interaction_distribution = {
            '1000-5000': sum(1 for s in interaction_scores if 1000 <= s < 5000),
            '5000-10000': sum(1 for s in interaction_scores if 5000 <= s < 10000),
            '10000-50000': sum(1 for s in interaction_scores if 10000 <= s < 50000),
            '>50000': sum(1 for s in interaction_scores if s >= 50000)
        }

        return {
            'avg_liked': round(avg_liked),
            'avg_collected': round(avg_collected),
            'avg_comment': round(avg_comment),
            'avg_collection_rate': round(avg_collection_rate, 3),
            'interaction_distribution': interaction_distribution,
            'max_interaction': max(interaction_scores) if interaction_scores else 0,
            'min_interaction': min(interaction_scores) if interaction_scores else 0
        }

    def _analyze_title_patterns(self, titles: List[str]) -> List[Dict[str, Any]]:
        """
        分析标题常见模式

        Returns:
            标题模式列表
        """
        patterns = [
            ('数字开头', r'^\d+'),
            ('感叹号结尾', r'[!！]$'),
            ('问号结尾', r'[?？]$'),
            ('包含省略号', r'\.{3}|…'),
            ('包含【】', r'【.*?】'),
            ('测评类', r'测评|评测|对比|PK'),
            ('教程类', r'教程|教学|攻略|指南|方法'),
            ('推荐类', r'推荐|种草|必买|必备'),
            ('避坑类', r'避坑|踩雷|避雷|慎买'),
            ('疑问句式', r'^(怎么|如何|什么|为什么|哪个|哪些)'),
            ('感叹词', r'绝了|yyds|绝绝子|太爱了|真香')
        ]

        pattern_stats = []
        for pattern_name, pattern_regex in patterns:
            count = sum(1 for title in titles if re.search(pattern_regex, title))
            if count > 0:
                pattern_stats.append({
                    'pattern': pattern_name,
                    'count': count,
                    'percentage': round(count / len(titles) * 100, 1)
                })

        # 按出现次数排序
        pattern_stats.sort(key=lambda x: x['count'], reverse=True)
        return pattern_stats

    def _analyze_emoji_usage(self, texts: List[str]) -> Dict[str, Any]:
        """
        分析Emoji使用情况

        Returns:
            Emoji统计信息
        """
        emoji_pattern = re.compile(
            "[\U0001F600-\U0001F64F"  # 表情符号
            "|\U0001F300-\U0001F5FF"  # 符号和象形文字
            "|\U0001F680-\U0001F6FF"  # 交通和地图符号
            "|\U0001F1E0-\U0001F1FF"  # 国旗
            "|\U00002702-\U000027B0"
            "|\U000024C2-\U0001F251"
            "]+"
        )

        texts_with_emoji = 0
        all_emojis = []

        for text in texts:
            emojis = emoji_pattern.findall(text)
            if emojis:
                texts_with_emoji += 1
                all_emojis.extend(emojis)

        emoji_freq = Counter(all_emojis)
        top_emojis = emoji_freq.most_common(10)

        return {
            'usage_rate': round(texts_with_emoji / len(texts) * 100, 1) if texts else 0,
            'top_emojis': [{'emoji': e, 'count': c} for e, c in top_emojis]
        }

    def _analyze_content_structure(self, contents: List[str]) -> Dict[str, Any]:
        """
        分析内容结构特征

        Returns:
            内容结构特征
        """
        structure_features = {
            'has_list': 0,  # 包含列表
            'has_steps': 0,  # 包含步骤
            'has_tips': 0,  # 包含小贴士
            'has_warning': 0,  # 包含注意事项
            'has_summary': 0,  # 包含总结
            'has_cta': 0  # 包含行动号召
        }

        for content in contents:
            # 检查列表
            if re.search(r'[1-9][\.、]|[①-⑩]|第[一二三四五六七八九十]', content):
                structure_features['has_list'] += 1

            # 检查步骤
            if re.search(r'步骤|第.步|step|STEP', content, re.I):
                structure_features['has_steps'] += 1

            # 检查小贴士
            if re.search(r'tips|小贴士|小技巧|注意事项|建议', content, re.I):
                structure_features['has_tips'] += 1

            # 检查警告
            if re.search(r'注意|警告|避免|不要|切记|千万', content):
                structure_features['has_warning'] += 1

            # 检查总结
            if re.search(r'总结|总的来说|综上|最后', content):
                structure_features['has_summary'] += 1

            # 检查行动号召
            if re.search(r'关注|点赞|收藏|评论|私信|链接', content):
                structure_features['has_cta'] += 1

        # 转换为百分比
        total = len(contents) if contents else 1
        return {
            k: round(v / total * 100, 1)
            for k, v in structure_features.items()
        }