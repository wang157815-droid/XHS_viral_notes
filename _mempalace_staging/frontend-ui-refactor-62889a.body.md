# [MemPalace 归档稿] RedMuse 前端 UI 重构计划

> 来源：`docs/frontend-ui-refactor-62889a.md`（全文归档）

# RedMuse 前端 UI 重构计划

本计划以循序渐进的方式，将当前 Next.js 前端改造成接近 `RED-MUSE-` 参考项目的视觉风格，同时保留现有业务逻辑、API 流程、RBAC 权限、SSE 任务流和小红书凭据能力。

## 核心原则

- **保留业务行为**：继续使用 `SessionProvider`、`WorkspaceProvider`、RBAC `can(...)`、对话流、任务 SSE、Canvas 适配器、设置页 API、历史中心和知识库流程。
- **只参考 UI 语言**：只迁移 `RED-MUSE-` 的视觉风格、布局模式和交互方式，不用 Vite 原型替换当前应用。
- **分阶段重构**：先做低风险的设计变量和应用外壳，再做工作区，最后逐步处理设置页、历史页和知识库页。
- **本阶段不改后端**：只适配现有前端行为和已有 API 契约。

## 参考 UI 方向

- **颜色体系**：纸张白 `#F7F7F5`、墨黑 `#1A1A1A`、浅苔绿 `#E8F0E8`、雾蓝绿 `#B9CED1`、浅雾灰 `#F0F0F0`。
- **字体气质**：正文使用清爽无衬线；品牌标题和大标题可使用衬线风格；日志、调试和代码类信息使用等宽字体。
- **布局感受**：编辑工作台、内容策展感、大留白、柔和卡片、玻璃面板、大圆角控件、轻微纸张质感。
- **交互方式**：可折叠侧边栏、中央 Prompt 输入框、参数 chips、Agent 执行时间线、悬浮 Canvas 工具栏。

## 阶段 1：设计系统基础

可能涉及文件：

- `frontend/src/app/globals.css`
- `frontend/src/app/layout.tsx`
- 新增 `frontend/src/components/redmuse-ui/*`

工作内容：

- 在全局 CSS / Tailwind v4 中加入 RedMuse 设计变量。
- 新增可复用基础组件，例如 `SoftButton`、`IconButton`、`StatusPill`、`GlassPanel`、`SectionCard`、`EmptyState`。
- 字体先保持现状，除非确认引入标题衬线字体不会带来风险。
- 如实现需要，可再考虑加入 `lucide-react` 和 `motion`，但不作为第一步的强依赖。

验收重点：

- 应用可以正常构建，主要路由可以正常加载。
- 登录、会话、工作区状态不发生行为变化。

## 阶段 2：应用外壳与侧边栏

可能涉及文件：

- `frontend/src/app/(app)/layout.tsx`
- `frontend/src/components/layout/app-sidebar.tsx`
- `frontend/src/components/layout/page-header.tsx`

工作内容：

- 将整体外壳改成纸张白工作区背景和浅苔绿 / 浅雾灰侧边栏风格。
- 保持当前路由结构和 `WorkspaceProvider` 的位置不变。
- 重做侧边栏视觉：展开约 259px，收起约 76px，柔和菜单项，底部用户信息，小红书 Cookie 状态指示。
- 保留当前“新建分析”逻辑：继续使用 `/workspace?new=<timestamp>`。
- 保留工作区、历史中心、知识库、设置页导航，以及用户角色和 Cookie 状态展示。

验收重点：

- 侧边栏导航正常。
- 折叠状态正常保存。
- 用户角色和 Cookie 健康状态仍然正确显示。

## 阶段 3：工作区初始态与中央输入框

可能涉及文件：

- `frontend/src/app/(app)/workspace/page.tsx`
- `frontend/src/components/workspace/chat-panel.tsx`
- 新增 `frontend/src/components/workspace/prompt-composer.tsx`

工作内容：

- 将空白 / 欢迎态替换为参考项目风格的中央输入框。
- 将 `AdvancedConfig` 改成可展开的参数 chips：
  - `note_type`
  - `time_range`
  - `sample_count`
  - `viral_ratio`
- 保留现有 `handleSubmit`、会话创建、流式发送、`task_handoff` 和 RBAC 权限判断。
- 保持 viewer 只读逻辑不变。

验收重点：

- 用户发送 Prompt 后仍然会创建或使用会话。
- assistant 的 task handoff 仍然可以启动任务和 SSE 监听。
- 高级参数仍然通过现有 payload 传到后端。

## 阶段 4：工作区执行态与 Agent 时间线

可能涉及文件：

- `frontend/src/components/workspace/chat-panel.tsx`
- 新增 `frontend/src/components/workspace/agent-timeline.tsx`
- 原则上不改 `frontend/src/lib/sse/event-reducer.ts`；除非只是增加无害的 UI 派生字段。

工作内容：

- 将 `streamState.agentStatus`、`logs`、`progress`、`status` 渲染为参考项目风格的 Agent 执行时间线。
- 将后端 Agent 映射成用户可理解的阶段名称：
  - `InputParserAgent` → 意图解析
  - `XhsAuthAgent` → 数据源授权
  - `CrawlerAgent` → 小红书采集
  - `ImageAnalysisAgent` → 图文分析
  - `VideoAnalysisAgent` → 视频分析
  - `ViralModelAgent` → 爆文模型
  - `InsightAgent` / `RAGAgent` → 洞察增强
  - `CanvasRenderAgent` → 画布渲染
- 保留任务控制能力：暂停、恢复、取消、重连。
- 保留 `AUTH_XHS_NOT_BOUND` 授权提示，但改成更符合新视觉的授权卡片。

验收重点：

- SSE 进度可以正常显示。
- 错误和重连入口仍然可见。
- 任务控制按钮的权限限制仍然生效。

## 阶段 5：Canvas 工作台视觉升级

可能涉及文件：

- `frontend/src/components/workspace/canvas-view.tsx`
- `frontend/src/components/workspace/module-card.tsx`
- 可选新增 `frontend/src/components/workspace/canvas-toolbar.tsx`

工作内容：

- 将 Canvas 背景、模块卡片、Layer 标题、维度标签、导出 / 刷新按钮改成参考风格。
- 增加类似 `CanvasToolbar` 的悬浮工具栏，同时保留当前操作：
  - 刷新画布
  - 导出 Excel
  - 导出 JSON
- 保持 `adaptCanvas`、`paragraphEnv`、模块重新生成 / 删除 / 恢复、忙碌状态和段落反馈行为不变。

验收重点：

- 真实 `CanvasSchema` 仍然可以正常渲染。
- 模块操作仍然调用现有 API。
- 导出和刷新仍然正常工作。

## 阶段 6：设置页结构重组

可能涉及文件：

- `frontend/src/app/(app)/settings/page.tsx`
- 新增 `frontend/src/components/settings/*`

工作内容：

- 将当前较大的设置页拆成参考项目风格的设置中心：左侧分区导航，右侧内容区域。
- 优先保留所有现有分区和权限：
  - 账号 / 用户信息
  - 小红书数据源授权
  - SMS 自动授权
  - QR 授权
  - Cookie 绑定
  - 模型治理
  - 爬虫 / 关注关键词
  - 用户管理
  - 系统维护
  - 运行观测
- 保持 `canManageCredential`、`canManageUsers`、`canManageSystem` 等 RBAC 判断不变。

验收重点：

- SMS / QR / Cookie 授权仍然可用。
- viewer / analyst / admin 的 UI 限制仍然正确。
- 设置页深链 `#xhs-credential` 继续可用，或提供等价的定位能力。

## 阶段 7：历史中心与知识库视觉统一

可能涉及文件：

- `frontend/src/app/(app)/history/page.tsx`
- `frontend/src/app/(app)/knowledge/page.tsx`
- `frontend/src/app/(app)/knowledge/chunks-modal.tsx`

工作内容：

- 将历史中心改成分组式任务 / 会话卡片，更接近内容工作台而不是后台表格。
- 将知识库上传区和文档卡片改成柔和资产卡片。
- 保留上传、删除、查看分块的权限控制和 API 调用。

验收重点：

- 历史任务和会话仍然可以恢复到工作区。
- 知识库上传、删除、查看分块仍然可用。

## 推荐第一轮实施范围

第一轮只实现阶段 1 到阶段 3：

1. 设计变量和基础 UI 组件。
2. 侧边栏与应用外壳视觉升级。
3. 工作区初始态中央输入框和参数 chips。

这样可以用最低回归风险获得最大视觉提升。主流程验证稳定后，再继续做 Agent 时间线和 Canvas 视觉升级。

## 验证清单

- 执行前端 lint / build，或至少执行 TypeScript / Next 基础检查。
- 手动验证：
  - 登录后进入工作区
  - 新建分析
  - 对话流式响应
  - task handoff 和 SSE 进度
  - 任务完成后 Canvas 渲染
  - viewer 无法执行写操作
  - 设置页小红书授权入口仍可访问
  - 侧边栏导航和折叠正常

## 本阶段明确不做

- 不改后端编排逻辑。
- 不改 API 契约。
- 不用 `RED-MUSE-` Vite 项目直接替换当前前端。
- 不移除现有 RBAC 权限判断。
- 暂不重设 `XhsAuthAgent` 的后端授权闭环。
