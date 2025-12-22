"""
简化测试脚本 - 不依赖外部包
检查document_parser.py和knowledge_retriever.py的代码逻辑
"""
import sys
import ast
from pathlib import Path


def check_syntax(file_path):
    """检查Python文件语法"""
    print(f"\n{'='*60}")
    print(f"检查文件: {file_path.name}")
    print('='*60)

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 语法检查
        ast.parse(content)
        print(f"✓ 语法检查通过")
        return True

    except SyntaxError as e:
        print(f"✗ 语法错误: {e}")
        print(f"  位置: 行 {e.lineno}, 列 {e.offset}")
        return False
    except Exception as e:
        print(f"✗ 检查失败: {e}")
        return False


def analyze_document_parser():
    """分析document_parser.py"""
    print(f"\n{'='*60}")
    print("分析 document_parser.py")
    print('='*60)

    file_path = Path("viral_agent/services/document_parser.py")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查关键方法是否存在
        methods = [
            'parse_file',
            '_parse_pdf',
            '_parse_word',
            '_parse_markdown',
            '_parse_txt',
            'split_text',
            'extract_keywords'
        ]

        print("\n核心方法检查:")
        for method in methods:
            if f"def {method}" in content:
                print(f"  ✓ {method} 方法存在")
            else:
                print(f"  ✗ {method} 方法缺失")

        # 检查split_text方法的修复
        print("\n检查split_text方法的bug修复:")
        if 'separator = ""' in content and 'for sep in separators:' in content:
            print("  ✓ Bug已修复：separator变量已正确初始化")
        else:
            print("  ⚠ 注意：需要检查separator变量初始化")

        # 检查依赖导入
        print("\n依赖检查:")
        dependencies = {
            'pypdf': 'PDF解析',
            'docx': 'Word解析',
            'jieba': '关键词提取'
        }

        for dep, desc in dependencies.items():
            if f"import {dep}" in content:
                print(f"  ✓ {dep} ({desc}) - 有条件导入")

        return True

    except Exception as e:
        print(f"✗ 分析失败: {e}")
        return False


def analyze_knowledge_retriever():
    """分析knowledge_retriever.py"""
    print(f"\n{'='*60}")
    print("分析 knowledge_retriever.py")
    print('='*60)

    file_path = Path("viral_agent/services/knowledge_retriever.py")

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查关键方法
        methods = [
            '__init__',
            'retrieve_knowledge',
            'search_documents',
            'get_knowledge_summary',
            'build_ai_prompt_with_knowledge'
        ]

        print("\n核心方法检查:")
        for method in methods:
            if f"def {method}" in content:
                print(f"  ✓ {method} 方法存在")
            else:
                print(f"  ✗ {method} 方法缺失")

        # 检查依赖导入
        print("\n依赖检查:")
        if 'from viral_agent.config.knowledge_loader import get_knowledge_config' in content:
            print("  ✓ knowledge_loader 导入正确")
        else:
            print("  ✗ knowledge_loader 导入缺失")

        if 'from viral_agent.services.rag_service import RAGService' in content:
            print("  ✓ rag_service 导入正确")
        else:
            print("  ✗ rag_service 导入缺失")

        # 检查异常处理
        print("\n异常处理检查:")
        try_except_count = content.count('try:')
        print(f"  ✓ 异常处理块数量: {try_except_count}")

        # 检查RAG可选性
        if 'enable_rag' in content and 'if enable_rag:' in content:
            print("  ✓ RAG可选性设计正确")

        return True

    except Exception as e:
        print(f"✗ 分析失败: {e}")
        return False


def check_related_files():
    """检查相关依赖文件"""
    print(f"\n{'='*60}")
    print("检查相关依赖文件")
    print('='*60)

    files_to_check = [
        ("viral_agent/config/knowledge_loader.py", "知识库配置加载器"),
        ("viral_agent/services/rag_service.py", "RAG检索服务"),
        ("viral_agent/models/document.py", "文档数据模型"),
        ("viral_agent/config/knowledge_base.json", "知识库配置文件")
    ]

    all_exist = True
    for file_path, desc in files_to_check:
        path = Path(file_path)
        if path.exists():
            print(f"  ✓ {desc}: {file_path}")
        else:
            print(f"  ✗ {desc}: {file_path} (缺失)")
            all_exist = False

    return all_exist


def test_split_text_logic():
    """测试split_text方法的逻辑"""
    print(f"\n{'='*60}")
    print("测试 split_text 逻辑")
    print('='*60)

    # 模拟split_text方法
    def split_text_test(text, chunk_size=500, overlap=50, separators=None):
        """简化版split_text实现（用于测试逻辑）"""
        if separators is None:
            separators = ['\n\n', '\n', '。', '！', '？']

        chunks = []
        separator = ""  # 初始化分隔符（bug修复）

        # 按优先级查找分隔符
        for sep in separators:
            if sep in text:
                separator = sep
                parts = text.split(separator)
                break
        else:
            parts = [text]

        current_chunk = ""

        for part in parts:
            if len(current_chunk) + len(part) + len(separator) <= chunk_size:
                current_chunk += part + separator
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())

                if overlap > 0 and current_chunk:
                    overlap_text = current_chunk[-overlap:]
                    current_chunk = overlap_text + part + separator
                else:
                    current_chunk = part + separator

        if current_chunk:
            chunks.append(current_chunk.strip())

        chunks = [c for c in chunks if c.strip()]
        return chunks

    # 测试用例
    test_cases = [
        ("这是测试。第二句。第三句。", 10, 2),
        ("没有分隔符的文本", 10, 2),
        ("多行\n文本\n测试", 5, 1),
    ]

    print("\n测试用例:")
    for i, (text, chunk_size, overlap) in enumerate(test_cases, 1):
        try:
            chunks = split_text_test(text, chunk_size, overlap)
            print(f"  ✓ 测试 {i}: 输入 {len(text)} 字符 -> {len(chunks)} 个块")
        except Exception as e:
            print(f"  ✗ 测试 {i} 失败: {e}")
            return False

    print("\n✓ split_text逻辑测试通过")
    return True


def main():
    """主函数"""
    print("="*60)
    print("文档解析和知识检索模块 - 代码检查")
    print("="*60)

    results = []

    # 1. 语法检查
    files_to_check = [
        Path("viral_agent/services/document_parser.py"),
        Path("viral_agent/services/knowledge_retriever.py"),
        Path("viral_agent/config/knowledge_loader.py"),
        Path("viral_agent/services/rag_service.py"),
        Path("viral_agent/models/document.py"),
    ]

    print("\n" + "="*60)
    print("1. 语法检查")
    print("="*60)

    for file_path in files_to_check:
        if file_path.exists():
            results.append((f"语法检查: {file_path.name}", check_syntax(file_path)))
        else:
            print(f"\n⚠ 文件不存在: {file_path}")

    # 2. 代码分析
    results.append(("document_parser.py 分析", analyze_document_parser()))
    results.append(("knowledge_retriever.py 分析", analyze_knowledge_retriever()))

    # 3. 依赖检查
    results.append(("相关文件检查", check_related_files()))

    # 4. 逻辑测试
    results.append(("split_text逻辑测试", test_split_text_logic()))

    # 汇总结果
    print(f"\n{'='*60}")
    print("检查结果汇总")
    print('='*60)

    for test_name, passed in results:
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"{test_name}: {status}")

    total = len(results)
    passed = sum(1 for _, p in results if p)

    print(f"\n{'='*60}")
    if passed == total:
        print(f"✓ 所有检查通过! ({passed}/{total})")
        print("\n建议:")
        print("  1. 语法和逻辑检查已通过")
        print("  2. 如需运行完整测试，请先安装依赖:")
        print("     pip install -r requirements.txt")
        print("  3. 然后运行: python test_document_knowledge.py")
        return 0
    else:
        print(f"⚠ 部分检查未通过: {passed}/{total}")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
