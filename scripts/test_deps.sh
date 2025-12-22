#!/bin/bash
# 测试依赖是否安装正确的脚本

echo "=========================================="
echo "Spider_XHS 依赖测试脚本"
echo "=========================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 测试函数
test_import() {
    local module=$1
    local package=$2

    if [ -z "$package" ]; then
        package=$module
    fi

    python3 -c "import $module" 2>/dev/null
    if [ $? -eq 0 ]; then
        version=$(pip show $package 2>/dev/null | grep Version | cut -d' ' -f2)
        echo -e "${GREEN}✓${NC} $package ($version)"
    else
        echo -e "${RED}✗${NC} $package - 未安装"
        return 1
    fi
}

# 测试基础依赖
echo ""
echo "1. 测试基础依赖："
test_import "execjs" "PyExecJS"
test_import "requests"
test_import "loguru"
test_import "dotenv" "python-dotenv"
test_import "retry"
test_import "openpyxl"

# 测试Web框架依赖
echo ""
echo "2. 测试Web框架依赖："
test_import "fastapi"
test_import "uvicorn"
test_import "jinja2"
test_import "multipart" "python-multipart"
test_import "aiofiles"

# 测试数据处理依赖
echo ""
echo "3. 测试数据处理依赖："
test_import "jieba"
test_import "openai"
test_import "wordcloud"
test_import "pandas"
test_import "numpy"
test_import "PIL" "Pillow"

# 测试OCR依赖（可选）
echo ""
echo "4. 测试OCR依赖（可选）："
python3 -c "import paddle" 2>/dev/null
if [ $? -eq 0 ]; then
    version=$(pip show paddlepaddle 2>/dev/null | grep Version | cut -d' ' -f2)
    if [ -z "$version" ]; then
        version=$(pip show paddlepaddle-gpu 2>/dev/null | grep Version | cut -d' ' -f2)
        echo -e "${GREEN}✓${NC} paddlepaddle-gpu ($version)"
    else
        echo -e "${GREEN}✓${NC} paddlepaddle ($version)"
    fi
else
    echo -e "${YELLOW}⚠${NC} PaddlePaddle - 未安装（可选）"
fi

python3 -c "import paddleocr" 2>/dev/null
if [ $? -eq 0 ]; then
    version=$(pip show paddleocr 2>/dev/null | grep Version | cut -d' ' -f2)
    echo -e "${GREEN}✓${NC} paddleocr ($version)"
else
    echo -e "${YELLOW}⚠${NC} PaddleOCR - 未安装（可选）"
fi

# 测试Node.js环境
echo ""
echo "5. 测试Node.js环境："
if command -v node &> /dev/null; then
    node_version=$(node --version)
    echo -e "${GREEN}✓${NC} Node.js ($node_version)"

    if [ -f "package.json" ]; then
        if [ -d "node_modules" ]; then
            echo -e "${GREEN}✓${NC} Node.js依赖已安装"
        else
            echo -e "${YELLOW}⚠${NC} Node.js依赖未安装，请运行: npm install"
        fi
    fi
else
    echo -e "${RED}✗${NC} Node.js - 未安装"
fi

# 检查OpenAI版本兼容性
echo ""
echo "6. 检查OpenAI API版本："
python3 -c "
import openai
import sys
version = openai.__version__
major_version = int(version.split('.')[0])
if major_version >= 1:
    print(f'\033[0;32m✓\033[0m OpenAI API v{version} (新版本)')
    print('  注意：代码需要适配新版本API')
else:
    print(f'\033[1;33m⚠\033[0m OpenAI API v{version} (旧版本)')
    print('  建议升级到1.x版本')
" 2>/dev/null

# 检查numpy和pandas兼容性
echo ""
echo "7. 检查numpy/pandas兼容性："
python3 -c "
import numpy as np
import pandas as pd
print(f'numpy版本: {np.__version__}')
print(f'pandas版本: {pd.__version__}')
# 测试基本功能
df = pd.DataFrame({'a': [1, 2, 3]})
arr = np.array([1, 2, 3])
print('\033[0;32m✓\033[0m numpy和pandas兼容性正常')
" 2>/dev/null || echo -e "${RED}✗${NC} numpy/pandas兼容性问题"

echo ""
echo "=========================================="
echo "测试完成！"
echo "=========================================="