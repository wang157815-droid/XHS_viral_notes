import {
  API_BASE,
  apiDelete,
  apiGet,
  apiPatch,
  apiPost,
  apiUpload,
  generateIdempotencyKey,
} from "@/lib/api-client";
import { getAuthToken } from "@/lib/auth-storage";
import type {
  ChatMessage,
  Conversation,
  ConversationDetail,
  ConversationSummary,
  ConversationStreamEvent,
  SendConversationMessageResponse,
} from "@/lib/contracts";

export async function createConversation(title?: string, metadata?: Record<string, unknown>) {
  return apiPost<Conversation>(
    "/conversations",
    { title, metadata: metadata ?? {} },
    { withAuth: true },
  );
}

export async function patchConversationTitle(conversationId: string, title: string) {
  return apiPatch<Conversation>(`/conversations/${encodeURIComponent(conversationId)}`, { title }, { withAuth: true });
}

export type ConversationUploadResult = {
  file_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  storage_subpath: string;
  /** 后端上传时预解析的文档元数据（PDF/文本） */
  parsed?: {
    word_count?: number;
    pages?: number;
    format?: string;
  } | null;
};

export async function uploadConversationFile(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return apiUpload<ConversationUploadResult>("/conversations/uploads", fd, { withAuth: true });
}

export async function getConversation(conversationId: string) {
  return apiGet<ConversationDetail>(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { withAuth: true },
  );
}

export async function listConversationMessages(conversationId: string) {
  return apiGet<{ items: ChatMessage[]; has_more: boolean }>(
    `/conversations/${encodeURIComponent(conversationId)}/messages`,
    { withAuth: true },
  );
}

export async function listConversations(input: {
  includeAll?: boolean;
  includeArchived?: boolean;
  keyword?: string;
  limit?: number;
  /** 逗号分隔：insight,hotspot,post_investment */
  surfaces?: string;
} = {}) {
  const params = new URLSearchParams();
  if (input.includeAll) params.set("include_all", "true");
  if (input.includeArchived) params.set("include_archived", "true");
  if (input.keyword?.trim()) params.set("keyword", input.keyword.trim());
  if (input.surfaces?.trim()) params.set("surfaces", input.surfaces.trim());
  params.set("limit", String(input.limit ?? 100));
  return apiGet<{ items: ConversationSummary[]; include_all_effective: boolean }>(
    `/conversations?${params.toString()}`,
    { withAuth: true },
  );
}

export async function archiveConversation(conversationId: string, reason?: string) {
  return apiPost<Conversation>(
    `/conversations/${encodeURIComponent(conversationId)}/archive`,
    { reason },
    { withAuth: true },
  );
}

export async function restoreConversation(conversationId: string) {
  return apiPost<Conversation>(
    `/conversations/${encodeURIComponent(conversationId)}/restore`,
    {},
    { withAuth: true },
  );
}

export async function deleteConversation(conversationId: string) {
  return apiDelete<Conversation>(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { withAuth: true },
  );
}

export async function sendConversationMessage(input: {
  conversationId: string;
  content: string;
  keywords?: string[];
  competitorKeywords?: string[];
  advancedConfig?: Record<string, unknown>;
  activeTaskId?: string | null;
  clientMessageId?: string;
  attachments?: Array<Record<string, unknown>>;
  knowledgeRefs?: Array<{ doc_id: string; title?: string }>;
}) {
  const clientMessageId = input.clientMessageId ?? generateIdempotencyKey();
  return apiPost<SendConversationMessageResponse>(
    `/conversations/${encodeURIComponent(input.conversationId)}/messages`,
    {
      content: input.content,
      keywords: input.keywords ?? [],
      competitor_keywords: input.competitorKeywords ?? [],
      advanced_config: input.advancedConfig ?? {},
      active_task_id: input.activeTaskId ?? null,
      client_message_id: clientMessageId,
      attachments: input.attachments ?? [],
      knowledge_refs: input.knowledgeRefs ?? [],
    },
    { withAuth: true },
  );
}

export async function sendConversationMessageStream(
  input: {
    conversationId: string;
    content: string;
    keywords?: string[];
    competitorKeywords?: string[];
    advancedConfig?: Record<string, unknown>;
    activeTaskId?: string | null;
    clientMessageId?: string;
    attachments?: Array<Record<string, unknown>>;
    knowledgeRefs?: Array<{ doc_id: string; title?: string }>;
  },
  onEvent: (event: ConversationStreamEvent) => void,
  signal?: AbortSignal,
) {
  const clientMessageId = input.clientMessageId ?? generateIdempotencyKey();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(
    `${API_BASE}/conversations/${encodeURIComponent(input.conversationId)}/messages/stream`,
    {
      method: "POST",
      headers,
      cache: "no-store",
      signal,
      body: JSON.stringify({
        content: input.content,
        keywords: input.keywords ?? [],
        competitor_keywords: input.competitorKeywords ?? [],
        advanced_config: input.advancedConfig ?? {},
        active_task_id: input.activeTaskId ?? null,
        client_message_id: clientMessageId,
        attachments: input.attachments ?? [],
        knowledge_refs: input.knowledgeRefs ?? [],
      }),
    },
  );
  if (!response.ok || !response.body) {
    const body = await response.text().catch(() => "");
    throw new Error(`HTTP_${response.status}: ${body || response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const extracted = extractSseEvents(buffer);
    buffer = extracted.remainder;
    for (const raw of extracted.events) {
      if (!raw.data) continue;
      try {
        onEvent(JSON.parse(raw.data) as ConversationStreamEvent);
      } catch {
        // Ignore malformed server chunks; the next valid event can still complete the stream.
      }
    }
  }
}

function extractSseEvents(buffer: string): {
  events: Array<{ event?: string; data: string }>;
  remainder: string;
} {
  const segments = buffer.split("\n\n");
  const remainder = segments.pop() ?? "";
  const events: Array<{ event?: string; data: string }> = [];
  for (const segment of segments) {
    if (!segment.trim()) continue;
    const parsed: { event?: string; data: string } = { data: "" };
    for (const rawLine of segment.split("\n")) {
      const line = rawLine.trimEnd();
      if (line.startsWith("event:")) {
        parsed.event = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        parsed.data += (parsed.data ? "\n" : "") + line.slice(5).trim();
      }
    }
    events.push(parsed);
  }
  return { events, remainder };
}
