"""
美妆护肤领域知识库
支持动态加载不同垂直领域的专业知识

注意：此文件已改为从JSON配置文件加载知识库
硬编码的知识库常量保留用于向后兼容和降级
"""

from loguru import logger

# 新增：导入JSON配置加载器
try:
    from viral_agent.config.knowledge_loader import get_knowledge_config
    _USE_JSON_CONFIG = True
except ImportError:
    _USE_JSON_CONFIG = False

# ==================== 通用基础知识 ====================

BASE_CATEGORY_KNOWLEDGE = """
【内容类型分类】
大类可能为：
- 单品推荐：重点推荐单个产品
- 干货教程：分享实用技巧或方法
- 剧情：讲故事或情景表演
- vlog/沉浸式：生活记录或沉浸式体验
- 合集：多产品推荐
- 系列推荐：同品牌系列产品推荐
- 测评：产品测试对比

【产品植入方式】
内容植入型：
- 流程中植入产品（在整体流程中自然展示）
- 干货手法中植入（在教学过程中使用）
- 妆教中植入（化妆教程中使用）

直接展示型：
- 手持口播（手持产品直接讲解）
- 使用方法中植入（展示具体使用方法）
"""


# ==================== 垂直领域知识库 ====================

# 眼部护理知识库
EYE_CARE_KNOWLEDGE = """
【眼部问题相关】
- 眼纹/细纹/鱼尾纹
- 黑眼圈/熊猫眼
- 眼袋/泪沟
- 眼部干纹/卡粉

【眼部产品引出方式】
- 眼部问题（指出眼周问题后引出）
- 护理眼部/眼周
- 眼部保养
- 功效型眼霜护理

【眼部产品植入方式】
- 眼部按摩植入（按摩过程中使用）
- 眼霜日常涂法植入（展示涂抹技巧）
- 眼部多种用法（展示多种使用方式）

【眼部护理示例】
Result_15s,22s,18s,干货教程-手法干货,眼部问题（黑眼圈）,眼部护理,干货手法中植入
Result_48s,/,52s,剧情-剧情单推,眼周紧致,护理眼部,流程中植入产品
"""

# 面部护理知识库
FACE_CARE_KNOWLEDGE = """
【面部问题相关】
- 痘痘/痘印/闭口
- 暗沉/黄气/肤色不均
- 毛孔粗大/黑头
- 皮肤干燥/爆皮
- 脸垮/法令纹/松弛

【面部产品引出方式】
- 皮肤问题（指出肌肤问题后引出）
- 针对性护肤
- 面部护理流程
- 换季护理需求

【面部产品植入方式】
- 面部按摩手法植入
- 护肤流程中植入
- 涂抹技巧展示
- 分区护肤植入

【面部护理示例】
Result_20s,30s,25s,干货教程-手法干货,痘痘肌护理,自用分享,干货手法中植入
Result_35s,/,40s,单品推荐-单品推荐,暗沉提亮,成分引出,手持口播
"""

# 唇部护理知识库
LIP_CARE_KNOWLEDGE = """
【唇部问题相关】
- 唇纹/干裂
- 唇色暗沉/发黑
- 死皮/起皮
- 唇部干燥

【唇部产品引出方式】
- 唇部问题（指出唇部问题后引出）
- 换季唇部护理
- 口红前打底
- 睡前护理

【唇部产品植入方式】
- 唇部护理流程植入
- 涂抹手法展示
- 妆前护理植入
- 晚间护理流程

【唇部护理示例】
Result_10s,15s,12s,干货教程-护理手法,唇部干裂,季节护理,护理流程中植入
Result_25s,/,30s,单品推荐-单品推荐,唇色暗沉,自用好物,手持口播
"""

# 彩妆知识库
MAKEUP_KNOWLEDGE = """
【彩妆问题相关】
- 底妆卡粉/斑驳
- 妆容不持久/脱妆
- 肤色不匹配
- 眼妆晕染
- 口红沾杯

【彩妆产品引出方式】
- 妆容问题（指出化妆问题后引出）
- 妆教需求
- 场合妆容推荐
- 色号推荐

【彩妆产品植入方式】
- 妆教中植入
- 化妆步骤中植入
- 妆容对比植入
- 手法演示植入

【彩妆示例】
Result_18s,25s,20s,干货教程-妆教,底妆卡粉,妆容技巧,妆教中植入
Result_30s,/,35s,单品推荐-单品推荐,持妆需求,自用分享,手持口播
"""

# 身体护理知识库
BODY_CARE_KNOWLEDGE = """
【身体护理问题相关】
- 身体干燥/粗糙
- 鸡皮肤/毛囊角化
- 颈纹/手纹
- 身体美白
- 后背痘痘

【身体护理产品引出方式】
- 身体问题（指出身体肌肤问题后引出）
- 全身护理流程
- 季节性护理
- 局部护理需求

【身体护理产品植入方式】
- 身体护理流程植入
- 涂抹手法展示
- 洗护流程植入
- 按摩手法植入

【身体护理示例】
Result_22s,30s,25s,干货教程-手法干货,身体干燥,季节护理,护理流程中植入
Result_40s,/,45s,单品推荐-单品推荐,鸡皮肤改善,自用好物,手持口播
"""


# ==================== 领域关键词映射 ====================

DOMAIN_KEYWORDS = {
    'eye_care': {
        'keywords': ['眼', '眼霜', '眼周', '眼纹', '黑眼圈', '眼袋', '泪沟', '鱼尾纹'],
        'knowledge': EYE_CARE_KNOWLEDGE,
        'name': '眼部护理'
    },
    'face_care': {
        'keywords': ['面', '面霜', '精华', '水乳', '痘痘', '暗沉', '毛孔', '松弛', '法令纹', '提亮', '美白'],
        'knowledge': FACE_CARE_KNOWLEDGE,
        'name': '面部护理'
    },
    'lip_care': {
        'keywords': ['唇', '唇膜', '唇部', '唇纹', '唇色', '死皮', '润唇'],
        'knowledge': LIP_CARE_KNOWLEDGE,
        'name': '唇部护理'
    },
    'makeup': {
        'keywords': ['妆', '底妆', '粉底', '遮瑕', '口红', '眼影', '腮红', '高光', '修容', '定妆'],
        'knowledge': MAKEUP_KNOWLEDGE,
        'name': '彩妆'
    },
    'body_care': {
        'keywords': ['身体', '身体乳', '颈', '手', '脚', '后背', '鸡皮', '去角质'],
        'knowledge': BODY_CARE_KNOWLEDGE,
        'name': '身体护理'
    }
}


# ==================== 智能知识库选择器 ====================

def detect_domain(title: str = "", description: str = "") -> list:
    """
    根据标题和描述检测所属领域

    Args:
        title: 视频标题
        description: 视频描述

    Returns:
        匹配的领域列表（按相关度排序）
    """
    # 优先使用JSON配置
    if _USE_JSON_CONFIG:
        try:
            config = get_knowledge_config()
            return config.detect_domain(title, description)
        except Exception as e:
            logger.warning(f"JSON配置加载失败，使用硬编码降级: {e}")

    # 降级：使用硬编码逻辑
    text = (title + " " + description).lower()

    domain_scores = {}
    for domain_key, domain_info in DOMAIN_KEYWORDS.items():
        score = sum(1 for keyword in domain_info['keywords'] if keyword in text)
        if score > 0:
            domain_scores[domain_key] = score

    # 按分数降序排序
    sorted_domains = sorted(domain_scores.items(), key=lambda x: x[1], reverse=True)
    return [domain for domain, _ in sorted_domains]


def get_domain_knowledge(domains: list) -> str:
    """
    获取指定领域的专业知识

    Args:
        domains: 领域列表（detect_domain的返回值）

    Returns:
        组合后的领域知识
    """
    # 优先使用JSON配置
    if _USE_JSON_CONFIG:
        try:
            config = get_knowledge_config()
            return config.get_domain_knowledge_text(domains)
        except Exception as e:
            logger.warning(f"JSON配置加载失败，使用硬编码降级: {e}")

    # 降级：使用硬编码逻辑
    if not domains:
        return ""

    knowledge_parts = []
    for domain in domains:
        if domain in DOMAIN_KEYWORDS:
            info = DOMAIN_KEYWORDS[domain]
            knowledge_parts.append(f"\n【{info['name']}专业知识】")
            knowledge_parts.append(info['knowledge'])

    return "\n".join(knowledge_parts)


def build_dynamic_prompt(title: str = "", description: str = "", base_prompt: str = "") -> str:
    """
    构建动态提示词（基础提示词 + 领域知识）

    Args:
        title: 视频标题
        description: 视频描述
        base_prompt: 基础提示词模板

    Returns:
        完整的动态提示词
    """
    # 检测领域
    domains = detect_domain(title, description)

    # 获取领域知识
    domain_knowledge = get_domain_knowledge(domains)

    # 构建提示词
    if domain_knowledge:
        # 获取领域名称（兼容JSON配置和硬编码）
        detected_domains = _get_domain_names(domains)
        dynamic_prompt = f"""{base_prompt}

【检测到的领域】: {detected_domains}

【相关专业知识】
{domain_knowledge}

请基于上述专业知识进行分析，确保术语和分类的准确性。
"""
    else:
        dynamic_prompt = f"""{base_prompt}

【通用知识】
{BASE_CATEGORY_KNOWLEDGE}

请基于通用知识进行分析。
"""

    return dynamic_prompt


def _get_domain_names(domains: list) -> str:
    """
    获取领域名称列表（兼容JSON配置和硬编码）

    Args:
        domains: 领域ID列表

    Returns:
        逗号分隔的领域名称字符串
    """
    names = []
    for d in domains:
        # 优先从JSON配置获取
        if _USE_JSON_CONFIG:
            try:
                config = get_knowledge_config()
                domain_info = config.get_domain_by_id(d)
                if domain_info:
                    names.append(domain_info['name'])
                    continue
            except Exception as e:
                logger.debug(f"JSON配置获取领域名称失败: {e}")
        # 降级：从硬编码字典获取
        if d in DOMAIN_KEYWORDS:
            names.append(DOMAIN_KEYWORDS[d]['name'])
        else:
            # 未知领域，使用ID作为名称
            names.append(d)
    return ", ".join(names)


# ==================== 测试用例 ====================

if __name__ == "__main__":
    # 测试领域检测
    test_cases = [
        ("别再无效涂眼霜❌6大不同眼纹👉正确涂抹手法", "眼部护理教程"),
        ("痘痘肌救星！超好用的祛痘精华分享", "面部护理"),
        ("冬季嘴唇干裂？这款唇膜用了3天就见效", "唇部护理"),
        ("底妆不卡粉的秘密🔥化妆小白必看", "彩妆教程"),
        ("全身美白攻略｜身体护理流程分享", "身体护理"),
    ]

    print("=" * 70)
    print("知识库领域检测测试")
    print("=" * 70 + "\n")

    for title, desc in test_cases:
        domains = detect_domain(title, desc)
        print(f"📝 标题: {title}")
        print(f"💬 描述: {desc}")

        if domains:
            detected_names = [DOMAIN_KEYWORDS[d]['name'] for d in domains]
            print(f"🎯 检测领域: {', '.join(detected_names)}")
            print(f"📚 匹配数量: {len(domains)}个领域\n")
        else:
            print(f"🎯 检测领域: 通用（无特定领域）\n")
