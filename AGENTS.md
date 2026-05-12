<!-- AGENTS.md — Spider_XHS / RedMuse 项目指南 -->
# AGENTS.md

本文件为 AI 编程助手提供项目背景、结构、构建方式和开发约定。阅读者应对本项目一无所知，本文档是唯一需要阅读的项目入门材料。

---

## 项目概述

**Spider_XHS / RedMuse** 是一个专业的小红书（Xiaohongshu）数据采集、爆文分析与内容创作平台。项目目前包含**两个并行的后端系统**和一个**Next.js 前端**：

| 系统 | 入口 | 端口 | 说明 |
|------|------|------|------|
| **Legacy System（遗留系统）** | `viral_app.py` | 8000 | 独立 FastAPI 应用，提供数据采集和爆文分析 Web UI（Jinja2 模板）。 |
| **New Backend System（新后端）** | `backend/run.py` | 8100 | 模块化 FastAPI 应用，采用 DDD 分层架构，支持多 Agent 编排、Canvas 渲染、结构化任务管理。 |
| **Frontend（前端）** | `frontend/` | 3000 | Next.js 16 + React 19 + Tailwind CSS v4，提供现代化工作区、历史记录、知识库和设置页面。 |

### 主要功能模块
- **数据采集**：支持笔记爬取、用户数据采集、多维度搜索，保存为 Excel 或媒体文件。
- **内容创作**：包含小红书创作者平台 API 接口，支持内容上传发布。
- **爆文分析**：智能分析爆款笔记特征，生成创作模型，支持国内大模型 API。
- **视频分析**：封面/标题/时间轴/音画同步多维度分析，支持智能降级。
- **RAG 知识库**：文档上传 + 自动分块向量化（pgvector）。
- **多 Agent 编排**：6 阶段流水线（InputParser → Crawler → Image/Video → ViralModel → Insight/RAG → CanvasRender）。

---

## 技术栈

### 后端 / 核心
- **Python 3.10+**
- **FastAPI** + **Uvicorn** — Web 框架 / ASGI 服务器
- **Pydantic** — 数据校验
- **OpenAI-compatible SDK** (`openai>=1.0.0`) — LLM 交互
- **Jinja2** — 遗留系统的服务端模板

### 前端
- **Next.js 16.2.4** + **React 19.2.4** + **TypeScript 5**
- **Tailwind CSS v4** — 原子化 CSS，无 UI 组件库，全自定义组件
- **App Router** — 文件系统路由

### 数据与 AI
- **Pandas**, **NumPy 1.x**, **Jieba** — 数据分析与 NLP
- **ChromaDB** — 向量数据库（RAG）
- **LangGraph 0.2.x** — Agent 编排引擎
- **OpenAI / DeepSeek / Zhipu GLM / DashScope (通义千问)** — LLM 与 Embedding 提供商

### 基础设施（阶段 4α）
- **Redis 7** — 缓存、ARQ Broker、SSE Backlog
- **ARQ** — 异步任务队列
- **PostgreSQL 17 + pgvector** — 向量存储（当前仅笔记向量，业务表计划 4.6 迁移）
- **SQLAlchemy 2.0 async** — ORM
- **Nginx** — 反向代理

### 浏览器自动化与媒体
- **Playwright** — QR 码登录自动化
- **ffmpeg** — 视频帧抽取 / AV 同步分析
- **OpenCV (headless)** — 视频处理

### 请求签名
- **Node.js 20** + **PyExecJS / xhshow** — 小红书 API 签名生成（`xs`、`xsc`、`xt`）
- 默认使用 `xhshow` 纯 Python 后端（`static/xhs_xs_xsc_56.js` 为 2024 旧版，已过时）

---

## 项目结构

### 根目录核心文件

| 文件 | 用途 |
|------|------|
| `config.py` | 遗留爬虫的集中配置（模式、搜索参数、输出路径） |
| `main.py` | 遗留核心爬虫，`Data_Spider` 类 |
| `run_spider.py` | 灵活 CLI 运行器，支持命令行 / 交互式 / JSON 配置 |
| `viral_app.py` | 遗留 FastAPI 爆文分析应用（端口 8000） |
| `requirements.txt` | Python 依赖清单 |
| `package.json` | Node.js 依赖（JS 签名算法：`crypto-js`、`jsdom`） |
| `Dockerfile` | Python 3.10-slim + Node 20 + ffmpeg + Playwright |
| `docker-compose.yml` | 5 服务编排 |
| `.env` / `.env.example` | 环境变量（含 AI 模型、Cookie、视频分析等 200+ 项配置） |

### `apis/` — API 接口层
- `xhs_pc_apis.py` — 小红书 PC 端数据接口（搜索、用户、笔记、评论等），约 1230 行
- `xhs_creator_apis.py` — 创作者平台 API（已发布笔记）

### `xhs_utils/` — 工具模块
- `xhs_util.py` — 核心签名生成（`generate_request_params`），支持 `execjs` 和 `xhshow` 双后端
- `xhshow_adapter.py` — 现代纯 Python 签名后端（默认），含 GET 请求 MD5 修复补丁
- `cookie_util.py` — Cookie 字符串 ↔ Dict 转换
- `data_util.py` — 笔记处理、媒体下载、Excel 导出
- `url_validator.py` — 视频 URL 验证、压缩 URL 选择、大小估算
- `common_util.py` — 通用工具函数（`load_env`、`init`）
- `xhs_creator_util.py` — 创作者平台签名

### `static/` — JS 加密脚本
- `xhs_xs_xsc_56.js` — 遗留签名生成 JS（2024 版，已过时）
- `xhs_creator_xs.js` — 创作者平台签名 JS
- `xhs_xray.js` — `xray-traceid` 生成
- `xhs_xray_pack1.js` / `xhs_xray_pack2.js` — xray 依赖包
- `xs-common-1128.js` — Native 环境补丁

### `viral_agent/` — 爆文分析模块（遗留系统核心）
```
viral_agent/
├── auth/                    # JWT 认证服务
├── config/                  # 知识库配置管理（JSON + loader）
├── models/                  # ViralNote、VideoAnalysisResult、Document 等
├── prompts/                 # 18+ 个 LLM Prompt 模块
├── services/
│   ├── core/                # viral_collector、viral_analyzer、feature_extractor、auto_resupply
│   ├── video/               # 视频增强分析器（VideoEnhancedAnalyzer 等 11 个模块）
│   ├── image/               # 多模态分析器、封面分析、产品分析、场景分析、OCR
│   ├── download/            # video_download_manager（共享下载 + 引用计数）
│   ├── export/              # Excel 导出、综合推理、多模式导出
│   ├── knowledge/           # RAGService、KnowledgeRetriever、DocumentParser
│   ├── av_sync/             # 音画同步分析（audio_extractor、qwen_asr、sync_analyzer）
│   ├── auth/                # QR 码登录服务（Playwright）
│   ├── keyword/             # keyword_expander
│   └── cleanup_service.py   # 清理服务
├── storage/chromadb/        # ChromaDB 向量数据库文件
├── task/                    # 任务生命周期管理（TaskManager、11 状态状态机）
└── utils/                   # async_utils、number_utils
```

### `backend/` — 新后端（RedMuse）

采用**分层架构 / Clean Architecture**：

```
backend/
├── app/
│   ├── main.py                 # FastAPI 应用工厂
│   ├── api/router.py           # 路由聚合
│   ├── api/routes/             # 各模块路由（auth, tasks, history, knowledge, settings, health）
│   ├── core/                   # 配置、响应封装、安全工具、Feature Flags
│   ├── domain/                 # 领域层：状态机、实体、值对象、CanvasSchema、事件
│   │   ├── canvas/schema.py    # CanvasSchema、CanvasModule、CanvasDimension
│   │   ├── task_status.py      # TaskStatus 7 状态机
│   │   ├── module_status.py    # ModuleStatus 6 状态机
│   │   ├── task_context.py     # TaskContext SSOT + Writer + Store
│   │   ├── viral_note.py       # ViralNote 22 字段 dataclass
│   │   ├── viral_model.py      # ViralModelMatrix、ElementCode
│   │   ├── events.py           # TaskEvent、TaskEventType、TaskEventFactory
│   │   ├── error_codes.py      # 5 类错误码枚举 + HTTP 映射
│   │   └── module_graph.py     # 模块依赖图（级联 STALE）
│   ├── application/            # 应用层
│   │   ├── agents/             # 6 阶段 Agent（BaseAgent + 8 个子类）
│   │   │   ├── base.py
│   │   │   ├── input_parser_agent.py
│   │   │   ├── crawler_agent.py
│   │   │   ├── image_analysis_agent.py
│   │   │   ├── video_analysis_agent.py
│   │   │   ├── viral_model_agent.py
│   │   │   ├── insight_agent.py
│   │   │   ├── rag_agent.py
│   │   │   ├── canvas_render_agent.py
│   │   │   ├── _json_parsing.py
│   │   │   └── prompts/        # Markdown prompt 模板
│   │   ├── orchestrator.py     # AgentOrchestrator
│   │   ├── task_service.py     # Task 用例服务
│   │   ├── auth/task_access.py # 任务归属校验
│   │   ├── idempotency.py      # 幂等键逻辑
│   │   ├── optimistic_locking.py # If-Match 乐观锁
│   │   └── orchestration/      # 可插拔执行引擎
│   │       ├── engine.py       # OrchestrationEngine ABC
│   │       ├── simple_engine.py # 线性异步编排器
│   │       ├── langgraph_engine.py # LangGraph 状态图引擎
│   │       ├── factory.py      # 引擎选择工厂
│   │       └── checkpoint_store.py # Checkpoint 持久化
│   ├── infrastructure/         # 基础设施层
│   │   ├── repository/task_repository.py   # JSON 文件仓库 + RLock
│   │   ├── event_bus/          # Pub/sub + SSE replay
│   │   ├── cache/              # KeywordCache、RedisClient
│   │   ├── execution/coordinator.py # ExecutionCoordinator（并发 + 取消）
│   │   ├── queue/              # ARQ 队列（settings、client、runner、tasks）
│   │   ├── storage/db_engine.py        # SQLAlchemy 2.0 async 引擎
│   │   └── storage/notes_vector_store.py # pgvector/ChromaDB 笔记向量
│   ├── llm/                    # LLM 网关
│   │   ├── model_gateway.py    # 统一 chat/embed/audit 入口
│   │   ├── provider_registry.py # 提供商配置注册表
│   │   ├── agent_model_policy.py # Agent → ModelProfile 路由
│   │   └── model_profiles.py   # 模型画像注册表
│   ├── services/               # 领域服务
│   │   ├── auth_orchestrator.py # QR 登录 + XHS 身份信息提取
│   │   ├── canvas_export/      # 7 页 Excel 导出（openpyxl）
│   │   ├── cookie_health_service.py
│   │   ├── focus_keywords_store.py
│   │   ├── identity_store.py
│   │   ├── knowledge_registry.py
│   │   ├── note_seo_extract.py
│   │   ├── system_settings_store.py
│   │   └── viral_taxonomy_loader.py
│   └── schemas/common.py       # Pydantic Schema
├── tests/                      # pytest 测试（46+ 文件）
│   ├── conftest.py             # 强隔离 fixture
│   ├── pytest.ini              # asyncio_mode = auto
│   └── eval/                   # 基线评估
└── run.py                      # 自定义开发服务器（Windows 兼容）
```

### `frontend/` — 新前端（Next.js 16）

```
frontend/
├── src/app/
│   ├── (app)/                  # 路由组（共享布局）
│   │   ├── workspace/page.tsx  # 主工作区（任务创建、SSE、Canvas）
│   │   ├── history/page.tsx    # 任务历史
│   │   ├── knowledge/page.tsx  # 知识库（RAG 文档上传 / 管理 / 分块检索）
│   │   ├── settings/page.tsx   # 系统设置
│   │   └── layout.tsx          # AppLayout（AuthGate + WorkspaceProvider + Sidebar）
│   ├── login/page.tsx          # 账号密码登录页（XHS 数据源授权见设置页）
│   ├── layout.tsx              # RootLayout（SessionProvider + Geist 字体）
│   ├── page.tsx                # 根重定向（/workspace 或 /login）
│   └── globals.css             # Tailwind 入口 + 设计 Token
├── src/components/
│   ├── layout/                 # AppSidebar、PageHeader
│   └── workspace/              # ChatPanel、CanvasView、ModuleCard
├── src/lib/
│   ├── api-client.ts           # HTTP 客户端（fetch 封装）
│   ├── contracts.ts            # TypeScript 类型定义
│   ├── session-context.tsx     # 认证上下文
│   ├── workspace-context.tsx   # 工作区上下文
│   ├── auth-storage.ts         # localStorage 封装
│   └── sse/                    # 自定义 SSE 客户端 + React Hook
├── next.config.ts              # Turbopack 启用
└── postcss.config.mjs          # Tailwind v4 PostCSS 插件
```

### `web/` — 遗留前端
- `templates/index.html` — 旧版 Bootstrap + Material Design 3 单页应用（~7600 行）
- `static/css/` — 自定义 CSS

### `prototypes/` — UI 原型
- `01-login.html` ~ `05-settings.html` — 纯 HTML/CSS 原型，已翻译为 Next.js 实现

---

## 构建与运行命令

### 环境准备

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Node.js 依赖（用于 JS 加密算法 + 前端）
npm install
cd frontend && npm install

# 安装 Playwright Chromium（扫码登录需要）
playwright install chromium

# OCR 依赖（可选，推荐 EasyOCR，与 ChromaDB 无冲突）
pip install easyocr

# RAG 文档知识库依赖（可选）
pip install chromadb pypdf python-docx markdown
```

**依赖冲突注意**：PaddleOCR 与 ChromaDB 存在 protobuf 版本冲突。如需同时使用 OCR 和 RAG，推荐使用 **EasyOCR + ChromaDB** 组合。

### 运行遗留系统

```bash
# 启动爆文分析 Web 界面（遗留系统，端口 8000）
python viral_app.py

# 数据采集（命令行方式）
python run_spider.py --mode search --query "关键词" --num 20 --save excel
python run_spider.py --mode interactive    # 交互式模式
python main.py                              # 直接运行核心爬虫
```

### 运行新后端（RedMuse）

```bash
# 开发模式（端口 8100，带自动重载）
python backend/run.py

# 或使用 uvicorn（Windows 下可能遇到 ProactorEventLoop 问题，推荐用 backend/run.py）
uvicorn backend.app.main:app --reload --port 8100
```

`backend/run.py` 针对 Windows 修复了 `ProactorEventLoop` 与 Playwright/Chromium 的兼容性问题。

### 运行前端

```bash
cd frontend

# 开发模式（端口 3000，Turbopack 已启用）
npm run dev

# 构建生产版本
npm run build

# 生产运行
npm run start
```

前端默认通过 `NEXT_PUBLIC_API_BASE_URL` 连接后端（默认 `http://localhost:8100/api/v1`）。

### Docker 部署

```bash
# 构建镜像
docker build -t spider-xhs .

# 运行完整 4α 基础设施（5 个服务）
docker compose up -d

# 仅运行 Web + 老流程
docker compose up spider-xhs nginx
```

`docker-compose.yml` 包含 5 个服务：`spider-xhs`（主应用）、`arq-worker`（后台任务）、`redis`（缓存/队列）、`postgres`（pgvector）、`nginx`（反向代理）。

---

## 核心架构要点

### 1. 请求签名生成
- 通过 `xhs_util.py` 生成 `xs`、`xsc`、`xt` 等签名参数
- 默认使用 `xhshow` 纯 Python 库（阶段 4.1 hotfix 3 引入）
- 如需回退老算法：`XHS_SIGN_BACKEND=execjs`
- 每个 API 请求都需要正确的签名才能获取数据

### 2. Cookie 管理
- Cookie 从 `.env` 读取，需要登录后的有效 Cookie（含 `a1` 等关键字段）
- 新后端 RedMuse 系统登录走**账号密码 + JWT Token**；XHS 数据源授权（**扫码** / SMS 自动登录 / 粘贴 Cookie）统一在「设置 → 数据源授权」面板内完成（Playwright 自动化）
- 多用户系统下 Cookie 按用户隔离存储

### 3. 数据采集流程（遗留系统）
`Data_Spider`（`main.py`）提供三种采集方式：
- 单个/批量笔记：`spider_note()` / `spider_some_note()`
- 用户全部笔记：`spider_user_all_note()`
- 搜索结果：`spider_some_search_note()`

### 4. Agent 编排流水线（新后端）
`AgentOrchestrator` 运行 **6 阶段异步流水线**：
```
Stage 1: InputParserAgent        → 解析原始输入为结构化规格
Stage 2: CrawlerAgent            → 四源数据采集（category_top, competitor, top_interaction, serp_top）
Stage 3: ImageAnalysisAgent ‖ VideoAnalysisAgent  → 并行 6 元素多模态标注
Stage 4: ViralModelAgent         → 混合聚类 → 爆文模型矩阵
Stage 5: InsightAgent ‖ RAGAgent → 并行洞察生成 + RAG 检索
Stage 6: CanvasRenderAgent       → 渲染最终 Canvas（9 个模块）
```

通信机制：TaskContext（数据分区）+ ModelGateway（LLM 调用）+ TaskEventBus（SSE 进度推送）

### 5. 任务状态机
新后端定义 7 状态任务生命周期：
```
pending → queued → running → paused → completed / failed / cancelled
```
以及 Canvas Module 生命周期：
```
PENDING → GENERATING → READY → STALE → DELETED
```

### 6. 数据持久化（当前阶段）
- **Tasks**: JSON 文件（`datas/tasks/*.json`）+ `RLock` 线程锁
- **Task Context**: 内存存储 + 版本控制（`datas/task_contexts/*.json`）
- **Canvas**: 内存 + 写回 Task Context
- **PostgreSQL + pgvector**: 仅用于笔记向量存储（`notes_vector_store.py`），业务表计划 4.6 迁移

---

## 代码风格与开发约定

### 代码风格
- **无强制 Lint/Format 工具**：项目中未配置 black、flake8、ruff、pre-commit 等
- 前端使用 `eslint-config-next`（`npm run lint`）
- 依靠严格的测试隔离和手工代码审查保证质量
- 使用 **loguru** 记录日志
- 中文注释和文档是项目的主要自然语言

### 目录与文件约定
- 新后端遵循 **Clean Architecture** 分层：`domain/` → `application/` → `infrastructure/`
- Agent 类统一继承 `BaseAgent`，位于 `backend/app/application/agents/`
- 所有路由模块位于 `backend/app/api/routes/`，通过 `api_router` 聚合
- 前端组件按页面分组：`components/workspace/`、`components/layout/`
- 类型定义集中在前端 `lib/contracts.ts`

### API 设计
- 新后端 API 前缀：`/api/v1`
- 标准响应格式：`{ ok: true, data: T } | { ok: false, error: { code, message, details } }`
- SSE 流：`/tasks/{id}/stream`，支持 `Last-Event-ID` 断线重连
- 幂等性：任务创建和模块再生需携带 `Idempotency-Key` 头部
- 乐观并发：模块变更使用 `If-Match` 头部

### 配置管理
- `.env` 文件管理所有环境变量（AI 模型、Cookie、视频分析、编排参数）
- `backend/app/core/config.py` — Pydantic `Settings` 基类（`app_name=RedMuse API`", `app_version="0.1.0-phase0"`, `api_prefix="/api/v1"`）
- `config.py`（根目录）— 遗留爬虫的纯 Python 配置

---

## 测试策略

### 测试框架：pytest
配置文件：`backend/tests/pytest.ini`
```ini
[pytest]
asyncio_mode = auto
asyncio_default_fixture_loop_scope = function
```

### 运行测试
```bash
# 运行全部后端测试
pytest backend/tests/ -q

# 运行特定测试
pytest backend/tests/test_canvas_render_new_modules.py -v
pytest backend/tests/eval -q

# 根目录手动集成测试（需要真实外部服务）
python tests/test_deepseek.py
python tests/test_ocr.py
python tests/test_viral_image.py
```

### 测试分类

**A. `backend/tests/` — 正式单元与集成测试（46+ 文件）**

| 类别 | 代表文件 | 覆盖内容 |
|------|---------|---------|
| 领域模型 | `test_viral_note_model.py`, `test_domain_state_machines.py` | 序列化、状态机、错误码 |
| Agent 逻辑 | `test_agents_real.py`, `test_image_agent_6elements.py` | Mock LLM Gateway、成功/重试/降级路径 |
| 编排 | `test_orchestration_engine.py`, `test_execution_coordinator.py` | LangGraph vs Simple 引擎、并行分支、取消传播 |
| 爬虫 | `test_crawler_four_sources.py`, `test_crawler_cache_integration.py` | 四源映射、维度去重 |
| Canvas/导出 | `test_canvas_render_new_modules.py`, `test_excel_exporter.py` | Module ID 契约、7 页 Excel 结构 |
| API 集成 | `test_focus_keywords_api.py`, `test_system_settings_api.py` | FastAPI TestClient、认证覆盖、CRUD |
| 基础设施 | `test_event_bus.py`, `test_idempotency_and_locking.py` | SSE Backlog 回放、乐观锁、幂等性 |
| RAG/向量 | `test_rag_service_dim_fingerprint.py`, `test_notes_vector_store_sql.py` | 向量维度处理 |

**B. `tests/` — 手动/集成验证脚本（12 文件）**
- 直接运行，使用 `loguru` 输出结果
- 常需要真实外部服务（DeepSeek API、XHS Cookie、OCR 引擎）

**C. `scripts/` — 开发与诊断脚本（35+ 文件）**
- `test_*.sh` / `test_*.py` — 各类专项测试
- `fix_*.sh` / `fix_*.py` — 修复脚本
- `check_cookie.py` — Cookie 有效性检查

### 测试隔离
`backend/tests/conftest.py` 提供强隔离：
- 所有有状态存储（SystemSettingsStore、FocusKeywordsStore、TaskRepository）被 monkeypatch 到临时路径
- FastAPI 依赖覆盖用于绕过认证
- 环境变量默认禁用 Redis / pgvector，防止测试超时：
```python
os.environ.setdefault("CRAWLER_CACHE_ENABLED", "false")
os.environ.setdefault("BACKLOG_BACKEND", "memory")
os.environ.setdefault("TASK_RUNNER", "inprocess")
```

**前端测试**：当前未配置任何测试框架（无 Jest、Vitest、Playwright、Cypress）。

---

## 安全注意事项

1. **Cookie 安全**
   - 必须使用登录后的有效 Cookie，会过期需定期更新
   - 从浏览器 F12 开发者工具的网络请求中获取
   - 多用户系统下 Cookie 按用户隔离存储

2. **认证与授权**
   - JWT Token，24 小时有效期
   - 角色区分：`admin` vs `user`
   - 任务归属校验（用户只能操作自己的任务）
   - 密码修改强制策略（首次登录必须改密码）

3. **API 安全**
   - 请求签名错误 → 检查 JS 文件是否最新、Cookie 是否有效；或切换 `XHS_SIGN_BACKEND`
   - `xsec_token` 会过期，需及时处理
   - 建议添加适当延时，避免请求过于频繁
   - 支持代理配置

4. **依赖安全**
   - `bcrypt==4.0.1` 被锁定（4.1+ 与 passlib 不兼容）
   - `.env` 文件含敏感信息，已加入 `.gitignore`

---

## 部署流程

### Docker（推荐）
- `Dockerfile` 基于 `python:3.10-slim`，安装 Node 20、ffmpeg、Playwright Chromium
- 使用阿里云镜像加速 Debian 和 PyPI
- 暴露端口 8000，默认运行 `python viral_app.py`

### 本地开发多服务启动
```bash
# 终端 1：Redis + Postgres（Docker）
docker compose up redis postgres

# 终端 2：新后端（端口 8100）
python backend/run.py

# 终端 3：前端（端口 3000）
cd frontend && npm run dev

# 终端 4（可选）：ARQ Worker
python -m backend.app.infrastructure.queue.runner
```

### Nginx 配置
- `nginx/nginx.conf` 提供 HTTP/HTTPS 模板，WebSocket 支持
- 上传限制 100MB
- 默认仅 HTTP，HTTPS 需取消注释并配置 SSL 证书

---

## 多关键词检索系统

为确保分析样本量充足（至少 50 条以上），系统支持多关键词检索模式：

- **最多 5 个关键词**：如「巧克力」+「北欧」+「Fazer」
- **来源追踪**：每篇笔记记录匹配的所有关键词
- **智能去重**：按 `note_id` 合并，避免重复采集
- **样本量校验**：低于最低要求时显示警告

### API 调用
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
系统计算每关键词目标数量（不超过总目标 1.5 倍分摊）
        ↓
依次按三维度（点赞/评论/收藏）采集每个关键词
        ↓
合并去重（记录每篇笔记的来源关键词）
        ↓
按互动分数排序 → 应用爆款比例筛选
        ↓
校验样本量 ≥ min_sample_count
```

---

## RAG 知识库系统

知识库已于 2026-05 由「双轨制」（JSON 领域 + RAG 文档）收敛为**纯 RAG 文档**单轨（B 方案）：
前端仅保留「RAG 文档」一个视图，后端仅暂开放 /knowledge/documents、/knowledge/documents/{id}/chunks 与 /knowledge/search 三组路由。
领域 CRUD（/knowledge/domains）、knowledge_registry 领域方法、UnifiedKnowledgeRetriever 领域检测分支与对话流的 domain_ids 链路已移除；
DB 表 `knowledge_domains` / `knowledge_documents.domains` / `knowledge_chunks.domains` 作为历史数据保留，待 Phase 5 一并清理。

```
┌────────────────────────────────────────────────────────┐
│                  RAG 文档知识库                          │
├────────────────────────────────────────────────────────┤
│ • 文档上传（Word / PDF / Markdown / TXT ≤ 10MB）            │
│ • DocumentParser 解析 + 分块（500 字/块，overlap=50）       │
│ • Embedding 自动向量化 → pgvector 入库                     │
│ • /knowledge/search 语义检索（仅 admin）                   │
│ • 在 knowledge_qa 对话分支中作为唯一检索源（无领域过滤）     │
└────────────────────────────────────────────────────────┘
```

### Embedding 配置（`.env`）
```bash
# OpenAI 兼容 Embedding API（推荐）
EMBEDDING_MODEL="text-embedding-3-small"

# 本地 Embedding（离线，需额外依赖）
# USE_LOCAL_EMBEDDING=true
# LOCAL_EMBEDDING_MODEL="paraphrase-multilingual-MiniLM-L12-v2"
```

### 文档处理流程
```
上传文档
  ↓
解析内容（PDF/Word/MD/TXT）
  ↓
文本分块（500 字/块，overlap=50）
  ↓
向量化（Embedding API）
  ↓
写入 pgvector（knowledge_chunks 表）
  ↓
/knowledge/search 供 admin 测试检索
```

---

## 视频分析系统

### 系统架构
视频分析采用**统一下载 + 并行分析**架构：

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

**降级原因**：通义千问等 AI API 有 **20MB 请求体限制**，视频 base64 编码后膨胀约 **1.33 倍**，因此原始视频需限制在 **15MB 以内**。

### 视频源模式配置（`.env`）
```bash
VIDEO_SOURCE_MODE="url"      # url: URL 直传给AI（默认）
VIDEO_SOURCE_MODE="proxy"    # proxy: 本地下载后转 base64（推荐，解决防盗链）

VIDEO_ANALYSIS_MODE="full"
VIDEO_MODEL_NAME="qwen3-vl-plus"
VIDEO_MAX_SIZE_MB=50
VIDEO_DOWNLOAD_TIMEOUT=60
VIDEO_MAX_CONCURRENT=2

ENABLE_AV_SYNC=false
CLEANUP_TEMP_FILES=true
FRAME_INTERVAL=5.0
MAX_FRAMES_PER_VIDEO=20
```

---

## AI 分析配置（`.env`）

系统支持多种国内大模型 API，显著降低成本：

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

# 多模态模型（图文/视频分析）
MULTIMODAL_MODEL_NAME="qwen3-vl-plus"
```

---

## 调试技巧

1. **日志查看**
   - 使用 `loguru` 记录详细日志
   - 关注请求成功率和错误信息

2. **单步调试**
   - 先测试单个笔记采集功能
   - 验证 Cookie 和签名是否正确生成

3. **常见问题**
   - "签名错误"：检查 JS 文件是否最新，Cookie 是否有效；或切换 `XHS_SIGN_BACKEND`
   - "笔记不存在"：`xsec_token` 可能已过期
   - "请求失败"：检查网络连接和代理设置
   - 视频仍被重复下载：检查 `note_id` 是否在调用链中正确透传，确认 `VIDEO_SOURCE_MODE=proxy`

4. **Windows 开发注意**
   - 使用 `backend/run.py` 而非直接 `uvicorn`，避免 `ProactorEventLoop` 问题
   - PowerShell 执行脚本可能需要设置执行策略
