/**
 * E2E mock 数据工厂 + SSE 序列化工具。
 *
 * 这里刻意不从 `@/lib/*` 引入类型：e2e 运行在 Playwright 自己的 TS 运行时，
 * 不共享 Next 的路径别名。字段命名与 `frontend/src/lib/contracts.ts` 保持一致即可。
 */

export type Role = "admin" | "analyst" | "viewer";

export interface AuthUser {
  user_id: string;
  nickname: string;
  role: Role;
}

export interface CookieHealth {
  status: "valid" | "expiring_soon" | "expired" | "unknown";
  saved_days: number;
  last_checked_at: string | null;
  message: string;
}

export const DEFAULT_USER: AuthUser = {
  user_id: "u_e2e_admin",
  nickname: "E2E 管理员",
  role: "admin",
};

export const DEFAULT_TOKEN = "e2e-test-token";

export const DEFAULT_COOKIE_HEALTH: CookieHealth = {
  status: "valid",
  saved_days: 3,
  last_checked_at: new Date().toISOString(),
  message: "Cookie 有效",
};

export function nowIso(): string {
  return new Date().toISOString();
}

// ── 会话 / 消息 ────────────────────────────────────────────────

export interface ChatMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  intent: string;
  intent_confidence: number;
  clarification_needed: boolean;
  clarification_question: string | null;
  citations: unknown[];
  task_handoff: TaskHandoff | null;
  linked_task_id?: string | null;
  debug?: Record<string, unknown> | null;
  attachments?: Array<Record<string, unknown>>;
  created_at: string;
}

export interface TaskHandoff {
  task_id: string;
  status: string;
  raw_input: string;
  keywords: string[];
  canvas_url_hint: string | null;
}

export interface Conversation {
  conversation_id: string;
  owner_user_id: string;
  title: string;
  summary: string;
  active_task_id: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    conversation_id: overrides.conversation_id ?? "conv_e2e_1",
    owner_user_id: overrides.owner_user_id ?? DEFAULT_USER.user_id,
    title: overrides.title ?? "新对话",
    summary: overrides.summary ?? "",
    active_task_id: overrides.active_task_id ?? null,
    created_at: overrides.created_at ?? nowIso(),
    updated_at: overrides.updated_at ?? nowIso(),
    metadata: overrides.metadata ?? { surface: "insight" },
  };
}

export function makeConversationSummary(
  overrides: Partial<Conversation> & { last_message_preview?: string; message_count?: number } = {},
) {
  const base = makeConversation(overrides);
  return {
    ...base,
    last_message_preview: overrides.last_message_preview ?? base.title,
    last_intent: "general_qa",
    message_count: overrides.message_count ?? 2,
    citations_count: 0,
  };
}

export function makeUserMessage(
  conversationId: string,
  content: string,
  overrides: Partial<ChatMessage> = {},
): ChatMessage {
  return {
    message_id: overrides.message_id ?? `umsg_${Math.random().toString(36).slice(2, 8)}`,
    conversation_id: conversationId,
    role: "user",
    content,
    intent: "general_qa",
    intent_confidence: 0,
    clarification_needed: false,
    clarification_question: null,
    citations: [],
    task_handoff: overrides.task_handoff ?? null,
    debug: overrides.debug ?? null,
    attachments: overrides.attachments ?? [],
    created_at: overrides.created_at ?? nowIso(),
    ...overrides,
  };
}

export function makeAssistantMessage(
  conversationId: string,
  content: string,
  overrides: Partial<ChatMessage> = {},
): ChatMessage {
  return {
    message_id: overrides.message_id ?? `amsg_${Math.random().toString(36).slice(2, 8)}`,
    conversation_id: conversationId,
    role: "assistant",
    content,
    intent: "general_qa",
    intent_confidence: 0.9,
    clarification_needed: false,
    clarification_question: null,
    citations: [],
    task_handoff: overrides.task_handoff ?? null,
    debug: overrides.debug ?? { streaming: false },
    attachments: [],
    created_at: overrides.created_at ?? nowIso(),
    ...overrides,
  };
}

// ── 画布（CanvasSchema） ───────────────────────────────────────

export function makeCanvas(taskId: string, overrides: Record<string, unknown> = {}) {
  return {
    task_id: taskId,
    canvas_version: 3,
    title: "防脱精华 · 爆文洞察",
    subtitle: "近半年视频类爆款笔记分析",
    dimensions: [
      { id: "dim-cover", label: "封面", active: true, highlighted: true },
      { id: "dim-title", label: "标题", active: true },
    ],
    themes: [{ id: "theme-1", label: "防脱精华" }],
    disclaimer: "内容由 AI 生成，请核查",
    modules: [
      {
        module_id: "mod-overview-stats",
        title: "数据总览",
        layer: 1,
        status: "ready",
        version: 2,
        content: {
          total_notes: 52,
          image_count: 20,
          video_count: 32,
          keyword: "防脱精华",
        },
        actions: [],
      },
      {
        module_id: "mod-viral-model-matrix",
        title: "爆文模型矩阵",
        layer: 1,
        status: "ready",
        version: 2,
        highlighted: true,
        default_expanded: true,
        content: { matrix: { models: [], unused_directions: [] } },
        actions: [
          { id: "regen", label: "重新生成", command: "regenerate" },
        ],
      },
      {
        module_id: "mod-pain-points",
        title: "高频痛点 TOP",
        layer: 2,
        status: "ready",
        version: 1,
        content: {
          stats_axis_label: "高频痛点 / 议程",
          items: [
            { keyword: "脱发焦虑", count: 18, paragraph_id: "pp-1" },
            { keyword: "见效慢", count: 12, paragraph_id: "pp-2" },
          ],
        },
        actions: [],
      },
    ],
    ...overrides,
  };
}

// ── SSE 序列化 ─────────────────────────────────────────────────

/** 把一组事件对象序列化成 `data: {...}\n\n` 形式（对话流 / 任务流通用）。 */
export function sseBody(events: Array<Record<string, unknown>>): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
}

/**
 * 对话消息流（POST /conversations/{id}/messages/stream）的常用脚本。
 * 默认：回显用户消息 → 流式吐字 → message_done。
 */
export function conversationChatStream(input: {
  conversationId: string;
  userContent: string;
  assistantContent: string;
  userMessageId?: string;
  assistantMessageId?: string;
}): string {
  const { conversationId, userContent, assistantContent } = input;
  const userMessageId = input.userMessageId ?? "srv_user_1";
  const assistantMessageId = input.assistantMessageId ?? "srv_asst_1";
  const mid = Math.floor(assistantContent.length / 2);
  return sseBody([
    {
      type: "user_message",
      user_message: makeUserMessage(conversationId, userContent, { message_id: userMessageId }),
    },
    { type: "status", status: "thinking", message: "正在生成回答..." },
    { type: "message_start", message_id: assistantMessageId },
    { type: "message_delta", message_id: assistantMessageId, delta: assistantContent.slice(0, mid) },
    { type: "message_delta", message_id: assistantMessageId, delta: assistantContent.slice(mid) },
    { type: "message_done", message_id: assistantMessageId, content: assistantContent },
    { type: "conversation_updated", conversation: makeConversation({ conversation_id: conversationId }) },
  ]);
}

/**
 * 对话消息流：返回携带任务移交（task_handoff）的最终 assistant_message，
 * 触发前端 startTask → 拉取画布。
 */
export function conversationHandoffStream(input: {
  conversationId: string;
  userContent: string;
  assistantContent: string;
  taskId: string;
}): string {
  const { conversationId, userContent, assistantContent, taskId } = input;
  return sseBody([
    {
      type: "user_message",
      user_message: makeUserMessage(conversationId, userContent, { message_id: "srv_user_h1" }),
    },
    { type: "status", status: "analyzing", message: "正在发起分析任务..." },
    {
      type: "assistant_message",
      assistant_message: makeAssistantMessage(conversationId, assistantContent, {
        message_id: "srv_asst_h1",
        task_handoff: {
          task_id: taskId,
          status: "completed",
          raw_input: userContent,
          keywords: ["防脱精华"],
          canvas_url_hint: null,
        },
        linked_task_id: taskId,
      }),
    },
  ]);
}

/** 任务 SSE（GET /tasks/{id}/stream）：一个心跳 + 一个 done，立即收敛连接。 */
export function taskDoneStream(taskId: string): string {
  return sseBody([
    { type: "ping", event_id: "ping-0", task_id: taskId },
    {
      type: "done",
      event_id: "done-0",
      sequence_id: 1,
      task_id: taskId,
      timestamp: nowIso(),
      payload: {},
    },
  ]);
}
