"""
场景方向分析服务
分析笔记的场景类型和内容方向
"""
import os
import json
import re
from typing import List, Dict, Any, Optional
from collections import Counter
from loguru import logger
from dotenv import load_dotenv

from viral_agent.models.viral_note import ViralNote

load_dotenv()


# ==================== 场景和内容方向分类体系 ====================

# 场景分类（根据小红书常见场景设计，扩充关键词提高识别率）
SCENE_CATEGORIES = {
    "户外": ["户外", "露营", "野餐", "徒步", "爬山", "海边", "沙滩", "公园", "草地", "阳光", "太阳", "紫外线", "晒", "防晒"],
    "军训": ["军训", "操场", "绿军装", "迷彩", "站军姿", "烈日", "新生", "入学"],
    "健身": ["健身", "健身房", "瑜伽", "跑步", "器械", "运动", "撸铁", "减肥", "减脂", "塑形", "马甲线"],
    "办公": ["办公", "办公室", "工位", "电脑", "会议", "上班", "打工", "加班", "工作", "职场"],
    "居家": ["居家", "家里", "宅家", "卧室", "客厅", "浴室", "厨房", "在家", "睡前", "洗澡", "洗护"],
    "旅行": ["旅行", "旅游", "出游", "度假", "酒店", "民宿", "景点", "打卡", "出差", "飞机", "高铁"],
    "约会": ["约会", "情侣", "恋爱", "见面", "第一次", "表白", "相亲", "男朋友", "女朋友"],
    "通勤": ["通勤", "上班", "地铁", "公交", "骑车", "开车", "早高峰", "挤地铁"],
    "聚会": ["聚会", "派对", "生日", "朋友", "闺蜜", "聚餐", "年会", "同学", "婚礼"],
    "送礼": ["送礼", "礼物", "送人", "伴手礼", "节日", "生日礼物", "情人节", "母亲节", "父亲节", "圣诞"],
    "学习": ["学习", "考试", "备考", "图书馆", "自习", "网课", "考研", "大学", "学生", "学校"],
    "日常": ["日常", "每天", "早起", "护肤", "早晚", "日常", "常用", "必备", "天天"],
    "美妆": ["化妆", "妆容", "底妆", "眼妆", "唇妆", "上妆", "卸妆", "试色", "素颜"],
    "护肤": ["护肤", "敷脸", "面膜", "精华", "乳液", "水乳", "保湿", "补水", "抗老", "美白"],
}

# 无效场景标记（用于过滤）
INVALID_SCENES = ["未识别", "通用场景", "其他", "未知"]

# 内容方向分类
CONTENT_DIRECTION_CATEGORIES = {
    "干货教程": ["教程", "教学", "怎么", "如何", "方法", "步骤", "技巧", "攻略", "保姆级"],
    "单品推荐": ["推荐", "安利", "好用", "好物", "神器", "必买", "入手"],
    "好物合集": ["合集", "盘点", "清单", "必备", "囤货", "大公开", "几款"],
    "测评对比": ["测评", "对比", "评测", "真实测评", "横评", "VS", "哪个好"],
    "开箱分享": ["开箱", "拆箱", "到货", "入手", "新买"],
    "日常分享": ["分享", "日常", "记录", "vlog", "一天"],
    "避雷警告": ["避雷", "踩雷", "别买", "慎入", "失望", "不推荐", "差评"],
    "效果展示": ["效果", "前后对比", "变化", "使用感受", "实测"],
    "省钱攻略": ["省钱", "平价", "便宜", "学生党", "白菜价", "性价比"],
}


class SceneAnalyzer:
    """场景方向分析器"""

    def __init__(self, multimodal_analyzer=None, video_ai_analyzer=None):
        """
        初始化场景分析器

        Args:
            multimodal_analyzer: 多模态分析器（用于图片场景识别）
            video_ai_analyzer: 视频AI分析器（用于视频场景识别）
        """
        self.multimodal_analyzer = multimodal_analyzer
        self.video_ai_analyzer = video_ai_analyzer

        logger.info("场景分析器初始化完成")
        if multimodal_analyzer:
            logger.info("  ✓ 图片场景分析已启用")
        if video_ai_analyzer:
            logger.info("  ✓ 视频场景分析已启用")

    def analyze_scenes(
        self,
        notes: List[ViralNote],
        keyword: str = ""
    ) -> Dict[str, Any]:
        """
        分析笔记列表的场景和内容方向

        Args:
            notes: 笔记列表
            keyword: 搜索关键词

        Returns:
            场景分析结果
        """
        logger.info(f"开始场景方向分析，共 {len(notes)} 篇笔记")

        # 分离图文和视频笔记
        image_notes = [n for n in notes if n.note_type != '视频']
        video_notes = [n for n in notes if n.note_type == '视频']

        logger.info(f"  - 图文笔记: {len(image_notes)} 篇")
        logger.info(f"  - 视频笔记: {len(video_notes)} 篇")

        # 1. 文本场景分析（所有笔记）
        text_scene_results = self._analyze_text_scenes(notes)

        # 2. 图文笔记的图片场景分析
        image_scene_results = {}
        if image_notes and self.multimodal_analyzer:
            image_scene_results = self._analyze_image_scenes(image_notes)

        # 3. 视频笔记的视频场景分析
        video_scene_results = {}
        if video_notes and self.video_ai_analyzer:
            video_scene_results = self._analyze_video_scenes(video_notes)

        # 4. 综合分析结果
        combined_results = self._combine_scene_results(
            text_scene_results,
            image_scene_results,
            video_scene_results,
            notes
        )

        # 5. 生成场景策略建议
        scene_strategy = self._generate_scene_strategy(combined_results, keyword)

        return {
            'status': 'success',
            'total_notes': len(notes),
            'image_notes_count': len(image_notes),
            'video_notes_count': len(video_notes),
            'text_analysis': text_scene_results,
            'image_analysis': image_scene_results,
            'video_analysis': video_scene_results,
            'combined_results': combined_results,
            'scene_strategy': scene_strategy
        }

    def _analyze_text_scenes(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """从文本中提取场景和内容方向"""
        scene_counter = Counter()
        direction_counter = Counter()
        note_scenes = []  # 每篇笔记的场景

        for note in notes:
            text = f"{note.title} {note.desc}"
            text_lower = text.lower()

            # 识别场景
            note_scene_list = []
            for scene_name, keywords in SCENE_CATEGORIES.items():
                for kw in keywords:
                    if kw in text_lower:
                        scene_counter[scene_name] += 1
                        if scene_name not in note_scene_list:
                            note_scene_list.append(scene_name)
                        break

            # 识别内容方向
            note_direction_list = []
            for direction_name, keywords in CONTENT_DIRECTION_CATEGORIES.items():
                for kw in keywords:
                    if kw in text_lower:
                        direction_counter[direction_name] += 1
                        if direction_name not in note_direction_list:
                            note_direction_list.append(direction_name)
                        break

            note_scenes.append({
                'note_id': note.note_id,
                'title': note.title[:30],
                'scenes': note_scene_list or ['日常'],  # 默认归类为日常场景（比"未识别"更有意义）
                'directions': note_direction_list or ['日常分享'],  # 默认归类为日常分享
                'interaction': note.interaction_score
            })

        return {
            'scene_distribution': dict(scene_counter.most_common(10)),
            'direction_distribution': dict(direction_counter.most_common(10)),
            'note_details': note_scenes[:20],  # 只保留前20篇的详情
            'top_scenes': scene_counter.most_common(5),
            'top_directions': direction_counter.most_common(5)
        }

    def _analyze_image_scenes(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """使用多模态AI分析图片场景"""
        if not self.multimodal_analyzer:
            return {'status': 'disabled', 'message': '多模态分析未配置'}

        logger.info("开始图片场景AI分析...")

        # 构建场景识别提示词
        scene_prompt = self._build_scene_prompt()

        ai_scene_results = []
        sample_notes = notes[:10]  # 最多分析10篇

        for note in sample_notes:
            try:
                # 复用现有的多模态分析器
                result = self.multimodal_analyzer.analyze_note_with_images(note)

                if result.get('status') == 'success':
                    # 从分析结果中提取场景信息
                    ai_scene_results.append({
                        'note_id': note.note_id,
                        'title': note.title[:30],
                        'ai_analysis': result.get('analysis', {}),
                        'detected_scenes': self._extract_scenes_from_ai(result)
                    })
            except Exception as e:
                logger.warning(f"图片场景分析失败 {note.note_id}: {e}")

        # 统计AI识别的场景
        ai_scene_counter = Counter()
        for item in ai_scene_results:
            for scene in item.get('detected_scenes', []):
                ai_scene_counter[scene] += 1

        return {
            'status': 'success',
            'analyzed_count': len(ai_scene_results),
            'ai_scene_distribution': dict(ai_scene_counter.most_common(10)),
            'sample_results': ai_scene_results[:5]
        }

    def _analyze_video_scenes(self, notes: List[ViralNote]) -> Dict[str, Any]:
        """使用视频AI分析视频场景（并行 + 单视频超时保护）"""
        if not self.video_ai_analyzer:
            return {'status': 'disabled', 'message': '视频AI分析未配置'}

        logger.info("开始视频场景AI分析...")

        import asyncio

        scene_prompt = """请分析这个视频的场景类型，识别：
1. 主要场景（如户外、室内、健身房、办公室、居家等）
2. 场景特征（如环境、氛围、时间等）
3. 内容方向（如教程、测评、分享、推荐等）

请用JSON格式返回：
{
    "main_scene": "场景名称",
    "scene_features": ["特征1", "特征2"],
    "content_direction": "内容方向"
}"""

        async def analyze_video_batch():
            sample_notes = notes[:5]  # 视频分析成本高，只分析5个
            semaphore = asyncio.Semaphore(2)  # 最多2个并发，避免资源争抢

            async def analyze_one(note: 'ViralNote'):
                video_url = note.get_best_video_url()
                if not video_url:
                    return None

                async with semaphore:
                    try:
                        result = await asyncio.wait_for(
                            self.video_ai_analyzer.analyze_video(
                                video_url=video_url,
                                prompt=scene_prompt,
                                title=note.title,
                                description=note.desc
                            ),
                            timeout=90  # 单视频90s超时保护
                        )
                        return {
                            'note_id': note.note_id,
                            'title': note.title[:30],
                            'ai_result': result,
                            'detected_scenes': self._parse_video_scene_result(result)
                        }
                    except asyncio.TimeoutError:
                        logger.warning(f"视频场景分析超时(90s): {note.note_id}")
                        return None
                    except Exception as e:
                        logger.warning(f"视频场景分析失败 {note.note_id}: {e}")
                        return None

            tasks = [analyze_one(note) for note in sample_notes]
            raw_results = await asyncio.gather(*tasks)
            return [r for r in raw_results if r is not None]

        # 运行异步任务（使用安全包装器，兼容 uvloop）
        from viral_agent.utils.async_utils import run_async_safely
        video_results = run_async_safely(analyze_video_batch(), timeout=600)

        # 统计视频场景
        video_scene_counter = Counter()
        for item in video_results:
            scenes = item.get('detected_scenes', {})
            if scenes.get('main_scene'):
                video_scene_counter[scenes['main_scene']] += 1

        return {
            'status': 'success',
            'analyzed_count': len(video_results),
            'video_scene_distribution': dict(video_scene_counter.most_common(10)),
            'sample_results': video_results[:3]
        }

    def _build_scene_prompt(self) -> str:
        """构建场景识别提示词"""
        scene_list = "、".join(SCENE_CATEGORIES.keys())
        direction_list = "、".join(CONTENT_DIRECTION_CATEGORIES.keys())

        return f"""请分析这张图片/视频的场景类型和内容方向。

可选场景类型：{scene_list}
可选内容方向：{direction_list}

请识别并返回JSON格式：
{{
    "scene": "场景类型",
    "direction": "内容方向",
    "scene_features": ["具体特征1", "具体特征2"]
}}"""

    def _extract_scenes_from_ai(self, ai_result: Dict) -> List[str]:
        """从AI分析结果中提取场景"""
        scenes = []
        analysis = ai_result.get('analysis', {})

        # 尝试从不同字段提取
        if isinstance(analysis, dict):
            if 'scene' in analysis:
                scenes.append(analysis['scene'])
            if 'visual_style' in analysis:
                # 从视觉风格推断场景
                style = analysis['visual_style']
                for scene_name, keywords in SCENE_CATEGORIES.items():
                    for kw in keywords:
                        if kw in str(style).lower():
                            scenes.append(scene_name)
                            break

        return list(set(scenes)) or ['日常']  # 默认归类为日常场景

    def _parse_video_scene_result(self, result: str) -> Dict:
        """解析视频场景分析结果"""
        try:
            # 尝试提取JSON
            json_match = re.search(r'\{[^{}]*\}', result, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"场景分析JSON解析失败: {e}")

        # 从文本中提取场景关键词
        detected = {'main_scene': '日常', 'scene_features': [], 'content_direction': '日常分享'}
        for scene_name, keywords in SCENE_CATEGORIES.items():
            for kw in keywords:
                if kw in result.lower():
                    detected['main_scene'] = scene_name
                    break
            if detected['main_scene'] != '日常':  # 如果已找到场景，跳出外层循环
                break

        return detected

    def _combine_scene_results(
        self,
        text_results: Dict,
        image_results: Dict,
        video_results: Dict,
        notes: List[ViralNote]
    ) -> Dict[str, Any]:
        """综合所有分析结果"""
        # 合并场景统计
        combined_scenes = Counter(text_results.get('scene_distribution', {}))

        if image_results.get('status') == 'success':
            for scene, count in image_results.get('ai_scene_distribution', {}).items():
                combined_scenes[scene] += count

        if video_results.get('status') == 'success':
            for scene, count in video_results.get('video_scene_distribution', {}).items():
                combined_scenes[scene] += count

        # 计算高互动场景
        scene_interaction = {}
        for note in notes:
            text = f"{note.title} {note.desc}"
            for scene_name, keywords in SCENE_CATEGORIES.items():
                for kw in keywords:
                    if kw in text.lower():
                        if scene_name not in scene_interaction:
                            scene_interaction[scene_name] = []
                        scene_interaction[scene_name].append(note.interaction_score)
                        break

        # 计算平均互动
        scene_avg_interaction = {}
        for scene, interactions in scene_interaction.items():
            if interactions:
                scene_avg_interaction[scene] = {
                    'count': len(interactions),
                    'avg_interaction': sum(interactions) // len(interactions),
                    'max_interaction': max(interactions)
                }

        # 按平均互动排序
        sorted_scenes = sorted(
            scene_avg_interaction.items(),
            key=lambda x: x[1]['avg_interaction'],
            reverse=True
        )

        # 过滤无效场景
        filtered_scenes = [(s, c) for s, c in combined_scenes.most_common(15) if s not in INVALID_SCENES]

        return {
            'scene_distribution': dict(filtered_scenes),
            'direction_distribution': text_results.get('direction_distribution', {}),
            'scene_interaction_stats': dict(sorted_scenes[:10]),
            'top_scene': sorted_scenes[0][0] if sorted_scenes else '日常',
            'top_direction': text_results.get('top_directions', [('日常分享', 0)])[0][0]
        }

    def _generate_scene_strategy(
        self,
        combined_results: Dict,
        keyword: str
    ) -> Dict[str, Any]:
        """生成场景策略建议"""
        # 过滤无效场景（确保推荐内容有意义）
        all_scenes = list(combined_results.get('scene_distribution', {}).keys())
        top_scenes = [s for s in all_scenes if s not in INVALID_SCENES][:5]

        all_directions = list(combined_results.get('direction_distribution', {}).keys())
        top_directions = [d for d in all_directions if d not in INVALID_SCENES][:5]

        scene_stats = combined_results.get('scene_interaction_stats', {})

        # 找出高互动场景（同样过滤无效值）
        high_interaction_scenes = []
        for scene, stats in scene_stats.items():
            if scene not in INVALID_SCENES and stats.get('avg_interaction', 0) > 5000:
                high_interaction_scenes.append(scene)

        # 确保有推荐内容（如果过滤后为空，使用默认值）
        recommended_scenes = top_scenes[:3] if top_scenes else ['日常', '居家', '护肤']
        recommended_directions = top_directions[:3] if top_directions else ['单品推荐', '好物合集', '效果展示']

        return {
            'recommended_scenes': recommended_scenes,
            'recommended_directions': recommended_directions,
            'high_interaction_scenes': high_interaction_scenes[:3],
            'strategy_summary': self._build_strategy_summary(
                recommended_scenes, recommended_directions, high_interaction_scenes, keyword
            ),
            'scene_templates': self._generate_scene_templates(recommended_scenes, keyword),
            'direction_templates': self._generate_direction_templates(recommended_directions, keyword)
        }

    def _build_strategy_summary(
        self,
        scenes: List[str],
        directions: List[str],
        high_interaction: List[str],
        keyword: str
    ) -> str:
        """构建场景策略总结"""
        scene_text = "、".join(scenes[:3]) if scenes else "日常"
        direction_text = "、".join(directions[:3]) if directions else "分享"
        high_text = "、".join(high_interaction[:2]) if high_interaction else "无明显高互动场景"

        return f"""【场景方向分析总结】
关键词"{keyword}"相关爆款笔记的场景特征：
1. 主要场景分布：{scene_text}
2. 主要内容方向：{direction_text}
3. 高互动场景：{high_text}

建议创作方向：优先选择"{scenes[0] if scenes else '日常'}"场景，采用"{directions[0] if directions else '分享'}"的内容形式。"""

    def _generate_scene_templates(self, scenes: List[str], keyword: str) -> List[str]:
        """生成场景相关的标题模板"""
        templates = []
        for scene in scenes[:3]:
            templates.append(f"【{scene}】{keyword}使用心得")
            templates.append(f"{scene}场景下的{keyword}推荐")

        return templates

    def _generate_direction_templates(self, directions: List[str], keyword: str) -> List[str]:
        """生成内容方向相关的标题模板"""
        templates = []
        direction_formats = {
            "干货教程": f"{keyword}使用教程｜保姆级攻略",
            "单品推荐": f"超好用的{keyword}推荐！",
            "好物合集": f"{keyword}合集｜这几款必入",
            "测评对比": f"{keyword}测评｜真实使用感受",
            "效果展示": f"{keyword}使用前后对比",
        }

        for direction in directions[:3]:
            if direction in direction_formats:
                templates.append(direction_formats[direction])

        return templates or [f"{keyword}推荐", f"{keyword}分享"]
