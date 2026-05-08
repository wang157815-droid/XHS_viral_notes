import type { TaskEvent, TaskEventType } from "@/lib/contracts";
import { getAuthToken } from "@/lib/auth-storage";
import { API_BASE } from "@/lib/api-client";

export type SseClientEventHandler = (event: TaskEvent) => void;
export type SseClientErrorHandler = (error: {
  code: string;
  message: string;
  recoverable: boolean;
}) => void;

export interface SseClientOptions {
  taskId: string;
  withAuth?: boolean;
  clientIdleTimeoutMs?: number;
  reconnectMinDelayMs?: number;
  reconnectMaxDelayMs?: number;
  onEvent: SseClientEventHandler;
  onError?: SseClientErrorHandler;
  onStatusChange?: (status: SseClientStatus) => void;
}

export type SseClientStatus =
  | "idle"
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed"
  | "failed";

interface ParsedSseEvent {
  id?: string;
  event?: string;
  data: string;
}

/**
 * SSE 客户端（fetch + ReadableStream 实现）
 *
 * 支持：
 * - Authorization: Bearer <token>
 * - 心跳超时（clientIdleTimeoutMs，默认 45s）自动重连
 * - Last-Event-ID 恢复（服务端回放缺失事件）
 * - 事件去重（按 event_id）
 * - 断线指数退避重连
 */
export class TaskEventStream {
  private readonly taskId: string;
  private readonly withAuth: boolean;
  private readonly idleTimeoutMs: number;
  private readonly minDelayMs: number;
  private readonly maxDelayMs: number;
  private readonly onEvent: SseClientEventHandler;
  private readonly onError?: SseClientErrorHandler;
  private readonly onStatusChange?: (status: SseClientStatus) => void;

  private controller: AbortController | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private lastEventId: string | null = null;
  private lastSequenceId: number | null = null;
  private seenEventIds: Set<string> = new Set();
  private attempt = 0;
  private status: SseClientStatus = "idle";
  private closed = false;
  // 4.2 视频异步支路: done 携带 video_pending=true 时,
  // 连接不立即关闭,保活等待 task_video_done;10 分钟兜底关流。
  private videoWatchdog: ReturnType<typeof setTimeout> | null = null;
  private readonly videoWatchdogMs = 10 * 60 * 1000;

  constructor(options: SseClientOptions) {
    this.taskId = options.taskId;
    this.withAuth = options.withAuth ?? true;
    this.idleTimeoutMs = options.clientIdleTimeoutMs ?? 45_000;
    this.minDelayMs = options.reconnectMinDelayMs ?? 1_000;
    this.maxDelayMs = options.reconnectMaxDelayMs ?? 15_000;
    this.onEvent = options.onEvent;
    this.onError = options.onError;
    this.onStatusChange = options.onStatusChange;
  }

  start(): void {
    if (this.closed) return;
    this.connect();
  }

  close(): void {
    this.closed = true;
    this.setStatus("closed");
    this.abortCurrent();
    this.clearIdleTimer();
    this.clearVideoWatchdog();
  }

  private setStatus(next: SseClientStatus): void {
    if (this.status === next) return;
    this.status = next;
    this.onStatusChange?.(next);
  }

  private clearIdleTimer(): void {
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }

  private resetIdleTimer(): void {
    this.clearIdleTimer();
    this.idleTimer = setTimeout(() => {
      this.onError?.({
        code: "SSE_IDLE_TIMEOUT",
        message: `超过 ${this.idleTimeoutMs}ms 未收到任何事件，主动重连`,
        recoverable: true,
      });
      this.reconnect();
    }, this.idleTimeoutMs);
  }

  private abortCurrent(): void {
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }

  private reconnect(): void {
    if (this.closed) return;
    this.abortCurrent();
    this.clearIdleTimer();
    this.attempt += 1;
    const delay = Math.min(this.minDelayMs * 2 ** (this.attempt - 1), this.maxDelayMs);
    this.setStatus("reconnecting");
    setTimeout(() => {
      if (!this.closed) this.connect();
    }, delay);
  }

  private async connect(): Promise<void> {
    if (this.closed) return;
    this.setStatus("connecting");
    this.controller = new AbortController();

    const url = this.buildUrl();
    const headers: Record<string, string> = {
      Accept: "text/event-stream",
    };
    if (this.withAuth) {
      const token = getAuthToken();
      if (token) headers["Authorization"] = `Bearer ${token}`;
    }
    if (this.lastEventId) {
      headers["Last-Event-ID"] = this.lastEventId;
    }

    try {
      const response = await fetch(url, {
        method: "GET",
        headers,
        cache: "no-store",
        signal: this.controller.signal,
      });

      if (!response.ok || !response.body) {
        const body = await response.text().catch(() => "");
        this.onError?.({
          code: `SSE_HTTP_${response.status}`,
          message: `SSE 连接失败: ${response.status} ${body}`,
          recoverable: response.status < 500 ? false : true,
        });
        if (response.status >= 400 && response.status < 500) {
          this.setStatus("failed");
          return;
        }
        this.reconnect();
        return;
      }

      this.setStatus("open");
      this.attempt = 0;
      this.resetIdleTimer();

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (!this.closed) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = this.extractEvents(buffer);
        buffer = events.remainder;
        for (const raw of events.parsed) {
          this.handleRawEvent(raw);
          this.resetIdleTimer();
        }
      }
    } catch (error) {
      if (this.closed) return;
      const message = error instanceof Error ? error.message : String(error);
      this.onError?.({
        code: "SSE_FETCH_ERROR",
        message,
        recoverable: true,
      });
      this.reconnect();
      return;
    }

    if (!this.closed) this.reconnect();
  }

  private buildUrl(): string {
    const search = new URLSearchParams();
    if (this.lastSequenceId !== null) {
      search.set("last_sequence_id", String(this.lastSequenceId));
    }
    if (this.lastEventId) {
      search.set("last_event_id", this.lastEventId);
    }
    const qs = search.toString();
    const base = `${API_BASE}/tasks/${encodeURIComponent(this.taskId)}/stream`;
    return qs ? `${base}?${qs}` : base;
  }

  private extractEvents(buffer: string): { parsed: ParsedSseEvent[]; remainder: string } {
    const events: ParsedSseEvent[] = [];
    const segments = buffer.split("\n\n");
    const remainder = segments.pop() ?? "";
    for (const seg of segments) {
      if (!seg.trim()) continue;
      const parsed: ParsedSseEvent = { data: "" };
      for (const rawLine of seg.split("\n")) {
        const line = rawLine.trimEnd();
        if (!line) continue;
        if (line.startsWith("id:")) {
          parsed.id = line.slice(3).trim();
        } else if (line.startsWith("event:")) {
          parsed.event = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          parsed.data += (parsed.data ? "\n" : "") + line.slice(5).trim();
        }
      }
      events.push(parsed);
    }
    return { parsed: events, remainder };
  }

  private handleRawEvent(raw: ParsedSseEvent): void {
    if (!raw.data) return;
    let payload: unknown;
    try {
      payload = JSON.parse(raw.data);
    } catch {
      return;
    }
    const event = this.asTaskEvent(payload, raw);
    if (!event) return;

    if (event.type === "ping") {
      this.lastEventId = event.event_id ?? this.lastEventId;
      return;
    }

    if (event.event_id && this.seenEventIds.has(event.event_id)) {
      return;
    }
    if (event.event_id) {
      this.seenEventIds.add(event.event_id);
      if (this.seenEventIds.size > 1000) {
        this.seenEventIds = new Set(Array.from(this.seenEventIds).slice(-500));
      }
      this.lastEventId = event.event_id;
    }
    if (typeof event.sequence_id === "number") {
      if (this.lastSequenceId === null || event.sequence_id > this.lastSequenceId) {
        this.lastSequenceId = event.sequence_id;
      }
    }

    this.onEvent(event);

    if (event.type === "done") {
      const videoPending = Boolean(
        (event.payload as Record<string, unknown> | undefined)?.["video_pending"]
      );
      if (videoPending) {
        // 主任务已完成但视频支路仍在跑,保活连接等 task_video_done
        this.clearVideoWatchdog();
        this.videoWatchdog = setTimeout(() => {
          this.close();
        }, this.videoWatchdogMs);
      } else {
        this.close();
      }
      return;
    }

    if (event.type === "task_video_done") {
      this.clearVideoWatchdog();
      this.close();
    }
  }

  private clearVideoWatchdog(): void {
    if (this.videoWatchdog) {
      clearTimeout(this.videoWatchdog);
      this.videoWatchdog = null;
    }
  }

  private asTaskEvent(payload: unknown, raw: ParsedSseEvent): TaskEvent | null {
    if (!payload || typeof payload !== "object") return null;
    const obj = payload as Record<string, unknown>;
    const type = (obj["type"] as TaskEventType) ?? (raw.event as TaskEventType);
    if (!type) return null;
    return {
      event_id: String(obj["event_id"] ?? raw.id ?? ""),
      sequence_id: Number(obj["sequence_id"] ?? 0),
      type,
      task_id: String(obj["task_id"] ?? this.taskId),
      timestamp: String(obj["timestamp"] ?? ""),
      branch_id: (obj["branch_id"] as string | null | undefined) ?? null,
      payload: (obj["payload"] as Record<string, unknown>) ?? {},
    };
  }
}
