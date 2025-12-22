"""
测试爆文分析系统 - 图片笔记专用测试
"""
import asyncio
import sys
from pathlib import Path
from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from viral_agent.services.viral_collector import ViralNoteCollector
from viral_agent.services.viral_analyzer import ViralAnalyzer
from viral_agent.services.export_service import ExportService
from config import SPIDER_MODE, SEARCH_CONFIG, SAVE_CHOICE

async def test_image_notes():
    """测试图片笔记分析功能"""

    # 测试配置
    test_keyword = "护肤"  # 选择一个容易有图片笔记的关键词
    test_count = 10  # 先测试收集10篇爆款笔记

    logger.info(f"开始测试图片笔记分析功能")
    logger.info(f"测试关键词: {test_keyword}")
    logger.info(f"目标收集数量: {test_count}")

    try:
        # 1. 初始化收集器
        logger.info("初始化爆文收集器...")
        collector = ViralNoteCollector()

        # 2. 收集爆款笔记（仅收集图文类型）
        logger.info("开始收集爆款图文笔记...")
        viral_notes = await collector.search_viral_notes(
            query=test_keyword,
            target_count=test_count,
            interaction_threshold=5000,  # 图文笔记互动阈值
            note_type=2  # 2表示只收集图文笔记
        )

        if not viral_notes:
            logger.error("未收集到任何爆款笔记")
            return

        logger.success(f"成功收集 {len(viral_notes)} 篇爆款图文笔记")

        # 输出一些基本信息
        for i, note in enumerate(viral_notes[:3], 1):
            logger.info(f"笔记{i}: {note.title[:20]}... | 互动数: {note.interaction_score}")

        # 3. 分析爆文特征
        logger.info("开始分析爆文特征...")
        analyzer = ViralAnalyzer(enable_ai_analysis=False)  # 先不启用AI分析
        analysis_result = await analyzer.analyze_viral_notes(
            notes=viral_notes,
            keyword=test_keyword
        )

        logger.success("特征分析完成")

        # 输出关键分析结果
        logger.info("=== 分析结果摘要 ===")

        # 标题特征
        if 'title_patterns' in analysis_result:
            title_data = analysis_result['title_patterns']
            logger.info(f"标题平均长度: {title_data.get('avg_length', 'N/A')}")
            if 'top_keywords' in title_data and title_data['top_keywords']:
                top_words = title_data['top_keywords'][:5]
                logger.info(f"标题高频词: {', '.join([w['word'] for w in top_words])}")

        # 内容特征
        if 'content_patterns' in analysis_result:
            content_data = analysis_result['content_patterns']
            logger.info(f"内容平均长度: {content_data.get('avg_length', 'N/A')}")
            if 'top_tags' in content_data and content_data['top_tags']:
                top_tags = content_data['top_tags'][:5]
                logger.info(f"热门标签: {', '.join([t['tag'] for t in top_tags])}")

        # 封面特征
        if 'cover_features' in analysis_result and analysis_result['cover_features'].get('enabled'):
            cover_data = analysis_result['cover_features']
            if 'text_analysis' in cover_data:
                text_data = cover_data['text_analysis']
                logger.info(f"有文字的封面占比: {text_data.get('covers_with_text_percent', 'N/A')}%")

        # 产品特征
        if 'product_features' in analysis_result:
            product_data = analysis_result['product_features']
            if 'marketing_scenarios' in product_data:
                scenarios = product_data['marketing_scenarios']
                top_scenario = max(scenarios.items(), key=lambda x: x[1])
                logger.info(f"最常见营销场景: {top_scenario[0]} ({top_scenario[1]}篇)")

        # 4. 生成爆文模型
        if 'viral_model' in analysis_result:
            model = analysis_result['viral_model']
            logger.info("=== 爆文创作建议 ===")

            if 'title_formula' in model:
                formula = model['title_formula']
                if 'templates' in formula:
                    logger.info(f"推荐标题模板: {formula['templates'][0] if formula['templates'] else 'N/A'}")

            if 'content_structure' in model:
                structure = model['content_structure']
                if 'opening_hooks' in structure:
                    logger.info(f"开头钩子建议: {structure['opening_hooks'][0] if structure['opening_hooks'] else 'N/A'}")

        # 5. 导出Excel报告
        logger.info("生成Excel分析报告...")
        export_service = ExportService()
        excel_path = export_service.export_to_excel(
            notes=viral_notes,
            analysis_result=analysis_result,
            keyword=test_keyword
        )

        if excel_path and Path(excel_path).exists():
            logger.success(f"Excel报告已生成: {excel_path}")
        else:
            logger.warning("Excel报告生成失败")

        logger.success("图片笔记分析测试完成！")

    except Exception as e:
        logger.error(f"测试过程中出错: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

def main():
    """主函数"""
    logger.info("启动爆文分析测试...")

    # 运行异步测试
    asyncio.run(test_image_notes())

    logger.info("测试结束")

if __name__ == "__main__":
    main()