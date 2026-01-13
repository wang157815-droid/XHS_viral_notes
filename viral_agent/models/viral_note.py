"""
爆款笔记数据模型定义
"""
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field, asdict
import json
from viral_agent.utils import parse_chinese_number


@dataclass
class ViralNote:
    """爆款笔记数据模型"""

    # 基础信息（必填）
    note_id: str
    note_url: str
    note_type: str  # 图集/视频

    # 用户信息（必填）
    user_id: str
    nickname: str
    avatar: str
    home_url: str

    # 内容信息（必填）
    title: str
    desc: str
    tags: List[str]

    # 互动数据（必填）
    liked_count: int
    collected_count: int
    comment_count: int

    # 元数据（必填）
    upload_time: str
    ip_location: str

    # 互动数据（可选，有默认值）
    share_count: int = 0

    # 媒体资源（可选，有默认值）
    image_list: List[str] = field(default_factory=list)
    video_addr: Optional[str] = None
    video_cover: Optional[str] = None
    video_urls: List[Dict[str, Any]] = field(default_factory=list)  # 新增：多源视频URL列表

    # 来源追踪（多关键词检索）
    source_keywords: List[str] = field(default_factory=list)  # 匹配到该笔记的关键词列表

    # 计算属性（有默认值）
    interaction_score: int = 0

    def __post_init__(self):
        """计算互动总分"""
        self.calculate_interaction_score()

    def calculate_interaction_score(self):
        """计算互动分数"""
        if self.note_type == "视频":
            # 视频笔记：点赞+收藏+评论+分享
            self.interaction_score = (
                self.liked_count +
                self.collected_count +
                self.comment_count +
                self.share_count
            )
        else:
            # 图文笔记：点赞+收藏+评论
            self.interaction_score = (
                self.liked_count +
                self.collected_count +
                self.comment_count
            )

    def is_viral(self, threshold: int = 5000) -> bool:
        """判断是否为爆款笔记"""
        if self.note_type == "视频":
            return self.interaction_score >= 1000
        else:
            return self.interaction_score >= threshold

    def get_best_video_url(self) -> Optional[str]:
        """
        获取最佳可用的视频URL

        Returns:
            第一个可用的视频URL，如果没有则返回None
        """
        if not self.video_urls:
            return self.video_addr  # 回退到单URL模式

        # 返回优先级最高的URL
        return self.video_urls[0].get('url') if self.video_urls else self.video_addr

    def get_all_video_urls(self) -> List[str]:
        """
        获取所有视频URL列表（仅URL字符串）

        Returns:
            URL字符串列表
        """
        if not self.video_urls:
            return [self.video_addr] if self.video_addr else []

        return [url_info.get('url') for url_info in self.video_urls if url_info.get('url')]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_spider_data(cls, note_data: Dict[str, Any]) -> 'ViralNote':
        """从爬虫数据创建实例"""
        # 处理笔记类型字段（兼容API原始数据和已处理数据）
        note_type = note_data.get('note_type', '')
        if not note_type:
            # 如果没有note_type，尝试从type字段获取（API原始数据）
            raw_type = note_data.get('type', '')
            if raw_type == 'normal':
                note_type = '图集'
            elif raw_type == 'video':
                note_type = '视频'
            else:
                note_type = ''

        # 优先从interact_info获取互动数据（API返回格式）
        interact_info = note_data.get('interact_info', {})

        if interact_info:
            # interact_info中的数据可能是字符串或中文格式（如"2.3万"），需要转换
            liked_count = parse_chinese_number(interact_info.get('liked_count', '0'))
            collected_count = parse_chinese_number(interact_info.get('collected_count', '0'))
            comment_count = parse_chinese_number(interact_info.get('comment_count', '0'))
            share_count = parse_chinese_number(interact_info.get('shared_count', '0'))  # 注意是shared_count
        else:
            # 如果没有interact_info，尝试从顶层获取（可能是已保存的JSON数据）
            liked_count = parse_chinese_number(note_data.get('liked_count', 0))
            collected_count = parse_chinese_number(note_data.get('collected_count', 0))
            comment_count = parse_chinese_number(note_data.get('comment_count', 0))
            share_count = parse_chinese_number(note_data.get('share_count', 0))

        # 获取视频封面（兼容多种数据格式）
        video_cover = cls._extract_video_cover(note_data, note_type)

        # 获取图片列表（兼容搜索结果和详情数据格式）
        image_list = cls._extract_image_list(note_data)

        return cls(
            note_id=note_data.get('note_id', ''),
            note_url=note_data.get('note_url', ''),
            note_type=note_type,
            user_id=note_data.get('user_id', ''),
            nickname=note_data.get('nickname', ''),
            avatar=note_data.get('avatar', ''),
            home_url=note_data.get('home_url', ''),
            title=note_data.get('title', ''),
            desc=note_data.get('desc', ''),
            tags=note_data.get('tags', []),
            liked_count=liked_count,
            collected_count=collected_count,
            comment_count=comment_count,
            share_count=share_count,
            image_list=image_list,
            video_addr=note_data.get('video_addr'),
            video_cover=video_cover,
            video_urls=note_data.get('video_urls', []),  # 多源视频URL列表
            source_keywords=note_data.get('source_keywords', []),  # 来源关键词列表
            upload_time=note_data.get('upload_time', ''),
            ip_location=note_data.get('ip_location', '')
        )

    @classmethod
    def _extract_video_cover(cls, note_data: Dict[str, Any], note_type: str) -> Optional[str]:
        """
        提取视频封面URL（兼容多种数据格式）

        数据来源优先级：
        1. 已处理的 video_cover 字段（来自 handle_note_info）
        2. cover 字段（搜索结果的封面字段）
        3. image_list 中的第一张图（视频封面通常是第一张）

        Args:
            note_data: 原始笔记数据
            note_type: 笔记类型

        Returns:
            视频封面URL或None
        """
        # 1. 优先使用已处理的 video_cover 字段
        if note_data.get('video_cover'):
            cover = note_data['video_cover']
            if isinstance(cover, dict):
                return cover.get('url_default') or cover.get('url') or cover.get('url_pre')
            return cover

        # 2. 尝试从 cover 字段获取（搜索结果格式）
        cover_data = note_data.get('cover')
        if cover_data:
            if isinstance(cover_data, dict):
                # 小红书API的cover格式：{url: xxx, info_list: [...]}
                url = cover_data.get('url_default') or cover_data.get('url') or cover_data.get('url_pre')
                if url:
                    return url
                # 从 info_list 获取
                info_list = cover_data.get('info_list', [])
                if info_list:
                    # 通常第二个是较高清的
                    target = info_list[1] if len(info_list) > 1 else info_list[0]
                    if isinstance(target, dict):
                        return target.get('url')
            elif isinstance(cover_data, str):
                return cover_data

        # 3. 从 image_list 获取第一张图（仅对视频笔记）
        if note_type == '视频':
            image_list = note_data.get('image_list', [])
            if image_list:
                first_image = image_list[0]
                if isinstance(first_image, dict):
                    return first_image.get('url_default') or first_image.get('url') or first_image.get('url_pre')
                elif isinstance(first_image, str):
                    return first_image

        return None

    @classmethod
    def _extract_image_list(cls, note_data: Dict[str, Any]) -> List[str]:
        """
        提取图片列表URL（兼容多种数据格式）

        Args:
            note_data: 原始笔记数据

        Returns:
            图片URL列表
        """
        # 1. 优先使用已处理的 image_list（字符串列表）
        image_list = note_data.get('image_list', [])
        if image_list:
            # 检查是否已经是URL字符串列表
            if isinstance(image_list[0], str):
                return image_list
            # 如果是字典列表，提取URL
            urls = []
            for img in image_list:
                if isinstance(img, dict):
                    url = img.get('url_default') or img.get('url') or img.get('url_pre')
                    # 尝试从 info_list 获取
                    if not url:
                        info_list = img.get('info_list', [])
                        if info_list:
                            target = info_list[1] if len(info_list) > 1 else info_list[0]
                            if isinstance(target, dict):
                                url = target.get('url')
                    if url:
                        urls.append(url)
                elif isinstance(img, str):
                    urls.append(img)
            return urls

        return []


@dataclass
class ViralAnalysisResult:
    """爆文分析结果模型"""

    # 分析元数据
    keyword: str
    analysis_time: str
    total_notes: int
    viral_threshold: int

    # 标题分析
    title_patterns: Dict[str, Any] = field(default_factory=dict)

    # 内容分析
    content_patterns: Dict[str, Any] = field(default_factory=dict)

    # 用户分析
    user_patterns: Dict[str, Any] = field(default_factory=dict)

    # 时间分析
    time_patterns: Dict[str, Any] = field(default_factory=dict)

    # 互动分析
    interaction_features: Dict[str, Any] = field(default_factory=dict)

    # 封面分析
    cover_features: Dict[str, Any] = field(default_factory=dict)

    # 所有图片OCR分析
    all_images_ocr: Dict[str, Any] = field(default_factory=dict)

    # 产品分析
    product_features: Dict[str, Any] = field(default_factory=dict)

    # 视频分析
    video_analysis: Dict[str, Any] = field(default_factory=dict)

    # 场景方向分析（新增）
    scene_features: Dict[str, Any] = field(default_factory=dict)

    # 爆文模型
    viral_model: Dict[str, Any] = field(default_factory=dict)

    # 图文/视频分离特征（用于混合分析时区分展示）
    image_note_features: Dict[str, Any] = field(default_factory=dict)  # 图文笔记专属特征
    video_note_features: Dict[str, Any] = field(default_factory=dict)  # 视频笔记专属特征
    type_summary: Dict[str, Any] = field(default_factory=dict)  # 类型统计摘要（数量、占比、对比结论）

    # 原始笔记数据（用于导出Excel原始数据分表）
    notes: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self):
        """初始化时间戳"""
        if not self.analysis_time:
            self.analysis_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)

    def to_json(self) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def save_to_file(self, filepath: str):
        """保存到文件"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(self.to_json())