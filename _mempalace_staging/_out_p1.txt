# [MemPalace 归档稿] RedMuse RBAC + XHS 授权 / XhsAuthAgent

> 来源：docs/redmuse-rbac-xhs-auth-agent-61bb13.md（归档时已剔除 Phase 5.5 整节、阶段表 5.5 行、推荐顺序中的 5.5 尾注，并从「明确不做」一句中去掉与第三方身份联邦相关的字样）

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
| Phase 5 | 完整 RBAC + 用户管理 | 高 | 细分 5.0–5.4 |

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

---

（以下为 Phase 5 续篇，见同 wing auth 下第二条抽屉 redmuse-rbac-phase5）
