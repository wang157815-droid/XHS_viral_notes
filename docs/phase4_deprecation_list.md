# Phase 4.5 Legacy Entry Deprecation List

本清单用于阶段 4.5 下线 `viral_app.py` 旧入口。主线入口从本阶段起以 `backend/app` + `frontend` 为准；旧 FastAPI/Jinja 应用只保留维护页与兼容提示。

## 总体决策

- `viral_app.py` 不再承载采集、分析、知识库、扫码登录、清理、导出等业务能力。
- `web/templates/index.html` 降级为维护/跳转页，避免旧书签 404。
- 旧 `/api/*` 路由统一返回 `410 Gone`，响应内给出新系统入口和典型替代路由。
- 旧代码不做双写、不做代理、不继续维护旧轮询协议。

## Endpoint 去向

| 旧入口 | 状态 | 新入口或处理 |
| --- | --- | --- |
| `GET /` | 保留兼容 | 维护页，提示使用新版 RedMuse |
| `GET /health` | 保留兼容 | Stub 健康检查 |
| `POST /api/auth/login` | 废弃 | 新系统使用小红书扫码登录：`POST /api/v1/auth/xhs-login/session` |
| `GET /api/auth/me` | 已迁移 | `GET /api/v1/auth/me` |
| `POST /api/auth/change-password` | 废弃 | 旧密码体系退出主链路 |
| `GET/POST/PUT/DELETE /api/admin/users*` | 已迁移 | `GET/PUT/DELETE /api/v1/settings/users*` |
| `POST /api/viral/cookie` | 废弃 | 新系统由扫码登录写入用户 Cookie |
| `GET /api/viral/cookie/status` | 已迁移 | `GET /api/v1/settings/cookie-health` |
| `POST /api/viral/search` | 已迁移 | `POST /api/v1/tasks` 或 Conversation OS 的 `POST /api/v1/conversations/{id}/messages` |
| `GET /api/viral/status/{task_id}` | 已迁移 | `GET /api/v1/tasks/{task_id}` + `GET /api/v1/tasks/{task_id}/stream` |
| `POST /api/viral/analyze` | 废弃 | 分析并入新任务编排与 Canvas 生成链路 |
| `GET /api/tasks*` | 已迁移 | `GET /api/v1/tasks` |
| `POST /api/tasks/{task_id}/pause` | 已迁移 | `POST /api/v1/tasks/{task_id}/pause` |
| `POST /api/tasks/{task_id}/resume` | 已迁移 | `POST /api/v1/tasks/{task_id}/resume` |
| `POST /api/tasks/{task_id}/cancel` | 已迁移 | `POST /api/v1/tasks/{task_id}/cancel` |
| `GET /api/viral/history` | 已迁移 | 历史中心：`GET /api/v1/tasks` + `GET /api/v1/conversations` |
| `GET /api/viral/export/{task_id}` | 已迁移 | `GET /api/v1/tasks/{task_id}/export/excel` 或 `/export/json` |
| `GET /api/viral/export/latest` | 废弃 | 新系统不保留“latest”隐式导出，必须指定 task_id |
| `GET /api/viral/download/{filename}` | 废弃 | 新导出直接由 task export endpoint 返回文件 |
| `GET /api/knowledge/domains` | 已迁移 | `GET /api/v1/knowledge/domains` |
| `POST /api/knowledge/domains` | 已迁移 | `POST /api/v1/knowledge/domains` |
| `PUT /api/knowledge/domains/{domain_id}` | 已迁移 | `PUT /api/v1/knowledge/domains/{domain_id}` |
| `DELETE /api/knowledge/domains/{domain_id}` | 已迁移 | `DELETE /api/v1/knowledge/domains/{domain_id}` |
| `POST/DELETE /api/knowledge/domains/{id}/keywords*` | 废弃 | 新系统通过 domain payload 整体更新 keywords |
| `POST /api/knowledge/reload` | 废弃 | JSON 热重载退出主链路，后续由 4.6 存储迁移承接 |
| `POST /api/knowledge/test-detection` | 废弃 | 后续如需要，归入知识库调试/可观测阶段 |
| `GET /api/knowledge/export` | 废弃 | 暂不保留旧 JSON 配置导出接口 |
| `POST /api/knowledge/import` | 废弃 | 暂不保留旧 JSON 配置导入接口 |
| `POST /api/documents/upload` | 已迁移 | `POST /api/v1/knowledge/documents/upload` |
| `GET /api/documents` | 已迁移 | `GET /api/v1/knowledge/documents` |
| `DELETE /api/documents/{doc_id}` | 已迁移 | `DELETE /api/v1/knowledge/documents/{doc_id}` |
| `POST /api/documents/search` | 已迁移 | `POST /api/v1/knowledge/search` |
| `GET /api/knowledge/summary` | 废弃 | 新知识库页直接组合 domains/documents 状态 |
| `GET /api/cleanup/info` | 已迁移 | `GET /api/v1/settings/maintenance` |
| `POST /api/cleanup` | 已迁移 | `POST /api/v1/settings/maintenance/clean` |
| `DELETE /api/cleanup/all` | 废弃 | 删除全部历史数据风险高，不进入新主线 |
| `GET /api/qrcode/status` | 已迁移 | `POST /api/v1/auth/xhs-login/session` 创建时返回可用性错误 |
| `POST /api/qrcode/session` | 已迁移 | `POST /api/v1/auth/xhs-login/session` |
| `GET /api/qrcode/{session_id}/status` | 已迁移 | `GET /api/v1/auth/xhs-login/session/{session_id}` |
| `DELETE /api/qrcode/{session_id}` | 已迁移 | `DELETE /api/v1/auth/xhs-login/session/{session_id}` |
| `POST /api/qrcode/{session_id}/sms` | 已迁移 | `POST /api/v1/auth/xhs-login/session/{session_id}/sms` |

## 部署入口调整

- Docker 主 Web 进程应运行 `python backend/run.py`，端口改为 `8100`。
- ARQ worker 继续使用 `python -m backend.app.infrastructure.queue.runner`。
- 如果仍手动运行 `python viral_app.py`，只会看到维护页与 410 兼容提示。

## 回滚点

4.5 改动采用 Git 回滚作为回退机制；旧 `datas/*.json` 和 `viral_agent` 业务内核不在本阶段删除。
