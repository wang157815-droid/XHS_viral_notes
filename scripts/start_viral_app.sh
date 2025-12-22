#!/bin/bash
# 启动爆文分析 Web 应用
# 使用方法: bash scripts/start_viral_app.sh

set -e

# 切换到项目根目录
cd "$(dirname "$0")/.."

echo "🚀 启动小红书爆文分析 Web 应用..."
echo "📁 工作目录: $(pwd)"

# 检查虚拟环境
if [ -d ".venv" ]; then
    echo "✅ 激活虚拟环境..."
    source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate 2>/dev/null || true
fi

# 检查依赖
echo "📦 检查依赖..."
python -c "import fastapi" 2>/dev/null || {
    echo "❌ 缺少 fastapi，正在安装..."
    pip install fastapi uvicorn
}

# 启动应用
echo "🌐 启动 Web 服务器..."
echo "📍 访问地址: http://localhost:8000"
echo "按 Ctrl+C 停止服务"
echo ""

python viral_app.py
