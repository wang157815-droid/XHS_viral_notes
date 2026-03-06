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
RUN pip install --no-cache-dir chromadb opencv-python-headless playwright==1.52.0

# Playwright 系统依赖（Debian Trixie 下 --with-deps 会因字体包重命名失败，手动安装）
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-unifont \
    libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libasound2 libxshmfence1 \
    libx11-xcb1 libxcb-dri3-0 libxfixes3 libpango-1.0-0 libcairo2 \
    && rm -rf /var/lib/apt/lists/*

# 下载 Playwright Chromium（使用淘宝镜像加速）
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
RUN playwright install chromium

COPY . .

# 安装 Node.js 依赖（crypto-js 等，用于请求签名生成）
RUN npm install

# 爆文分析Web应用端口
EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production

# 启动爆文分析Web应用
CMD ["python", "viral_app.py"] 
