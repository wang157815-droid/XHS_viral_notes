# RedMuse RBAC（最小版）+ XHS 数据源授权 / XhsAuthAgent Gate 方案

本方案先用一个最小可用的登录框架替换现有「XHS 扫码即系统登录」入口，然后把小红书授权收敛到「设置页 / 数据源授权」中管理；任务执行链路只在 `Crawler` 前插入轻量 `XhsAuthAgent` Gate，用来判断当前 RedMuse 用户是否已有可用 XHS Cookie。若未授权或已失效，任务立即失败并提示用户去设置页授权；不在任务执行过程中自动拉起虚拟号、短信、Playwright 等耗时授权流程。待这条边界稳定后，再扩展完整 RBAC。

## 关键决策

- **目标**：系统功能是「RedMuse 用户登录 → 绑定小红书数据源 → 生成爆文模型」。Phase 0 不做完整 RBAC，只做最小登录框架，Phase 5 才做完整 RBAC。
- **登录强度（Phase 0）**：`JSON 文件多账号 + bcrypt + JWT`。理由：
  - 单账号硬编码限制 admin/user 联调；
  - 复用 `identity_store` 改造工作量大，会拖慢 XhsAuthAgent 验证节奏；
  - JSON + bcrypt 不绑死后续 RBAC 设计，迁移到 PostgreSQL 用户表只需替换存储层。
- **现有 admin 数据**：合并迁移。新建 RedMuse admin 用户时，把 `datas/users/admin/cookies.json` 直接作为它的 XHS Cookie 引用，避免调试期重新扫码。
- **小红书授权入口**：只放在设置页「数据源授权」中，支持扫码 / 自动 SMS 重新授权 / 粘贴 Cookie 排障 / 刷新状态 / 立即检查。
- **Agent 拓扑**：`InputParser → XhsAuthAgent → Crawler → ...`
- **XhsAuthAgent 边界**：只做快速凭据 Gate，不触发自动授权、不等待短信、不启动浏览器、不做长轮询。
- **任务失败策略**：Cookie 未绑定、文件不存在、状态已过期或不可用时，任务停止并返回 `AUTH_XHS_NOT_BOUND`，前端提示用户去设置页授权数据源，授权后重新发起任务。
- **身份模型解耦**：RedMuse JWT ≠ XHS Cookie；RedMuse user_id ≠ XHS user_id。
- **当前已完成的关键优化**：RedNote 国际站 host 选择、selfinfo GET 签名、登录成功判定、SMS 取码 10 秒轮询、凭据健康状态 `valid → active` 映射、设置页「刷新状态 / 立即检查」独立 loading 与直连状态更新。

## 阶段总览

| Phase | 内容 | 复杂度 | 是否细分 |
|-------|------|--------|----------|
| Phase 0 | 最小登录框架（JSON 多账号 + JWT） | 中低 | 不细分 |
| Phase 1 | 语义解耦 + XhsCredentialStore | 中 | 不细分 |
| Phase 2 | XhsAuthAgent 快速 Gate（只校验，不自动授权） | 中 | 不细分 |
| Phase 3 | 设置页数据源授权 + 自动 SMS 重新授权（用户触发） | 高 | 细分 3a/3b/3c |
| Phase 4 | 任务侧授权提示 + 前端收口 | 中 | 细分 4a/4b（✅ 已完成） |
| Phase 5 | 完整 RBAC + 用户管理 | 高 | 细分 5.0–5.4（5.5 延后） |

## Phase 0：最小登录框架

### 0.1 目标

- `/auth/login` 用用户名 + 密码登录，发 RedMuse JWT。
- `/auth/me` 返回当前 RedMuse 用户信息（user_id / username / role）。
- 不再用 `/auth/xhs-login/session` 做系统登录；小红书授权入口后续收敛到设置页「数据源授权」。
- 至少有 1 个 admin + 可创建普通 user。
- 兼容现有 `datas/users/admin/cookies.json` 作为 admin 的 XHS Cookie。

### 0.2 关键改造点

- **新增**：`backend/app/services/redmuse_auth/`
  - `user_store.py`：JSON 用户表读写（`datas/redmuse_auth/users.json`）。
  - `password_hash.py`：bcrypt 包装。
  - `auth_service.py`：登录校验、JWT 签发、`get_current_user` 已存在，只需对接。
- **新增**：`backend/app/api/routes/auth.py` 增加 `POST /auth/login`、`POST /auth/users`（admin 创建）、`GET /auth/users`（admin 列表）。
- **修改**：原 `/auth/xhs-login/session` 路由暂时保留但不再发 RedMuse JWT；Phase 2 改为 XHS 授权专用。
- **修改**：`security.create_access_token` 不再消费 XHS selfinfo 字段，只消费 RedMuse 用户字段。
- **种子数据**：首次启动若 `users.json` 不存在，用 env `REDMUSE_BOOTSTRAP_ADMIN_USER` / `REDMUSE_BOOTSTRAP_ADMIN_PASSWORD` 初始化第一个 admin，并在该用户下挂载 `xhs_credential_path = "datas/users/admin/cookies.json"`。
- **前端**：`frontend/src/app/login/page.tsx` 加用户名 + 密码表单；扫码 / SMS / Cookie 绑定入口后续迁移到设置页「数据源授权」。

### 0.3 数据结构

`datas/redmuse_auth/users.json`：

```json
{
  "version": 1,
  "users": [
    {
      "user_id": "u_admin_001",
      "username": "admin",
      "nickname": "管理员",
      "password_hash": "$2b$12$...",
      "role": "admin",
      "status": "active",
      "xhs_credential_path": "datas/users/admin/cookies.json",
      "created_at": "2026-05-08T...",
      "last_login_at": null
    }
  ]
}
```

### 0.4 验收

- `curl /auth/login` 返回 JWT，前端可登录到工作台。
- 工作台/历史/设置不再依赖 XHS Cookie 即可访问（除运行任务）。
- admin 调用现有 Crawler 仍可用（因为 `xhs_credential_path` 指向旧 cookies.json）。

## Phase 1：语义解耦 + XhsCredentialStore

### 1.1 目标

- 引入 `XhsCredential` 概念，把「该 RedMuse 用户的小红书外部授权」从 `identity_store` / `cookie_health_service` 内的「XHS selfinfo 解析」彻底拆出。
- `CrawlerAgent` 明确只用当前任务 owner 的 XHS Cookie，不再 admin fallback（默认关闭）。
- 不引入新 Agent，不改编排拓扑。

### 1.2 关键改造点

- **新增**：`backend/app/services/xhs_auth/credential_store.py`
  - `get_credential(redmuse_user_id) -> XhsCredential | None`
  - `save_credential(redmuse_user_id, cookie_str, xhs_user_id, xhs_nickname)`
  - `update_status(...)`
  - 第一版仍然落到 `datas/users/<redmuse_username>/cookies.json`，但通过 `users.json.xhs_credential_path` 间接寻址。
- **新增**：`backend/app/services/xhs_auth/credential_health.py`
  - `check(redmuse_user_id) -> XhsCredentialStatus`
  - 复用现有 selfinfo 调用逻辑。
- **修改**：`credential_health.check(redmuse_user_id)` 封装 `cookie_health_service` 的 selfinfo 检查结果，并负责写回 `XhsCredentialStore`；避免业务层直接按 username fallback 推断 Cookie。
- **修改**：`crawler_agent._resolve_cookies_str(owner_user_id)` 改为优先从 `XhsCredentialStore` 取；`ALLOW_ADMIN_COOKIE_FALLBACK` 默认关闭，只有显式开启才走旧路径。
- **域模型**：新增 `backend/app/domain/xhs_credential.py`（`XhsCredentialStatus = active | expired | expiring_soon | unknown | unbound`，底层健康检查返回 `valid` 时必须标准化为 `active`）。

### 1.3 验收

- 切换不同 RedMuse 用户登录后，`GET /xhs-auth/credential/status`（新接口）返回各自的 cookie 状态。
- admin 跑任务仍正常（cookies.json 通过 credential 间接读到）。
- 普通 user 在没有 XHS Cookie 的情况下，跑任务到 Crawler 阶段会显式失败（Phase 2 改为 XhsAuthAgent 拦截）。

## Phase 2：XhsAuthAgent 快速 Gate（只校验，不自动授权）

### 2.1 目标

- 在编排里插入 `XhsAuthAgent`，位于 `InputParser` 与 `Crawler` 之间。
- 从任务记录解析当前 `owner_user_id`，确保校验的是当前 RedMuse 用户绑定的 XHS 数据源。
- Cookie 可用 → 写 `xhs_auth_status` → 放行 Crawler。
- Cookie 缺失/过期/不可用 → 任务失败，错误码 `AUTH_XHS_NOT_BOUND`，前端展示「请前往设置页授权小红书数据源」。
- **明确不实现任务内自动续登**：不调用 hero-sms、不启动 Playwright、不等待验证码、不在任务里做长时间阻塞。

### 2.2 关键改造点

- **新增**：`backend/app/application/agents/xhs_auth_agent.py`
- **修改**：`backend/app/application/orchestrator.py` 与 LangGraph engine 加入新阶段。
- **新增/使用**：`backend/app/domain/error_codes.py` 中的 `AUTH_XHS_NOT_BOUND`。
- **新增**：`TaskContext` 分区 `xhs_auth_status`。
- **校验定义**：
  - 必须能通过 `XhsCredentialStore` / `XhsCredentialResolver` 找到当前 RedMuse 用户的 Cookie；
  - `cookies_path` 指向的文件必须存在且能解析出非空 Cookie；
  - store 状态为 `expired` / `unbound` / 明确不可用时直接拦截；
  - 任务链路默认不做实时 selfinfo 远程检查，实时健康检查由设置页「立即检查」触发并写回 store。
- **前端**：工作台运行任务时如果收到 `AUTH_XHS_NOT_BOUND`，弹出「请前往设置授权小红书数据源」。

### 2.3 验收

- 全新 RedMuse user 第一次跑任务，会被 `XhsAuthAgent` 拦截并提示授权。
- 设置页完成授权并健康检查为 `active` 后，再次跑任务可以走通完整流水线。
- admin 已绑定 XHS Cookie 且状态可用时，任务无感跑通。
- Cookie 失效时任务快速失败，不出现 5–8 分钟自动续登等待。

## Phase 3：设置页数据源授权 + 自动 SMS 重新授权（用户触发）

### 3.1 总目标

- 小红书授权能力统一放在设置页「数据源授权」中，作为 RedMuse 用户主动维护数据源的入口。
- 支持扫码绑定、自动 SMS 重新授权、粘贴 Cookie 排障、解绑、刷新状态、立即检查。
- 授权成功后写入当前 RedMuse 用户的 `XhsCredentialStore`，后续任务由 `XhsAuthAgent` 快速读取并放行。
- **不让任务执行链路承担自动授权职责**：任务只消费已准备好的数据源，不负责创建/刷新数据源。

### Phase 3a：SMS Provider 适配器（hero-sms）

本期只接一家：**hero-sms**。抽象 provider 接口便于后续替换平台，但首版只保证 hero-sms 可用。

#### 3a.1 文件

```text
backend/app/services/xhs_auth/sms_provider.py          SmsProvider ABC + 领域类型
backend/app/services/xhs_auth/hero_sms_provider.py     hero-sms 实现
backend/app/services/xhs_auth/sms_login_service.py     用户触发的 SMS 登录会话编排
backend/app/services/xhs_auth/sms_login_driver.py      登录 driver 协议
backend/app/services/xhs_auth/playwright_sms_login_driver.py
```

#### 3a.2 Provider 接口语义

```text
purchase_phone() / acquire_phone:
  功能：取号，按照 provider 内部轮询策略尝试直到拿到号或超时。
  返回：PhonePurchase(order_id, country_code, national_number, phone_raw, ...)
  失败：无库存 / 余额不足 / 鉴权失败 / 超时 / 网络异常。

fetch_sms_code(order_id):
  功能：按照 provider 内部轮询策略取码，收到非空验证码即返回。
  返回：SmsCodeResult(code, received_at, raw)
  失败：SMS_TIMEOUT / 响应格式异常 / 订单取消 / 网络异常。

release_phone(order_id, status):
  功能：hero-sms 当前无需主动释放，默认 no-op；接口保留。
```

#### 3a.3 hero-sms 实现规格

- **Base URL**：`https://hero-sms.com/stubs/handler_api.php`
- **认证**：Query 参数 `api_key`，值取自 env `SMS_PROVIDER_API_KEY`。
- **服务**：`service=qf`
- **地区**：`country=14`（Hong Kong）
- **最高价格**：`maxPrice=1`

**取号请求**：

```text
GET /stubs/handler_api.php
Query: action=getNumber, service=qf, country=14, maxPrice=1, api_key=<env>
Headers: Accept: text/plain
```

**取号轮询策略**：

```text
SMS_PROVIDER_PHONE_POLL_INTERVAL_SEC = 8
SMS_PROVIDER_PHONE_POLL_TIMEOUT_SEC  = 120
```

**取码请求**：

```text
GET /stubs/handler_api.php
Query: action=getAllSms, id=<order_id>, api_key=<env>
Headers: Accept: application/json
```

**取码响应兼容**：

- JSON：`{"data": [{"code": "167446", "service": "qf", ...}]}`
- 文本：`STATUS_OK:<code>`
- 等待中：`STATUS_WAIT_CODE` / `STATUS_WAIT_RETRY` / `WAIT_SMS` 等继续轮询。
- 未知等待态按 lenient 策略继续轮询到 deadline，避免「点完获取验证码立刻失败」。

**取码轮询策略（当前优化后默认值）**：

```text
SMS_PROVIDER_SMS_POLL_INTERVAL_SEC = 10
SMS_PROVIDER_SMS_POLL_TIMEOUT_SEC  = 300
```

#### 3a.4 环境变量

```text
SMS_PROVIDER_API_KEY=...                    # 必填
SMS_PROVIDER_BASE_URL=https://hero-sms.com/stubs/handler_api.php
SMS_PROVIDER_SERVICE=qf
SMS_PROVIDER_COUNTRY=14
SMS_PROVIDER_MAX_PRICE=1
SMS_PROVIDER_PHONE_POLL_INTERVAL_SEC=8
SMS_PROVIDER_PHONE_POLL_TIMEOUT_SEC=120
SMS_PROVIDER_SMS_POLL_INTERVAL_SEC=10
SMS_PROVIDER_SMS_POLL_TIMEOUT_SEC=300
```

#### 3a.5 pytest 覆盖范围

- hero-sms 返回 `ACCESS_NUMBER:123:85257419261` → 成功解析。
- hero-sms 连续 `NO_NUMBERS` → 继续轮询直到成功或超时。
- hero-sms 返回 `NO_BALANCE` / `BAD_KEY` → 明确失败。
- 取号超时 → 抛超时错误。
- 取码 `data=[]` 或等待态 → 按 10 秒间隔继续轮询。
- 取码首次返回 `code=167446` → 成功返回。
- JSON / 文本两种响应格式都能解析。
- 网络异常 / 超时 → 重试或按 deadline 失败。

### Phase 3b：用户触发的 XHS SMS 登录会话

- **入口**：`POST /xhs-auth/sms-login/session`
- **查询**：`GET /xhs-auth/sms-login/session/{id}`
- **取消**：`DELETE /xhs-auth/sms-login/session/{id}`
- **绑定**：`POST /xhs-auth/sms-login/session/{id}/bind`
- **执行边界**：只能由当前已登录 RedMuse 用户在设置页触发，会话归属必须校验 `redmuse_user_id`。
- **串联步骤**：
  1. `HeroSmsProvider` 取号。
  2. `PlaywrightSmsLoginDriver` 打开 `https://www.rednote.com/explore`。
  3. 切换区号到「香港 +852」并填入手机号。
  4. 点击「获取验证码」。
  5. provider 每 10 秒轮询验证码。
  6. Playwright 填码并提交。
  7. 等待登录成功，收集 RedNote/API 域可用 Cookie。
  8. 调用 selfinfo 校验，确认不是 guest。
  9. `CredentialBinder` 写入当前 RedMuse 用户的 `XhsCredentialStore`。
- **RedNote 国际站要求**：
  - 登录入口默认 `https://www.rednote.com/explore`；
  - API base 使用 `https://webapi.rednote.com`；
  - selfinfo GET 请求必须按 GET 签名；
  - 登录成功判断必须排除 login form 仍可见的过渡态 / guest cookie。

### Phase 3c：设置页数据源授权 UI

- `frontend/src/app/(app)/settings/page.tsx` 增加/完善「数据源授权」面板：
  - 当前授权状态；
  - 绑定的小红书昵称；
  - XHS user_id；
  - 状态描述；
  - 上次校验时间；
  - 自动 SMS 重新授权入口；
  - 粘贴 Cookie 绑定排障入口；
  - 解绑；
  - 刷新状态；
  - 立即检查。
- `刷新状态`：调用 `GET /xhs-auth/credential`，只读取当前 store 状态，不触发远程健康检查。
- `立即检查`：调用 `GET /xhs-auth/credential/status?force=true`，强制 selfinfo 健康检查并把结果写回 store。
- `/xhs-auth/credential/status` 返回中必须包含最新 `credential`，前端可直接更新 UI。
- 前端操作 loading 使用独立 action 状态：
  - `refresh` → `刷新中…`
  - `probe` → `检查中…`
  - `unbind` → `解绑中…`
  - `bind` → `绑定中…`

### 3.x 验收

- 未绑定用户在设置页能看到 `unbound`，并能通过 SMS 自动授权绑定成功。
- 授权成功后能显示 XHS 昵称、XHS user_id、状态描述和上次校验时间。
- `刷新状态` 不发起 selfinfo，只刷新 store 当前状态。
- `立即检查` 触发 selfinfo，后端把 `valid` 标准化为 `active` 并返回最新 `credential`。
- 解绑后再次跑任务会被 `XhsAuthAgent` 快速拦截。
- SMS 平台无号 / 无余额 / 取码超时 / 风控验证码时，设置页展示失败原因，不影响正在运行的任务队列。

## Phase 4：任务侧授权提示 + 前端收口（✅ 已完成）

### Phase 4a：工作台任务授权提示 ✅

- 工作台 SSE 任务返回 `AUTH_XHS_NOT_BOUND` 时展示专属提示卡片：「小红书数据源需要授权」与跳转「设置 → 数据源授权」的按钮（错误代码为唯一跳转错误码）。
- 任务里程碑气泡与推送状态文案区分 `AUTH_XHS_NOT_BOUND` 与通用失败，连同 `cookieBlocked` 提示一起提供 `设置 → 数据源授权` 锚点跳转。
- `chat-panel.tsx` + `settings/page.tsx#xhs-credential` 锚点联调，不为这个错误码引入任何任务内授权等待 / `WAITING_AUTH` / resume 路径。

### Phase 4b：登录页与授权页语义收口 ✅　（3 个子阶段）

- **4b-1　登录页瘦身** ✅
  - `frontend/src/app/login/page.tsx` 从 563 行瘦到 ≋ 134 行，只保留账号密码登录。
  - 删除「小红书扫码（旧版）」Tab、Tab 切换 UI、QR 会话创建 / 轮询 / SMS 验证码 / pagehide 取消会话等全部扫码逻辑代码。
  - 同步移除设置页 `XhsCredentialSection` 的「重新授权 → /login」死链 Row。
- **4b-2　设置页内嵌扫码授权面板** ✅
  - `lib/xhs-credential.ts` 加 `XhsQrLoginStatus`/`XhsQrLoginSessionDto`/`XHS_QR_FINAL_STATUSES` + `createXhsQrLoginSession` 等 4 个 client 函数 + `describeXhsQrLoginStatus` 状态文案。
  - `settings/page.tsx` 新增 `XhsQrLoginPanel` inline 组件：QR 截图、SMS 输入、刷新/取消/重置/绑定动作；卸载时自动 `cancelXhsQrLoginSession`；绑定调用 `bindXhsFromQrSession` 命中 `/xhs-auth/bind/from-session`。
  - `XhsCredentialSection` 三入口成形：扫码 / SMS / 粘贴 Cookie，设置页成为数据源授权唯一入口。
- **4b-3　清理与文档收口** ✅
  - 删除 `auth-storage.ts` 中 `LAST_XHS_USER_ID_KEY` 常量与所有占用。
  - 删除 `session-context.tsx` 中所有 `redmuse_last_xhs_user_id` 读写与 `clearFallbackSession` 辅助函数，清除 Phase 0 过渡注释。
  - 同步更新 `AGENTS.md`：登录页职责描述 + Cookie 管理区块说明 XHS 授权统一在「设置 → 数据源授权」。
  - **后端保留**：`/auth/xhs-login/session` GET 仍接受 `X-Redmuse-Previous-User-Id` 头，空值为无操作，兼容旧前端。Phase 5 RBAC 重构时再评估是否进一步移除。

## Phase 5：完整 RBAC + 用户管理（5.0–5.4 必做；5.5 延后）

**范围决策（2026-05-10）**：启用 5.0–5.4 为 Phase 5 必做项，总耗时 3–5 天；SSO 5.5 延后评估。

**前瞻性约束（2026-05-10 二次澄清）**：未来 UI 会**视觉模仿** ChatGPT-web，但**仅可能引入"左侧会话/项目树"一项**。**明确不做**多工作区 / 组织 / 团队席位 / 公开分享链接 / API Token / OAuth / 订阅分层 / 配额计费等多租户能力。因此 Phase 5 RBAC **不引入** workspace 原语 / capability policy 引擎 / share_token / api_token / 轻 JWT claims；继续沿用「user × 三档 role × owner_user_id 过滤 + admin 穿透」的最小授权模型。**唯一前瞻**：5.1 顺手把 `redmuse_projects` 空表与 `conversations.project_id` / `tasks.project_id` nullable 列建好，等 UI 重做期补 CRUD 即可，避免再跑一次 DDL（详见 5.0 决策点 E'）。

### 阶段总览

| 子阶段 | 内容 | 耗时 | 产物类型 |
|---|---|---|---|
| 5.0 | Schema 设计与决策对齐（含 projects 预留） | 0.5 天 | 书面规划（本文档） |
| 5.1 | PostgreSQL 用户表 + projects 预留表落地 + 存储层迁移 | 1 天 | 代码 + 迁移脚本 |
| 5.2 | 角色矩阵（admin/analyst/viewer）+ `require_role` Dependency | 0.5–1 天 | 代码 |
| 5.3 | 细粒度资源归属（tasks / knowledge / settings 行级过滤） | 1 天 | 代码 |
| 5.4 | 操作审计日志（表 + 装饰器 + admin 查询接口 + 前端 Tab） | 1 天 | 代码 + 前端 |
| 5.5 | SSO（飞书 / 企业微信） | 延后 | —— |

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

### Phase 5.5：SSO（延后评估）

Phase 5 必做项完成后再评估。预留接入点：

- `core/security.py` 的 token 解析保持现 `token_type` 字段，SSO 接入时新增 `token_type=feishu | wxwork`。
- `users` 表可扩列 `sso_provider` / `sso_subject` 作外部身份映射。

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
5.3 (Resource Ownership) → 5.4 (Audit) → [5.5 SSO 延后]
```

每完成一个阶段就联调一次，避免一次性大改导致回归不可控。
