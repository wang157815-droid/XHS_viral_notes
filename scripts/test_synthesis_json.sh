#!/bin/bash
# 测试 synthesis_service JSON 解析功能

cd "$(dirname "$0")/.."

echo "运行 JSON 解析测试..."
python scripts/test_synthesis_json.py
