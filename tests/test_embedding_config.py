"""
测试Embedding API配置
验证阿里云text-embedding-v4或其他Embedding模型配置是否正确
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 添加项目路径
sys.path.append(str(Path(__file__).parent))

print("=" * 60)
print("Embedding API配置测试")
print("=" * 60)

# 步骤1：检查环境变量配置
print("\n步骤1：检查环境变量配置")
print("-" * 60)

embedding_api_key = os.getenv("EMBEDDING_API_KEY")
embedding_api_base = os.getenv("EMBEDDING_API_BASE")
embedding_model = os.getenv("EMBEDDING_MODEL")
openai_api_key = os.getenv("OPENAI_API_KEY")
openai_api_base = os.getenv("OPENAI_API_BASE")

if embedding_api_key:
    print(f"✅ 配置了单独的Embedding API")
    print(f"   EMBEDDING_API_KEY: {embedding_api_key[:20]}...")
    print(f"   EMBEDDING_API_BASE: {embedding_api_base}")
    print(f"   EMBEDDING_MODEL: {embedding_model}")
elif openai_api_key:
    print(f"⚠️  使用主模型API配置（OPENAI_API_KEY）")
    print(f"   OPENAI_API_KEY: {openai_api_key[:20]}...")
    print(f"   OPENAI_API_BASE: {openai_api_base}")
    print(f"   EMBEDDING_MODEL: {embedding_model or 'text-embedding-3-small'}")
else:
    print("❌ 未配置任何API Key")
    print("   请在.env文件中配置EMBEDDING_API_KEY或OPENAI_API_KEY")
    sys.exit(1)

# 步骤2：测试Embedding API连接
print("\n步骤2：测试Embedding API连接")
print("-" * 60)

try:
    from openai import OpenAI

    # 使用与RAG服务相同的逻辑
    api_key = embedding_api_key or openai_api_key
    api_base = embedding_api_base or openai_api_base or "https://api.openai.com/v1"
    model = embedding_model or "text-embedding-3-small"

    print(f"正在连接到: {api_base}")
    print(f"使用模型: {model}")

    client = OpenAI(
        api_key=api_key,
        base_url=api_base
    )

    # 测试生成embedding
    print("\n正在测试向量生成...")
    test_texts = [
        "测试文本一：防脱精华推荐",
        "测试文本二：小红书爆款笔记分析"
    ]

    response = client.embeddings.create(
        input=test_texts,
        model=model
    )

    print(f"✅ Embedding生成成功！")
    print(f"   返回向量数量: {len(response.data)}")
    print(f"   向量维度: {len(response.data[0].embedding)}")
    print(f"   第一个向量前5维: {response.data[0].embedding[:5]}")

    # 显示成本估算（基于阿里云定价）
    if "dashscope.aliyuncs.com" in api_base:
        print(f"\n💰 成本估算（阿里云text-embedding-v4）：")
        print(f"   本次调用: {len(test_texts)} 个文本")
        print(f"   预计成本: 约 0.0007元/1000次 × {len(test_texts)}/1000 = 0.0000014元")
        print(f"   100篇文档: 约 0.07元")

except ImportError:
    print("❌ 缺少依赖：pip install openai")
    sys.exit(1)

except Exception as e:
    print(f"❌ Embedding API调用失败: {type(e).__name__}")
    print(f"   错误信息: {e}")
    print("\n可能的原因：")
    print("   1. API Key无效或已过期")
    print("   2. API Base地址错误")
    print("   3. 模型名称不正确")
    print("   4. 网络连接问题")
    print("\n请检查.env文件配置")
    sys.exit(1)

# 步骤3：测试RAG服务初始化
print("\n步骤3：测试RAG服务初始化")
print("-" * 60)

try:
    from viral_agent.services.rag_service import RAGService

    rag = RAGService()
    print(f"✅ RAG服务初始化成功")
    print(f"   Embedding模型: {rag.embedding_model}")

    # 测试简单的文档添加和检索
    print("\n测试文档添加和检索...")

    from viral_agent.models.document import KnowledgeDocument, DocumentMetadata

    test_doc = KnowledgeDocument(
        doc_id="test_embedding_001",
        title="测试文档：防脱精华使用技巧",
        description="测试Embedding API配置",
        domains=["hair_care"],
        content="""
        防脱精华的正确使用方法：
        1. 洗发后，擦干头发至半干状态
        2. 分区涂抹精华液于头皮
        3. 轻轻按摩2-3分钟促进吸收
        4. 无需冲洗，早晚各用一次
        """,
        metadata=DocumentMetadata(
            filename="test_embedding.txt",
            format="txt",
            word_count=100
        )
    )

    # 添加文档
    success = rag.add_document(test_doc)
    if success:
        print(f"✅ 文档添加成功")
    else:
        print(f"❌ 文档添加失败")

    # 搜索测试
    results = rag.search("如何使用防脱精华", top_k=3)
    if results:
        print(f"✅ 检索成功，找到 {len(results)} 个结果")
        print(f"   最佳匹配: {results[0].text[:50]}...")
        print(f"   相似度: {results[0].score:.2%}")
    else:
        print(f"⚠️  检索无结果（可能向量化失败）")

    # 清理测试数据
    rag.delete_document("test_embedding_001")
    print(f"✅ 测试数据清理完成")

except Exception as e:
    print(f"❌ RAG服务测试失败: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

# 总结
print("\n" + "=" * 60)
print("🎉 Embedding配置测试完成！")
print("=" * 60)
print("\n配置建议：")
if embedding_api_key and "dashscope.aliyuncs.com" in (embedding_api_base or ""):
    print("✅ 当前使用阿里云text-embedding-v4")
    print("   优点：成本低（0.0007元/1000次），精度高，速度快")
    print("   推荐用于生产环境")
elif embedding_api_key:
    print("✅ 当前使用单独配置的Embedding API")
    print("   可以根据需要灵活切换不同的API服务商")
elif openai_api_key:
    print("⚠️  当前使用主模型API配置")
    print("   建议：如果主模型不支持Embedding，请单独配置EMBEDDING_API_KEY")
else:
    print("❌ 未正确配置Embedding API")

print("\n可以开始使用RAG文档知识库功能了！")
print("运行: python viral_app.py")
print("访问: http://localhost:8000")
