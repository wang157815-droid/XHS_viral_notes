# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Spider_XHS 是一个专业的小红书数据采集和分析解决方案：
- **数据采集**：支持笔记爬取、用户数据采集，保存为 Excel 或媒体文件格式
- **内容创作**：包含小红书创作者平台的 API 接口，支持内容上传发布
- **爆文分析**（新增）：智能分析爆款笔记特征，生成创作模型，支持国内大模型API

## 开发命令

### 环境准备
```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Node.js 依赖（用于 JS 加密算法）
npm install
```

### 运行项目

#### 数据采集功能
```bash
# 推荐方式：使用配置文件
python easy_run.py

# 命令行参数方式
python run_spider.py --mode search --query "关键词" --num 20 --save excel

# 交互式模式
python run_spider.py --mode interactive

# 传统方式：直接运行
python main.py
```

#### 爆文分析系统（新功能）
```bash
# 启动Web界面
python viral_app.py
# 访问 http://localhost:8000

# 测试脚本
python test_viral_image.py  # 测试图文笔记分析
python test_deepseek.py     # 测试国内大模型API
```

#### 问题诊断
```bash
# 检查环境和依赖
python test_simple.py
```

### Docker 部署
```bash
# 构建镜像
docker build -t spider-xhs .

# 运行容器
docker run -p 5000:5000 spider-xhs
```

## 核心架构

### 项目结构
- **核心文件**
  - `config.py` - 集中配置文件，管理所有运行参数
  - `easy_run.py` - 简化运行脚本，推荐日常使用
  - `run_spider.py` - 灵活运行脚本，支持命令行和交互式
  - `main.py` - 包含 Data_Spider 核心类
  - `test_simple.py` - 环境诊断脚本
  - `.env.example` - 环境变量配置示例（含国内大模型配置）

- **apis/** - API 接口层
  - `xhs_pc_apis.py` - 小红书 PC 端数据接口，包含笔记、用户、搜索等核心功能
  - `xhs_creator_apis.py` - 小红书创作者平台 API，支持内容上传发布

- **xhs_utils/** - 工具模块
  - `xhs_util.py` - 核心加密算法，生成 xs、xsc 等关键参数
  - `cookie_util.py` - Cookie 处理工具
  - `data_util.py` - 数据处理，包括下载、保存 Excel 等
  - `common_util.py` - 通用工具函数

- **static/** - JS 加密脚本
  - `xhs_xs_xsc_56.js` - 生成请求签名的核心 JS 文件
  - `xhs_xray.js` - 生成 xray-traceid 的 JS 文件
  - 其他加密相关 JS 文件

- **viral_agent/** - 爆文分析模块（新增）
  - `models/viral_note.py` - 爆款笔记数据模型
  - `models/document.py` - 知识库文档数据模型
  - `services/viral_collector.py` - 爆款笔记收集器
  - `services/feature_extractor.py` - 特征提取器
  - `services/cover_analyzer.py` - 封面OCR分析
  - `services/product_analyzer.py` - 产品植入分析
  - `services/viral_analyzer.py` - AI深度分析（支持国内大模型，集成RAG）
  - `services/export_service.py` - Excel报告导出
  - `services/document_parser.py` - 文档解析服务（支持PDF/Word/MD/TXT）
  - `services/rag_service.py` - RAG向量检索服务（基于ChromaDB）
  - `services/knowledge_retriever.py` - 统一知识检索器（融合JSON+RAG）
  - `config/knowledge_loader.py` - 知识库配置管理
  - `storage/chromadb/` - 向量数据库存储
  - `storage/documents/` - 原始文档存储

- **web/** - Web界面
  - `templates/index.html` - 爆文分析Web界面

### 核心技术要点

1. **请求签名生成**
   - 通过 `execjs` 执行 JS 文件生成 xs、xsc、xt 等签名参数
   - 关键函数：`generate_xs_xs_common()` 在 xhs_util.py:23-26
   - 每个 API 请求都需要正确的签名才能获取数据

2. **Cookie 管理**
   - Cookie 从 .env 文件读取
   - 需要登录后的有效 Cookie，包含 a1 等关键字段
   - Cookie 用于身份验证和签名生成

3. **数据采集流程**
   - `Data_Spider` 类（main.py）提供三种主要采集方式：
     - 单个/批量笔记采集：`spider_note()` / `spider_some_note()`
     - 用户全部笔记采集：`spider_user_all_note()`
     - 搜索结果采集：`spider_some_search_note()`

4. **数据保存机制**
   - 支持三种保存方式：media（图片/视频）、excel、all（全部）
   - 自动创建目录结构：download/excel/ 和 download/media/
   - Excel 保存使用 openpyxl，媒体文件按笔记 ID 分目录存储

### API 调用链路

1. **获取笔记信息**
   ```
   main.py (Data_Spider)
   → xhs_pc_apis.py (get_note_info)
   → xhs_util.py (generate_request_params)
   → static/xhs_xs_xsc_56.js (签名生成)
   → 发送请求
   ```

2. **搜索功能**
   - 支持多维度搜索：排序方式、笔记类型、时间范围、地理位置等
   - 分页获取，自动处理 cursor 游标

## 配置管理

### config.py 参数详解
```python
# 爬虫模式
SPIDER_MODE = 'search'  # 'search' | 'user' | 'notes'

# 保存选项
SAVE_CHOICE = 'all'     # 'all' | 'excel' | 'media' | 'media-video' | 'media-image'

# 搜索配置
SEARCH_CONFIG = {
    'query': '关键词',
    'query_num': 10,
    'sort_type': 0,     # 0综合 1最新 2点赞 3评论 4收藏
    'note_type': 0,     # 0不限 1视频 2图文
    'note_time': 0,     # 0不限 1一天内 2一周内 3半年内
    'note_range': 0,    # 0不限 1已看过 2未看过 3已关注
}
```

### AI分析配置（.env文件）
系统支持多种国内大模型API，显著降低成本：

```bash
# 方案1：DeepSeek（推荐）- 成本降低95%
OPENAI_API_BASE="https://api.deepseek.com/v1"
OPENAI_API_KEY="sk-deepseek-xxx"
MODEL_NAME="deepseek-chat"

# 方案2：智谱GLM（免费）
OPENAI_API_BASE="https://open.bigmodel.cn/api/paas/v4"
OPENAI_API_KEY="xxx"
MODEL_NAME="glm-4-flash"

# 方案3：通义千问（中文最强）
OPENAI_API_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
OPENAI_API_KEY="sk-xxx"
MODEL_NAME="qwen-max"
```

## 多关键词检索系统（新功能）

### 功能概述

为确保分析样本量充足（至少50条以上），系统支持多关键词检索模式：

- **最多5个关键词**：如「巧克力」+「北欧」+「Fazer」
- **来源追踪**：每篇笔记记录匹配的所有关键词
- **智能去重**：按 `note_id` 合并，避免重复采集
- **样本量校验**：低于最低要求时显示警告

### 使用方式

#### Web 界面

1. 在搜索框输入关键词，按**回车**添加为标签
2. 点击标签上的 **×** 可删除
3. 设置**最低分析样本量**（默认50条）
4. 点击开始采集

#### API 调用

```bash
POST /api/viral/search
{
    "keywords": ["巧克力", "北欧", "Fazer"],
    "target_count": 150,
    "viral_ratio": 0.5,
    "min_sample_count": 50,
    "note_type": 0,
    "time_range": 0
}
```

### 采集策略

```
用户输入多个关键词
        ↓
系统计算每关键词目标数量（不超过总目标1.5倍分摊）
        ↓
依次按三维度（点赞/评论/收藏）采集每个关键词
        ↓
合并去重（记录每篇笔记的来源关键词）
        ↓
按互动分数排序 → 应用爆款比例筛选
        ↓
校验样本量 ≥ min_sample_count
```

### 数据格式

采集结果保存时包含多关键词统计：

```json
{
    "search_keywords": ["巧克力", "北欧", "Fazer"],
    "keyword_statistics": {
        "keyword_count": 3,
        "by_keyword": {
            "巧克力": 35,
            "北欧": 28,
            "Fazer": 15
        },
        "multi_match_count": 12
    },
    "notes": [
        {
            "note_id": "xxx",
            "title": "北欧巧克力推荐",
            "source_keywords": ["巧克力", "北欧"],
            "interaction_score": 15000
        }
    ]
}
```

### 配置参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `keywords` | 关键词列表（最多5个） | 必填 |
| `target_count` | 目标采集总数量 | 100 |
| `viral_ratio` | 爆款筛选比例 | 0.5 |
| `min_sample_count` | 最低分析样本量 | 50 |

### 注意事项

1. **关键词数量**：最多5个，避免采集时间过长
2. **目标数量分配**：系统自动计算每关键词目标，不会大幅超出用户设定
3. **样本量不足**：系统会显示警告，但允许继续分析
4. **关键词间隔**：每个关键词采集后等待5秒，避免触发反爬

## 注意事项

1. **Cookie 配置**
   - 必须使用登录后的有效 Cookie
   - Cookie 会过期，需要定期更新
   - 从浏览器 F12 开发者工具的网络请求中获取

2. **请求限制**
   - 建议添加适当延时，避免请求过于频繁
   - 支持代理配置，可通过 proxies 参数传入

3. **数据完整性**
   - URL 中的 xsec_token 会过期，需要及时处理
   - 下载失败会自动重试（通过 retry 装饰器）

4. **加密算法更新**
   - 小红书会不定期更新加密算法
   - 核心加密逻辑在 static/ 目录下的 JS 文件中
   - 如遇到接口失效，需要更新对应的 JS 文件

## RAG知识库系统（新功能）

### 系统架构

项目采用**双轨制知识库**设计，融合结构化配置和智能文档检索：

```
┌─────────────────────────────────────────────────────────┐
│              知识库系统（双轨制）                          │
├──────────────────────┬──────────────────────────────────┤
│  轨道1: 结构化知识     │  轨道2: 文档知识库 + RAG          │
├──────────────────────┼──────────────────────────────────┤
│ • JSON配置文件        │ • 文档上传（Word/PDF/MD/TXT）    │
│ • 手动填写表单        │ • 自动解析向量化                  │
│ • 固定规则和模板      │ • ChromaDB存储                   │
│ • 前端CRUD管理        │ • 智能语义检索                    │
├──────────────────────┴──────────────────────────────────┤
│          统一检索接口（融合两种知识）                      │
│  领域检测 → 获取JSON规则 + RAG检索文档 → 组合Prompt      │
└─────────────────────────────────────────────────────────┘
```

### 使用方式

#### 1. 上传知识文档

访问 Web 界面 (http://localhost:8000)，进入"知识库管理"：

- **支持格式**：PDF、Word (.docx)、Markdown (.md)、TXT
- **上传流程**：
  1. 选择文件或拖拽到上传区
  2. 填写文档标题和描述
  3. 选择关联领域（可多选）
  4. 点击上传

系统会自动：
- 解析文档内容
- 提取关键词
- 分块并向量化
- 存入ChromaDB向量数据库

#### 2. 文档管理操作

**查看文档列表**
```bash
GET /api/documents
GET /api/documents?domain=hair_care  # 按领域筛选
```

**搜索文档**
```bash
POST /api/documents/search
{
  "query": "防脱产品如何自然引出",
  "domains": ["hair_care"],
  "top_k": 5
}
```

**删除文档**
```bash
DELETE /api/documents/{doc_id}
```

#### 3. AI分析自动集成

当进行爆款笔记分析时，系统会自动：

1. **领域检测**：根据关键词识别所属领域
2. **知识检索**：
   - 从JSON配置获取结构化规则
   - 从RAG向量库检索相关文档
3. **Prompt增强**：将检索到的知识整合到AI分析提示词
4. **生成报告**：AI基于知识库生成更精准的分析

### 配置说明

#### Embedding模型配置

在 `.env` 文件中配置：

```bash
# 使用OpenAI兼容的Embedding API（推荐）
EMBEDDING_MODEL="text-embedding-3-small"

# 或使用本地Embedding模型（离线，但需要额外依赖）
# USE_LOCAL_EMBEDDING=true
# LOCAL_EMBEDDING_MODEL="paraphrase-multilingual-MiniLM-L12-v2"
```

#### 成本估算

- **向量化成本**：约 0.002元/100篇文档（使用text-embedding-3-small）
- **存储成本**：ChromaDB本地存储，无额外费用
- **检索成本**：本地向量检索，无API调用费用

### 技术细节

#### 文档处理流程

```python
上传文档
  ↓
解析内容（PDF/Word/MD/TXT）
  ↓
文本分块（500字/块，overlap=50）
  ↓
向量化（Embedding API）
  ↓
存入ChromaDB
  ↓
建立索引（支持元数据过滤）
```

#### 检索策略

**混合检索**：
- **向量检索**：语义相似度匹配
- **元数据过滤**：按领域、格式、上传时间等筛选
- **相似度阈值**：可配置最小匹配分数

**示例代码**：
```python
from viral_agent.services.knowledge_retriever import UnifiedKnowledgeRetriever

retriever = UnifiedKnowledgeRetriever()

# 检索知识
knowledge = retriever.retrieve_knowledge(
    title="防脱精华推荐",
    description="...",
    query="防脱产品如何自然引出"
)

print(knowledge['structured_knowledge'])  # JSON配置
print(knowledge['document_knowledge'])     # RAG检索结果
```

### 最佳实践

1. **文档组织**：
   - 按领域分类上传文档
   - 使用清晰的标题和描述
   - 定期更新过时内容

2. **知识质量**：
   - 上传高质量的成功案例
   - 避免重复或低质量文档
   - 保持文档内容的时效性

3. **性能优化**：
   - 控制单个文档大小（建议 < 10MB）
   - 合理设置检索结果数量（top_k=5-10）
   - 定期清理无用文档

### 故障排查

**问题1：文档上传失败**
- 检查文件格式是否支持
- 确认文件未损坏
- 查看日志获取详细错误信息

**问题2：RAG检索无结果**
- 确认已配置OPENAI_API_KEY
- 检查Embedding模型是否正确
- 验证ChromaDB目录权限

**问题3：向量化速度慢**
- 考虑使用本地Embedding模型
- 减小文档分块大小
- 批量上传时分批处理

## 调试技巧

1. **日志查看**
   - 使用 loguru 记录详细日志
   - 关注请求成功率和错误信息

2. **单步调试**
   - 可先测试单个笔记采集功能
   - 验证 Cookie 和签名是否正确生成

3. **常见问题**
   - "签名错误"：检查 JS 文件是否最新，Cookie 是否有效
   - "笔记不存在"：xsec_token 可能已过期
   - "请求失败"：检查网络连接和代理设置

## 视频分析系统（新功能）

### 系统架构

视频分析采用**统一下载 + 并行分析**架构，实现高效的视频内容分析：

```
┌─────────────────────────────────────────────────────────────────┐
│                    VideoEnhancedAnalyzer（协调器）                │
├─────────────────────────────────────────────────────────────────┤
│  1. 顶层统一下载视频（VideoDownloadManager）                      │
│  2. 并行执行所有分析任务                                          │
│  3. 统一释放视频资源                                              │
├───────────────┬───────────────┬───────────────┬─────────────────┤
│ 封面分析       │ 标题分析       │ 时间轴分析     │ 音画同步分析     │
│ (CoverClass)  │ (TitleClass)  │ (TimelineAn)  │ (AVSyncAnal)   │
└───────────────┴───────────────┴───────────────┴─────────────────┘
                        ↓ 共享下载 ↓
┌─────────────────────────────────────────────────────────────────┐
│              VideoDownloadManager（下载管理器）                   │
├─────────────────────────────────────────────────────────────────┤
│ • 下载去重：同一视频只下载一次                                     │
│ • 引用计数：acquire/release 管理生命周期                          │
│ • 原子写入：临时文件 + rename 避免读取半成品                       │
│ • 并发控制：Semaphore 限制同时下载数                              │
│ • 大小限制：考虑 base64 膨胀（×1.33）和 API 请求体限制            │
│ • 智能降级：大视频自动切换 URL 模式                                │
└─────────────────────────────────────────────────────────────────┘
```

### 智能降级机制

系统会自动检测视频大小，对大视频智能降级到 URL 模式：

```
视频分析请求（proxy 模式）
         ↓
HEAD 请求预估视频大小
         ↓
    ┌─────────────────┐
    │  大小 > 15MB?   │
    └────────┬────────┘
             │
    ┌────────┴────────┐
    ↓ 是              ↓ 否
自动降级到 URL 模式   正常下载 → base64 → AI 分析
    ↓
直接传 URL 给 AI
```

**为什么需要智能降级**：
- 通义千问等 AI API 有 **20MB 请求体限制**
- 视频经 base64 编码后会膨胀约 **1.33 倍**
- 因此原始视频需限制在 **15MB 以内**（15 × 1.33 ≈ 20MB）

**降级日志示例**：
```
⚠️ 视频预估 60.6MB > 15MB，自动降级到 URL 模式（避免下载失败和 API 超限）
```

### 视频源模式配置

在 `.env` 文件中配置视频处理方式：

```bash
# 视频源模式
VIDEO_SOURCE_MODE="url"      # url: URL直传给AI（默认，依赖AI能访问视频URL）
VIDEO_SOURCE_MODE="proxy"    # proxy: 本地下载后转base64传给AI（推荐，解决防盗链）

# 视频分析配置
VIDEO_ANALYSIS_MODE="full"   # full: 完整视频分析 | metadata: 仅元数据分析
VIDEO_MODEL_NAME="qwen3-vl-plus"  # 支持视频的多模态模型

# 下载配置
VIDEO_MAX_SIZE_MB=50         # 视频大小配置（MB），实际限制 = min(配置×0.75, 15MB)
VIDEO_DOWNLOAD_TIMEOUT=60    # 下载超时（秒）
VIDEO_MAX_CONCURRENT=2       # 最大并发下载数

# 音画同步分析
ENABLE_AV_SYNC=false         # 是否启用音画同步分析
CLEANUP_TEMP_FILES=true      # 是否清理临时文件
FRAME_INTERVAL=5.0           # 帧抽取间隔（秒）
MAX_FRAMES_PER_VIDEO=20      # 每视频最大帧数
```

### 核心组件

#### 1. VideoDownloadManager（下载管理器）

位置：`viral_agent/services/download/video_download_manager.py`

**关键特性**：
- **下载去重**：使用 `_in_progress` dict + `asyncio.Task` 确保同一 URL 只下载一次
- **引用计数**：`acquire()` 增加引用，`release()` 减少引用，归零时清理文件
- **缓存键设计**：优先使用 `note_id`，否则使用 URL path hash
- **原子写入**：先写临时文件，完成后 `os.rename()` 避免读取半成品

**使用示例**：
```python
from viral_agent.services.download import VideoDownloadManager

manager = VideoDownloadManager(
    cache_dir="datas/video_cache",
    max_size_mb=50,
    download_timeout=60
)

# 获取视频（自动去重）
handle = await manager.acquire(
    url=video_url,
    require_file=True,    # 需要本地文件路径
    require_bytes=True,   # 需要二进制数据
    note_id="note_123"
)

# 使用视频
print(f"文件路径: {handle.local_path}")
print(f"大小: {handle.size_bytes / 1024 / 1024:.1f}MB")

# 释放引用
manager.release(video_url, "note_123")
```

#### 2. VideoEnhancedAnalyzer（增强分析器）

位置：`viral_agent/services/video_enhanced_analyzer.py`

**工作流程**：
1. 顶层统一 `acquire` 视频（避免子任务各自下载）
2. 并发执行所有分析任务（封面、标题、时间轴、音画同步）
3. 所有任务完成后统一 `release` 释放资源

**使用示例**：
```python
from viral_agent.services.video_enhanced_analyzer import VideoEnhancedAnalyzer

analyzer = VideoEnhancedAnalyzer(
    enable_ai=True,
    enable_av_sync=True,
    video_source_mode='proxy'
)

# 分析单个视频
result = await analyzer.analyze_single_video(note)
print(f"状态: {result.analysis_status}")
print(f"时间轴: {result.timeline_analysis}")
print(f"音画同步: {result.av_sync_result}")

# 查看下载统计
stats = analyzer.get_download_stats()
print(f"缓存数: {stats['cached_count']}, 大小: {stats['total_size_mb']:.1f}MB")

# 清理缓存
analyzer.cleanup()
```

#### 3. AVSyncAnalyzer（音画同步分析器）

位置：`viral_agent/services/av_sync/__init__.py`

**功能**：
- 视频帧抽取（使用 ffmpeg）
- 音频提取和 ASR 语音识别（使用通义千问 ASR）
- 音画时间轴对齐
- 关键事件检测

**依赖**：
- ffmpeg（用于视频/音频处理）
- 通义千问 ASR API（用于语音识别）

### 数据流和缓存键一致性

为确保下载共享生效，`note_id` 需要在整个调用链中透传：

```
VideoEnhancedAnalyzer.analyze_single_video(note)
  │
  ├─ 顶层 acquire(url, note_id) ─────────────────────────────┐
  │                                                          │
  ├─ timeline_analyzer.analyze_timeline(..., note_id)        │
  │     └─ ai_analyzer.analyze_video(..., note_id)           │
  │         └─ _download_via_manager(note_id) ← 缓存键一致！ ─┤
  │                                                          │
  ├─ _run_av_sync_analysis(note, video_handle)               │
  │     └─ av_sync_analyzer.analyze_with_local_video(path)   │
  │                                                          │
  └─ 顶层 release(url, note_id) ─────────────────────────────┘
```

### 测试验证

```bash
# 启用 proxy 模式和音画同步进行测试
VIDEO_SOURCE_MODE=proxy ENABLE_AV_SYNC=true python scripts/test_video.sh

# 预期日志：
# "🎬 顶层统一下载视频: note_xxx"
# "✅ 视频下载完成: xxMB"
# "🔗 AVSync 使用预下载的视频: ..."
# "✅ 通过共享管理器获取视频: xxMB, 缓存=True"  ← 复用缓存
```

### 故障排查

**问题1：视频下载失败**
- 检查网络连接和代理配置
- 确认 `VIDEO_DOWNLOAD_TIMEOUT` 足够长
- 查看日志中的 HTTP 状态码

**问题2：视频仍被重复下载**
- 检查 `note_id` 是否在调用链中正确透传
- 确认 `VIDEO_SOURCE_MODE=proxy` 已设置
- 查看日志中的缓存命中情况

**问题3：音画同步分析失败**
- 确认 ffmpeg 已安装：`ffmpeg -version`
- 检查通义千问 ASR API 配置
- 查看 `datas/av_sync_cache/` 目录权限

**问题4：内存占用过高**
- 降低 `VIDEO_MAX_CONCURRENT` 值
- 减小 `VIDEO_MAX_SIZE_MB` 限制
- 启用 `CLEANUP_TEMP_FILES=true`