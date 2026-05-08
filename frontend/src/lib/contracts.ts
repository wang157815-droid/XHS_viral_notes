export type CookieHealthStatus = "valid" | "expiring_soon" | "expired" | "unknown";

export type TaskStatus =
  | "pending"
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled";

export type ModuleStatus =
  | "pending"
  | "generating"
  | "ready"
  | "stale"
  | "failed"
  | "deleted";

export type TaskEventType =
  | "ping"
  | "task_status"
  | "agent_progress"
  | "log"
  | "canvas_module_updated"
  | "canvas_schema_updated"
  | "error"
  | "done"
  | "task_video_done";

export interface TaskEvent<P = Record<string, unknown>> {
  event_id: string;
  sequence_id: number;
  type: TaskEventType;
  task_id: string;
  timestamp: string;
  branch_id?: string | null;
  payload: P;
}

export interface UserSession {
  user_id: string;
  nickname: string;
  role: "admin" | "user";
  token: string;
}

export type ConversationIntent =
  | "general_qa"
  | "knowledge_qa"
  | "xhs_analysis"
  | "refine_canvas"
  | "export"
  | "unknown";

export interface Conversation {
  conversation_id: string;
  owner_user_id: string;
  title: string;
  summary: string;
  active_task_id: string | null;
  created_at: string;
  updated_at: string;
  metadata: {
    domain_ids?: string[];
    recent_keywords?: string[];
    user_preferences?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface ConversationSummary {
  conversation_id: string;
  owner_user_id: string;
  title: string;
  summary: string;
  active_task_id: string | null;
  last_message_preview: string;
  last_intent: ConversationIntent;
  message_count: number;
  citations_count?: number;
  created_at: string;
  updated_at: string;
  metadata: Conversation["metadata"];
}

export interface KnowledgeCitation {
  doc_id: string;
  chunk_index: number;
  title: string;
  snippet: string;
  score: number;
  source: "vector" | "domain_keyword" | "task_context";
}

export interface TaskHandoff {
  task_id: string;
  status: TaskStatus;
  raw_input: string;
  keywords: string[];
  canvas_url_hint: string | null;
}

export interface IntentClassification {
  intent: ConversationIntent;
  confidence: number;
  reason: string;
  target_module_ids: string[];
  extracted_keywords: string[];
  competitor_keywords?: string[];
  domain_ids: string[];
  should_retrieve_knowledge: boolean;
  clarification_needed: boolean;
  clarification_question: string | null;
}

export interface ChatMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  intent: ConversationIntent;
  intent_confidence: number;
  clarification_needed: boolean;
  clarification_question: string | null;
  citations: KnowledgeCitation[];
  task_handoff: TaskHandoff | null;
  linked_task_id?: string | null;
  debug?: Record<string, unknown> | null;
  created_at: string;
}

export interface ConversationDetail {
  conversation: Conversation;
  messages: ChatMessage[];
}

export interface SendConversationMessageResponse {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  conversation: Conversation;
  intent: IntentClassification;
}

export type ConversationStreamEvent =
  | { type: "user_message"; user_message: ChatMessage }
  | { type: "status"; status: string; message?: string; tool?: string }
  | { type: "tool_selected"; tool: { name: string; arguments?: Record<string, unknown>; confidence?: number; reason?: string } }
  | { type: "message_start"; message_id: string }
  | { type: "message_delta"; message_id: string; delta: string }
  | {
      type: "message_done";
      message_id: string;
      content: string;
      assistant_message?: ChatMessage;
      conversation?: Conversation | null;
      intent?: IntentClassification;
    }
  | { type: "message_error"; message_id: string; code: string; message: string }
  | {
      type: "assistant_message";
      assistant_message: ChatMessage;
      conversation?: Conversation | null;
      intent?: IntentClassification;
    };

export type HistoryTimelineItem =
  | {
      type: "task";
      id: string;
      title: string;
      status: TaskStatus;
      updated_at: string;
      created_at: string;
      preview: string;
      task_id: string;
    }
  | {
      type: "conversation";
      id: string;
      title: string;
      status: "active" | "archived";
      updated_at: string;
      created_at: string;
      preview: string;
      conversation_id: string;
      active_task_id?: string | null;
      message_count?: number;
    };

export interface CookieHealth {
  status: CookieHealthStatus;
  saved_days: number;
  last_checked_at: string | null;
  message: string;
}

export interface TaskSummary {
  task_id: string;
  keywords: string[];
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  progress: number;
  collected_count: number;
  duration_seconds: number;
  owner_user_id?: string;
}

export interface CanvasDimension {
  id: string;
  label: string;
  active: boolean;
  highlighted?: boolean;
}

export interface CanvasTheme {
  id: string;
  label: string;
  description?: string;
}

export interface CanvasModuleAction {
  id: string;
  label: string;
  command: "regenerate" | "regenerate_cascade" | "delete" | "restore" | "deep_dive" | "export";
}

/** 与后端 `CanvasModule.content.feedback_map` 对齐（阶段 4.3） */
export interface ParagraphFeedbackRecord {
  action: "like" | "dislike" | "delete" | "edit" | "reset";
  edited_text?: string;
  feedback_hint?: string;
  created_at: string;
  actor?: string;
}

export interface CanvasModule {
  module_id: string;
  title: string;
  layer: 1 | 2 | 3;
  status: ModuleStatus;
  version: number;
  highlighted?: boolean;
  default_expanded?: boolean;
  summary?: string;
  /** 可含 `feedback_map: Record<string, ParagraphFeedbackRecord>` 等 */
  content?: Record<string, unknown>;
  actions?: CanvasModuleAction[];
  dirty_reason?: string | null;
  depends_on?: string[];
}

export interface CanvasSchema {
  task_id: string;
  canvas_version: number;
  title: string;
  subtitle?: string;
  dimensions: CanvasDimension[];
  themes: CanvasTheme[];
  disclaimer?: string;
  modules: CanvasModule[];
}

export type ErrorCode = string; // e.g. "AUTH_COOKIE_EXPIRED" / "MODEL_TIMEOUT" / ...

export interface ApiSuccess<T = Record<string, unknown>> {
  ok: true;
  data: T;
}

export interface ApiError {
  ok: false;
  error: {
    code: ErrorCode;
    message: string;
    details: Record<string, unknown>;
    trace_id?: string;
  };
}

export type ApiResponse<T = Record<string, unknown>> = ApiSuccess<T> | ApiError;
