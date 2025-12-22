#!/bin/bash
# 视频分析功能快速测试脚本

cd "$(dirname "$0")/.."

echo "=============================="
echo "视频分析功能测试"
echo "=============================="

# 默认测试（基础+时间轴）
if [ -z "$1" ]; then
    python scripts/test_video_analysis.py --mode both
else
    # 传入自定义视频URL
    python scripts/test_video_analysis.py --url "$1" --mode both
fi
