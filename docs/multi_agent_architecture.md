# 多 Agent 架构总览（阶段 4.4 需求升级版）

> 本文档基于实际代码（`backend/` 目录）整理,**每个断言旁注了可以自行核对的源文件**。
> 阶段标记：4.0 编排骨架 / 4.1 真实采集落地 / 4.α 缓存-队列-调度基础设施 / 4.2 视频 / 4.3 画布反馈。
> 下一阶段 4.4 = 对话中枢、意图路由、上下文记忆与用户侧 RAG 问答；4.6 = Postgres 切迁。

## 目录

1. [整体分层与数据流](#1-整体分层与数据流)
2. [Conversation OS 与三条运行路径](#2-conversation-os-与三条运行路径)
3. [多 Agent 的职责与 I/O 契约](#3-多-agent-的职责与-io-契约)
4. [DAG 拓扑与并行关系](#4-dag-拓扑与并行关系)
5. [TaskContext 数据分区与版本机制](#5-taskcontext-数据分区与版本机制)
6. [两种编排引擎：SimpleEngine vs LangGraphEngine](#6-两种编排引擎simpleengine-vs-langgraphengine)
7. [ARQ 队列 + cron 定时任务](#7-arq-队列--cron-定时任务)
8. [CrawlerAgent 的三层缓存（L1/L2/L3）](#8-crawleragent-的三层缓存l1l2l3)
9. [SSE 事件流契约](#9-sse-事件流契约)
10. [完整一次任务的运行时序](#10-完整一次任务的运行时序)
11. [关键入口文件速查表](#11-关键入口文件速查表)

---

## 1. 整体分层与数据流

```
┌──────────────────────────────────────────────────────────────────┐
│ 前端 Next.js (workspace/canvas/history/settings/knowledge)         │
└──────────────────────────────────────────────────────────────────┘
        ↓ HTTP POST /api/v1/conversations/{id}/messages
┌──────────────────────────────────────────────────────────────────┐
│ Conversation API (规划: backend/app/api/routes/conversations.py)   │
│   - 会话/消息持久化 / 上下文摘要 / 当前 active_task_id              │
│   - IntentRouter: general_qa / knowledge_qa / xhs_analysis / ...  │
└──────────────────────────────────────────────────────────────────┘
        ↓ 普通问答/RAG问答                         ↓ 爆文任务交接
┌──────────────────────────┐              ┌─────────────────────────┐
│ ModelGateway + RAGService│              │ FastAPI Tasks API        │
│ - general_qa             │              │ backend/app/api/routes/  │
│ - knowledge_qa + citation│              │ tasks.py                 │
└──────────────────────────┘              └─────────────────────────┘
                                            ↓ HTTP POST /api/v1/tasks
                                            ↓ SSE /api/v1/tasks/{id}/stream
┌──────────────────────────────────────────────────────────────────┐
│ FastAPI 任务路由层 (backend/app/api/routes/tasks.py)                │
│   - 幂等校验 / Cookie 健康校验 / 权限门禁                          │
│   - 调 OrchestrationEngine.start(task_id)                         │
│   - /stream 持续消费 task_event_bus.subscribe(task_id)            │
└──────────────────────────────────────────────────────────────────┘
        ↓                                  ↑ 事件推送 (TaskEvent)
┌──────────────────────────────────────────────────────────────────┐
│ 编排层 (backend/app/application/orchestration)                    │
│   - OrchestrationEngine 抽象                                      │
│   - SimpleEngine  (进程内 或 ARQ)  ← 默认                         │
│   - LangGraphEngine (状态图,feature flag 切换)                   │
└──────────────────────────────────────────────────────────────────┘
        ↓ 按 DAG 调度 Agent
┌──────────────────────────────────────────────────────────────────┐
│ Agent 层 (backend/app/application/agents)                         │
│   8 个 Agent 只能通过 TaskContext 读写 / ModelGateway 调模型       │
│   禁止:节点间互调 / 直接访问 DB / 直接 import openai                │
└──────────────────────────────────────────────────────────────────┘
        ↓ 读写                       ↓ chat/vision/embed
┌───────────────────────┐      ┌──────────────────────────┐
│ TaskContext           │      │ ModelGateway             │
│ (domain/task_context) │      │ (llm/model_gateway)      │
│ 9 个 partition 分区    │      │ 路由 agent_id → provider │
└───────────────────────┘      └──────────────────────────┘
        ↓
┌──────────────────────────────────────────────────────────────────┐
│ 基础设施 (backend/app/infrastructure)                              │
│  cache:   Redis (L1 热缓存 / 冷启动 warmup)                        │
│  storage: pgvector (L2 笔记向量 + 历史过滤)                         │
│  queue:   ARQ 队列 + 4 个 cron 任务                                │
│  event_bus: 内存广播 + 可选 RedisStream backlog                    │
│  execution: ExecutionCoordinator (并发/限流/取消)                  │
└──────────────────────────────────────────────────────────────────┘
```

**核心约束**（见 `application/agents/base.py:13`）：

> Agent 内部只能做四件事：
> 1. 从 TaskContext 读数据
> 2. 通过 ModelGateway 调模型
> 3. 写回自己的 TaskContext 分区
> 4. 通过 EventBus 发 progress/log 事件
>
> **禁止**：节点间互调、直接 openai、直接访问 DB。

这条"门禁规则"是多 Agent 稳定性的基石——它让每个 Agent 都能被单独 mock 测试,也让"换 LangGraph / 换 ARQ"只需改编排层,不用动任何 Agent 代码。

---

## 2. Conversation OS 与三条运行路径

4.4 需求升级后，工作区左侧不再把所有用户输入直接当作分析任务。新增的 `ConversationController` / `IntentRouter` 位于 `/tasks` 和多 Agent 编排之前，负责把同一条用户消息路由到三类路径：

| 路径 | 意图 | 后端动作 | 前端表现 |
| --- | --- | --- | --- |
| 普通问答 | `general_qa` | 读取会话摘要 + 最近消息，直接调用 `ModelGateway.chat` 生成 assistant 消息 | 左侧追加普通回答，不创建任务，不展开 Canvas |
| 知识库问答 | `knowledge_qa` | 通过用户侧 Knowledge QA 服务调用 `RAGService`/ChromaDB 检索，生成带 `KnowledgeCitation[]` 的回答 | 左侧展示答案和来源引用，不强制生成 Canvas |
| 爆文模型任务 | `xhs_analysis` | 调用现有 `TaskService.create_task` + `OrchestrationEngine.start(task_id)`，并把 `task_id` 写入 `Conversation.active_task_id` | 左侧展示任务启动/进度消息，右侧 Canvas 继续通过任务 SSE 更新 |

后续 `refine_canvas` 与 `export` 也归属 Conversation OS：它们不启动完整分析链，而是消费当前 `active_task_id`、Canvas 模块摘要和 4.3 的 `paragraph_id`/`module_regeneration` 能力。

### 2.1 Conversation 记忆边界

- 短期上下文：最近 N 轮消息直接进入普通 QA / 意图路由 prompt。
- 长期摘要：每轮写入 `Conversation.summary`，只保留业务目标、品类/品牌、知识库领域、已绑定任务、用户偏好和未完成指令。
- 任务绑定：一次小红书爆文模型任务完成后，`active_task_id` 继续留在会话里，供追加指令、导出和解释 Canvas 使用。
- 存储迁移：首版可按当前项目节奏用 JSON Store；4.6 业务表迁移时并入 PostgreSQL，避免 Web/Worker 多进程写入不一致。

### 2.2 RAG 的两种角色

- 任务内 `RAGAgent`：仍是多 Agent 流程中的业务硬约束提供者，输出 `rag_output.business_rules` 给后续模型使用，不直接面向用户展示。
- 对话内 Knowledge QA：是面向用户的问题回答能力，需要返回可读答案和引用来源，不能直接复用管理员调试接口 `/knowledge/search` 作为产品 API。

---

## 3. 多 Agent 的职责与 I/O 契约

> 注：下表保留任务内 Agent 的职责视角。4.3pre 后画布模块已从旧策略模块切换到“爆文模型矩阵 + 洞察 + 样本”结构；`RAGAgent` 不再直接产出画布模块，而是保留 `rag_output` 供任务内模型约束和 4.4 对话中枢复用。

| Agent | agent_id | 读分区 | 写分区 | provides (module_id) | depends_on (module_id) |
| --- | --- | --- | --- | --- | --- |
| InputParserAgent | `InputParserAgent` | `input_spec.raw_input` `input_spec.keywords` | `input_spec.parsed` | — (内部解析) | — |
| CrawlerAgent | `CrawlerAgent` | `input_spec.parsed` `input_spec.advanced_config` | `crawler_output` | `mod-crawler-sample` | — |
| ImageAnalysisAgent | `ImageAnalysisAgent` | `crawler_output.notes_image` | `multimodal_output.image` | `mod-image-analysis` | `mod-crawler-sample` |
| VideoAnalysisAgent | `VideoAnalysisAgent` | `crawler_output.notes_video` | `multimodal_output.video` | `mod-video-analysis` | `mod-crawler-sample` |
| InsightAgent | `InsightAgent` | `crawler_output.samples_by_dimension` | `semantic_output` | `mod-insight-industry` `mod-insight-competitor` `mod-insight-brand` | `mod-crawler-sample` |
| RAGAgent | `RAGAgent` | `input_spec.raw_input` `input_spec.parsed.keywords` | `rag_output` | — (业务约束/对话检索源) | — |
| StrategyAgent | `StrategyAgent` | `input_spec.parsed` `semantic_output` `rag_output` `multimodal_output.image` | `strategy_output` | `mod-title-strategy` `mod-product-strategy` `mod-cover-strategy` `mod-structure-strategy` | insight × 3 / knowledge / image / crawler |
| CanvasRenderAgent | `CanvasRenderAgent` | 全部 | `canvas_document` | — (聚合者) | 全部 module_id |

### 3.1 InputParserAgent — 自然语言入口

位置：`backend/app/application/agents/input_parser_agent.py`

- 输入：用户原话（raw_input）+ 可选关键词提示
- 动作：调 LLM 解析出 `{keywords, dimensions:{brand/competitor/industry}, adjustments, confidence}`
- 容错：LLM 返回非 JSON 时最多重试 1 次,再失败就用本地兜底（把 raw_input 直接当单个关键词）
- 产物：只写 `input_spec.parsed`,不产生 canvas module

### 3.2 CrawlerAgent — 全文只有它动 XHS 真实 API

位置：`backend/app/application/agents/crawler_agent.py`（全项目最重的 Agent,约 860 行）

**核心逻辑分三段**：

1. **三层缓存查询**（阶段 4.α 新增）
   - L1 Redis 热缓存（严格同词命中,TTL 默认 2h）→ 命中则立刻返回
   - L2 pgvector 历史笔记（≥10 条同词命中才算命中）→ 命中回写 L1 后返回
   - L3 实时采集（走小红书 API）→ 完成后回写 L1 + L2
2. **实时采集策略**（阶段 4.1 反 XHS 反爬）
   - 相同关键词的多个维度 → **只真实采集一次**,结果复制到同组维度
   - 不同关键词组 → **串行** 采集（同 IP 并发易触发软封）
   - 组间 sleep（`CRAWLER_INTER_GROUP_SLEEP` 默认 5s）
   - 按 `note_id` 去重,同一笔记记录 `dimensions_hit`
3. **精确数据富化**
   - 搜索列表 API 的 `liked_count` 是模糊下界（`"100+"`）
   - top-N 笔记调详情接口拿精确值,覆盖 samples_by_dim 和 all_notes
   - `metrics_precise` 字段标记"此条是精确数据"

**前端高级配置直读**（阶段 4.1）：
`input_spec.advanced_config` 里的 `target_count` / `viral_ratio` / `note_type` / `time_range` 覆盖 env 默认值。

### 3.3 ImageAnalysisAgent — 图文封面 Vision 分析

位置：`image_analysis_agent.py`

- 从 `crawler_output.notes_image` 取 top-N（默认 6）按互动分排序
- 并行调 `ModelGateway.chat(modality="multimodal")`,每条笔记一次
- 输出：每条笔记的"封面风格 / 关键文字 / 色彩基调"等结构化字段
- 写 `multimodal_output.image`（merge 模式,不覆盖 video 分区）

### 3.4 VideoAnalysisAgent — 异步支路(阶段 4.2 已激活)

位置：[`video_analysis_agent.py`](backend/app/application/agents/video_analysis_agent.py)

**两段式设计**(解决"视频耗时长 vs 主画布要快"的取舍):

```
┌─────────────────────────────────────────────┐
│  第 1 段(同步)  │  第 2 段(fire-and-forget) │
├─────────────────┼───────────────────────────┤
│ 从 crawler_output│ asyncio.create_task()     │
│ 取 top-30 视频   │ 并发调 ModelGateway       │
│                 │ (video_url + 5 并发)       │
│ 写 TaskContext: │                           │
│ multimodal      │ 每批 5 条,每完成一批:     │
│ _output.video = │  - AGENT_PROGRESS 事件    │
│ {               │                           │
│   async_pending │ 全部完成后:               │
│     :True,      │  - 聚合结果               │
│   queued:30,    │  - 更新 TaskContext       │
│   completed:0   │  - 更新持久化 canvas      │
│ }               │  - 发 CANVAS_MODULE_UPDATED│
│                 │  - 发 TASK_VIDEO_DONE     │
│ 立即 return     │                           │
│ (主编排继续)     │                           │
└─────────────────┴───────────────────────────┘
```

- **视频源模式**:URL 直传(不下载),通义千问 qwen3-vl 服务端自己拉 xhscdn
- **并发控制**:任务内 `asyncio.Semaphore(5)`,单视频超时 60s
- **失败降级**:单条失败不中断整批,只聚合成功条;全部失败 → module=FAILED
- **防 GC**:模块级 `_BACKGROUND_TASKS: set[asyncio.Task]` + `add_done_callback` 清理
- **取消联动**:`handle.is_cancelled()` 每批检查,感知主任务 cancel
- **env 开关**:`VIDEO_ANALYSIS_MAX_COUNT=30` / `VIDEO_ANALYSIS_CONCURRENCY=5` / `VIDEO_ANALYSIS_PER_TIMEOUT=60`

**前端配合**(阶段 4.2 前端侧改动):
- SSE 客户端:主任务 `done.payload.video_pending=true` 时**不关流**,等 `task_video_done` 才关
- Canvas adapter:`mod-video-analysis` 在 `async_pending=true` 时渲染 loading 骨架 + 进度文案
- event-reducer:识别 `task_video_done` 更新 `videoAsyncState`(completed/partial/failed)

### 3.5 InsightAgent — 行业/竞品/本品 三维并行洞察

位置：`insight_agent.py`

- 从 `crawler_output.samples_by_dimension` 按 `industry/competitor/brand` 切三个样本集
- `asyncio.gather` 并行调三个子 prompt（`insight_industry.md` / `insight_competitor.md` / `insight_brand.md`）
- 任一子任务失败 → 用本地 fallback 兜底（不影响其他两个）
- 写 `semantic_output = {industry, competitor, brand}`

### 3.6 RAGAgent — 业务硬约束检索

位置：`rag_agent.py`

**特别注意**：RAG 在这里**不是"补充参考信息"**,而是**硬约束**——比如"医疗类不能说疗效"、"护肤不能承诺祛痘"。

- Query 改写：`rag_query_rewrite.md` 把用户原话 + 关键词改写成 3-5 个 chromadb 检索 query
- ChromaDB 向量检索业务规则（`viral_agent/storage/chromadb/` 预置）
- 写 `rag_output = {query, rewritten_queries, business_rules, hits}`
- StrategyAgent 会把这些规则显式注入 prompt,让策略输出不越界

### 3.7 StrategyAgent — 4 子策略并行

位置：`strategy_agent.py`

- 依赖已经最多（insight × 3 + knowledge + image + crawler）
- 内部 `asyncio.gather` 并行调 4 个子 prompt：
  - `strategy_title.md` → 标题策略
  - `strategy_product.md` → 产品植入策略
  - `strategy_cover.md` → 封面策略
  - `strategy_structure.md` → 正文结构策略
- 写 `strategy_output = {title, product, cover, structure}`

### 3.8 CanvasRenderAgent — 聚合者

位置：`canvas_render_agent.py`

- 唯一 `provides = []` 的 Agent（不产 module,整合别人的 module）
- 按 `module_graph` 拼装出完整的 `CanvasSchema`
- 发 `CANVAS_SCHEMA_UPDATED` 事件,前端 Canvas 页面立刻刷新

---

## 4. DAG 拓扑与并行关系

### 4.1 SimpleEngine 流程（6 Stage,Video 作为附加 Stage）

```
Stage 1: InputParser    (串行)
Stage 2: Crawler        (串行)
Stage 3: ImageAnalysis  (串行,内部对 N 条图文并行)
Stage 4: Insight ‖ RAG  (并行,ExecutionCoordinator.run_branches)
Stage 5: Strategy       (串行,内部 4 子 prompt 并行)
Stage 6: CanvasRender   (串行)
————————————— 附加 —————————————
        VideoAnalysis   (串行,4.0 为占位;4.2 将改为 ARQ enqueue)
```

对应代码：`application/orchestrator.py:95-130`

### 4.2 LangGraphEngine 拓扑（7 节点状态图）

```
     START
       │
  input_parser
       │
    crawler
       │
     image            ← fan-out 起点
      / \
 insight  rag         ← 两条独立路径
      \ /
   strategy           ← fan-in 汇合(LangGraph 自动等两边)
       │
    canvas
       │
      END
```

代码：`application/orchestration/langgraph_engine.py:99-121`

**关键实现**：
- `completed_stages` 用 `Annotated[List[str], operator.add]` 让并行节点的 return 自动合并（LangGraph reducer）
- `MemorySaver` checkpointer 按 `thread_id=task_id` 存状态,4.6 接 Postgres 时替换即可
- 取消通过 `asyncio.create_task(_cancel_watcher())` 监听 handle.cancel_event,触发后 `invoke_task.cancel()`

### 4.3 两条引擎的切换

```python
# backend/app/application/orchestration/factory.py
ORCHESTRATION_ENGINE = os.getenv("ORCHESTRATION_ENGINE", "simple")
# simple (default) | langgraph
```

路由层只依赖抽象：

```python
# api/routes/tasks.py:109
await get_orchestration_engine().start(result.record.task_id)
```

**当前默认走 SimpleEngine**——LangGraph 为平行实现,用于 4.0 验证"抽象层设计正确性",4.6/4.8 会做 AB 对比后决定是否切换主路。

---

## 5. TaskContext 数据分区与版本机制

位置：`backend/app/domain/task_context.py`

### 5.1 9 个合法分区（`_VALID_PARTITIONS`）

| 分区 | 谁写 | 谁读 |
| --- | --- | --- |
| `input_spec` | 路由创建时写 raw_input/keywords/advanced_config;InputParser 写 parsed | 所有 Agent |
| `crawler_output` | CrawlerAgent | Image/Video/Insight/Strategy/Canvas |
| `semantic_output` | InsightAgent | Strategy/Canvas |
| `rag_output` | RAGAgent | Strategy/Canvas |
| `multimodal_output` | Image/Video (merge 模式,各写自己 key) | Strategy/Canvas |
| `strategy_output` | StrategyAgent | Canvas |
| `canvas_document` | CanvasRenderAgent | 路由 GET `/tasks/{id}/canvas` |
| `cookie_health` | cookie_health_service 定时写 | 设置页 + 任务创建前 |
| `metrics` | 未来 4.8 观测性 | 未来 4.8 |

### 5.2 写入协议

```python
from ...domain.task_context import TaskContextWriter

TaskContextWriter(context.task_context).write(
    "multimodal_output",        # 分区名（必须在白名单）
    {"image": summary},          # 数据
    agent_id=self.agent_id,      # 谁写的（写进审计日志）
    merge=True,                  # 是否和已有 dict 深合并
    note="image_analysis_real",  # 给审计日志的描述
)
```

**副作用**：
1. `context_version` 自增 1（乐观锁基础）
2. 追加一条 `ContextAuditEntry` 到 `audit_log`（谁、什么时候、写了哪个分区、note）
3. `task_repository.bump_context_version` 让 GET `/tasks/{id}` 能看到新版本

### 5.3 为什么要分区?

- **隔离 Agent 间写冲突**：每个 Agent 只写自己的分区,不会互相覆盖
- **便于 checkpoint / 断点续跑**：4.6 迁 Postgres 后,每个 partition 独立一行
- **便于 dry-run 和 mock**：测试时只注入 `crawler_output`,跳过前置 stage 也能测下游

---

## 6. 两种编排引擎：SimpleEngine vs LangGraphEngine

### 6.1 SimpleEngine（默认）

```python
# orchestration/simple_engine.py
class SimpleEngine:
    async def start(task_id):
        mode = env TASK_RUNNER  # inprocess | arq
        if mode == "arq":
            try: enqueue_job("run_analysis_task", task_id)
            except: fallback to inprocess
        await agent_orchestrator.start(task_id)
```

两种执行模式：

| 模式 | 行为 | 场景 |
| --- | --- | --- |
| `inprocess`（默认） | `asyncio.create_task` 在 FastAPI 进程内跑 | 本地开发 / 小流量 |
| `arq` | enqueue 到 ARQ 队列,worker 进程执行 | 生产环境（水平扩展） |

取消路径：
- `execution_coordinator.cancel` 通过 `handle.cancel_event` 协作式中断
- ARQ 模式额外调 `pool.abort_job(f"task:{task_id}")`

### 6.2 LangGraphEngine（平行实现）

```python
# orchestration/langgraph_engine.py
builder = StateGraph(_GraphState)
builder.add_edge("image", "insight")   # fan-out
builder.add_edge("image", "rag")
builder.add_edge("insight", "strategy")  # fan-in
builder.add_edge("rag", "strategy")
graph = builder.compile(checkpointer=MemorySaver())
```

优势：
- **声明式 DAG**：修改拓扑只改 `add_edge`,不用动调度逻辑
- **原生并发**：fan-out/fan-in 由 LangGraph runtime 处理
- **checkpointer 可插拔**：MemorySaver（当前） → PostgresSaver（4.6）

**何时会切主路？**
待 4.8 LLM Eval 做 AB 对比后决定。现在两套都能跑通 144 条单测。

---

## 7. ARQ 队列 + cron 定时任务

位置：`backend/app/infrastructure/queue/`

### 7.1 任务清单（`arq_settings.py`）

```python
functions = [
    # 用户/API 触发
    run_analysis_task,        # 完整 Agent 编排（TASK_RUNNER=arq 时）
    run_video_analysis,       # 4.2 视频异步（占位）
    warmup_keyword_on_demand, # 为指定关键词预热

    # cron 任务也可手动 enqueue
    scheduled_warmup,
    cookie_health_check,
    cleanup_tmp,
    rebuild_hot_queries,
]

cron_jobs = [
    # 每小时探针,函数内部按 enabled + interval_hours 决定是否真跑
    cron(scheduled_warmup, minute={0}),
    cron(cookie_health_check, hour={0,4,8,12,16,20}, minute=15),
    cron(cleanup_tmp, hour={3}, minute=30),
    cron(rebuild_hot_queries, minute={0, 30}),
]
```

### 7.2 `scheduled_warmup` 的双入口机制（阶段 4.α-patch2）

```python
async def scheduled_warmup(ctx, force: bool = False):
    # 门控 1: 开关 (总开关,force 也不能绕过)
    if not system_settings.crawler_schedule.enabled:
        return {"status": "skipped", "reason": "admin_disabled"}

    # 门控 2: interval (只在 force=False 时检查)
    if not force:
        if not _interval_reached(now, interval_hours):
            return {"status": "skipped", "reason": "interval_not_reached"}

    # 真正的预热逻辑
    ...
```

两种触发场景：

| 触发 | force | 行为 |
| --- | --- | --- |
| ARQ cron(每小时探针) | `False` | 受开关+interval 双门控（体现用户前端选的 6h/12h/24h） |
| `/settings/crawler/run-now`（用户按钮） | `True` | **穿透 interval** 立即跑（仅受总开关限制） |

用户改前端 `interval_hours = 12` → JSON 落盘（`datas/config/system_settings.json`） → 下次探针读到新值 → 立即生效（**无需重启 worker**）。

### 7.3 关键词来源（`_collect_warmup_keywords`）

预热用的关键词三路合并,大小写不敏感去重:

1. **主渠道** `FocusKeywordsStore` (`datas/config/focus_keywords.json`)
   - 前端"设置 → 焦点关键词"用户配置的
2. **兼容渠道** `viral_agent.config.knowledge_loader.load_focus_keywords()`（若存在）
3. **热词** Redis `hot:queries`（由 `rebuild_hot_queries` cron 填充,近期 Top 查询）

---

## 8. CrawlerAgent 的三层缓存（L1/L2/L3）

```
用户提交任务 / 定时预热命中
         │
         ▼
┌─────────────────┐ 命中 → 返回
│ L1 Redis 热缓存   │ (key = hash(sorted_keywords), TTL=2h)
└────────┬────────┘ 未命中
         ▼
┌─────────────────┐ 命中(≥10条同词) → 回写 L1 + 返回
│ L2 pgvector 历史 │ (WHERE source_keywords && keywords AND crawled_at > now-3d)
└────────┬────────┘ 未命中
         ▼
┌─────────────────┐
│ L3 XHS 实时采集  │ → 回写 L1 + L2（供下次命中)
└─────────────────┘
```

**严格同词命中判定**：`source_keywords && query_keywords`（PG 数组 overlap 运算符）
→ 避免"查防脱洗发水误命中防脱精华"这类跨品类污染

**pgvector SQL 的两个坑**（都已修）：
1. `:embedding::vector` 混用 SQLAlchemy 命名参数和 PG 类型转换 → 改用 `CAST(:embedding AS vector)`
2. `CASE WHEN :embedding IS NULL ELSE CAST(:embedding AS vector) END` 两分支参数类型约束不一致 → 改用 `CASE WHEN CAST(:embedding AS text) IS NULL ...`

单测 `test_notes_vector_store_sql.py` 做了编译期保险丝,禁止这两种歧义语法复发。

---

## 9. SSE 事件流契约

位置：`backend/app/domain/events.py` + `infrastructure/event_bus/`

### 9.1 事件类型

```python
class TaskEventType(str, Enum):
    PING = "ping"                          # SSE 心跳 (15s 一次)
    TASK_STATUS = "task_status"            # queued/running/completed/failed/cancelled
    AGENT_PROGRESS = "agent_progress"      # 每个 Agent 阶段开始/进度条
    LOG = "log"                            # info/warn/error 结构化日志
    CANVAS_MODULE_UPDATED = "canvas_module_updated"   # 单模块更新
    CANVAS_SCHEMA_UPDATED = "canvas_schema_updated"   # 整份画布更新
    ERROR = "error"                        # 致命错误 + ErrorCode
    DONE = "done"                          # 流结束标识
```

### 9.2 事件字段

```python
{
    "event_id": "uuid hex",      # 全局唯一,前端去重/Last-Event-ID 回放
    "sequence_id": 42,           # 任务内严格单调递增
    "type": "agent_progress",
    "task_id": "...",
    "timestamp": "ISO8601",
    "branch_id": null,           # 并行分支(insight/rag) 时才有
    "payload": {
        "agent_id": "CrawlerAgent",
        "message": "采集完成(L3) · 共 150 条 · 图文 120 / 视频 30",
        "progress": 30
    }
}
```

### 9.3 Backlog 补发机制

- SSE 断线重连时,前端带 `Last-Event-ID` header
- 服务端从 `TaskEventBacklogStore` 读出 > last_event_id 的事件一次性回放
- 两种后端：`InMemoryRingBuffer`（默认,进程内 500 条上限）/ `RedisStreamBacklog`（`BACKLOG_BACKEND=redis`,跨进程共享）

---

## 10. 完整一次任务的运行时序

```
用户在前端提交"巧克力 北欧 Fazer"
  │
  │ POST /api/v1/tasks  { raw_input, keywords, advanced_config }
  ▼
FastAPI tasks.py:create_task
  ├─ idempotency 校验
  ├─ cookie_health_service.check → 过期则 409 AUTH_COOKIE_EXPIRED
  ├─ task_service.create_task → TaskRecord + TaskContext 初始化
  ├─ 发 TASK_STATUS (queued)
  └─ get_orchestration_engine().start(task_id)      ← 异步,不阻塞响应

orchestration_engine.start(task_id)
  │ SimpleEngine (默认) / LangGraphEngine
  ▼
execution_coordinator.run_task(task_id, runner)
  │ 限流+注册 handle,允许取消
  ▼
AgentOrchestrator._run(task_id, handle)
  │
  ├─ TASK_STATUS (running, progress=3)
  │
  ├─ Stage 1: InputParserAgent.run(ctx)
  │   └─ emit_progress("输入解析", 5)
  │   └─ LLM(input_parser.md) → {keywords,dimensions,adjustments}
  │   └─ write input_spec.parsed
  │
  ├─ Stage 2: CrawlerAgent.run(ctx)
  │   └─ emit_progress("开始采集", 10)
  │   ├─ L1 Redis → 未命中
  │   ├─ L2 pgvector → 未命中
  │   └─ L3: 关键词分组 + 串行采集 + 详情富化
  │   └─ 回写 L1+L2
  │   └─ write crawler_output
  │   └─ emit_progress("采集完成(L3) · 150 条", 30)
  │
  ├─ Stage 3: ImageAnalysisAgent.run(ctx)
  │   └─ top-6 图文笔记并行 Vision
  │   └─ write multimodal_output.image (merge)
  │
  ├─ Stage 4: Insight ‖ RAG (execution_coordinator.run_branches)
  │   ├─ Branch A: InsightAgent
  │   │   └─ 三维并行 (insight_industry/competitor/brand)
  │   │   └─ write semantic_output
  │   └─ Branch B: RAGAgent
  │       └─ query 改写 → ChromaDB 检索业务规则
  │       └─ write rag_output
  │
  ├─ Stage 5: StrategyAgent.run(ctx)
  │   └─ 4 子 prompt 并行 (title/product/cover/structure)
  │   └─ write strategy_output
  │
  ├─ Stage 6a: VideoAnalysisAgent.run(ctx)   ← 阶段 4.2 位置上移
  │   └─ 写 multimodal_output.video = {async_pending:True, queued:30}
  │   └─ asyncio.create_task(_run_background)  ← fire-and-forget
  │   └─ 立即返回 (主编排继续)
  │
  ├─ Stage 6b: CanvasRenderAgent.run(ctx)
  │   └─ build_modules (video 模块 status=PENDING, 因 async_pending=True)
  │   └─ task_service.set_canvas
  │   └─ emit CANVAS_SCHEMA_UPDATED  ← 前端首屏展示骨架
  │
  ├─ TASK_STATUS (completed, progress=100)
  └─ DONE (payload.video_pending=True)  ← 前端 SSE 不关流

=== 后台协程(与主编排并行) ==========================
  VideoAsync._run_background(task_id, top-30 videos):
     └─ Semaphore(5) 并发,每条调 ModelGateway(video_url)
     └─ 每批完成发 AGENT_PROGRESS
     └─ 全部完成后:
        ├─ 聚合 segments_template / golden_quotes / hook_types...
        ├─ 写 multimodal_output.video (async_pending=False)
        ├─ task_service.update_module_content(mod-video-analysis, READY)
        ├─ emit CANVAS_MODULE_UPDATED  ← 前端单模块局部刷新
        └─ emit TASK_VIDEO_DONE  ← 前端 SSE 关流

全程前端 /stream 持续收事件,Canvas 页面按 sequence_id 顺序应用
```

**关键时间节点**（典型值）：
- 第 5% 进度：InputParser 完成（~2s）
- 第 10-30% 进度：Crawler 采集（L3 冷启动 30-120s；L1 命中 <1s；L2 命中 <3s）
- 第 35-45% 进度：ImageAnalysis（top-6 并行,~30s）
- 第 45-55% 进度：Insight ‖ RAG（并行,取较慢的一边,~30-60s）
- 第 75-90% 进度：Strategy（并行,~30s）
- 第 90% 进度：VideoAgent 启动后台任务 (< 1s)
- 第 100% 进度：Canvas 渲染 + DONE（<1s,视频模块以骨架展示）
- **主任务完成后 ~3 分钟**:视频后台任务完成,SSE 推 CANVAS_MODULE_UPDATED 局部刷新视频模块

---

## 11. 关键入口文件速查表

### 编排层

| 职责 | 文件 |
| --- | --- |
| 抽象基类 | `backend/app/application/orchestration/engine.py` |
| Simple 引擎（默认） | `backend/app/application/orchestration/simple_engine.py` |
| LangGraph 引擎 | `backend/app/application/orchestration/langgraph_engine.py` |
| 工厂 + feature flag | `backend/app/application/orchestration/factory.py` |
| 自研 8-Agent 编排实现 | `backend/app/application/orchestrator.py` |

### Agent 层

| Agent | 文件 |
| --- | --- |
| BaseAgent | `backend/app/application/agents/base.py` |
| InputParserAgent | `backend/app/application/agents/input_parser_agent.py` |
| CrawlerAgent | `backend/app/application/agents/crawler_agent.py`（最大,860 行） |
| ImageAnalysisAgent | `backend/app/application/agents/image_analysis_agent.py` |
| VideoAnalysisAgent | `backend/app/application/agents/video_analysis_agent.py`（4.2 已激活异步支路） |
| InsightAgent | `backend/app/application/agents/insight_agent.py` |
| RAGAgent | `backend/app/application/agents/rag_agent.py` |
| StrategyAgent | `backend/app/application/agents/strategy_agent.py` |
| CanvasRenderAgent | `backend/app/application/agents/canvas_render_agent.py` |

### 领域层（Agent 协议）

| 对象 | 文件 |
| --- | --- |
| TaskContext / Writer / 分区白名单 | `backend/app/domain/task_context.py` |
| SSE 事件契约 | `backend/app/domain/events.py` |
| TaskRecord / TaskStatus | `backend/app/domain/task_status.py` |
| Canvas schema + module_graph | `backend/app/domain/canvas.py` + `module_graph.py` |
| ErrorCode 5 大类 | `backend/app/domain/error_codes.py` |

### 基础设施

| 职责 | 文件 |
| --- | --- |
| ModelGateway（LLM 路由） | `backend/app/llm/model_gateway.py` |
| EventBus + Backlog | `backend/app/infrastructure/event_bus/` |
| Execution Coordinator（并发/取消） | `backend/app/infrastructure/execution/` |
| TaskRepository + ContextStore | `backend/app/infrastructure/repository/` |
| Redis client / KeywordCache | `backend/app/infrastructure/cache/` |
| Postgres engine / NotesVectorStore | `backend/app/infrastructure/storage/` |
| ARQ queue + tasks/ + cron | `backend/app/infrastructure/queue/` |

### API 层

| 路由 | 文件 |
| --- | --- |
| 任务 CRUD + SSE | `backend/app/api/routes/tasks.py` |
| 登录 + 会话 | `backend/app/api/routes/auth.py` |
| 知识库 CRUD | `backend/app/api/routes/knowledge.py` |
| 系统设置 + 定时任务触发 | `backend/app/api/routes/settings.py` |
| 任务历史 | `backend/app/api/routes/history.py` |

### Prompt 层（纯 Markdown）

位置：`backend/app/application/agents/prompts/*.md`

- `input_parser.md`
- `insight_industry.md` / `insight_competitor.md` / `insight_brand.md`
- `rag_query_rewrite.md`
- `strategy_title.md` / `strategy_product.md` / `strategy_cover.md` / `strategy_structure.md`
- `image_analysis.md`
- `video_analysis.md`（4.2 激活）

### 服务层（业务对象）

| 职责 | 文件 |
| --- | --- |
| 系统设置持久化 | `backend/app/services/system_settings_store.py` |
| 焦点关键词持久化 | `backend/app/services/focus_keywords_store.py` |
| Cookie 健康诊断 | `backend/app/services/cookie_health_service.py` |
| 身份映射 | `backend/app/services/identity_store.py` |
| 知识库 CRUD | `backend/app/services/knowledge_registry.py` |
| 扫码登录编排 | `backend/app/services/auth_orchestrator.py` |

---

## 附：推荐的"自己走一遍"顺序

如果你想手动吃透这套架构,建议按这个顺序读代码（每步 5-10 分钟）：

1. `domain/task_context.py` — 理解"9 分区 + 版本号 + 审计日志"的状态模型
2. `application/agents/base.py` — 理解"Agent 四件事门禁"
3. `application/agents/input_parser_agent.py` — 最简单的 Agent,看 run() 的写法
4. `application/agents/crawler_agent.py:89-247` (只看主 run) — 最复杂的 Agent,看三层缓存怎么串
5. `application/orchestrator.py` — 看 6 Stage 怎么串 + insight‖rag 怎么并行
6. `application/orchestration/langgraph_engine.py:99-121` — 对比 LangGraph 如何用 graph 表达同样拓扑
7. `infrastructure/queue/arq_settings.py` + `tasks/warmup.py` — 看 cron 和 force 参数双入口
8. `api/routes/tasks.py:58-130` — 串起整个外部入口

读完基本可以自信地说"我理解这个系统怎么跑一次任务"。当前阶段 4.2 已完成,视频分析以异步支路形态接入(见 2.4 节)。下一阶段 4.3 将做段落级反馈闭环,4.6 做 Postgres 硬切换。
