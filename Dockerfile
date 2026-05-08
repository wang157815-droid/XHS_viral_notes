FROM python:3.10-slim

WORKDIR /app

# 配置国内Debian镜像源（阿里云）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true

RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    build-essential \
    git \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN python --version && node --version && npm --version

# 配置国内 pip 镜像源（阿里云）
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config set global.trusted-host mirrors.aliyun.com

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 安装额外依赖（requirements.txt 中被注释或遗漏的）
# - chromadb: RAG知识库向量检索
# - opencv-python-headless: 视频帧提取分析（headless版无GUI依赖）
# - playwright: 扫码登录需要浏览器自动化
# 注：OCR 使用 AI 多模态 API（ai_ocr_service.py），无需本地 EasyOCR/PyTorch
RUN pip install --no-cache-dir chromadb opencv-python-headless playwright

# 安装 Playwright Chromium 浏览器及其系统依赖
# --with-deps 会自动安装 libnss3, libatk1.0 等必要的系统库
RUN playwright install chromium --with-deps

COPY . .

# 安装 Node.js 依赖（crypto-js 等，用于请求签名生成）
RUN npm install

# RedMuse 新后端端口
EXPOSE 8100

ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production

# 启动 RedMuse 新后端；旧 viral_app.py 仅保留维护 stub
CMD ["python", "backend/run.py", "--no-reload", "--host", "0.0.0.0", "--port", "8100"]
