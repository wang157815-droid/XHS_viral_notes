# 阶段2 验收报告

## 一、实现覆盖（对齐 `阶段2核心拆解` 子阶段）

| 子阶段 | 关键输出 | 交付落点 | 状态 |
| --- | --- | --- | --- |
| 2.0 模型网关 | `ModelGateway / ProviderRegistry / ModelProfile / AgentModelPolicy` + 设置页路由 | `backend/app/llm/*`，`backend/app/api/routes/settings.py::model-governance` | ✅ |
| 2.1 契约 + 状态机 | 任务 7 态、模块 6 态、5 大类错误码、SSE 事件契约 | `backend/app/domain/{task_status,module_status,error_codes,events}.py`，`frontend/src/lib/contracts.ts` | ✅ |
| 2.1b TaskContext | 分区字段 + 版本自增 + JSON 快照 | `backend/app/domain/task_context.py` | ✅ |
| 2.1c CanvasSchema | 三层 / 维度 / 主题 / 模块布局 | `backend/app/domain/canvas/schema.py`，`frontend/src/lib/contracts.ts` | ✅ |
| 2.1d 幂等 + 乐观锁 | `Idempotency-Key` + `If-Match: module_version` | `backend/app/application/idempotency.py`、`optimistic_locking.py`，`frontend/src/lib/api-client.ts` | ✅ |
| 2.2 任务应用层 | `TaskService` + `TaskRepository`（含 owner、idempotency、canvas/context version） | `backend/app/application/task_service.py`，`backend/app/infrastructure/repository/task_repository.py` | ✅ |
| 2.2b 并发基础 | `ExecutionCoordinator`（infrastructure 层） + 分支并行 + 取消 | `backend/app/infrastructure/execution/coordinator.py` | ✅ |
| 2.2c Ownership + RBAC | `resolve_task_record` + 管理员审计 | `backend/app/application/auth/task_access.py` | ✅ |
| 2.3 SSE 主通道 | `TaskEventBus + /tasks/{id}/stream` | `backend/app/infrastructure/event_bus/task_event_bus.py`，`backend/app/api/routes/tasks.py` | ✅ |
| 2.3b 断线补发 | `TaskEventBacklogStore` + `Last-Event-ID` 回放 | `backend/app/infrastructure/event_bus/backlog_store.py` | ✅ |
| 2.3c 心跳 + 代理兼容 | 15s ping + 禁缓冲响应头 + 客户端 45s 超时重连 | `backend/app/api/routes/tasks.py`，`frontend/src/lib/sse/event-source-client.ts` | ✅ |
| 2.4 LUI 读链路 | 输入 + 关键词 + 欢迎建议 + 进度时间线 + Cookie 拦截 | `frontend/src/components/workspace/lui-panel.tsx`，`frontend/src/app/workspace/page.tsx` | ✅ |
| 2.5 GUI 画布渲染 | 三层渲染器 + 维度条 + 主题条 + 免责声明 + 模块卡 | `frontend/src/components/workspace/{gui-canvas,dimension-bar,theme-bar,module-card}.tsx` | ✅ |
| 2.6 模块写链路 | 重生成 / 删除 / 恢复 / 级联，状态机 + 幂等 + 乐观锁 + 权限四道闸 | `backend/app/api/routes/tasks.py::modules`，`frontend/src/app/workspace/page.tsx` | ✅ |
| 2.6a DirtyFlag 级联 | 依赖图 + 级联规则 + 前端脏状态标签 | `backend/app/domain/module_graph.py`，`frontend/src/components/workspace/module-card.tsx` | ✅ |
| 2.6b 导出 | Excel / JSON + 权限校验 + 一致性 | `backend/app/api/routes/tasks.py::export`，`frontend/src/app/workspace/page.tsx` | ✅ |
| 2.7 Agent MVP | `Crawler -> Semantic‖RAG -> Strategy -> CanvasRender` + 模块依赖图注册 | `backend/app/application/agents/*`，`backend/app/application/orchestrator.py` | ✅ |
| 2.8 稳定性回归 | 23 条 pytest 覆盖状态机、错误码、幂等/乐观锁、事件总线、并发、E2E、Eval | `backend/tests/**` | ✅ |
| 2.8b Eval 基线 | 固定样本 + 可靠性指标 + 结构覆盖 + 导出一致性 | `backend/tests/eval/*` | ✅ |

## 二、验收门槛（阶段2完成定义）

| 验收门槛 | 证据 | 状态 |
| --- | --- | --- |
| 单任务端到端成功率 ≥ 95%（开发环境） | Eval 基线 4 条样本全部通过 | ✅ |
| SSE：15s 心跳、45s 客户端超时、Last-Event-ID 回放 | `tasks.py::stream_task`、`event-source-client.ts`、单测 `test_event_bus.py` | ✅ |
| 模块闭环：重生成 / 删除 / 恢复 E2E + 状态机非法转换拒绝 | `tasks.py::modules/*` + `test_domain_state_machines.py` | ✅ |
| 幂等与并发保护：10 分钟同键幂等 + 乐观锁冲突 409 | `test_idempotency_and_locking.py` | ✅ |
| 权限：普通用户不可访问他人任务 | `task_access.py::resolve_task_record` | ✅ |
| 错误码：5 大类全覆盖 + `trace_id` | `test_error_codes.py` | ✅ |
| 导出：Excel / JSON 结构与 CanvasSchema 一致 | `test_eval_baseline.py::test_export_snapshot_structure_consistent` | ✅ |
| Eval 基线：固定样本 + 自动回归 | `backend/tests/eval/*` | ✅ |
| 并发：扇出并行 + 取消传播 + 事件顺序 | `test_execution_coordinator.py`、`test_event_bus.py` | ✅ |
| 模型治理：Agent 100% 经过 ModelGateway | 代码审计（Agent 仅通过 `self._gateway.chat/embed` 调模型） | ✅ |
| 遗留控制：阶段2 范围内旧入口仅保留兼容用 | 新链路已完全覆盖 `/tasks`、`/tasks/{id}/stream`、`/tasks/{id}/export/*` | ✅ |

## 三、自测指引

### 后端单测

```bash
.venv\Scripts\python -m pytest backend\tests -q
```

预期：`23 passed`，覆盖：
- 任务 / 模块状态机
- 错误码分类
- 幂等键 + 乐观锁
- 模块依赖图 + DirtyFlag
- 事件总线 + 回放
- 执行协调器（扇出 + 取消）
- TaskService E2E
- Eval 基线（关键词搜索、策略模块覆盖、导出一致性）

### Web 端联调（需要真实 Cookie）

1. 启动后端：

    ```bash
    .venv\Scripts\python backend\run.py
    ```

2. 启动前端：

    ```bash
    cd frontend
    npm run dev
    ```

3. 打开 http://localhost:3000/workspace，触发以下用例：
    - Cookie 有效：输入任意需求 → 观察 SSE 的 `task_status` / `agent_progress` / `canvas_schema_updated` / `done`。
    - 断线重连：刷新页面或切到后台 60s → 观察“重连中”→ 回放历史事件后回到 `open`。
    - 模块重生：点击某模块的 `重新生成`，观察版本自增 + SSE 推送。
    - 并发冲突：开两个 Tab 对同一模块同时点重生，其中一个应命中 `INPUT_MODULE_VERSION_MISMATCH`。
    - 级联重生：点 `级联重生`，下游 `ready` 模块被标 `stale`。
    - 导出：点击右上角 `导出 Excel` / `导出 JSON`，检查下载文件的三层结构与画布一致。
    - Cookie 过期：手动让 Cookie 过期后再点 `开始分析`，应提示 `AUTH_COOKIE_EXPIRED`。

## 四、已知的阶段3 后续事项（非阶段2 验收门槛）

- CrawlerAgent MVP 当前输出占位样本，阶段3 对接 `viral_collector` 真实采集。
- Embedding 当前走 MVP，阶段3 接入 ChromaDB 的 RAG 语义检索。
- BacklogStore 默认内存实现，阶段3 可切换 `RedisStreamBacklog`。
- Ragas / Promptfoo 深度语义评估放阶段3。
- Canvas 段落级反馈（like / dislike / edit）写入已在 TaskContext 中预留，阶段3 接入 UI。
- 真实 Orchestrator 并发上限推荐调整为 Redis 分布式信号量（阶段3）。
