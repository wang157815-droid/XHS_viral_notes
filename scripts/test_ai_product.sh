#!/bin/bash
# 测试AI产品分析功能

cd "$(dirname "$0")/.."

echo "========================================"
echo "测试AI产品提及位置分析"
echo "========================================"

# 检查.env文件
if [ ! -f .env ]; then
    echo "警告: .env文件不存在，请复制.env.example并配置API密钥"
    exit 1
fi

# 运行测试
python tests/test_ai_product_analysis.py
