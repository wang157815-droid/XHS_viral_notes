# ragent 对话框发送/终止按钮逻辑分析

> 来源：https://github.com/nageoffer/ragent
> 核心文件：`ChatInput.tsx` / `chatStore.ts` / `useStreamResponse.ts`

---

## 一、按钮交互逻辑

### 按钮形态切换

```tsx
// ChatInput.tsx
{isStreaming ? <Square /> : <Send />}
```

| 状态 | 图标 | 含义 |
|------|------|------|
| `isStreaming = false` | `Send`（→）| 发送消息 |
| `isStreaming = true`  | `Square`（□）| 终止生成 |

**一个按钮，一个 handler**，通过 `isStreaming` 状态切换两种行为：

```tsx
const handleSubmit = async () => {
  if (isStreaming) {
    cancelGeneration();   // 终止模式
    focusInput();
    return;
  }
  if (!value.trim()) return;
  const next = value;
  setValue("");
  focusInput();
  await sendMessage(next); // 发送模式
  focusInput();
};
```

---

## 二、状态字段（chatStore）

| 字段 | 类型 | 用途 |
|------|------|------|
| `isStreaming` | `boolean` | 是否正在流式输出 |
| `streamTaskId` | `string \| null` | 服务端任务 ID（来自 SSE `meta` 事件） |
| `streamAbort` | `(() => void) \| null` | `AbortController.abort` 引用，客户端级取消 |
| `streamingMessageId` | `string \| null` | 当前正在流式写入的 assistant 消息 ID |
| `cancelRequested` | `boolean` | 用户已点取消但 `taskId` 尚未到达时的标记 |

---

## 三、发送流程（sendMessage）

```
用户点击发送
  ↓
1. 乐观更新 UI：立即追加 user 消息 + 空 assistant 占位
2. 设置 isStreaming = true
3. 构建 SSE URL，携带 question / conversationId / deepThinking
4. createStreamResponse(url, handlers) → 返回 { start, cancel }
5. 保存 cancel 到 streamAbort
6. await start() 开始消费 SSE 流
```

### SSE 事件映射

| SSE event | 含义 | 处理 |
|-----------|------|------|
| `meta` | 返回 `conversationId` + `taskId` | 保存 `streamTaskId`；若 `cancelRequested` 立刻调 `stopTask` |
| `message` | 内容增量（`type: "response"`） | `appendStreamContent(delta)` |
| `message` | 思考增量（`type: "think"`）| `appendThinkingContent(delta)` |
| `finish` | 正常完成 + 最终 messageId / title | 更新消息 ID，`status = "done"` |
| `done` | 流结束信号 | 重置所有 streaming 状态 |
| `cancel` | 服务端确认取消 | 追加"（已停止生成）"，重置状态 |
| `error` | 服务端错误 | `status = "error"`，toast 报错 |
| `title` | 对话标题 | 更新侧边栏 session 标题 |

---

## 四、取消（终止）流程

```
用户点击 □ 按钮
  ↓
cancelGeneration()
  ├─ 设置 cancelRequested = true
  └─ 若 streamTaskId 存在 → stopTask(taskId)  // HTTP 请求通知服务端停止
       ↓
       服务端返回 SSE event: cancel
         ↓
         onCancel handler:
           - 消息末尾追加 "\n\n（已停止生成）"
           - status = "cancelled"
           - 重置 isStreaming / streamTaskId / streamAbort / cancelRequested
```

**竞态处理**：若用户在 `taskId` 到达前就点了取消，`cancelRequested = true`。等 `onMeta` 收到 `taskId` 后立即调 `stopTask`。

---

## 五、SSE 客户端实现（useStreamResponse.ts）

```
createStreamResponse(options, handlers)
  └─ 返回 { start, cancel }
       ├─ start() → fetch(url) + ReadableStream 解析
       └─ cancel() → AbortController.abort()
```

### 关键特性

- **纯 fetch + ReadableStream**，不依赖 EventSource（支持自定义 headers / token）
- **指数退避重试**：`retryCount`（默认 2）+ `retryDelayMs`（默认 600ms）× 2^attempt
- **标准 SSE 解析**：逐行处理 `event:` / `data:` 字段，空行触发 dispatch
- **AbortController** 取消客户端 fetch，配合服务端 `stopTask` 双重取消

---

## 六、按钮禁用逻辑

| 场景 | 发送按钮 | 深度思考按钮 |
|------|---------|------------|
| 无内容 + 不在流式 | 可点（但 handler 内 early return） | 可切换 |
| 有内容 + 不在流式 | ✅ 发送 | 可切换 |
| 流式进行中 | ✅ 变为终止按钮 | ❌ `disabled` |

> **注意**：`hasContent` 仅用于视觉状态（`disabled` 样式），按钮本身不设 `disabled`，流式时始终可点用于取消。

---

## 七、焦点管理

`inputFocusKey`（时间戳）作为 trigger，通过 `useEffect` 监听变化来聚焦 textarea，避免直接暴露 ref：

```tsx
React.useEffect(() => {
  if (!inputFocusKey) return;
  focusInput(); // el.focus({ preventScroll: true })
}, [inputFocusKey, focusInput]);
```

触发时机：`sendMessage` 开始时、发送后、取消后。

---

## 八、中文输入法防误发（双保险）

```tsx
isComposingRef.current = true   // onCompositionStart
isComposingRef.current = false  // onCompositionEnd

onKeyDown: (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    const native = event.nativeEvent as KeyboardEvent;
    // 三重检测：isComposing / ref / keyCode 229
    if (native.isComposing || isComposingRef.current || native.keyCode === 229) return;
    event.preventDefault();
    handleSubmit();
  }
}
```

比我们当前方案多了 `isComposingRef`（React ref 备份）和 `keyCode === 229`（兼容老浏览器）。
