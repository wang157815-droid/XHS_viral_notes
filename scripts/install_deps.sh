#!/bin/bash
# 安装Spider_XHS项目依赖的脚本

echo "=========================================="
echo "Spider_XHS 依赖安装脚本"
echo "=========================================="

# 检查Python版本
python_version=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
echo "检测到Python版本: $python_version"

if [[ $(echo "$python_version < 3.8" | bc -l) -eq 1 ]]; then
    echo "错误: Python版本需要3.8或更高"
    exit 1
fi

# 升级pip
echo ""
echo "1. 升级pip..."
pip install --upgrade pip

# 安装基础依赖
echo ""
echo "2. 安装基础依赖..."
pip install -r requirements.txt

# 询问是否需要OCR功能
echo ""
echo "3. OCR功能安装（可选）"
echo "OCR功能用于分析小红书封面图片中的文字"
read -p "是否需要安装OCR依赖？(y/n): " install_ocr

if [[ $install_ocr == "y" || $install_ocr == "Y" ]]; then
    echo ""
    echo "选择PaddlePaddle版本:"
    echo "1) CPU版本（推荐，适合大多数用户）"
    echo "2) GPU版本（需要NVIDIA显卡和CUDA）"
    read -p "请选择 (1/2): " paddle_choice

    if [[ $paddle_choice == "2" ]]; then
        echo "安装GPU版本PaddlePaddle..."
        echo "注意：请确保已安装CUDA"
        pip install paddlepaddle-gpu -i https://pypi.tuna.tsinghua.edu.cn/simple
    else
        echo "安装CPU版本PaddlePaddle..."
        pip install paddlepaddle==2.6.2 -i https://pypi.tuna.tsinghua.edu.cn/simple
    fi

    echo "安装PaddleOCR..."
    pip install paddleocr>=2.7.0.3 -i https://pypi.tuna.tsinghua.edu.cn/simple
fi

# 安装Node.js依赖
echo ""
echo "4. 检查Node.js环境..."
if command -v npm &> /dev/null; then
    echo "安装Node.js依赖..."
    npm install
else
    echo "警告: 未检测到npm，跳过Node.js依赖安装"
    echo "请手动安装Node.js后运行: npm install"
fi

echo ""
echo "=========================================="
echo "安装完成！"
echo ""
echo "注意事项："
echo "1. OpenAI库已升级到1.x版本，API调用方式有变化"
echo "2. 如果遇到numpy版本冲突，可以尝试："
echo "   pip install numpy==1.26.0"
echo "3. 请确保.env文件中的配置正确"
echo "=========================================="