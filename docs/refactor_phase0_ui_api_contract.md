# RedMuse 阶段0：UI 与 API 契约冻结（v1）

> 2026-04-16 更新：阶段1登录切片已在新后端落地，`/auth/xhs-login/session` 与状态轮询已可用；工作台域路由已接入会话门禁。
> 2026-04-27 更新：4.4 需求升级为对话中枢，新增 `/conversations`、消息、意图路由、知识库引用和任务交接契约。

## 1. 目标与范围

本文件用于冻结阶段0（骨架搭建期）的 UI 与 API 契约，作为前后端并行开发的唯一对齐基线。

- UI 来源：
  - `prototypes/01-login.html`
  - `prototypes/02-workspace.html`
  - `prototypes/03-history.html`
  - `prototypes/04-knowledge.html`
  - `prototypes/05-settings.html`
- 架构决策：同仓双轨重构（不新建独立仓库）
  - 新前端：`frontend/`
  - 新后端：`backend/`
  - 旧系统：`viral_app.py` + `web/templates/index.html` 作为过渡/回退轨道

## 2. 信息架构冻结

### 2.1 页面与路由

- 登录页：`/login`
- 工作台：`/workspace`
- 历史任务：`/history`
- 知识库：`/knowledge`
- 系统设置：`/settings`

### 2.2 核心对象模型

- `UserSession`
  - `user_id`: string
  - `nickname`: string
  - `role`: `"admin"` | `"user"`
  - `token`: string
- `CookieHealth`
  - `status`: `"valid"` | `"expiring_soon"` | `"expired"` | `"unknown"`
  - `saved_days`: number
  - `last_checked_at`: string (ISO8601)
  - `message`: string
- `TaskSummary`
  - `task_id`: string
  - `keywords`: string[]
  - `status`: `"pending"` | `"running"` | `"paused"` | `"completed"` | `"failed"` | `"cancelled"`
  - `created_at`: string
  - `updated_at`: string
  - `progress`: number (0-100)
  - `collected_count`: number
  - `duration_seconds`: number
- `CanvasDocument`
  - `task_id`: string
  - `title`: string
  - `theme_name`: string
  - `focus_dimensions`: string[]
  - `layers`: `CanvasLayer[]`
- `CanvasLayer`
  - `layer_id`: `"framework"` | `"insight"` | `"raw_data"`
  - `title`: string
  - `modules`: `CanvasModule[]`
- `CanvasModule`
  - `module_id`: string
  - `title`: string
  - `status`: `"ready"` | `"generating"` | `"error"`
  - `editable`: boolean
  - `summary`: string
  - `content`: object
- `Conversation`
  - `conversation_id`: string
  - `owner_user_id`: string
  - `title`: string
  - `summary`: string
  - `active_task_id`: string | null
  - `created_at`: string
  - `updated_at`: string
  - `metadata`: `{ domain_ids?: string[], recent_keywords?: string[], user_preferences?: object }`
- `ChatMessage`
  - `message_id`: string
  - `conversation_id`: string
  - `role`: `"user"` | `"assistant"` | `"system"` | `"tool"`
  - `content`: string
  - `intent`: `"general_qa"` | `"knowledge_qa"` | `"xhs_analysis"` | `"refine_canvas"` | `"export"` | `"unknown"`
  - `intent_confidence`: number
  - `clarification_needed`: boolean
  - `clarification_question`: string | null
  - `citations`: `KnowledgeCitation[]`
  - `task_handoff`: `TaskHandoff | null`
  - `debug`: `{ rewrite_queries?: string[], retrieval_summary?: object, model_error_code?: string } | null`
  - `created_at`: string
- `KnowledgeCitation`
  - `doc_id`: string
  - `chunk_index`: number
  - `title`: string
  - `snippet`: string
  - `score`: number
  - `source`: `"vector"` | `"domain_keyword"` | `"task_context"`
- `TaskHandoff`
  - `task_id`: string
  - `status`: `"pending"` | `"queued"` | `"running"` | `"completed"` | `"failed"` | `"cancelled"`
  - `raw_input`: string
  - `keywords`: string[]
  - `canvas_url_hint`: string | null
- `IntentClassification`
  - `intent`: `ChatMessage.intent`
  - `confidence`: number
  - `reason`: string
  - `target_module_ids`: string[]
  - `extracted_keywords`: string[]
  - `domain_ids`: string[]
  - `should_retrieve_knowledge`: boolean
  - `clarification_needed`: boolean
  - `clarification_question`: string | null

## 3. API 契约冻结（阶段0 + 4.4 增补版）

统一前缀：`/api/v1`

### 3.1 登录与会话

- `POST /auth/xhs-login/session`
  - 说明：创建扫码登录会话
  - 请求：
    - `{ "scene": "dashboard_login" }`
  - 响应：
    - `{ "session_id": "string", "status": "waiting_scan", "expires_in": 180 }`
- `GET /auth/xhs-login/session/{session_id}`
  - 说明：查询扫码状态
  - 响应：
    - `{ "session_id": "string", "status": "waiting_scan|scanned|confirmed|success|expired|error", "qr_code_base64": "string|null", "token": "string|null", "user": { ... } | null }`
- `GET /auth/me`
  - 说明：获取当前用户
  - 响应：
    - `{ "user_id": "string", "nickname": "string", "role": "admin|user" }`
- `POST /auth/logout`
  - 说明：退出登录
  - 响应：
    - `{ "ok": true }`

### 3.2 对话中枢（4.4 新增契约）

- `POST /conversations`
  - 说明：创建一个工作台对话，会话可绑定 0 或 1 个当前活跃任务。
  - 请求：
    - `{ "title": "string?", "metadata": { "domain_ids": ["string"] } }`
  - 响应：
    - `Conversation`
- `GET /conversations/{conversation_id}`
  - 说明：获取会话详情、消息列表和当前活跃任务。
  - 响应：
    - `{ "conversation": Conversation, "messages": ChatMessage[] }`
- `POST /conversations/{conversation_id}/messages`
  - 说明：发送一条用户消息，由后端进行意图路由。
  - 请求：
    - `{ "content": "string", "domain_ids": ["string"]?, "active_task_id": "string|null", "client_message_id": "string?" }`
  - 响应：
    - `{ "user_message": ChatMessage, "assistant_message": ChatMessage, "conversation": Conversation, "intent": IntentClassification }`
  - 行为：
    - `intent=general_qa`：直接通过 `ModelGateway.chat` 回答，不创建任务。
    - `intent=knowledge_qa`：检索知识库，返回 `assistant_message.citations`。
    - `intent=xhs_analysis`：创建/启动 `Task`，返回 `assistant_message.task_handoff`，前端随后订阅 `/tasks/{task_id}/stream`。
    - `intent=refine_canvas`：使用 `active_task_id` 和 Canvas 模块摘要触发局部重生。
    - `intent=export`：使用 `active_task_id` 触发导出或返回缺少任务的引导。
    - `clarification_needed=true`：不执行任务创建、导出或局部重生，只返回澄清问题。
- `GET /conversations/{conversation_id}/messages`
  - 说明：分页读取会话消息。
  - 查询参数：
    - `limit=50`
    - `before?=message_id`
  - 响应：
    - `{ "items": ChatMessage[], "has_more": true }`
- `GET /conversations/{conversation_id}/stream` (可选 SSE)
  - 说明：对话级流式消息。首版可先不做，任务进度仍复用 `/tasks/{task_id}/stream`。
  - 事件类型：
    - `message_delta`
    - `message_done`
    - `intent_classified`
    - `task_handoff`
    - `error`
  - 首版默认：`CONVERSATION_OS_ENABLED=true` 时开放同步消息接口；流式输出可延后。
  - 说明：4.4 MVP 不提供 Trace 查询接口；仅在 `ChatMessage.debug` 和后端日志中保留轻量调试信息，4.7 可观测阶段再评估是否独立产品化。

### 3.3 工作台任务与 Canvas

- `POST /tasks`
  - 说明：创建分析任务
  - 请求：
    - `{ "raw_input": "string", "advanced_config": { "note_type": 0, "time_range": 0, "target_count": 100, "viral_ratio": 0.5, "min_sample_count": 50 } }`
  - 响应：
    - `{ "task_id": "string", "status": "pending" }`
- `GET /tasks/{task_id}`
  - 说明：查询任务状态
  - 响应：`TaskSummary`
- `POST /tasks/{task_id}/pause`
- `POST /tasks/{task_id}/resume`
- `POST /tasks/{task_id}/cancel`
  - 统一响应：
    - `{ "ok": true, "task_id": "string", "status": "paused|running|cancelled" }`
- `GET /tasks/{task_id}/canvas`
  - 说明：获取画布结果
  - 响应：`CanvasDocument`
- `POST /tasks/{task_id}/modules/{module_id}/regenerate`
  - 说明：模块级重生成
  - 请求：
    - `{ "instruction": "string", "paragraph_id": "string?", "feedback_hint": "string?", "cascade": false }`
  - 响应：
    - `{ "ok": true, "module_id": "string", "status": "generating" }`
- `GET /tasks/{task_id}/stream` (SSE)
  - 说明：任务流式事件
  - 事件类型：
    - `task_status`
    - `agent_progress`
    - `log`
    - `canvas_module_updated`
    - `error`
    - `done`

### 3.4 历史任务

- `GET /history/tasks`
  - 查询参数：
    - `keyword?=string`
    - `status?=string`
    - `time_range?=7d|30d|all`
    - `page?=number`
    - `page_size?=number`
  - 响应：
    - `{ "items": TaskSummary[], "total": number, "page": number, "page_size": number }`

### 3.5 知识库

- `GET /knowledge/domains`
  - 响应：
    - `{ "items": [{ "domain_id": "string", "name": "string", "enabled": true, "keywords": ["string"], "rule_count": 0, "priority": "high|medium|low" }] }`
- `POST /knowledge/domains`
- `PUT /knowledge/domains/{domain_id}`
- `DELETE /knowledge/domains/{domain_id}`
- `GET /knowledge/documents`
- `POST /knowledge/documents/upload`
- `DELETE /knowledge/documents/{doc_id}`
- `POST /knowledge/search`
  - 说明：管理员检索测试接口，仅用于知识库管理页调试，不直接作为用户侧 RAG 问答 API。
  - 请求：
    - `{ "query": "string", "top_k": 5, "doc_id": "string?" }`
  - 响应：
    - `{ "query": "string", "top_k": 5, "hits": [{ "chunk_id": "string", "text": "string", "metadata": {}, "similarity": 0.0 }] }`

### 3.6 系统设置

- `GET /settings/system`
  - 响应：
    - `{ "text_model": "string", "vision_model": "string", "embedding_model": "string", "video_analysis_enabled": true, "crawler_schedule": { "enabled": true, "interval_hours": 6, "hot_keywords_top_n": 50 } }`
- `PUT /settings/system`
- `GET /settings/cookie-health`
  - 响应：`CookieHealth`
- `GET /settings/focus-keywords`
- `PUT /settings/focus-keywords`

## 4. 统一响应规范

- 成功响应：
  - `{ "ok": true, "data": { ... } }`
- 失败响应：
  - `{ "ok": false, "error": { "code": "string", "message": "string", "details": {} } }`

### 4.1 错误码约定（首批）

- `UNAUTHORIZED`
- `FORBIDDEN`
- `NOT_FOUND`
- `VALIDATION_ERROR`
- `TASK_CONFLICT`
- `COOKIE_EXPIRED`
- `INTERNAL_ERROR`

## 5. 阶段0/4.4 验收标准

- 前端已按上述路由创建骨架页面，页面间可正常跳转。
- 后端已按上述分组暴露占位接口，返回结构符合契约。
- 工作台具备 SSE 占位事件能力（至少可收到 `task_status` 或 `ping`）。
- 旧系统保持可运行，不阻断现网使用。
- 4.4 增补：工作台对话区可持久化多轮消息；普通问答不创建任务；知识库问答返回 `KnowledgeCitation[]`；爆文模型需求返回 `TaskHandoff` 并继续复用任务 SSE 与 Canvas。