#!/usr/bin/env python3
"""
OpenAI API兼容性修复脚本
用于检测并修复新旧版本OpenAI API的兼容性问题
"""

import os
import sys
import re
from pathlib import Path

def check_openai_version():
    """检查OpenAI库版本"""
    try:
        import openai
        version = openai.__version__
        major_version = int(version.split('.')[0])
        return major_version, version
    except ImportError:
        print("错误：未安装openai库")
        return None, None

def find_python_files(directory):
    """查找所有Python文件"""
    python_files = []
    for root, dirs, files in os.walk(directory):
        # 跳过虚拟环境和缓存目录
        dirs[:] = [d for d in dirs if d not in {'.venv', '__pycache__', 'node_modules', '.git'}]
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    return python_files

def analyze_openai_usage(file_path):
    """分析文件中的OpenAI API使用情况"""
    issues = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')

        # 检查旧版本API模式
        old_patterns = [
            (r'openai\.ChatCompletion\.create', 'ChatCompletion.create'),
            (r'openai\.Completion\.create', 'Completion.create'),
            (r'openai\.api_key\s*=', 'openai.api_key ='),
            (r'openai\.api_base\s*=', 'openai.api_base ='),
        ]

        for line_no, line in enumerate(lines, 1):
            for pattern, description in old_patterns:
                if re.search(pattern, line):
                    issues.append({
                        'file': file_path,
                        'line': line_no,
                        'issue': description,
                        'code': line.strip()
                    })
    except Exception as e:
        print(f"分析文件 {file_path} 时出错: {e}")

    return issues

def generate_migration_guide():
    """生成迁移指南"""
    guide = """
# OpenAI API v0.x 到 v1.x 迁移指南

## 主要变化

### 1. 客户端初始化
旧版本:
```python
import openai
openai.api_key = "sk-..."
openai.api_base = "https://api.openai.com/v1"
```

新版本:
```python
from openai import OpenAI
client = OpenAI(
    api_key="sk-...",
    base_url="https://api.openai.com/v1"  # 或其他API端点
)
```

### 2. Chat Completions API
旧版本:
```python
response = openai.ChatCompletion.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}]
)
answer = response.choices[0].message.content
```

新版本:
```python
response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}]
)
answer = response.choices[0].message.content
```

### 3. 流式响应
旧版本:
```python
for chunk in openai.ChatCompletion.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
):
    print(chunk.choices[0].delta.content)
```

新版本:
```python
stream = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
)
for chunk in stream:
    print(chunk.choices[0].delta.content)
```

### 4. 异步调用
新版本支持异步客户端:
```python
from openai import AsyncOpenAI

async_client = AsyncOpenAI(api_key="sk-...")

async def main():
    response = await async_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello!"}]
    )
    return response.choices[0].message.content
```
"""
    return guide

def main():
    """主函数"""
    print("=" * 50)
    print("OpenAI API 兼容性检查工具")
    print("=" * 50)

    # 检查版本
    major_version, version = check_openai_version()
    if major_version is None:
        sys.exit(1)

    print(f"\n当前OpenAI版本: {version}")
    if major_version >= 1:
        print("✓ 已安装新版本API (v1.x)")
    else:
        print("⚠ 使用旧版本API (v0.x)")
        print("建议升级: pip install openai --upgrade")

    # 扫描项目文件
    print("\n正在扫描项目文件...")
    project_root = Path(__file__).parent.parent
    python_files = find_python_files(project_root)

    all_issues = []
    for file_path in python_files:
        issues = analyze_openai_usage(file_path)
        all_issues.extend(issues)

    # 报告结果
    if all_issues:
        print(f"\n发现 {len(all_issues)} 个可能的兼容性问题：")
        for issue in all_issues:
            rel_path = os.path.relpath(issue['file'], project_root)
            print(f"\n文件: {rel_path}:{issue['line']}")
            print(f"问题: {issue['issue']}")
            print(f"代码: {issue['code']}")

        # 生成迁移指南
        print("\n" + "=" * 50)
        print("迁移建议")
        print("=" * 50)
        print(generate_migration_guide())

        # 保存报告
        report_file = project_root / "openai_migration_report.md"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("# OpenAI API 兼容性报告\n\n")
            f.write(f"## 发现的问题\n\n")
            for issue in all_issues:
                rel_path = os.path.relpath(issue['file'], project_root)
                f.write(f"- **{rel_path}:{issue['line']}**\n")
                f.write(f"  - 问题: {issue['issue']}\n")
                f.write(f"  - 代码: `{issue['code']}`\n\n")
            f.write("\n" + generate_migration_guide())

        print(f"\n详细报告已保存到: {report_file}")
    else:
        print("\n✓ 未发现明显的兼容性问题")

if __name__ == "__main__":
    main()