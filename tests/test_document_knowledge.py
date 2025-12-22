"""
测试文档解析和知识检索模块
测试以下核心功能：
1. DocumentParser - 文档解析器
2. UnifiedKnowledgeRetriever - 统一知识检索器
"""
import sys
from pathlib import Path
from loguru import logger

# 添加项目路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 配置日志
logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")


def test_document_parser():
    """测试文档解析器"""
    from viral_agent.services.document_parser import DocumentParser

    logger.info("=" * 60)
    logger.info("测试 1: DocumentParser 文档解析器")
    logger.info("=" * 60)

    try:
        parser = DocumentParser()
        logger.success("✓ DocumentParser 初始化成功")

        # 1.1 测试文本分块功能
        logger.info("\n--- 测试文本分块 ---")
        test_text = """
        这是一个测试文档的内容。我们需要测试文本分块功能是否正常工作。

        第一段：小红书爆款笔记通常具有以下特点：标题吸引人、内容有价值、排版美观、话题选择精准。

        第二段：在创作过程中，我们需要注意用户痛点，提供解决方案，同时自然地植入产品信息。

        第三段：成功的案例显示，真诚分享和情感共鸣是获得高互动的关键因素。
        """

        # 测试不同分块大小
        chunks = parser.split_text(test_text, chunk_size=100, overlap=20)
        logger.info(f"✓ 文本分块成功：{len(chunks)} 个块")
        for i, chunk in enumerate(chunks[:3], 1):  # 只显示前3个
            logger.debug(f"  块 {i}: {chunk[:50]}...")

        # 1.2 测试关键词提取
        logger.info("\n--- 测试关键词提取 ---")
        try:
            keywords = parser.extract_keywords(test_text, top_k=5)
            if keywords:
                logger.success(f"✓ 关键词提取成功: {', '.join(keywords)}")
            else:
                logger.warning("⚠ 关键词提取返回空（可能缺少jieba）")
        except Exception as e:
            logger.warning(f"⚠ 关键词提取跳过: {e}")

        # 1.3 测试创建测试文件并解析
        logger.info("\n--- 测试文件解析 ---")

        # 创建测试TXT文件
        test_file = project_root / "test_sample_doc.txt"
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write("测试文档\n\n这是一个用于测试的简单文本文件。\n内容包括多行文本。")

        result = parser.parse_file(str(test_file))
        logger.success(f"✓ TXT文件解析成功")
        logger.info(f"  - 文件名: {result['metadata']['filename']}")
        logger.info(f"  - 格式: {result['metadata']['format']}")
        logger.info(f"  - 字数: {result['metadata']['word_count']}")
        logger.info(f"  - 行数: {result['metadata']['lines']}")

        # 清理测试文件
        test_file.unlink()

        logger.success("\n✓ DocumentParser 所有测试通过!")
        return True

    except Exception as e:
        logger.error(f"✗ DocumentParser 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_knowledge_retriever_without_rag():
    """测试知识检索器（不启用RAG）"""
    from viral_agent.services.knowledge_retriever import UnifiedKnowledgeRetriever

    logger.info("\n" + "=" * 60)
    logger.info("测试 2: UnifiedKnowledgeRetriever (禁用RAG)")
    logger.info("=" * 60)

    try:
        # 禁用RAG，只测试JSON配置部分
        retriever = UnifiedKnowledgeRetriever(enable_rag=False)
        logger.success("✓ UnifiedKnowledgeRetriever 初始化成功（RAG已禁用）")

        # 2.1 测试领域检测
        logger.info("\n--- 测试领域检测 ---")
        test_cases = [
            ("防脱精华推荐", "最近掉发严重，有什么好用的防脱产品吗？"),
            ("美白面膜使用心得", "分享一款超好用的美白面膜"),
            ("减肥餐食谱", "低卡健康的减肥餐怎么做")
        ]

        for title, desc in test_cases:
            knowledge = retriever.retrieve_knowledge(title=title, description=desc)
            logger.info(f"\n标题: {title}")
            logger.info(f"检测到领域: {knowledge['domains']}")
            logger.info(f"是否有JSON知识: {knowledge['has_json']}")
            if knowledge['structured_knowledge']:
                logger.info(f"JSON知识预览: {knowledge['structured_knowledge'][:100]}...")

        # 2.2 测试知识库摘要
        logger.info("\n--- 测试知识库摘要 ---")
        summary = retriever.get_knowledge_summary()
        logger.success(f"✓ 知识库统计:")
        logger.info(f"  - JSON领域数: {summary['json_domains']}")
        logger.info(f"  - RAG文档数: {summary['rag_documents']}")
        logger.info(f"  - 总知识源: {summary['total_knowledge']}")

        # 2.3 测试构建AI Prompt
        logger.info("\n--- 测试构建AI Prompt ---")
        base_prompt = "请分析这篇笔记的爆款特征"
        enhanced_prompt = retriever.build_ai_prompt_with_knowledge(
            base_prompt=base_prompt,
            title="防脱精华推荐",
            description="分享一款真实有效的防脱产品"
        )
        logger.success(f"✓ AI Prompt增强成功")
        logger.info(f"  - 原始Prompt长度: {len(base_prompt)}")
        logger.info(f"  - 增强后长度: {len(enhanced_prompt)}")
        logger.debug(f"  - Prompt预览:\n{enhanced_prompt[:200]}...")

        logger.success("\n✓ UnifiedKnowledgeRetriever (无RAG) 所有测试通过!")
        return True

    except Exception as e:
        logger.error(f"✗ UnifiedKnowledgeRetriever 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_knowledge_retriever_with_rag():
    """测试知识检索器（启用RAG）"""
    from viral_agent.services.knowledge_retriever import UnifiedKnowledgeRetriever

    logger.info("\n" + "=" * 60)
    logger.info("测试 3: UnifiedKnowledgeRetriever (启用RAG)")
    logger.info("=" * 60)

    try:
        # 启用RAG（需要环境配置正确）
        retriever = UnifiedKnowledgeRetriever(enable_rag=True)

        if not retriever.rag_service:
            logger.warning("⚠ RAG服务未初始化（可能缺少配置或依赖），跳过RAG测试")
            return True

        logger.success("✓ UnifiedKnowledgeRetriever 初始化成功（RAG已启用）")

        # 3.1 测试RAG搜索
        logger.info("\n--- 测试RAG文档搜索 ---")
        search_query = "如何自然引出产品"
        results = retriever.search_documents(query=search_query, top_k=3)

        if results:
            logger.success(f"✓ RAG搜索成功，找到 {len(results)} 个结果")
            for i, result in enumerate(results, 1):
                logger.info(f"\n  结果 {i}:")
                logger.info(f"    文档ID: {result['doc_id']}")
                logger.info(f"    相似度: {result['score']:.2%}")
                logger.info(f"    文本片段: {result['text'][:80]}...")
        else:
            logger.warning("⚠ RAG搜索无结果（可能知识库为空）")

        # 3.2 测试融合检索
        logger.info("\n--- 测试融合检索（JSON + RAG）---")
        knowledge = retriever.retrieve_knowledge(
            title="防脱精华推荐",
            description="分享一款真实有效的防脱产品",
            query="产品引出技巧"
        )

        logger.info(f"检测到领域: {knowledge['domains']}")
        logger.info(f"JSON知识: {'有' if knowledge['has_json'] else '无'}")
        logger.info(f"RAG知识: {'有' if knowledge['has_rag'] else '无'} ({len(knowledge['rag_results'])} 个片段)")

        if knowledge['document_knowledge']:
            logger.info(f"\nRAG知识预览:")
            logger.info(knowledge['document_knowledge'][0][:150] + "...")

        logger.success("\n✓ UnifiedKnowledgeRetriever (含RAG) 所有测试通过!")
        return True

    except ImportError as e:
        logger.warning(f"⚠ RAG依赖未安装，跳过RAG测试: {e}")
        return True
    except Exception as e:
        logger.error(f"✗ UnifiedKnowledgeRetriever (RAG) 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_integration():
    """集成测试：完整工作流"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 4: 集成测试 - 完整工作流")
    logger.info("=" * 60)

    try:
        from viral_agent.services.document_parser import DocumentParser
        from viral_agent.services.knowledge_retriever import UnifiedKnowledgeRetriever

        # 创建测试文档
        logger.info("\n--- 创建测试文档 ---")
        test_doc_path = project_root / "test_integration_doc.md"
        test_content = """# 小红书爆款笔记创作指南

## 防脱发领域技巧

在防脱发领域创作笔记时，应该：
1. 从用户痛点出发，描述掉发困扰
2. 分享真实使用体验
3. 自然引出产品信息
4. 避免生硬广告

## 成功案例

某笔记通过讲述"程序员掉发危机"的故事，自然引出防脱精华产品，获得10万+点赞。
"""

        with open(test_doc_path, 'w', encoding='utf-8') as f:
            f.write(test_content)
        logger.success("✓ 测试文档创建成功")

        # 解析文档
        logger.info("\n--- 解析文档 ---")
        parser = DocumentParser()
        parsed = parser.parse_file(str(test_doc_path))
        logger.success(f"✓ 文档解析成功: {parsed['metadata']['word_count']} 字")

        # 分块
        chunks = parser.split_text(parsed['text'], chunk_size=200, overlap=30)
        logger.success(f"✓ 文本分块成功: {len(chunks)} 个块")

        # 知识检索
        logger.info("\n--- 知识检索 ---")
        retriever = UnifiedKnowledgeRetriever(enable_rag=False)
        knowledge = retriever.retrieve_knowledge(
            title="防脱精华使用心得",
            description="分享我的防脱发经验"
        )
        logger.success(f"✓ 知识检索成功")
        logger.info(f"  - 领域: {knowledge['domains']}")
        logger.info(f"  - JSON知识: {len(knowledge['structured_knowledge'])} 字符")

        # 清理测试文件
        test_doc_path.unlink()

        logger.success("\n✓ 集成测试通过!")
        return True

    except Exception as e:
        logger.error(f"✗ 集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_dependencies():
    """检查依赖是否安装"""
    logger.info("\n" + "=" * 60)
    logger.info("检查依赖包")
    logger.info("=" * 60)

    required_packages = [
        ("loguru", "日志"),
        ("pydantic", "数据模型"),
    ]

    optional_packages = [
        ("pypdf", "PDF解析"),
        ("docx", "Word解析", "python-docx"),
        ("jieba", "中文分词"),
        ("chromadb", "向量数据库"),
        ("openai", "OpenAI API"),
    ]

    # 检查必需包
    logger.info("\n必需依赖:")
    for package_info in required_packages:
        package = package_info[0]
        desc = package_info[1]
        try:
            __import__(package)
            logger.success(f"  ✓ {package} ({desc})")
        except ImportError:
            logger.error(f"  ✗ {package} ({desc}) - 未安装")

    # 检查可选包
    logger.info("\n可选依赖:")
    for package_info in optional_packages:
        package = package_info[0]
        desc = package_info[1]
        install_name = package_info[2] if len(package_info) > 2 else package
        try:
            __import__(package)
            logger.success(f"  ✓ {package} ({desc})")
        except ImportError:
            logger.warning(f"  ⚠ {package} ({desc}) - 未安装 [可选]")
            logger.info(f"    安装命令: pip install {install_name}")


def main():
    """主测试函数"""
    logger.info("=" * 60)
    logger.info("开始测试文档解析和知识检索模块")
    logger.info("=" * 60)

    # 检查依赖
    check_dependencies()

    # 运行测试
    results = []

    # 测试1: DocumentParser
    results.append(("DocumentParser", test_document_parser()))

    # 测试2: UnifiedKnowledgeRetriever (无RAG)
    results.append(("KnowledgeRetriever (无RAG)", test_knowledge_retriever_without_rag()))

    # 测试3: UnifiedKnowledgeRetriever (含RAG)
    results.append(("KnowledgeRetriever (含RAG)", test_knowledge_retriever_with_rag()))

    # 测试4: 集成测试
    results.append(("集成测试", test_integration()))

    # 汇总结果
    logger.info("\n" + "=" * 60)
    logger.info("测试结果汇总")
    logger.info("=" * 60)

    for test_name, passed in results:
        status = "✓ 通过" if passed else "✗ 失败"
        logger.info(f"{test_name}: {status}")

    total = len(results)
    passed = sum(1 for _, p in results if p)

    logger.info("\n" + "=" * 60)
    if passed == total:
        logger.success(f"所有测试通过! ({passed}/{total})")
        return 0
    else:
        logger.error(f"部分测试失败: {passed}/{total} 通过")
        return 1


if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
