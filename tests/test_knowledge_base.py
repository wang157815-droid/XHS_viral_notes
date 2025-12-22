"""
测试动态知识库系统
验证不同领域视频是否能加载正确的专业知识
"""
from loguru import logger
from viral_agent.prompts.knowledge_base import detect_domain, DOMAIN_KEYWORDS
from viral_agent.prompts.video_timeline_prompts import get_timeline_analysis_prompt


def test_domain_detection():
    """测试领域检测功能"""
    logger.info("=" * 80)
    logger.info("测试1: 领域检测功能")
    logger.info("=" * 80 + "\n")

    test_cases = [
        ("别再无效涂眼霜❌6大不同眼纹👉正确涂抹手法", "眼部护理教程", "眼部护理"),
        ("痘痘肌救星！超好用的祛痘精华分享", "面部护理", "面部护理"),
        ("冬季嘴唇干裂？这款唇膜用了3天就见效", "唇部护理", "唇部护理"),
        ("底妆不卡粉的秘密🔥化妆小白必看", "彩妆教程", "彩妆"),
        ("全身美白攻略｜身体护理流程分享", "身体护理", "身体护理"),
        ("熬夜党必看！黑眼圈救星来了", "眼部护理", "眼部护理"),
        ("30+抗老精华推荐｜面部提拉紧致", "面部抗老", "面部护理"),
    ]

    success_count = 0
    for title, desc, expected in test_cases:
        domains = detect_domain(title, desc)
        logger.info(f"📝 标题: {title}")
        logger.info(f"💬 描述: {desc}")

        if domains:
            detected_names = [DOMAIN_KEYWORDS[d]['name'] for d in domains]
            logger.info(f"🎯 检测到的领域: {', '.join(detected_names)}")

            # 验证是否匹配预期
            if expected in detected_names:
                logger.success(f"✅ 检测正确！预期: {expected}\n")
                success_count += 1
            else:
                logger.warning(f"⚠️ 检测不匹配。预期: {expected}，实际: {detected_names[0]}\n")
        else:
            logger.warning(f"⚠️ 未检测到任何领域（预期: {expected}）\n")

    logger.info(f"准确率: {success_count}/{len(test_cases)} ({success_count/len(test_cases)*100:.1f}%)\n")


def test_dynamic_prompt_loading():
    """测试动态提示词加载"""
    logger.info("=" * 80)
    logger.info("测试2: 动态提示词加载")
    logger.info("=" * 80 + "\n")

    test_videos = [
        {
            "title": "眼部按摩手法｜淡化黑眼圈",
            "description": "分享我的眼部护理方法",
            "expected_keywords": ["眼部问题相关", "眼纹", "黑眼圈"]
        },
        {
            "title": "痘痘肌护理流程｜祛痘精华推荐",
            "description": "针对痘痘肌的护理方案",
            "expected_keywords": ["面部问题相关", "痘痘", "痘印"]
        },
        {
            "title": "底妆不卡粉技巧｜化妆小白必看",
            "description": "解决底妆卡粉问题",
            "expected_keywords": ["彩妆问题相关", "底妆卡粉", "妆容"]
        }
    ]

    for i, video in enumerate(test_videos, 1):
        logger.info(f"--- 视频案例 {i} ---")
        logger.info(f"📝 标题: {video['title']}")
        logger.info(f"💬 描述: {video['description']}")

        # 生成动态提示词
        prompt = get_timeline_analysis_prompt(
            title=video['title'],
            description=video['description']
        )

        # 检查是否包含预期关键词
        found_keywords = [kw for kw in video['expected_keywords'] if kw in prompt]

        logger.info(f"🔍 提示词长度: {len(prompt)} 字符")
        logger.info(f"✅ 找到预期关键词: {len(found_keywords)}/{len(video['expected_keywords'])}")

        if found_keywords:
            logger.info(f"   匹配关键词: {', '.join(found_keywords)}")

        # 显示提示词片段（领域知识部分）
        if "【检测到的领域】" in prompt:
            domain_start = prompt.index("【检测到的领域】")
            domain_section = prompt[domain_start:domain_start+200]
            logger.info(f"📚 加载的领域知识片段:\n{domain_section}...\n")
        else:
            logger.warning("⚠️ 未检测到领域知识加载\n")


def test_no_pollution():
    """测试不同领域之间是否互不污染"""
    logger.info("=" * 80)
    logger.info("测试3: 领域知识隔离（无污染测试）")
    logger.info("=" * 80 + "\n")

    # 测试面部护理视频是否会加载眼部知识
    face_care_prompt = get_timeline_analysis_prompt(
        title="痘痘肌救星！祛痘精华推荐",
        description="面部护理"
    )

    # 测试唇部护理视频是否会加载眼部知识
    lip_care_prompt = get_timeline_analysis_prompt(
        title="唇部干裂救急｜唇膜推荐",
        description="唇部护理"
    )

    logger.info("测试1: 面部护理视频中是否有眼部污染")
    if "眼" in face_care_prompt and "眼部问题相关" in face_care_prompt:
        logger.error("❌ 发现眼部知识污染！")
    else:
        logger.success("✅ 无眼部知识污染")

    logger.info("\n测试2: 唇部护理视频中是否有眼部污染")
    if "眼" in lip_care_prompt and "眼部问题相关" in lip_care_prompt:
        logger.error("❌ 发现眼部知识污染！")
    else:
        logger.success("✅ 无眼部知识污染")

    # 测试眼部视频是否加载了眼部知识
    logger.info("\n测试3: 眼部护理视频中是否正确加载眼部知识")
    eye_care_prompt = get_timeline_analysis_prompt(
        title="眼部按摩手法｜淡化黑眼圈",
        description="眼部护理"
    )

    if "眼部问题相关" in eye_care_prompt and "黑眼圈" in eye_care_prompt:
        logger.success("✅ 正确加载眼部知识")
    else:
        logger.error("❌ 未加载眼部知识！")


def main():
    """运行所有测试"""
    print("\n" + "=" * 80)
    print("   动态知识库系统测试")
    print("=" * 80 + "\n")

    test_domain_detection()
    test_dynamic_prompt_loading()
    test_no_pollution()

    print("\n" + "=" * 80)
    logger.success("所有测试完成！")
    print("=" * 80 + "\n")

    logger.info("💡 测试总结:")
    logger.info("  1. 领域检测: 根据标题关键词自动识别领域")
    logger.info("  2. 动态加载: 只加载相关领域的专业知识")
    logger.info("  3. 知识隔离: 不同领域之间互不污染")
    logger.info("")
    logger.info("📌 优势:")
    logger.info("  - 避免了提示词污染问题")
    logger.info("  - 支持多领域扩展（可随时添加新领域）")
    logger.info("  - 提示词更精准，分析结果更准确")


if __name__ == "__main__":
    main()
