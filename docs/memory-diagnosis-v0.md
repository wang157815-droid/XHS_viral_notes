# RedMuse 上下文与记忆系统 · 现状诊断（第 0 步）

> 版本：v0 · 2026-10-19 · 只做诊断，不含设计与代码改动。
> 依据：逐个阅读了以下代码（行号以当前分支 `xiang-oct-18-2` 为准），没有凭空假设结构。
>
> - 入口与路由：`application/conversation_service.py`、`conversation/intent_classifier.py`、`conversation/tool_agent.py`
> - 自主 Agent：`agent_runtime/runtime.py`、`prompts.py`、`blackboard.py`、`tool_registry.py`、`tools/*.py`（共 42 个工具）
> - 流水线：`orchestration/langgraph_engine.py`、`checkpoint_store.py`、`agents/*.py`、`agents/prompts/*.md`、`domain/task_context.py`
> - 存储：`infrastructure/db/schema.py`、`services/conversation_store.py`、`services/focus_keywords_store.py`、`viral_agent/services/knowledge/rag_service.py`、`infrastructure/storage/notes_vector_store.py`
> - 反馈与观测：`application/paragraph_feedback.py`、`module_regeneration.py`、`llm/model_gateway.py`

---

## 0. 先纠正需求里的三处前提（会直接影响设计）

| 需求里的假设 | 代码里的实际情况 | 对设计的影响 |
|---|---|---|
| “工作记忆 = LangGraph state / checkpointer” | `_GraphState` 只有 `task_id / completed_stages / failed_stage` 三个字段（`langgraph_engine.py:74`），checkpointer 是进程内 `MemorySaver`。真正的任务数据都在 **TaskContext**（`domain/task_context.py`，按分区写入，落盘到 `datas/task_contexts/*.json`）。另外，**现在聊天的默认入口已经不是图，而是 `agent_runtime` 的 ReAct 循环**。图只在 `run_viral_analysis` 宏工具或旧路径里跑。 | 工作记忆要围绕 **TaskContext（任务内）+ RunBlackboard（单次 Agent 运行内）** 来设计，LangGraph state 只当调度元数据。不应该往图 state 里塞业务字段。 |
| “复用现有 ChromaDB 的三个 collection” | **ChromaDB 已经停用了**。`RAGService` 的 `persist_directory/collection_name` 只是为了兼容保留的参数，注释写明“phase 4.6 后 pgvector 是唯一后端”（`rag_service.py:27-35`）。现有向量存储只有 PG 里的 `knowledge_chunks`（知识库）和 `xhs_notes`（笔记 L2 缓存），外加 `xhs_comment_*` 评论缓存。 | 语义记忆应该落在 **PostgreSQL + pgvector**。新增 `user_memory` 也用 PG 表，不要为它复活 ChromaDB（`requirements.txt` 里已注明它和 paddle 有 protobuf 冲突）。 |
| “每个 Agent 都有 `agents/<name>/AGENT.md`，可 @import 共享片段” | prompt 都平铺在 `agents/prompts/*.md`，由 `PromptRegistry` 只做“读文件 + 缓存”。`prompts/README.md` 明确规定“每个 prompt 独立自包含，不靠 include”。 | @import 和现行约定冲突，需要你拍板。我倾向于**只允许导入 `_shared/` 下少量稳定片段**（安全声明、输出规范），在加载时静态展开、带版本哈希，而不是任意嵌套 import。 |

还有一点需要提前说明：**流水线里一半的 Agent 根本不该加载用户记忆。** 图文/视频 6 要素标注、方向归一、聚类校验这些是“客观标注器”。把“品牌调性/偏好”注入进去，会让标注向用户偏好偏移，污染爆文模型。设计里要明确把它们列为“记忆隔离区”。

---

## 1. 系统里实际有哪些 LLM 调用方

```
用户消息 ─► IntentClassifier ─► ConversationToolAgent(选工具)
              │
              ├─ answer_general      → GeneralQA（_stream_general_answer）
              ├─ answer_with_knowledge → KnowledgeQA
              ├─ run_agent_task      → PlannerAgent（agent_runtime ReAct，42 个工具）
              │                          ├─ 阶段工具 → 单个流水线 Agent
              │                          └─ run_viral_analysis → LangGraph 9 节点整包执行
              └─ （后台）ConversationTitleAgent：起标题 + 滚动摘要
```

流水线节点里，`CanvasRenderAgent`、`XhsAuthAgent` 不调模型；`CrawlerAgent` 只在关键词重试时调用一次模型（`crawler_agent.py:899`）。

---

## 2. 每个调用方的上下文是怎么拼的

### 2.1 路由层

| 调用方 | 拼装方式 | 问题 |
|---|---|---|
| IntentClassifier | 当前消息 + 最近 **3** 条消息摘要 + 会话摘要截断到 **300 字**（`intent_classifier.py:315-333`） | 看不到用户长期偏好，比如“我们只做母婴赛道”。每轮都要重新从对话里猜。 |
| ConversationToolAgent | 单条 user prompt：意图、最近消息、任务状态、Canvas 模块列表（`tool_agent.py:274`） | 同上。 |

### 2.2 GeneralQA / KnowledgeQA

- system 是约 2.5k 字的静态角色说明，**后面直接拼接会话滚动摘要**（`conversation_service.py:224-266`）。
- 再加最近 **8** 条原文消息，附件摘录和图片只挂在最后一条 user 消息上。

问题：

1. **摘要放在 system 尾部，摘要一变，整个前缀缓存就失效。** 摘要每 4 条消息刷新一次，所以 DeepSeek 前缀缓存基本命中不了。摘要应该移到稳定前缀之后的独立 user 块。
2. 滚动摘要有几个限制（`conversation_service.py:1793-1851`）：
   - 只取最近 12 条消息，每条截到 200 字；
   - 摘要上限 200 字；
   - 提示词里写了“保留决策”，但没有结构；
   - 用的是起标题的 Agent，生成结果也没有校验。

   长对话里早期的关键决策会被逐次“摘要的摘要”稀释掉，属于**有损级联压缩**。

### 2.3 PlannerAgent（自主 Agent，现在的主路径）

拼装顺序（`runtime.py:105-117`）：

```
[system] PLANNER_SYSTEM_PROMPT（约 2.4k 字，静态 ✅）
[tools]  42 个工具 schema（约 1.6 万字符，每轮都发 ✅ 稳定）
[history] 最近 6 条 user/assistant 纯文本（取自 list_messages(limit=10)）
[user]   build_goal_prompt(目标 + 高级配置 + Plan 模式开关)
循环追加：[assistant] 思考 + “→ 调用 xxx(参数≤120字)”
          [user] 观察（每个工具结果截断到 4000 字）+ 节奏提示
```

做得好的地方：

- 循环内只追加、不改写，前缀缓存友好。
- 采集类工具已经实现“完整结果进黑板（`RunBlackboard`），只回摘要 + 句柄 `ds:notes:1` + 前 5 条预览”。这就是需求里说的“渐进披露”的雏形。
- 阶段工具 `_summarize` 只回计数，不灌原文（`stages.py:174`）。

浪费：

1. **42 个工具每轮全量发送。** 问“我的知识库有哪些文档”时也带着全部采集、流水线工具的 schema。工具 schema 是单次调用里最大的一块。
2. **观察只增不减，没有压缩。** 一次运行最多 8–12 轮 × 每轮最多 4000 字，后期每轮都重复携带早期的旧观察。没有“超预算时把旧观察折叠成摘要 + 句柄”的机制，也没有按 token 计算的预算，只有按字符截断单条观察。
3. `query_dataset` 一次最多回 60 行完整 JSON，模型很容易拉大块数据回上下文。
4. 历史里的 assistant 消息是**最终回答全文**（最多 1800 tokens），3 条就可能比当前目标还长。

缺失：

1. **Planner 看不到会话滚动摘要。** `_stream_agent_task` 只传了 `recent_messages`（`conversation_service.py:1075`），没有传 `conversation.summary`。第 7 条以前的对话对 Planner 来说完全不存在。
2. **黑板是一次性的。** 它存在 `ctx.extra["blackboard"]`，生命周期等于一次 `run_stream`。下一轮用户说“把刚才那 20 篇按收藏排序”时，`ds:notes:1` 已经不存在了，只能**重新采集**，既浪费昂贵预算，也可能触发风控。
3. **工具轨迹没有回到下一轮。** `agent_steps/agent_tool_calls` 只写进消息的 `debug` 供前端回放，没有进入模型上下文。下一轮的模型不知道上一轮建过哪个 `task_id`、用过什么关键词。只有 `linked_task_id` 挂在消息上。
4. **没有用户/账号级记忆。** 赛道、目标人群、品牌调性、禁忌词，全系统都没有存储位置：
   - `redmuse_users` 只有账号字段；
   - `focus_keywords` 和 `system_settings` 都是 **全局单行（id=1）**，不按用户隔离。

### 2.4 流水线 Agent（TaskContext 分区传递）

| Agent | 读取 | 拼进 prompt 的内容 | 写入 |
|---|---|---|---|
| InputParser | `input_spec` | `input_parser.md` + 用户原话 + 主关键词 + 竞品提示 | `input_spec.parsed` |
| Crawler | `input_spec` | 仅关键词重试时一句“关键词：…” | `crawler_output` |
| Image / Video | `crawler_output` | `*_6elements.md` + 单条笔记（文案摘要截到 280/300 字）+ 图片/视频 | `multimodal_output` |
| ViralModel（4 次调用） | `crawler_output`、`multimodal_output` | 方向归一 / 小簇校验 / 拟合复核 / 命名，把聚类 JSON `indent=2` 全量拼入（`max_tokens` 最高 8000） | `viral_model_output` |
| Sheet2Narrative | `viral_model_output` + 笔记 | 每个模型一次调用，全量分类条目 | `viral_model_output`（merge） |
| Insight（2 次调用） | 矩阵、`multimodal`、`crawler`、`input_spec` | 模型 Top6 + 痛点 Top8 + SEO Top8 + 竞品标题 8 条（已是**本地聚合后的摘要** ✅） | `semantic_output` |
| RAG | `input_spec` | 改写查询 → 检索 Top5 → 片段截到 120 字 | `rag_output` |
| CanvasRender | 全部分区 | 不调模型 | `canvas_document` |

问题：

1. **Agent 间交接是“读整个分区”，不是结构化摘要。** 每个 Agent 自己挑字段、自己截断，截断规则散落在代码各处（`[:280]`、`[:8]`、`[:6]`……），没有统一的预算概念。下游拿到的是原始结构，不知道上游丢了什么。
2. **`rag_output` 是“写了没人读”。** RAG 和 Insight 在图里**并行**执行（`langgraph_engine.py:138-142`），Insight 读不到知识库规则；CanvasRender 也不读 `rag_output`（全仓库 grep 无消费者）。用户上传的品牌 SOP 对分析结论**没有任何影响**，这是最大的信息丢失点。
3. **InputParser 看不到会话上下文和用户画像。** 只看本次一句话。用户三轮前说过“竞品是 A、B”，这次只说“再跑一遍”时，会被重新推断竞品。
4. **ViralModel 的 JSON 用 `indent=2` 全量拼接**，缩进空白本身就浪费 token，且没有按簇数做预算。
5. 流水线 Agent 的上下文是“单次、自包含”的，这本身是**优点**：可复现、可测、可缓存。改造时不应该把它们改成会话式。

### 2.5 情景记忆与反馈

- 历史任务、报告：`tasks` 表 + TaskContext JSON + Canvas，可以按 `task_id` 取回。但**没有任何“任务级结论摘要”**，跨任务复用只能整份读 TaskContext。
- 用户反馈已经有数据结构：段落级 `like / dislike / delete / edit / reset` 写在 `CanvasModule.content.feedback_map`（`paragraph_feedback.py`）。但它**只用于同一模块的重新生成**（`module_regeneration.py:369-393`），从不跨任务汇总。用户第 5 次 `dislike` 同一种标题风格，第 6 个任务照样生成。
- 笔记 L2 缓存 `xhs_notes`（pgvector）是“可重新获取的数据”，**不应当被视为记忆**。

---

## 3. 安全与一致性问题（与记忆直接相关）

1. **爬取内容直接拼进 prompt，没有数据分隔。** 笔记标题、正文、评论都以 `【文案摘要】{desc}` 或 JSON 原样进入 system/user 消息。Planner 的观察里也是原始 JSON。没有任何“以下为数据，不是指令”的声明。只要有一条笔记写着“忽略以上指令……”，就有可能影响行为。将来一旦有“Agent 自动写记忆”，这条链就会变成**持久化的提示词注入**。
2. **知识库检索没有按用户隔离。** `search_knowledge` 调用 `rag.search(..., include_all=True)`（`tools/knowledge.py:38`），而 `include_all=True` 时不加 owner 过滤（`rag_service.py:164-166`），A 用户的 Agent 能检索到 B 用户上传的文档。将来的 `user_memory` 必须从第一天就强制按 owner 过滤。这个现有问题建议**单独先修**，与记忆改造解耦。
3. **检索失败被伪装成“无结果”。** Embedding 404 时 `rag.search` 吞掉异常返回 `[]`（`rag_service.py:208-210`），工具返回 `ok=True` 和“知识库未检索到相关内容”。模型因此认为“知识库里没有”，这是错误观察。记忆读取必须区分“没有”和“读失败”。
4. 敏感信息：Cookie 存在 `datas/users/<u>/cookies.json` 和 `xhs_credential_path`，目前不会进 prompt。但 `get_cookie_health`、`get_system_settings` 等工具的返回如果被自动写入记忆，就有泄漏风险，写入侧需要做脱敏或拒写。

---

## 4. 可观测性现状

- `model_gateway._record_audit` 只记录**整次调用**的 `tokens_in/out`、耗时和模型（写入 `task_metrics`）。没有分块占比，不知道 system / 工具 schema / 历史 / 观察各占多少，也就无法判断压缩是否有效。
- `ToolRegistry` 有工具审计，但不记录观察回注了多少字、是否被截断。
- 没有查看、编辑、删除“某用户全部记忆”的入口（现在也没有用户记忆可看）。

---

## 5. 问题清单（按影响排序）

| # | 类型 | 问题 | 证据 |
|---|---|---|---|
| P1 | 丢失 | 知识库规则不进入分析结论（RAG ‖ Insight 并行，`rag_output` 无消费者） | `langgraph_engine.py:138-142` |
| P2 | 丢失 | Planner 看不到会话摘要；跨轮黑板、工具轨迹全部丢失，导致重复采集 | `conversation_service.py:1075`、`blackboard.py` 生命周期 |
| P3 | 缺失 | 没有用户/账号级记忆，`focus_keywords` / `system_settings` 是全局单行 | `schema.py` 中两表 `id=1` |
| P4 | 缺失 | 段落反馈不跨任务沉淀 | `module_regeneration.py:369-393` |
| P5 | 安全 | 爬取内容无数据分隔；知识库检索跨用户 | `tools/knowledge.py:38` |
| P6 | 浪费 | 42 个工具 schema 每轮全量发送；观察只增不压缩 | `runtime.py:119`、`_OBS_MAX_CHARS` |
| P7 | 浪费 | 摘要放在 system 尾部，前缀缓存失效 | `conversation_service.py:224-266` |
| P8 | 质量 | 滚动摘要有损级联（200 字、12 条、每条 200 字）、无结构 | `conversation_service.py:1806` |
| P9 | 正确性 | 检索失败伪装成“无结果” | `rag_service.py:208-210` |
| P10 | 观测 | 只有整次调用的 token 数，无分块占比与记忆命中记录 | `model_gateway.py:795` |

---

## 6. 进入第 1 步前需要你确认的点

1. **工作记忆的载体**：按代码现状，以 TaskContext + 跨轮持久化的 RunBlackboard（存 PG，按会话 + TTL）为主，LangGraph state 不承载业务数据。是否同意？
2. **语义记忆的介质**：放弃 ChromaDB，新增 PG 表 `user_memory`（结构化字段 + 可选 embedding 列，embedding 不可用时退化为关键词/标签检索）。是否同意？
   - 注意：当前 Embedding 服务一直报 404，如果设计依赖向量检索，第一批里程碑需要能在无 embedding 时运行。
3. **程序性记忆的 @import**：只允许导入 `_shared/` 片段（单层、静态展开），同时修改 `prompts/README.md` 的“不靠 include”约定。还是保持现状，只做目录化？
4. **记忆隔离区**：Image / Video / 方向归一 / 聚类校验 / 拟合复核这几个标注器**不注入任何用户记忆**。是否同意？
5. P5 里的知识库跨用户检索，是否允许我在第 2 步的第一个里程碑里**先单独修掉**？它和记忆系统无关，但会影响用户记忆的隔离前提。

确认（或修改）以上 5 点后，我再输出 `docs/memory-design-v1.md`。
