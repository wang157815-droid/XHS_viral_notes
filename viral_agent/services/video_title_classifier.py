"""
视频标题分类器
用于分析小红书视频笔记标题的类型和策略
"""
import re
from typing import Dict, List, Any, Optional
from loguru import logger
from collections import Counter
import jieba

from viral_agent.prompts.video_title_prompts import (
    get_title_classification_prompt,
    parse_title_classification,
    TITLE_PATTERNS
)


class VideoTitleClassifier:
    """视频标题分类器"""

    def __init__(self, ai_analyzer=None):
        """
        初始化标题分类器

        Args:
            ai_analyzer: AI分析器实例（可选）
        """
        self.ai_analyzer = ai_analyzer
        self.classification_cache = {}
        self._init_jieba()

    def _init_jieba(self):
        """初始化jieba分词器，添加自定义词典"""
        custom_words = [
            # 美妆护肤
            '黑眼圈', '眼纹', '眼袋', '泪沟', '细纹', '鱼尾纹',
            '痘痘', '痘印', '闭口', '黑头', '毛孔', '暗沉',
            '护肤', '保养', '抗老', '抗衰', '补水', '保湿',
            '眼霜', '精华', '面霜', '面膜', '水乳', '防晒',
            # 美食
            '好吃', '超好吃', '巨好吃', '太好吃', '绝绝子',
            '下饭', '早餐', '午餐', '晚餐', '夜宵', '下午茶',
            '食谱', '菜谱', '做法', '烹饪', '料理',
            # 通用
            '姐妹们', '宝子们', '集美们', '家人们',
            '干货', '攻略', '教程', '种草', '安利', '回购',
            '神仙', '绝了', '无敌', '炸裂', '平替',
        ]
        for word in custom_words:
            jieba.add_word(word)

    async def classify_title(self, title: str) -> Dict[str, Any]:
        """
        分类单个视频标题

        Args:
            title: 视频标题

        Returns:
            分类结果字典，包含 main_category, sub_category, keywords
        """
        try:
            # 检查缓存
            if title in self.classification_cache:
                return self.classification_cache[title]

            classification = None

            # 如果有AI分析器，先尝试AI分类
            if self.ai_analyzer:
                try:
                    # 获取提示词
                    prompt = get_title_classification_prompt(title)

                    # 调用AI分析
                    result = await self.ai_analyzer.analyze_text(
                        text=title,
                        prompt=prompt
                    )

                    # 解析结果
                    classification = parse_title_classification(result)

                    # 检查是否需要回退到规则分类
                    if classification.get('needs_fallback') or not classification.get('main_category'):
                        logger.debug(f"AI分类结果无效，回退到规则分类: {result[:50] if result else 'empty'}")
                        classification = None  # 触发回退
                except Exception as e:
                    logger.warning(f"AI标题分类失败，回退到规则分类: {e}")
                    classification = None

            # 如果没有AI结果，使用规则分类
            if not classification:
                logger.debug("使用规则引擎进行标题分类")
                classification = self._classify_by_rules(title)

            # 提取关键词（无论AI还是规则分类都需要）
            keywords = self._extract_keywords(title)
            classification['keywords'] = keywords

            # 缓存结果
            self.classification_cache[title] = classification

            return classification

        except Exception as e:
            logger.error(f"标题分类失败: {e}")
            result = self._classify_by_rules(title)
            result['keywords'] = self._extract_keywords(title)
            return result

    def _extract_keywords(self, title: str) -> List[str]:
        """
        从标题中提取关键词

        Args:
            title: 标题文本

        Returns:
            关键词列表（最多5个）
        """
        try:
            # 使用jieba分词
            words = list(jieba.cut(title))

            # 过滤停用词和短词
            stopwords = {'的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都',
                        '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你',
                        '会', '着', '没有', '看', '好', '自己', '这', '那', '她', '他',
                        '什么', '如何', '怎么', '为什么', '哪个', '哪些', '多少'}

            # 过滤：保留长度>=2的词，排除停用词，排除纯数字和纯符号
            keywords = []
            for word in words:
                word = word.strip()
                if (len(word) >= 2 and
                    word not in stopwords and
                    not word.isdigit() and
                    any(c.isalnum() for c in word)):
                    keywords.append(word)

            # 去重并保持顺序
            seen = set()
            unique_keywords = []
            for kw in keywords:
                if kw not in seen:
                    seen.add(kw)
                    unique_keywords.append(kw)

            return unique_keywords[:5]  # 最多返回5个关键词

        except Exception as e:
            logger.warning(f"关键词提取失败: {e}")
            return []

    def classify_titles_batch(self, notes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        批量分类视频标题

        Args:
            notes: 笔记列表，每个笔记包含title字段

        Returns:
            标题分类统计结果
        """
        logger.info(f"开始批量分类 {len(notes)} 个视频标题...")

        # 收集所有标题
        titles = []
        for note in notes:
            if note.get('note_type') == '视频' and note.get('title'):
                titles.append(note['title'])

        if not titles:
            logger.warning("没有找到视频标题")
            return self._get_empty_stats()

        # 批量分类
        classifications = []
        for title in titles:
            classification = self._classify_by_rules(title)
            classifications.append(classification)

        # 统计分类结果
        stats = self._calculate_statistics(classifications, titles)

        return stats

    def _classify_by_rules(self, title: str) -> Dict[str, Any]:
        """
        基于规则的标题分类

        Args:
            title: 视频标题

        Returns:
            分类结果
        """
        # 检查每个分类的关键词和模式
        scores = {}

        for category, config in TITLE_PATTERNS.items():
            score = 0

            # 关键词匹配
            for keyword in config['keywords']:
                if keyword in title:
                    score += 2

            # 正则模式匹配
            for pattern in config['patterns']:
                if re.search(pattern, title):
                    score += 3

            scores[category] = score

        # 找出得分最高的分类
        if scores:
            best_category = max(scores, key=scores.get)
            if scores[best_category] > 0:
                return {
                    'main_category': best_category,
                    'sub_category': self._get_subcategory(title, best_category),
                    'raw_result': f"{best_category}-规则推断"
                }

        # 尝试基于标题特征进行智能分类
        smart_category = self._smart_classify(title)
        if smart_category:
            return smart_category

        # 默认分类
        return {
            'main_category': '通用内容类',
            'sub_category': '待细分',
            'raw_result': 'default'
        }

    def _smart_classify(self, title: str) -> Optional[Dict[str, Any]]:
        """
        智能分类：基于标题结构和语义特征进行分类

        Args:
            title: 标题

        Returns:
            分类结果或 None
        """
        # 检测是否是疑问句
        if '?' in title or '？' in title or '吗' in title or '怎么' in title:
            return {
                'main_category': '问题解决类',
                'sub_category': '疑问引导',
                'raw_result': '问题解决类-智能推断'
            }

        # 检测是否包含数字
        if re.search(r'\d+', title):
            return {
                'main_category': '数字营销类',
                'sub_category': '数量型',
                'raw_result': '数字营销类-智能推断'
            }

        # 检测是否是分享/推荐语气
        share_words = ['真的', '超级', '太', '绝了', '爱了', '哭了', '服了']
        if any(word in title for word in share_words):
            return {
                'main_category': '效果展示类',
                'sub_category': '情感共鸣',
                'raw_result': '效果展示类-智能推断'
            }

        # 检测是否是指导性标题
        if '这样' in title or '这么' in title or '原来' in title:
            return {
                'main_category': '干货指导类',
                'sub_category': '方法技巧',
                'raw_result': '干货指导类-智能推断'
            }

        return None

    def _get_subcategory(self, title: str, main_category: str) -> str:
        """
        根据标题和主分类推断子分类

        Args:
            title: 标题
            main_category: 主分类

        Returns:
            子分类名称
        """
        subcategory_rules = {
            '问题解决类': {
                '问题+解决方案': ['解决', '搞定', '有救', '拯救'],
                '痛点描述': ['困扰', '烦恼', '问题', '难'],
                '告别类': ['告别', '终结', '拜拜'],
                '反面教材': ['失败', '翻车', '踩雷'],
            },
            '干货指导类': {
                '教程类': ['教程', '教你', '学会', '怎么'],
                '方法技巧': ['方法', '技巧', '手法', '诀窍'],
                '攻略指南': ['攻略', '指南', '秘籍'],
                '美食做法': ['做法', '食谱', '菜谱', '配方'],
                '分享类': ['分享', '干货'],
            },
            '效果展示类': {
                '口味评价': ['好吃', '香', '绝绝子', '太香'],
                '视觉效果': ['好看', '绝了', '惊艳', '炸裂'],
                '变化对比': ['变化', '逆袭', '蜕变'],
                '神仙推荐': ['神仙', '无敌', '宝藏'],
            },
            '数字营销类': {
                '数量型': ['个', '种', '款', '道'],
                '步骤型': ['步', '招'],
                '排行型': ['TOP', '第一', '最'],
            },
            '年龄相关类': {
                '年龄标签': ['岁'],
                '人群定位': ['妈妈', '姐姐', '宝宝'],
            },
            '热点引流类': {
                '明星同款': ['明星', '同款'],
                '网红爆款': ['网红', '爆款', '火了'],
                '平替推荐': ['平替'],
            },
            '场景应用类': {
                '餐食场景': ['早餐', '午餐', '晚餐', '夜宵', '下午茶'],
                '日常场景': ['日常', '居家', '上班', '通勤'],
                '特殊场合': ['约会', '聚餐', '旅行'],
                '季节场景': ['春天', '夏天', '秋天', '冬天', '换季'],
            },
            '产品推广类': {
                '种草安利': ['种草', '安利', '推荐'],
                '好物分享': ['好物', '神器', '宝藏'],
                '购买引导': ['必买', '必囤', '回购', '入手'],
                '性价比': ['平价', '便宜', '性价比'],
            },
        }

        # 根据关键词匹配子分类
        if main_category in subcategory_rules:
            for sub_name, keywords in subcategory_rules[main_category].items():
                if any(kw in title for kw in keywords):
                    return sub_name

        return '通用'

    def _calculate_statistics(
        self,
        classifications: List[Dict[str, Any]],
        titles: List[str]
    ) -> Dict[str, Any]:
        """
        计算标题统计信息

        Args:
            classifications: 分类结果列表
            titles: 原始标题列表

        Returns:
            统计信息字典
        """
        # 主分类统计
        main_categories = Counter()
        sub_categories = Counter()
        category_combinations = Counter()

        for cls in classifications:
            main = cls.get('main_category', 'unknown')
            sub = cls.get('sub_category', 'unknown')

            main_categories[main] += 1
            sub_categories[sub] += 1
            category_combinations[f"{main}-{sub}"] += 1

        # 标题长度分析
        title_lengths = [len(t) for t in titles]
        avg_length = sum(title_lengths) / len(title_lengths) if title_lengths else 0

        # 关键词提取
        all_words = []
        for title in titles:
            words = jieba.cut(title)
            all_words.extend(words)

        word_counter = Counter(all_words)
        # 过滤停用词和单字
        top_keywords = [
            (word, count) for word, count in word_counter.most_common(30)
            if len(word) > 1 and word not in ['的', '了', '和', '是', '在', '有', '我', '你', '他', '她']
        ]

        # 特殊符号使用统计
        emoji_count = sum(1 for t in titles if any(ord(c) > 0x1F300 for c in t))
        question_count = sum(1 for t in titles if '？' in t or '?' in t)
        exclamation_count = sum(1 for t in titles if '！' in t or '!' in t)
        number_count = sum(1 for t in titles if any(c.isdigit() for c in t))

        total = len(classifications)

        # 新增：标题元素分析
        title_elements = self._analyze_title_elements(titles)

        # 新增：元素组合统计
        element_combinations = self._analyze_element_combinations(titles)

        return {
            'total_analyzed': total,
            'main_categories': dict(main_categories.most_common()),
            'sub_categories': dict(sub_categories.most_common(10)),
            'top_combinations': dict(category_combinations.most_common(10)),
            'avg_title_length': round(avg_length, 1),
            'top_keywords': top_keywords[:20],
            'special_symbols': {
                'emoji_usage_rate': round(emoji_count / total * 100, 1) if total > 0 else 0,
                'question_mark_rate': round(question_count / total * 100, 1) if total > 0 else 0,
                'exclamation_mark_rate': round(exclamation_count / total * 100, 1) if total > 0 else 0,
                'number_usage_rate': round(number_count / total * 100, 1) if total > 0 else 0
            },
            # 新增：标题元素统计
            'title_elements': title_elements,
            # 新增：元素组合统计
            'element_combinations': element_combinations,
            'recommendations': self._generate_recommendations(
                main_categories,
                sub_categories,
                top_keywords,
                avg_length
            )
        }

    def _analyze_title_elements(self, titles: List[str]) -> Dict[str, Any]:
        """
        分析标题元素占比

        Args:
            titles: 标题列表

        Returns:
            元素统计字典
        """
        total = len(titles)
        if total == 0:
            return {
                'has_age_rate': 0, 'has_age_count': 0,
                'has_problem_rate': 0, 'has_problem_count': 0,
                'has_number_rate': 0, 'has_number_count': 0,
                'has_product_rate': 0, 'has_product_count': 0,
                'has_effect_rate': 0, 'has_effect_count': 0
            }

        # 定义检测关键词
        age_keywords = ['岁', '30+', '35+', '40+', '25+', '姐姐', '妈妈', '阿姨', '少女', '熟龄']
        problem_keywords = [
            '黑眼圈', '眼袋', '眼纹', '细纹', '泪沟', '痘痘', '痘印', '毛孔', '暗沉',
            '松弛', '下垂', '敏感', '干燥', '出油', '斑点', '皱纹', '干纹', '法令纹'
        ]
        product_keywords = [
            '眼霜', '精华', '面霜', '乳液', '水乳', '面膜', '防晒', '洗面奶',
            '卸妆', '护肤品', '美白', '抗老', '补水', '保湿'
        ]
        effect_keywords = [
            '消失', '改善', '变', '效果', '终结', '告别', '拯救', '搞定',
            '立竿见影', '年轻', '嫩', '亮', '紧致', '有救', '解决'
        ]

        # 统计各元素出现次数
        age_count = sum(1 for t in titles if any(kw in t for kw in age_keywords))
        problem_count = sum(1 for t in titles if any(kw in t for kw in problem_keywords))
        number_count = sum(1 for t in titles if any(c.isdigit() for c in t))
        product_count = sum(1 for t in titles if any(kw in t for kw in product_keywords))
        effect_count = sum(1 for t in titles if any(kw in t for kw in effect_keywords))

        return {
            'has_age_rate': round(age_count / total * 100, 1),
            'has_age_count': age_count,
            'has_problem_rate': round(problem_count / total * 100, 1),
            'has_problem_count': problem_count,
            'has_number_rate': round(number_count / total * 100, 1),
            'has_number_count': number_count,
            'has_product_rate': round(product_count / total * 100, 1),
            'has_product_count': product_count,
            'has_effect_rate': round(effect_count / total * 100, 1),
            'has_effect_count': effect_count
        }

    def _analyze_element_combinations(self, titles: List[str]) -> Dict[str, int]:
        """
        分析标题元素组合

        Args:
            titles: 标题列表

        Returns:
            组合统计字典
        """
        combinations = Counter()

        # 定义元素检测函数
        def has_age(t):
            return any(kw in t for kw in ['岁', '30+', '35+', '40+', '25+', '姐姐', '少女'])

        def has_problem(t):
            return any(kw in t for kw in ['黑眼圈', '眼袋', '眼纹', '痘痘', '毛孔', '暗沉', '松弛'])

        def has_number(t):
            return any(c.isdigit() for c in t)

        def has_product(t):
            return any(kw in t for kw in ['眼霜', '精华', '面霜', '面膜', '防晒'])

        def has_effect(t):
            return any(kw in t for kw in ['消失', '改善', '效果', '终结', '告别', '年轻'])

        def has_tutorial(t):
            return any(kw in t for kw in ['手法', '方法', '技巧', '教程', '步骤', '怎么'])

        # 统计各组合
        for title in titles:
            elements = []
            if has_problem(title):
                elements.append('问题')
            if has_number(title):
                elements.append('数字')
            if has_age(title):
                elements.append('年龄')
            if has_effect(title):
                elements.append('效果')
            if has_product(title):
                elements.append('产品')
            if has_tutorial(title):
                elements.append('干货')

            # 生成2元素组合
            if len(elements) >= 2:
                for i in range(len(elements)):
                    for j in range(i + 1, len(elements)):
                        combo = f"{elements[i]}+{elements[j]}"
                        combinations[combo] += 1

        return dict(combinations.most_common(10))

    def _generate_recommendations(
        self,
        main_categories: Counter,
        sub_categories: Counter,
        top_keywords: List[tuple],
        avg_length: float
    ) -> List[str]:
        """
        生成标题写作建议

        Args:
            main_categories: 主分类统计
            sub_categories: 子分类统计
            top_keywords: 高频关键词
            avg_length: 平均长度

        Returns:
            建议列表
        """
        recommendations = []

        # 最受欢迎的主分类
        if main_categories:
            top_main = main_categories.most_common(1)[0]
            recommendations.append(
                f"最流行的标题类型是【{top_main[0]}】，"
                f"占比{top_main[1]/sum(main_categories.values())*100:.1f}%"
            )

        # 标题长度建议
        if avg_length > 0:
            if avg_length < 15:
                recommendations.append(f"平均标题长度{avg_length:.0f}字，建议适当增加至15-25字")
            elif avg_length > 30:
                recommendations.append(f"平均标题长度{avg_length:.0f}字，建议精简至20-30字")
            else:
                recommendations.append(f"标题长度控制合理（平均{avg_length:.0f}字）")

        # 高频关键词建议
        if top_keywords and len(top_keywords) >= 5:
            top_5_words = [word for word, _ in top_keywords[:5]]
            recommendations.append(
                f"高频关键词包括：{', '.join(top_5_words)}，建议合理使用"
            )

        # 根据分类分布给出建议
        if main_categories:
            if '问题解决类' in main_categories:
                recommendations.append(
                    "问题解决类标题转化率高，建议突出用户痛点和解决方案"
                )
            if '数字营销类' in main_categories:
                recommendations.append(
                    "数字化标题更吸睛，如'3步'、'5个技巧'等"
                )
            if '年龄相关类' in main_categories:
                recommendations.append(
                    "年龄标签能精准定位目标人群，如'30+'、'35岁姐姐'等"
                )

        return recommendations

    def _get_empty_stats(self) -> Dict[str, Any]:
        """
        获取空统计结果

        Returns:
            空统计字典
        """
        return {
            'total_analyzed': 0,
            'main_categories': {},
            'sub_categories': {},
            'top_combinations': {},
            'avg_title_length': 0,
            'top_keywords': [],
            'special_symbols': {
                'emoji_usage_rate': 0,
                'question_mark_rate': 0,
                'exclamation_mark_rate': 0,
                'number_usage_rate': 0
            },
            'title_elements': {
                'has_age_rate': 0, 'has_age_count': 0,
                'has_problem_rate': 0, 'has_problem_count': 0,
                'has_number_rate': 0, 'has_number_count': 0,
                'has_product_rate': 0, 'has_product_count': 0,
                'has_effect_rate': 0, 'has_effect_count': 0
            },
            'element_combinations': {},
            'recommendations': ["没有找到可分析的视频标题"]
        }