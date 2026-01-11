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

# 检查 .env 文件和 JWT_SECRET
if [ ! -f ".env" ]; then
    echo "⚠️  警告: .env 文件不存在"
    echo "   请复制 .env.example 为 .env 并配置必要参数"
    echo ""
fi

# 检查 JWT_SECRET 是否配置
if [ -f ".env" ]; then
    # 提取 JWT_SECRET 的值（去除引号）
    jwt_value=$(grep "^JWT_SECRET=" .env | sed 's/^JWT_SECRET=//' | tr -d '"' | tr -d "'")

    # 检查是否为空或使用了默认占位符
    if [ -z "$jwt_value" ] || \
       [[ "$jwt_value" == *"your-jwt-secret"* ]] || \
       [[ "$jwt_value" == *"replace-with"* ]] || \
       [[ "$jwt_value" == *"here"* ]]; then
        echo "❌ 错误: JWT_SECRET 未配置或使用了默认占位符"
        echo ""
        echo "   请在 .env 文件中配置 JWT_SECRET:"
        echo "   JWT_SECRET=\"$(python3 -c 'import secrets; print(secrets.token_hex(32))' 2>/dev/null || echo '<随机32位十六进制字符串>')\""
        echo ""
        echo "   生成方法: python -c \"import secrets; print(secrets.token_hex(32))\""
        echo ""
        exit 1
    fi
fi

# 检查依赖
echo "📦 检查依赖..."
python -c "import fastapi" 2>/dev/null || {
    echo "❌ 缺少 fastapi，正在安装..."
    pip install fastapi uvicorn
}

python -c "import jwt" 2>/dev/null || {
    echo "❌ 缺少 PyJWT，正在安装..."
    pip install PyJWT
}

python -c "from passlib.context import CryptContext" 2>/dev/null || {
    echo "❌ 缺少 passlib，正在安装..."
    pip install 'passlib[bcrypt]'
}

# 启动应用
echo "🌐 启动 Web 服务器..."
echo "📍 访问地址: http://localhost:8000"
echo ""
echo "📌 首次启动说明:"
echo "   1. 系统会自动创建 admin 账户并在日志中显示随机密码"
echo "   2. 请使用该密码登录，登录后必须修改密码"
echo "   3. Cookie 等敏感配置需要登录后才能访问"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""

python viral_app.py
