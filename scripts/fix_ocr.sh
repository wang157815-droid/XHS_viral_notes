#!/bin/bash

# OCR修复脚本
# 如果PaddleOCR初始化失败，运行此脚本降级到兼容版本

echo "========================================="
echo "  小红书爆文Agent - OCR修复工具"
echo "========================================="
echo ""

echo "当前PaddlePaddle版本："
pip show paddlepaddle 2>/dev/null || echo "未安装"

echo ""
echo "当前PaddleOCR版本："
pip show paddleocr 2>/dev/null || echo "未安装"

echo ""
echo "========================================="
echo ""

read -p "是否卸载当前版本并安装兼容版本？(y/n) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]
then
    echo ""
    echo "步骤1: 卸载当前版本..."
    pip uninstall -y paddlepaddle paddleocr

    echo ""
    echo "步骤2: 安装兼容版本..."
    echo "  - PaddlePaddle 2.5.2"
    echo "  - PaddleOCR 2.6.1"
    pip install paddlepaddle==2.5.2 paddleocr==2.6.1

    echo ""
    echo "步骤3: 验证安装..."
    python test_ocr.py

    if [ $? -eq 0 ]; then
        echo ""
        echo "✅ OCR修复成功！"
        echo ""
        echo "您现在可以："
        echo "1. 重启 viral_app.py 服务"
        echo "2. 进行新的爆文分析任务"
        echo "3. 查看封面文字识别结果"
    else
        echo ""
        echo "❌ OCR修复失败，可能需要手动排查"
    fi
else
    echo ""
    echo "取消修复"
fi
