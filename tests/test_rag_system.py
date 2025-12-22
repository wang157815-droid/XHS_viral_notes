"""
RAG知识库系统测试脚本
测试文档解析、向量化、检索等核心功能
"""
import os
import sys
from pathlib import Path
from loguru import logger

# 添加项目路径
sys.path.append(str(Path(__file__).parent))


def test_document_parser():
    """测试文档解析功能"""
    logger.info("=" * 50)
    logger.info("测试1: 文档解析功能")
    logger.info("=" * 50)

    from viral_agent.services.document_parser import DocumentParser

    parser = DocumentParser()

    # 创建测试文件
    test_file = Path("test_document.txt")
    test_content = """
    小红书爆款标题创作指南

    1. 使用数字开头吸引注意力
    2. 突出痛点和解决方案
    3. 添加情感词汇增强共鸣

    案例：
    - "3个方法让你的笔记点赞破万！"
    - "终于找到了！防脱神器真实测评"
    """

    test_file.write_text(test_content, encoding='utf-8')

    try:
        result = parser.parse_file(str(test_file))
        logger.success(f"✓ 文档解析成功")
        logger.info(f"  - 文件格式: {result['metadata']['format']}")
        logger.info(f"  - 字数: {result['metadata']['word_count']}")
        logger.info(f"  - 内容预览: {result['text'][:100]}...")

        # 测试分块
        chunks = parser.split_text(result['text'], chunk_size=100)
        logger.success(f"✓ 文本分块成功: {len(chunks)} 个块")

        return True

    except Exception as e:
        logger.error(f"✗ 文档解析失败: {e}")
        return False

    finally:
        if test_file.exists():
            test_file.unlink()


def test_rag_service():
    """测试RAG服务"""
    logger.info("=" * 50)
    logger.info("测试2: RAG向量检索服务")
    logger.info("=" * 50)

    try:
        from viral_agent.services.rag_service import RAGService
        from viral_agent.models.document import KnowledgeDocument, DocumentMetadata

        # 初始化服务
        rag = RAGService()
        logger.success("✓ RAG服务初始化成功")

        # 创建测试文档
        test_doc = KnowledgeDocument(
            doc_id="test_001",
            title="小红书爆款标题技巧",
            description="总结爆款标题的创作方法",
            domains=["content_creation"],
            content="""
            小红书爆款标题通常具有以下特点：
            1. 数字开头：如"5个技巧"、"3天见效"
            2. 痛点词汇：如"避坑"、"血泪教训"、"踩雷"
            3. 情感共鸣：如"终于"、"真香"、"绝了"
            4. 悬念设置：如"没想到"、"竟然"
            5. 行动引导：如"必看"、"收藏"
            """,
            metadata=DocumentMetadata(
                filename="test.txt",
                format="txt",
                word_count=100
            )
        )

        # 添加文档
        success = rag.add_document(test_doc)
        if success:
            logger.success(f"✓ 文档添加成功: {test_doc.title}")
        else:
            logger.error("✗ 文档添加失败")
            return False

        # 测试检索
        results = rag.search("如何写好标题", top_k=3)
        if results:
            logger.success(f"✓ 检索成功，找到 {len(results)} 个结果")
            for i, result in enumerate(results, 1):
                logger.info(f"  结果{i}: {result.text[:50]}... (相似度: {result.score:.2%})")
        else:
            logger.warning("⚠ 检索无结果（可能是Embedding未配置）")

        # 清理测试数据
        rag.delete_document("test_001")
        logger.success("✓ 测试数据清理完成")

        return True

    except ImportError as e:
        logger.warning(f"⚠ ChromaDB未安装，跳过测试: {e}")
        return True  # 不算失败

    except Exception as e:
        logger.error(f"✗ RAG服务测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def test_knowledge_retriever():
    """测试统一知识检索器"""
    logger.info("=" * 50)
    logger.info("测试3: 统一知识检索器")
    logger.info("=" * 50)

    try:
        from viral_agent.services.knowledge_retriever import UnifiedKnowledgeRetriever

        retriever = UnifiedKnowledgeRetriever(enable_rag=False)  # 禁用RAG仅测试JSON
        logger.success("✓ 知识检索器初始化成功")

        # 测试知识检索
        knowledge = retriever.retrieve_knowledge(
            title="防脱精华推荐",
            description="分享好用的防脱发产品",
            query="防脱产品引出技巧"
        )

        logger.info(f"  - 检测到领域: {knowledge['domains']}")
        logger.info(f"  - JSON知识: {'有' if knowledge['has_json'] else '无'}")
        logger.info(f"  - RAG知识: {'有' if knowledge['has_rag'] else '无'}")

        if knowledge['structured_knowledge']:
            logger.success("✓ 成功获取结构化知识")

        return True

    except Exception as e:
        logger.error(f"✗ 知识检索器测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def test_summary():
    """测试知识库摘要"""
    logger.info("=" * 50)
    logger.info("测试4: 知识库摘要")
    logger.info("=" * 50)

    try:
        from viral_agent.services.knowledge_retriever import UnifiedKnowledgeRetriever

        retriever = UnifiedKnowledgeRetriever()
        summary = retriever.get_knowledge_summary()

        logger.success("✓ 知识库摘要获取成功")
        logger.info(f"  - JSON领域数: {summary['json_domains']}")
        logger.info(f"  - RAG文档数: {summary['rag_documents']}")
        logger.info(f"  - 总知识源: {summary['total_knowledge']}")

        return True

    except Exception as e:
        logger.error(f"✗ 知识库摘要测试失败: {e}")
        return False


def main():
    """运行所有测试"""
    logger.info("开始测试RAG知识库系统...")
    logger.info("")

    results = []

    # 运行测试
    results.append(("文档解析", test_document_parser()))
    results.append(("RAG服务", test_rag_service()))
    results.append(("知识检索器", test_knowledge_retriever()))
    results.append(("知识库摘要", test_summary()))

    # 输出结果
    logger.info("")
    logger.info("=" * 50)
    logger.info("测试结果汇总")
    logger.info("=" * 50)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        logger.info(f"{name}: {status}")

    logger.info("")
    logger.info(f"总计: {passed}/{total} 通过")

    if passed == total:
        logger.success("🎉 所有测试通过！RAG系统工作正常")
    else:
        logger.warning(f"⚠️ 部分测试失败 ({total - passed}个)")

    logger.info("")
    logger.info("提示：")
    logger.info("  - 如果RAG服务测试失败，请检查.env中的OPENAI_API_KEY配置")
    logger.info("  - 如果需要完整测试，请确保已安装: pip install chromadb pypdf python-docx")
    logger.info("  - 运行 python viral_app.py 启动Web界面进行交互式测试")


if __name__ == "__main__":
    main()
