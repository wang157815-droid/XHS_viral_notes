# 小红书数据采集稳定性修复方案（v3：采集节点下沉 + 活浏览器 cookie + 风控自愈）

## Context（背景与问题）

**现状**：单账号一天采不到 100 条，采集"时好时坏"。目标：**单账号稳定几百条/天**。

**已实证根因（代码核对，新系统扫码登录流程同样如此）：**
1. **Cookie 喂死值**：扫码成功后把 cookie 存成静态字符串（`qrcode_login_service.py:1345-1346` → `auth_orchestrator._save_cookie_for_identity` 写 `datas/users/<id>/cookies.json`）、**随即关浏览器**（`:1372 sleep → :1574-1579 context.close()`）；采集用 `requests` 裸发喂这个死 cookie，不接小红书 `Set-Cookie` 轮换，放久即失效。
2. **失败硬中止**：`CaptchaError`(461/471) 在 `viral_collector.py:155-157,688-690` 直接 raise 中止整任务，无退避/重试。
3. **签名静默降级**：`xhshow` 失败时悄悄降 execjs（`xhs_util.py:154-192`）→ 小红书返回 `data:{}`，流程不报错继续凑不满。

**对标 MediaCrawler（源码核对）**：请求库都是 http 库裸发、签名都是同一个 `xhshow`——**签名不是瓶颈**。它赢在 ① cookie 从**活浏览器上下文实时取**、② 失败 `@retry` 重试、③ CDP 复用真实 Chrome 降风控。

**判断**：修 ①② = 复刻 MediaCrawler 稳定性来源；③ 在住宅IP节点上可用，作为最强增强。

## 已确认决策（与用户对齐）

| 决策 | 选择 |
|------|------|
| **采集 IP** | **下沉到住宅 IP**——云端只跑 API/DB/前端，采集+浏览器跑住宅 IP 节点 |
| **采集节点** | **家用 Windows 即采集机**（住宅 IP + 可用真实 Chrome → CDP 可用，抗风控最强） |
| 代理 | **暂不用**（住宅 IP 已干净，几百条/天单 IP 足够；`proxies` 仅预留） |
| 账号池 | 暂缓（养号成本高），仅留接口 |
| 路线 | 活浏览器 cookie + 风控自愈优先；CDP 连真实 Chrome 作可选增强 |

---

## 部署架构：采集节点下沉（关键，绕开数据中心 IP 风险）

```
┌─── 云服务器（机房IP，不碰采集）───┐        ┌─── 家用 Windows（住宅IP，采集机）───┐
│  FastAPI API (8100)              │        │  ARQ Worker                          │
│  Postgres + pgvector             │◀──────▶│   python -m backend.app...queue.runner│
│  Redis（ARQ broker + SSE）       │ 安全隧道 │   ├ CrawlerAgent（住宅IP出口）        │
│  前端 Next.js (3000)             │ /VPN    │   ├ LiveCookieProvider（真实浏览器）  │
│  用户请求 → enqueue 采集任务     │        │   └ Playwright/真实Chrome（可CDP）   │
└──────────────────────────────────┘        └──────────────────────────────────────┘
```

**桥接已基本现成（复用已有 ARQ 队列，计算侧零新建）：**
- `backend/app/infrastructure/queue/runner.py` 是独立可启动 worker，**已内置 Windows `ProactorEventLoopPolicy`**（Playwright 所需）。
- Redis 连接全 env 驱动（`client.py:28-36`：`REDIS_HOST/REDIS_PORT/REDIS_ARQ_DB/password`）。
- 云端设 `TASK_RUNNER=arq`（API enqueue 而非 inprocess）；家用机跑 worker，`REDIS_HOST=<云IP>` 指向云端 Redis 即接通。

**两项部署前置（本方案需补的运维项）：**
1. **安全网络通道**：家用 worker 需访问云端 Redis（+ Postgres）。**禁止裸暴露**——用 WireGuard VPN 或 SSH 隧道；Redis/PG 设强密码。备选：worker 不直连 DB，改为**结果经云端 HTTP API 回传**（更安全，但需补回调路径）。
2. **共享状态须在云端**：任务/上下文/结果须落云端 Redis/Postgres，家用 worker 与云端 API 才能共享。当前部分存本地 JSON（`datas/tasks/*.json`）——**采集下沉要求这些状态走云端 PG/Redis**（与既有"JSON→PG 迁移"技术债重叠，本方案将相关 store 切到 PG，或走 API 回传规避）。

**收益**：采集走住宅 IP + 真实浏览器，数据中心 IP 风险被架构消除，"暂不用代理"成立，且可启用 CDP 连真实 Chrome（最强抗风控）。

---

## 实施方案

### 阶段 0：诊断先行（~0.5 天）
只观测不改行为。采集关键路径加结构化计数器（`loguru`）分类每次请求：`ok_with_data / soft_block(data:{}) / captcha_461 / sign_degraded / cookie_invalid / timeout_5xx`。埋点：`xhs_pc_apis.py` 471/461 与 `data:{}` 分支、`xhs_util.py` execjs 降级分支、`viral_collector.py:676-686,771,842`。在家用机跑一个真实关键词任务，输出「采集健康报告」（按关键词/页/维度汇总 + 最终条数），确认瓶颈。

### 阶段 1：活浏览器 Cookie 提供器（~1.5 天）— 核心招，复刻 MediaCrawler
1. 新增 `LiveCookieProvider`：复用现有 `launch_persistent_context(browser_data/<identity>)`（登录态仍在 profile，**无需重扫**）+ `stealth.js`（`:361`）+ `_warmed_browser` 预热。打开页面 `goto xiaohongshu.com` 触发真实访问 → 小红书下发 `Set-Cookie` 轮换 → `context.cookies()` 取**刚刷新**的 cookie。**家用机有显示器，可 headful**（更不易被检测）。
2. **采集 cookie 来源切换**：`credential_resolver`/`crawler_agent.py:629,1505` 优先调 `LiveCookieProvider.get_fresh_cookies()`，失败（Playwright 不可用）回退静态 cookies.json，不退化。
3. **周期刷新 + 写回**：长任务每 5-8 分钟或每 M 次请求重取一次，并回写 cookies.json。
4. **成本控制**：浏览器只管周期取 cookie，高频数据请求仍走 `requests`（同 MediaCrawler 架构）。
5. **可选增强**：`LiveCookieProvider` 支持 CDP 模式（连家用机真实 Chrome 9222 端口），抗风控拉满。

### 阶段 2：风控自愈（~1.5 天）
1. **统一请求封装**：`xhs_pc_apis.py` 抽 `_request()`，集中分类 `471/461→CaptchaError`、`data/items 空→SoftBlockError`、`超时/5xx→Retryable`。
2. **退避重试替代硬中止**：`viral_collector.py:155-157,688-690` 的 `CaptchaError/SoftBlock` 改为**指数退避+jitter 重试**（5s/15s/45s，最多 N 次，引入 `tenacity`），**重试前先调 `LiveCookieProvider` 重取 cookie**（软封换新 cookie 常即恢复）。保留详情层 HTML 兜底（`:773-783`）。
3. **禁止静默降 execjs**：`xhs_util.py:generate_headers` 失败分支默认告警 + 标记签名不健康，开关 `XHS_ALLOW_EXECJS_FALLBACK`（默认 false）。
4. **节奏参数化 + 降速**：硬编码延时（`viral_collector.py:357,519,1018,1025`）抽 `COLLECTOR_*_SLEEP` env，默认略放大+jitter；并发 `Semaphore(3)→2`。
5. **断点续采**：复用 `TaskCheckpoint`（`viral_agent/task/models.py`，已含 keyword/dimension/page index）。

---

## 预留（不实现，留接口）
- **账号池 + 代理池**：多 profile 养号 + `CredentialPoolSelector`（复用 `cookie_health_service`）；`proxies` 透传（形参已就绪）。扩量上千条/天再上。
- 注：账号池本质是养号系统（持续保活 + 补扫死号）。

---

## 关键文件与复用件
**改动（阶段0-2）**：`qrcode_login_service.py`（抽"开持久化context+取cookie"给 Provider）、新增 `LiveCookieProvider`、`credential_resolver.py`+`crawler_agent.py:629,1505`（cookie 来源切换）、`xhs_pc_apis.py`（`_request()` 封装）、`xhs_util.py:154-192`（禁静默降级）、`viral_collector.py`（退避/重取/续采/埋点/参数化）。
**部署（无需写代码，配置 + 运维）**：云端 `TASK_RUNNER=arq`；家用机跑 `queue/runner.py`、`REDIS_HOST` 指云端；WireGuard/SSH 隧道；相关 store 切云端 PG/Redis。
**直接复用**：`launch_persistent_context`/`browser_data/`/`stealth.js`/`_warmed_browser`、ARQ `runner.py`（Windows 就绪）、`cookie_health_service`、`TaskCheckpoint`、`loguru`。

---

## 对用户质疑的回答
- **"MediaCrawler 签名最新还能爬，我的不行？"**：签名是同一个 xhshow、请求都是 http 库裸发——签名不是差异。它赢在 cookie 从活浏览器现取 + 失败重试。你的新系统虽扫码登录，cookie 拿到后照样存死值、关浏览器，所以照样过期、一抖就废。阶段1/2 正是补这两点。
- **"云服务器部署考虑了吗？"**：考虑了——云端机房 IP 是采集大忌，故采纳"采集下沉到家用住宅 IP"。计算桥接复用已有 ARQ worker（已 Windows 就绪），仅需补安全隧道 + 共享状态上云。

---

## Verification
1. **阶段0**：家用机跑真实任务 → 「采集健康报告」→ 确认失败分类。
2. **阶段1**：对比"静态cookie" vs "live现取" 连续采集 30 分钟的 `cookie_invalid/soft_block` 率与总条数。
3. **阶段2**：同关键词修复前后各跑 3 次，对比成功条数/软封次数/中止次数。目标：单账号单任务稳定达标、一天几百条不中断。
4. **部署联调**：云端 enqueue → 家用 worker（指向云端 Redis）执行 → 结果回云端可见；验证 SSE 进度、隧道连通、Windows worker 正常起。
5. **单测**（复用 `backend/tests/conftest.py`）：`_request()` 分支、退避重试、`LiveCookieProvider` 取/刷/回退、断点续采。
6. **回归**：两后端共用 `viral_collector`，跑 `backend/tests/test_crawler_*`。

## 风险与边界
- **采集机必须常开**：家用 Windows 关机则采集停（可设唤醒/常驻；或保留云端 headless 降级路径，但 IP 风险回归）。
- **网络通道安全**：Redis/PG 经隧道/VPN，切勿裸暴露公网。
- **共享状态上云**：本地 JSON store 在下沉架构下不可用，须切 PG/Redis 或 API 回传。
- **不突破账号级硬上限**：阶段0 若证明账号被降权，提前进账号池。
- **execjs 兜底关闭**：xhshow 失效将 fail-loud 报错而非静默跑空（有意为之，便于发现签名过时）。
