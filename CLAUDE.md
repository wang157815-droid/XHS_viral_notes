# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **权威参考**：`AGENTS.md` 是本项目最完整、最新的工程指南（结构、技术栈、测试、部署、各子系统细节）。本文件是精简的快速上手版；遇到与本文件冲突或本文件未覆盖的内容，以 `AGENTS.md` 为准。中文是本项目的主要文档与注释语言。

## 项目概述

**Spider_XHS / RedMuse** 是小红书数据采集、爆文分析与内容创作平台。代码库目前包含**三个并行系统**，理解它们的边界是高效工作的前提：

| 系统 | 入口 | 端口 | 状态 | 说明 |
|------|------|------|------|------|
| **Legacy 后端** | `viral_app.py` | 8000 | 维护中 | 单体 FastAPI + Jinja2 模板（`web/templates/index.html`）。爆文/视频分析核心逻辑在 `viral_agent/`。 |
| **新后端 RedMuse** | `backend/run.py` | 8100 | 主力开发 | 模块化 FastAPI，Clean Architecture 分层，多 Agent 编排、Canvas 渲染、任务状态机。 |
| **前端** | `frontend/` | 3000 | 主力开发 | Next.js 16 + React 19 + Tailwind v4，App Router。 |

**新功能默认放在 `backend/` + `frontend/`**；`viral_app.py` / `viral_agent/` 多为已有功能维护。两个后端共享根目录的采集与签名底座（`apis/`、`xhs_utils/`、`static/`）。

## 常用命令

```bash
# —— 环境准备 ——
pip install -r requirements.txt          # Python 依赖
npm install                              # 根目录：JS 签名算法依赖（crypto-js, jsdom）
cd frontend && npm install               # 前端依赖
playwright install chromium              # 扫码登录所需

# —— 运行三个系统 ——
python viral_app.py                      # Legacy 后端 (8000)
python backend/run.py                    # 新后端 (8100)，--reload 开启文件热重启
cd frontend && npm run dev               # 前端 (3000, Turbopack)

# —— Legacy 采集 CLI ——
python run_spider.py --mode search --query "关键词" --num 20 --save excel
python run_spider.py --mode interactive
python main.py                           # 直接运行核心爬虫 Data_Spider

# —— 测试 ——
pytest backend/tests/ -q                 # 新后端正式测试（46+ 文件，asyncio_mode=auto）
pytest backend/tests/test_canvas_render_new_modules.py -v   # 单个测试文件
pytest backend/tests/eval -q             # 基线评估
python tests/test_deepseek.py            # 根目录手动集成脚本（需真实外部服务/Cookie/API）

# —— 前端 ——
cd frontend && npm run build             # 生产构建
cd frontend && npm run lint              # eslint（前端唯一的 lint）

# —— DB 迁移（新后端，Alembic）——
alembic upgrade head
alembic revision --autogenerate -m "描述"

# —— Docker ——
docker compose up -d                     # 完整 5 服务（spider-xhs/arq-worker/redis/postgres/nginx）
```

> **Windows 注意**：始终用 `python backend/run.py` 启动新后端，**不要**直接 `uvicorn ... --reload` —— `run.py` 修复了 `ProactorEventLoop` 与 Playwright/Chromium 子进程的兼容问题。

> **无强制 Lint/Format**：Python 侧未配置 black/ruff/flake8/pre-commit；质量靠测试隔离 + 人工审查。前端用 `eslint-config-next`。

## 架构要点（需跨文件阅读才能理解的部分）

### 1. 请求签名（所有采集的命脉）
- 每个小红书 API 请求都需要正确的 `xs`、`xsc`、`xt` 签名，否则拿不到数据。
- 入口 `xhs_utils/xhs_util.py:generate_request_params`，**默认走 `xhshow_adapter.py` 纯 Python 后端**。
- 回退旧 JS 算法：环境变量 `XHS_SIGN_BACKEND=execjs`（执行 `static/xhs_xs_xsc_56.js`，2024 旧版已过时）。
- 「签名错误」排查顺序：Cookie 是否有效 → 切换 `XHS_SIGN_BACKEND` → 检查 JS 是否最新。

### 2. Cookie / 身份
- Cookie 从 `.env` 读取，需登录后的有效值（含 `a1` 等字段），会过期。
- 新后端登录走**账号密码 + JWT**（24h）；XHS 数据源授权（扫码 / SMS / 粘贴 Cookie）在前端「设置 → 数据源授权」完成（Playwright 自动化），Cookie 按用户隔离。
- `xsec_token` 内嵌在笔记 URL 中且会过期 —— 「笔记不存在」通常是它失效。

### 3. 新后端分层（Clean Architecture）
依赖方向严格单向：`domain/` → `application/` → `infrastructure/`。
- `backend/app/domain/` —— 纯领域：`task_status.py`(7 状态机)、`module_status.py`、`task_context.py`(SSOT)、`viral_note.py`、`canvas/schema.py`、`module_graph.py`(级联 STALE)。
- `backend/app/application/agents/` —— 6 阶段 Agent，统一继承 `base.py:BaseAgent`。
- `backend/app/application/orchestration/` —— 可插拔执行引擎：`simple_engine.py`（线性）vs `langgraph_engine.py`（状态图），由 `factory.py` 选择。
- `backend/app/infrastructure/` —— Task JSON 仓库、event_bus（SSE replay）、cache、queue(ARQ)、storage(SQLAlchemy async + pgvector)。
- `backend/app/llm/` —— LLM 网关：`model_gateway.py` 统一 chat/embed/audit；`agent_model_policy.py` 把 Agent 路由到 ModelProfile。

### 4. Agent 编排流水线（新后端核心数据流）
`AgentOrchestrator` 跑 6 阶段异步流水线：
```
1 InputParser → 2 Crawler（四源：category_top/competitor/top_interaction/serp_top）
→ 3 ImageAnalysis ‖ VideoAnalysis（并行，6 元素多模态标注）
→ 4 ViralModel（聚类 → 爆文模型矩阵）
→ 5 Insight ‖ RAG（并行）→ 6 CanvasRender（9 模块）
```
贯穿机制：`TaskContext`（数据分区 SSOT）+ `ModelGateway`（LLM）+ `TaskEventBus`（SSE 进度推送）。

### 5. API / SSE 约定（新后端）
- 前缀 `/api/v1`；响应统一 `{ ok: true, data } | { ok: false, error: { code, message, details } }`。
- SSE：`/tasks/{id}/stream`，支持 `Last-Event-ID` 断线重连。
- 幂等：任务创建 / 模块再生需 `Idempotency-Key` 头；模块变更用 `If-Match` 乐观锁。

### 6. 数据持久化现状（迁移中）
- Tasks → JSON 文件（`datas/tasks/*.json`）+ `RLock`；TaskContext → 内存 + 版本化 JSON。
- **PostgreSQL + pgvector 当前仅存笔记向量**（`notes_vector_store.py`）；业务表计划 4.6 迁移到 DB，Alembic 已就位。

### 7. 视频分析（统一下载 + 并行分析）
- `viral_agent/services/video/VideoEnhancedAnalyzer` 顶层 `acquire` 视频一次，并行跑封面/标题/时间轴/音画同步，结束统一 `release`。
- `download/video_download_manager.py` 负责去重 + 引用计数 + 原子写入；**缓存共享依赖 `note_id` 在整条调用链透传**——视频被重复下载多半是 `note_id` 没传下去。
- 智能降级：>15MB 自动切 URL 模式（通义千问等有 20MB 请求体限制，base64 膨胀 ~1.33×）。需 `VIDEO_SOURCE_MODE=proxy` 才会本地下载。

### 8. RAG 知识库（已收敛为纯文档单轨）
- 2026-05 起由「JSON 领域 + RAG 文档」双轨**收敛为纯 RAG 文档**：仅 `/knowledge/documents`、`/knowledge/documents/{id}/chunks`、`/knowledge/search` 三组路由有效。
- 领域 CRUD（`/knowledge/domains`）与 `UnifiedKnowledgeRetriever` 领域分支已移除；DB 中 `knowledge_domains` 等表作历史数据保留，待 Phase 5 清理。**勿再为「领域」分支写新代码。**

## 配置

- `.env` 管理全部环境变量（AI 模型、Cookie、视频、编排，200+ 项），见 `.env.example`。
- `config.py`（根目录）—— 仅 Legacy 爬虫的纯 Python 配置（`SPIDER_MODE`、`SEARCH_CONFIG`、`SAVE_CHOICE`）。
- `backend/app/core/config.py` —— 新后端 Pydantic `Settings`。
- AI 模型走 OpenAI 兼容接口，支持 DeepSeek / 智谱 GLM / 通义千问；关键变量：`OPENAI_API_BASE`、`OPENAI_API_KEY`、`MODEL_NAME`、`MULTIMODAL_MODEL_NAME`、`EMBEDDING_MODEL`。

## 测试隔离（重要约定）

`backend/tests/conftest.py` 提供强隔离：所有有状态存储被 monkeypatch 到临时路径，FastAPI 认证依赖被覆盖，并默认禁用外部服务防止超时：
```python
os.environ.setdefault("CRAWLER_CACHE_ENABLED", "false")
os.environ.setdefault("BACKLOG_BACKEND", "memory")
os.environ.setdefault("TASK_RUNNER", "inprocess")
```
新增测试请沿用该 fixture 模式，勿在单测里依赖真实 Redis/pgvector/外部 API。前端暂无测试框架。

## 已知坑

- `bcrypt==4.0.1` 被锁定（4.1+ 与 passlib 不兼容）。
- PaddleOCR 与 ChromaDB 有 protobuf 冲突；需同时用 OCR + RAG 时选 **EasyOCR + ChromaDB**。
- 音画同步分析依赖系统 `ffmpeg`（`ffmpeg -version` 确认）。
