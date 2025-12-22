# -*- coding: utf-8 -*-
"""
AI产品提及位置分析测试

测试AI语义分析相比关键词匹配的准确度提升
"""
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from loguru import logger

load_dotenv()


def create_test_notes():
    """创建测试用的模拟笔记数据"""
    from viral_agent.models.viral_note import ViralNote

    test_cases = [
        # 案例1：开头直接提及品牌名
        ViralNote(
            note_id="test_001",
            title="雅诗兰黛眼霜真的太绝了！",
            desc="雅诗兰黛小棕瓶眼霜用了一个月，黑眼圈淡了好多！之前试过很多眼霜都没用，这款真的惊艳到我了。涂抹的时候要用无名指轻轻点拍，不要拉扯眼周皮肤。坚持用真的会有效果！",
            liked_count=5000,
            collected_count=3000,
            comment_count=200
        ),
        # 案例2：中间隐性指代（"它"）
        ViralNote(
            note_id="test_002",
            title="眼纹淡了80%的秘密",
            desc="最近很多姐妹问我眼纹怎么改善的。其实就是坚持护肤+按摩。每天晚上洁面后，我都会用眼霜配合按摩手法。它的质地很好推开，不会长脂肪粒。坚持一个月真的看到效果了！",
            liked_count=8000,
            collected_count=5000,
            comment_count=500
        ),
        # 案例3：结尾才提及产品
        ViralNote(
            note_id="test_003",
            title="护眼攻略｜上班族必看",
            desc="作为每天对着电脑12小时的打工人，眼睛真的太累了。干涩、酸胀、黑眼圈样样不落。后来我开始注意用眼卫生，每隔一小时休息5分钟，晚上用蒸汽眼罩。最后安利一下这款眼部精华，是我用过最温和的！",
            liked_count=3000,
            collected_count=2000,
            comment_count=100
        ),
        # 案例4：全文无产品提及（纯干货）
        ViralNote(
            note_id="test_004",
            title="眼部按摩手法教程",
            desc="分享一个我坚持了半年的眼部按摩手法！第一步：用食指和中指轻轻按压眼头。第二步：沿着眼眶画圈按摩。第三步：用指腹轻拍眼周促进吸收。每天坚持5分钟，眼周循环会好很多。",
            liked_count=2000,
            collected_count=1500,
            comment_count=50
        ),
        # 案例5：标题有产品，正文中间提及
        ViralNote(
            note_id="test_005",
            title="科颜氏牛油果眼霜测评",
            desc="被种草很久的一款眼霜，终于入手了！先说包装，绿色的玻璃瓶很有质感。打开是淡淡的清香。质地是乳霜状，有点厚重但好推开。用了两周，感觉眼周确实滋润了很多，干纹有改善。不过对黑眼圈效果一般。",
            liked_count=6000,
            collected_count=4000,
            comment_count=300
        ),
    ]

    return test_cases


def test_keyword_method():
    """测试关键词匹配方法"""
    from viral_agent.services.product_analyzer import ProductAnalyzer

    logger.info("=" * 50)
    logger.info("测试关键词匹配方法")
    logger.info("=" * 50)

    analyzer = ProductAnalyzer()  # 不传AI客户端
    notes = create_test_notes()

    result = analyzer.analyze_product_mentions(notes, use_ai=False)

    timing = result['product_timing']
    logger.info(f"分析方法: {timing.get('analysis_method', 'keyword')}")
    logger.info(f"分布: {timing['distribution']}")
    logger.info(f"推荐策略: {timing.get('optimal_strategy', 'N/A')}")

    return result


def test_ai_method():
    """测试AI语义分析方法"""
    from openai import OpenAI
    from viral_agent.services.product_analyzer import ProductAnalyzer

    logger.info("=" * 50)
    logger.info("测试AI语义分析方法")
    logger.info("=" * 50)

    api_key = os.getenv("OPENAI_API_KEY")
    api_base = os.getenv("OPENAI_API_BASE")
    model_name = os.getenv("MODEL_NAME", "gpt-4")

    if not api_key:
        logger.warning("未配置OPENAI_API_KEY，跳过AI测试")
        return None

    client = OpenAI(api_key=api_key, base_url=api_base)
    analyzer = ProductAnalyzer(ai_client=client, model_name=model_name)
    notes = create_test_notes()

    result = analyzer.analyze_product_mentions(notes, use_ai=True)

    timing = result['product_timing']
    logger.info(f"分析方法: {timing.get('analysis_method', 'unknown')}")
    logger.info(f"分布: {timing['distribution']}")

    if 'ai_details' in timing:
        logger.info(f"平均置信度: {timing['ai_details'].get('avg_confidence', 0):.2f}")

        # 显示每篇笔记的分析结果
        logger.info("\n每篇笔记的AI分析结果:")
        for item in timing['ai_details'].get('notes_analysis', []):
            logger.info(
                f"  笔记{item.get('note_index')}: "
                f"标题提及={item.get('title_has_product')}, "
                f"正文位置={item.get('content_position')}, "
                f"置信度={item.get('confidence', 0):.2f}"
            )

    logger.info(f"推荐策略: {timing.get('optimal_strategy', 'N/A')}")

    return result


def compare_methods():
    """对比两种方法的结果"""
    logger.info("\n" + "=" * 60)
    logger.info("对比分析：关键词匹配 vs AI语义分析")
    logger.info("=" * 60)

    keyword_result = test_keyword_method()
    print()
    ai_result = test_ai_method()

    if ai_result:
        kw_dist = keyword_result['product_timing']['distribution']
        ai_dist = ai_result['product_timing']['distribution']

        logger.info("\n" + "-" * 40)
        logger.info("结果对比:")
        logger.info("-" * 40)
        logger.info(f"{'指标':<20} {'关键词':>10} {'AI分析':>10}")
        logger.info("-" * 40)

        for key in ['early_mention_rate', 'middle_mention_rate', 'late_mention_rate']:
            kw_val = kw_dist.get(key, 0)
            ai_val = ai_dist.get(key, 0)
            diff = ai_val - kw_val
            diff_str = f"({'+' if diff >= 0 else ''}{diff:.1f})"
            logger.info(f"{key:<20} {kw_val:>10.1f}% {ai_val:>10.1f}% {diff_str}")

        logger.info("-" * 40)


if __name__ == "__main__":
    compare_methods()
