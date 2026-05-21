# [MemPalace 归档稿 · 续] RedMuse RBAC Phase 5（5.0–5.4）

> 接续同文档 Phase 5 节；不含 Phase 5.5

## Phase 5：完整 RBAC + 用户管理（5.0–5.4）

**范围决策（2026-05-10）**：启用 5.0–5.4 为 Phase 5 必做项，总耗时 3–5 天。

**前瞻性约束（2026-05-10 二次澄清）**：未来 UI 会**视觉模仿** ChatGPT-web，但**仅可能引入"左侧会话/项目树"一项**。**明确不做**多工作区 / 组织 / 团队席位 / 公开分享链接 / API Token / 订阅分层 / 配额计费等多租户能力。因此 Phase 5 RBAC **不引入** workspace 原语 / capability policy 引擎 / share_token / api_token / 轻 JWT claims；继续沿用「user × 三档 role × owner_user_id 过滤 + admin 穿透」的最小授权模型。**唯一前瞻**：5.1 顺手把 `redmuse_projects` 空表与 `conversations.project_id` / `tasks.project_id` nullable 列建好，等 UI 重做期补 CRUD 即可，避免再跑一次 DDL（详见 5.0 决策点 E'）。

### 阶段总览

| 子阶段 | 内容 | 耗时 | 产物类型 |
|---|---|---|---|
| 5.0 | Schema 设计与决策对齐（含 projects 预留） | 0.5 天 | 书面规划（本文档） |
| 5.1 | PostgreSQL 用户表 + projects 预留表落地 + 存储层迁移 | 1 天 | 代码 + 迁移脚本 |
| 5.2 | 角色矩阵（admin/analyst/viewer）+ `require_role` Dependency | 0.5–1 天 | 代码 |
| 5.3 | 细粒度资源归属（tasks / knowledge / settings 行级过滤） | 1 天 | 代码 |
| 5.4 | 操作审计日志（表 + 装饰器 + admin 查询接口 + 前端 Tab） | 1 天 | 代码 + 前端 |
### Phase 5.0：Schema 设计与决策对齐

#### 5.0.1 目标

- 把 Phase 5.1–5.4 的 PG schema 固定下来，避免 5.1 实现到一半发现 5.2/5.3 需要 schema 动、返工。
- 对齐关键决策点（XhsCredentialStore 去向、审计表保留期、向后兼容策略），进入 5.1 时不留歧义。

#### 5.0.2 决策点（已于 2026-05-10 全部锁定为推荐项）

- **A. `XhsCredentialStore` 是否随 5.1 一并迁 PG？** → **A2 已确认**
  - 保持当前 `datas/xhs_credential_store.json`，凭据加密 / PG 化拆到 5.x 独立子阶段，Phase 5 聚焦用户/权限/审计。
- **B. 审计表保留期 + 清理策略？** → **B1 已确认**
  - 90 天滚动，ARQ 定时 job 每日清理 `at < now() - 90d`。后续合规诉求再升级到 B2（PG 按月分区 + 永久保留）。
- **C. 向后兼容 `role=user`？** → **C1 已确认**
  - 5.2 在 `_normalize_role` 中把 `user` 自动映射为 `analyst`；旧 JWT 在路由侧无感兼容。
- **D. Feature Flag 粒度？** → **D2 已确认**
  - 三个独立 flag 单独灰度：`REDMUSE_USER_STORE_BACKEND` / `REDMUSE_AUDIT_ENABLED` / `REDMUSE_OWNERSHIP_ENFORCEMENT`。
- **E'. `redmuse_projects` 空表 + `conversations.project_id` / `tasks.project_id` nullable 列何时建？** → **E'1 已确认**
  - 在 5.1 PG 落地的同一个 Alembic revision 里**建表 + 加列，但不开放任何 CRUD 路由 / 业务逻辑**。仅 schema 预留，等 UI 重做期再补「项目分组」前后端。
  - 鉴权语义不变：`redmuse_projects.owner_user_id` 强归属当前 user，`project_id` 不引入跨用户分享 / capability 抽象。

#### 5.0.3 PG Schema 草案

```sql
-- 5.1
CREATE TABLE redmuse_users (
  user_id           TEXT PRIMARY KEY,                 -- 兼容现有 u_xxxxxxxx 格式
  username          TEXT UNIQUE NOT NULL,             -- 存时小写
  nickname          TEXT NOT NULL,
  password_hash     TEXT NOT NULL,
  role              TEXT NOT NULL DEFAULT 'analyst',  -- admin | analyst | viewer
  status            TEXT NOT NULL DEFAULT 'active',   -- active | disabled
  xhs_credential_path TEXT,                           -- 5.x 凭据迁移后改 xhs_credential_id
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at     TIMESTAMPTZ
);

-- 5.1 同 revision 顺手预留：UI 重做期补「项目分组」前后端时无需再跑 DDL
--      仅建表 + 加 nullable 列，本阶段不开放 CRUD 路由 / 业务逻辑
CREATE TABLE redmuse_projects (
  project_id     TEXT PRIMARY KEY,                 -- 例：p_xxxxxxxx
  owner_user_id  TEXT NOT NULL REFERENCES redmuse_users(user_id) ON DELETE CASCADE,
  name           TEXT NOT NULL,
  sort_order     INT  NOT NULL DEFAULT 0,
  archived_at    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_projects_owner ON redmuse_projects (owner_user_id, sort_order);

-- conversations / tasks 加 nullable 列；不加外键强约束以兼容 owner 删除/迁移场景
-- conversations 表已存在于 backend/app/infrastructure/db/schema.py
-- tasks 表已存在于 Phase 4α，列保持 NULL 不影响现有路径
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS project_id TEXT NULL;
ALTER TABLE tasks         ADD COLUMN IF NOT EXISTS project_id TEXT NULL;
CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations (project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks (project_id);

-- 5.3 任务归属（现已在 task_repository JSON 里有 owner_user_id，这次迁 PG）
-- 业务层延续现有 Task JSON schema，PG 仅承担索引查询层（预计 Phase 4α 已计划）

-- 5.4
CREATE TABLE redmuse_audit_log (
  id              BIGSERIAL PRIMARY KEY,
  actor_user_id   TEXT NOT NULL REFERENCES redmuse_users(user_id) ON DELETE SET NULL,
  action          TEXT NOT NULL,           -- 例："task.create" / "user.role_change"
  resource_type  TEXT,                     -- 例："task" / "user" / "credential"
  resource_id    TEXT,                     -- 对应资源主键（如 task_id / user_id）
  payload_digest JSONB,                    -- 关键字段摘要（非全量）
  ip             TEXT,
  user_agent     TEXT,
  at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_actor_at ON redmuse_audit_log (actor_user_id, at DESC);
CREATE INDEX idx_audit_resource ON redmuse_audit_log (resource_type, resource_id);
CREATE INDEX idx_audit_at ON redmuse_audit_log (at);
```

### Phase 5.1：PostgreSQL 用户表 + projects 预留落地

#### 5.1.1 目标

- 用 SQLAlchemy 2.0 async + Alembic 在 PG 上落地 `redmuse_users` 表。
- **同 revision 顺手建** `redmuse_projects` 空表 + `conversations.project_id` / `tasks.project_id` nullable 列（仅 schema 预留，不开放路由 / 业务）。
- 实现 `PgRedMuseUserStore`，与 `RedMuseUserStore`（JSON 版）接口完全等价。
- 提供 Feature Flag 切换 + 一次性迁移脚本，JSON/PG 两套存储灰度共存。

#### 5.1.2 关键改造点

- **新增**：`backend/app/infrastructure/storage/models/redmuse_user.py`（ORM）。
- **新增**：`backend/app/infrastructure/repository/pg_user_store.py`（`PgRedMuseUserStore`）。
- **重构**：`backend/app/services/redmuse_auth/__init__.py` 的 `get_user_store()` 根据 env flag 返回 JSON 或 PG 实现。
- **新增**：`backend/scripts/migrate_users_json_to_pg.py`（一次性幂等迁移，按 `user_id` upsert）。
- **新增**：`backend/alembic/versions/xxxx_create_redmuse_users_and_projects.py`
  - 建 `redmuse_users`、`redmuse_projects` 表 + 两张索引。
  - `ALTER TABLE conversations / tasks ADD COLUMN project_id TEXT NULL` 与对应 index。
  - 不写任何 ORM model / DAO / 路由：`redmuse_projects` 与 `project_id` 列在本阶段视为"占位"，只参与 schema 创建，不参与查询过滤。
- **不变**：`auth.py` / `security.py` / 所有调用方，因为接口等价；`conversations` / `tasks` 业务逻辑也不读写 `project_id`。

#### 5.1.3 Feature Flag

- `REDMUSE_USER_STORE_BACKEND=pg|json`（默认 `json`，生产默认不切）。
- 切到 `pg` 前必须先跑迁移脚本，脚本成功后 flag 才生效。
- `redmuse_projects` / `project_id` 列与该 flag 无关，PG 实例升级后自动具备，JSON 模式下完全不感知。

#### 5.1.4 验收

- flag=pg：登录、注册、改密、set_role、set_status、delete 全部 ok；`list_users` 与 JSON 版返回一致。
- flag=json：完全无回归。
- 迁移脚本幂等（重复跑不出错、不重复插入）。
- 回滚路径：flag 切回 json，数据以 JSON 为准（这一版不双写，以 flag 当下值为准）。
- **projects 预留**：`SELECT * FROM redmuse_projects` 可执行（空表）；`SELECT project_id FROM conversations LIMIT 1` 与 `SELECT project_id FROM tasks LIMIT 1` 返回 NULL；现有 conversations / tasks 路由读写不受影响（pytest 现有用例零回归）。

### Phase 5.2：角色矩阵 + `require_role` Dependency

#### 5.2.1 目标

- 把现有 `role == "admin"` 的散布判断统一收口到 `core/security.py` 的 `require_role(min_role)`。
- 扩展角色为三级：`admin > analyst > viewer`，旧 `user` 向后兼容映射为 `analyst`。
- 前端 `SessionState` 暴露 `can(action)`，隐藏越权菜单。

#### 5.2.2 关键改造点

- **新增**：`core/security.py` 中
  - `RoleLevel = IntEnum("viewer"=1, "analyst"=2, "admin"=3)`
  - `_normalize_role(raw)` 兼容 `user → analyst`
  - `require_role(min: RoleLevel)` Dependency 工厂
- **重构**：7 个路由文件（settings / tasks / knowledge / conversations / history / metrics / auth）把 `require_admin_user` 改为 `require_role(RoleLevel.admin)` 或其他级别。
- **保留**：`require_admin_user` 仍可用，内部转发到 `require_role(RoleLevel.admin)`，避免大批量改动。
- **前端**：
  - `frontend/src/lib/session-context.tsx` 暴露 `can(action: string)`。
  - `frontend/src/lib/auth-actions.ts`（新）维护 action → minRole 映射表。
  - `AppSidebar` / 设置页各 Tab 用 `can()` 隐藏越权菜单。

#### 5.2.3 角色矩阵（5.0 决策后落定）

```text
action                        viewer  analyst  admin
--------------------------------------------------
tasks.read                      ✓       ✓        ✓
tasks.create                    ✗       ✓        ✓
tasks.cancel                    ✗       ✓*       ✓      *仅自己拥有的
knowledge.read                  ✓       ✓        ✓
knowledge.write                 ✗       ✓        ✓
knowledge.delete                ✗       ✗        ✓
settings.system                 ✗       ✗        ✓
settings.profile_self           ✓       ✓        ✓
settings.profile_others         ✗       ✗        ✓
users.manage                    ✗       ✗        ✓
audit.read                      ✗       ✗        ✓
xhs_credential.self             ✓       ✓        ✓
xhs_credential.others           ✗       ✗        ✓
```

#### 5.2.4 验收

- 所有路由都有明确 role 标签，pytest 用 3 角色 x 关键路由矩阵测。
- 前端菜单按 role 显隐，越权菜单点不到。
- 旧 JWT（role=user）登录无感知，自动映射成 analyst 权限。

### Phase 5.3：细粒度资源归属

#### 5.3.1 目标

- 所有"用户级资源"（任务、知识库条目、设置 profile）强制 owner 过滤。
- admin 可穿透查看 / 修改所有用户资源；analyst / viewer 只能操作自己。

#### 5.3.2 关键改造点

- **Task**：`task_repository.get_task` / `list_tasks` 接受 `requestor: {user_id, role}`，非 admin 自动加 `owner_user_id = requestor.user_id` 过滤。现有 `application/auth/task_access.py` 已有雏形，完善为唯一入口。
- **Knowledge**：给 `DomainDocument` / keyword / rag 条目加 `owner_user_id` 列（migration）；所有写入强制填当前 user；查询 analyst/viewer 自动按 owner 过滤。
- **Settings**：
  - `/settings/system-settings` 由 `require_role(admin)` 保护。
  - `/settings/profile`（昵称、密码、绑定）只操作 `current_user.user_id`；`/admin/users/{id}/...` 由 admin 操作他人。
- **归属补齐**：对已存在但无 owner 的旧数据，迁移脚本一次性给 admin 用户 ownership，避免被锁死。

#### 5.3.3 验收

- 用户 A 登录后无法读 / 改 / 删 B 的任务 / 知识库条目（403）。
- admin 可跨用户操作。
- 历史数据（无 owner）迁移后由 admin 持有，可审计可转交。

### Phase 5.4：操作审计日志

#### 5.4.1 目标

- 所有关键动作留痕，admin 可查可筛。
- 保留期默认 90 天（flag `REDMUSE_AUDIT_RETENTION_DAYS=90`），ARQ 定时清理。

#### 5.4.2 关键改造点

- **新增**：`backend/alembic/versions/xxxx_create_audit_log.py` 迁移。
- **新增**：`backend/app/services/audit/audit_service.py`（Write API）。
- **新增**：`backend/app/core/audit_decorator.py`：`@audit("task.cancel", resource_type="task", resource_id_arg="task_id")` 装饰器。
- **接入**：login/logout、user CRUD、task create/cancel、settings change、credential bind/unbind/probe、role change、知识库写 / 删。
- **新增**：`GET /admin/audit-logs?actor=&action=&resource=&from=&to=&page=&size=`。
- **新增**：`backend/app/infrastructure/queue/tasks/audit_retention.py`（ARQ cron，每日清过期）。
- **前端**：`settings/page.tsx` 新增 `AuditSection` Tab（admin only），列表 + 筛选 + 详情抽屉。

#### 5.4.3 验收

- 任意被装饰动作执行后，`redmuse_audit_log` 出现记录。
- admin 能查到 90 天内记录；非 admin 接口 403。
- 定时任务执行后，`at < now() - 90d` 的记录被物理删除（test：mock 时间 + 断言）。
- 装饰器异常不阻断主流程（审计失败只记 WARN 日志）。


## TaskContext 与错误码新增项

```text
xhs_auth_status:
  status: ready | not_bound | expired | skipped
  owner_user_id: <RedMuse user_id>
  xhs_user_id: <XHS user_id>
  source: credential | legacy_username | admin_fallback | env_override | env_compat
  credential_ref: <relative path>
  checked_at: ISO 时间
  requires_user_action: bool
  message: 文本

ErrorCode:
  AUTH_XHS_NOT_BOUND           （Phase 2 引入；任务侧唯一授权拦截错误码）

非任务 ErrorCode 的授权会话错误：
  SMS_PROVIDER_NOT_CONFIGURED
  PHONE_ACQUIRE_FAIL / PHONE_ACQUIRE_TIMEOUT
  SMS_TIMEOUT / SMS_FETCH_FAIL
  CAPTCHA_REQUIRED / LOGIN_REJECTED / SELFINFO_INVALID
```

## 风险与边界

- **任务卡住**：不在 `XhsAuthAgent` 内做自动续登，避免任务等待 5–8 分钟；未授权直接失败并提示用户。
- **Cookie 串号**：设置页绑定/刷新后必须 selfinfo 校验，与当前 RedMuse 用户绑定；任务侧只消费已校验状态。
- **Cookie 明文**：第一版仍是文件存储，Phase 5 加密。
- **兼容现有数据**：Phase 0 把旧 `datas/users/admin/cookies.json` 挂到新 admin，无需重新扫码。
- **非 XHS 功能解耦**：知识库、历史、设置、对话不依赖 XHS Cookie。
- **RedNote 国际站**：默认登录入口和 selfinfo host 必须保持国际站配置；避免拿到 guest 身份。
- **全局 fallback**：`ALLOW_ADMIN_COOKIE_FALLBACK` 默认关闭，只有显式排障时允许，避免多用户串号。

## 推荐实施顺序

```text
Phase 0 (最小登录) → Phase 1 (Credential 解耦) → Phase 2 (XhsAuthAgent 拦截) →
Phase 3a (SMS adapter) → Phase 3b (用户触发 SMS 登录会话) → Phase 3c (设置页数据源授权 UI) →
Phase 4a (任务授权提示) → Phase 4b (登录/授权语义收口) →
Phase 5.0 (Schema 决策) → 5.1 (PG UserStore) → 5.2 (Role Matrix) →
5.3 (Resource Ownership) → 5.4 (Audit)
```

每完成一个阶段就联调一次，避免一次性大改导致回归不可控。
