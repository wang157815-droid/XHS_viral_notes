#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
视频分析功能单独测试脚本
用于快速验证视频AI分析是否正常工作
"""
import os
import sys
import asyncio

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from viral_agent.services.video_ai_analyzer import VideoAIAnalyzer


# 测试用的小红书视频URL（可以替换成其他视频）
TEST_VIDEO_URLS = [
    # 示例视频URL，运行时会从命令行参数或下方默认值获取
    "https://sns-video-bd.xhscdn.com/pre_post/1040g2t031967e9cd587049dlsncjkrudmecaes0"
]


async def test_single_video(video_url: str, title: str = "测试视频"):
    """测试单个视频分析"""
    logger.info("=" * 60)
    logger.info("视频分析功能测试")
    logger.info("=" * 60)

    # 打印配置信息
    logger.info(f"MULTIMODAL_API_BASE: {os.getenv('MULTIMODAL_API_BASE', '未配置')}")
    logger.info(f"MULTIMODAL_MODEL_NAME: {os.getenv('MULTIMODAL_MODEL_NAME', '未配置')}")
    logger.info(f"VIDEO_MODEL_NAME: {os.getenv('VIDEO_MODEL_NAME', '未配置')}")
    logger.info(f"VIDEO_ANALYSIS_MODE: {os.getenv('VIDEO_ANALYSIS_MODE', 'full')}")
    logger.info("-" * 60)

    # 初始化分析器
    logger.info("初始化 VideoAIAnalyzer...")
    analyzer = VideoAIAnalyzer()

    # 检查视频能力
    logger.info(f"当前视频模型: {analyzer.video_model}")
    logger.info(f"是否支持视频分析: {analyzer.check_video_capability()}")
    logger.info("-" * 60)

    # 执行视频分析
    logger.info(f"开始分析视频: {video_url[:80]}...")
    logger.info(f"视频标题: {title}")

    try:
        result = await analyzer.analyze_video(
            video_url=video_url,
            title=title,
            description="这是一个测试视频"
        )

        logger.info("-" * 60)
        logger.success("分析完成！")
        logger.info(f"分析结果:\n{result[:1000]}..." if len(result) > 1000 else f"分析结果:\n{result}")

        return True, result

    except Exception as e:
        logger.error(f"分析失败: {e}")
        import traceback
        traceback.print_exc()
        return False, str(e)


async def test_video_timeline(video_url: str, title: str = "测试视频"):
    """测试视频时间轴分析（7个数据点）"""
    logger.info("=" * 60)
    logger.info("视频时间轴分析测试（7个数据点）")
    logger.info("=" * 60)

    from viral_agent.services.video_timeline_analyzer import VideoTimelineAnalyzer
    from viral_agent.services.video_ai_analyzer import VideoAIAnalyzer

    # 初始化
    ai_analyzer = VideoAIAnalyzer()
    timeline_analyzer = VideoTimelineAnalyzer(ai_analyzer)

    logger.info(f"视频URL: {video_url[:80]}...")
    logger.info(f"标题: {title}")
    logger.info("-" * 60)

    try:
        result = await timeline_analyzer.analyze_timeline(
            video_url=video_url,
            title=title,
            description="测试描述"
        )

        logger.info("-" * 60)
        logger.success("时间轴分析完成！")

        # 打印7个数据点
        logger.info("📊 7个数据点:")
        logger.info(f"  1. 产品出现时间: {result.get('product_appear_time', '/')}")
        logger.info(f"  2. 产品使用时间: {result.get('product_use_time', '/')}")
        logger.info(f"  3. 干货开始时间: {result.get('content_start_time', '/')}")
        logger.info(f"  4. 内容类型: {result.get('content_type', '/')}")
        logger.info(f"  5. 切入方式: {result.get('entry_point', '/')}")
        logger.info(f"  6. 产品引出方式: {result.get('product_intro_way', '/')}")
        logger.info(f"  7. 产品植入方式: {result.get('product_embed_way', '/')}")

        return True, result

    except Exception as e:
        logger.error(f"时间轴分析失败: {e}")
        import traceback
        traceback.print_exc()
        return False, str(e)


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="视频分析功能测试")
    parser.add_argument(
        "--url", "-u",
        type=str,
        help="要测试的视频URL"
    )
    parser.add_argument(
        "--title", "-t",
        type=str,
        default="测试视频",
        help="视频标题（可选）"
    )
    parser.add_argument(
        "--mode", "-m",
        type=str,
        choices=["basic", "timeline", "both"],
        default="both",
        help="测试模式: basic=基础分析, timeline=时间轴分析, both=全部"
    )

    args = parser.parse_args()

    # 获取视频URL
    video_url = args.url or TEST_VIDEO_URLS[0]
    title = args.title

    logger.info(f"测试模式: {args.mode}")
    logger.info(f"视频URL: {video_url}")

    # 运行测试
    async def run_tests():
        results = {}

        if args.mode in ["basic", "both"]:
            success, result = await test_single_video(video_url, title)
            results["basic"] = {"success": success, "result": result}

        if args.mode in ["timeline", "both"]:
            # 基础测试和时间轴测试之间加个间隔
            if args.mode == "both":
                logger.info("\n等待2秒后进行时间轴测试...\n")
                await asyncio.sleep(2)

            success, result = await test_video_timeline(video_url, title)
            results["timeline"] = {"success": success, "result": result}

        return results

    results = asyncio.run(run_tests())

    # 打印总结
    logger.info("\n" + "=" * 60)
    logger.info("测试总结")
    logger.info("=" * 60)

    all_success = True
    for test_name, test_result in results.items():
        status = "✅ 通过" if test_result["success"] else "❌ 失败"
        logger.info(f"{test_name}: {status}")
        if not test_result["success"]:
            all_success = False

    if all_success:
        logger.success("\n🎉 所有测试通过！视频分析功能正常工作。")
    else:
        logger.error("\n❌ 部分测试失败，请检查配置和日志。")

    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
