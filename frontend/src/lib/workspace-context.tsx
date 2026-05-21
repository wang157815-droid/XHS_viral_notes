"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { apiGet } from "@/lib/api-client";
import type { CanvasModule, CanvasSchema, ChatMessage } from "@/lib/contracts";
import type { SseClientStatus } from "@/lib/sse/event-source-client";
import { type TaskStreamState } from "@/lib/sse/event-reducer";
import { useTaskStream } from "@/lib/sse/use-task-stream";

// ── sessionStorage 快照 ────────────────────────────────────────────────────────
const STREAM_SNAP_KEY = (id: string) => `redmuse:stream:${id}`;

type StreamSnapshot = Pick<
  TaskStreamState,
  | "status"
  | "progress"
  | "agentStatus"
  | "agentThinkingDone"
  | "agentLogs"
  | "lastEventAt"
  | "error"
  | "videoAsyncState"
>;

function loadSnapshot(taskId: string): StreamSnapshot | null {
  try {
    if (typeof window === "undefined") return null;
    const raw = sessionStorage.getItem(STREAM_SNAP_KEY(taskId));
    if (!raw) return null;
    const snap = JSON.parse(raw) as StreamSnapshot;
    // 去重 agentLogs：旧快照可能在 SSE 重连时积累了重复 event_id 条目
    if (snap.agentLogs) {
      const deduped: typeof snap.agentLogs = {};
      for (const [aid, logs] of Object.entries(snap.agentLogs)) {
        const seen = new Set<string>();
        deduped[aid] = (logs as Array<{ event_id?: string }>).filter((l) => {
          if (!l.event_id) return true;
          if (seen.has(l.event_id)) return false;
          seen.add(l.event_id);
          return true;
        }) as typeof logs;
      }
      snap.agentLogs = deduped;
    }
    return snap;
  } catch {
    return null;
  }
}

function saveSnapshot(taskId: string, state: TaskStreamState): void {
  try {
    if (typeof window === "undefined") return;
    const snap: StreamSnapshot = {
      status: state.status,
      progress: state.progress,
      agentStatus: state.agentStatus,
      agentThinkingDone: state.agentThinkingDone,
      agentLogs: state.agentLogs,
      lastEventAt: state.lastEventAt,
      error: state.error,
      videoAsyncState: state.videoAsyncState,
    };
    sessionStorage.setItem(STREAM_SNAP_KEY(taskId), JSON.stringify(snap));
  } catch {
    // sessionStorage 不可用或已满，忽略
  }
}

export interface WorkspaceContextValue {
  // 状态
  taskId: string | null;
  lastUserInput: string | null;
  conversationId: string | null;
  messages: ChatMessage[];
  canvasCollapsed: boolean;
  busyModuleIds: Set<string>;
  fallbackCanvas: CanvasSchema | null;
  streamState: TaskStreamState;
  connectionStatus: SseClientStatus;
  streamError: { code: string; message: string } | null;

  // Action
  startTask: (taskId: string, rawInput: string) => void;
  resetWorkspace: () => void;
  setTaskId: (taskId: string | null, rawInput?: string | null) => void;
  setConversation: (conversationId: string | null, messages?: ChatMessage[]) => void;
  appendConversationMessages: (messages: ChatMessage[]) => void;
  replaceConversationMessage: (messageId: string, message: ChatMessage) => void;
  replaceConversationMessages: (messages: ChatMessage[]) => void;
  setCanvasCollapsed: (value: boolean) => void;
  toggleCanvasCollapsed: () => void;
  setModuleBusy: (moduleId: string, busy: boolean) => void;
  reconnectStream: () => void;
  patchCanvasModule: (module: CanvasModule) => void;
  refreshCanvas: () => Promise<CanvasSchema | null>;
}

const WorkspaceCtx = createContext<WorkspaceContextValue | null>(null);

function applyModulePatch(canvas: CanvasSchema, patch: CanvasModule): CanvasSchema {
  const exists = canvas.modules.some((module) => module.module_id === patch.module_id);
  const modules = exists
    ? canvas.modules.map((module) => (module.module_id === patch.module_id ? patch : module))
    : [...canvas.modules, patch];
  return {
    ...canvas,
    canvas_version: Math.max(canvas.canvas_version ?? 1, 1) + 1,
    modules,
  };
}

/**
 * 工作台状态 Provider。
 *
 * 放在 (app)/layout.tsx 里，与 AppSidebar 同层常驻。
 * 切换 /history /knowledge /settings 等页面时 Provider 不卸载，
 * 因此 taskId / SSE 订阅 / 画布状态全部保持。
 */
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [taskId, setTaskIdState] = useState<string | null>(null);
  const [lastUserInput, setLastUserInput] = useState<string | null>(null);
  const [conversationId, setConversationIdState] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [canvasCollapsed, setCanvasCollapsedState] = useState<boolean>(true);
  const [busyModuleIds, setBusyModuleIds] = useState<Set<string>>(new Set());
  const [fallbackCanvas, setFallbackCanvas] = useState<CanvasSchema | null>(null);
  // REST 侧获取的真实任务状态（用于纠正 SSE backlog 清空后的 "running" 幽灵状态）
  const [verifiedTaskStatus, setVerifiedTaskStatus] = useState<string | null>(null);

  const {
    state: streamState,
    connectionStatus,
    lastError: streamError,
    reconnect,
  } = useTaskStream(taskId);

  // taskId 变化时重置 REST 验证状态
  useEffect(() => {
    setVerifiedTaskStatus(null);
  }, [taskId]);

  // ── sessionStorage 快照：taskId 变化时尝试加载 ─────────────────────────────
  const [restoredSnapshot, setRestoredSnapshot] = useState<StreamSnapshot | null>(null);

  useEffect(() => {
    if (!taskId) {
      setRestoredSnapshot(null);
      return;
    }
    setRestoredSnapshot(loadSnapshot(taskId));
  }, [taskId]);

  // ── sessionStorage 快照：有步骤数据时随时保存（不限于任务终止） ──────────────
  // 这样执行中途刷新也能恢复已完成的步骤，终止态后保存完整记录
  useEffect(() => {
    if (!taskId) return;
    // 只有 agentStatus 有数据时才有意义保存（避免保存空快照）
    if (Object.keys(streamState.agentStatus).length === 0) return;
    saveSnapshot(taskId, streamState);
  }, [
    taskId,
    streamState.status,
    streamState.agentStatus,
    streamState.agentLogs,
    streamState.agentThinkingDone,
    streamState.lastEventAt,
    streamState.error,
    streamState.videoAsyncState,
    streamState.progress,
  ]);

  /**
   * effectiveStreamState：
   * 始终将 sessionStorage 快照作为基线，再把 SSE 实时流的数据合并覆盖其上。
   *
   * 这样解决 SSE backlog 溢出问题：
   * - 长任务产生大量 AGENT_THINKING_CHUNK，backlog 1000 条被挤满，早期步骤事件丢失
   * - 刷新后 SSE 只回放后半段，导致早期步骤从界面消失
   * - 有了合并逻辑，快照提供早期步骤 base，实时流提供最新状态，两者互补
   */
  const effectiveStreamState = useMemo<TaskStreamState>(() => {
    const snapHasData =
      restoredSnapshot &&
      Object.keys(restoredSnapshot.agentStatus ?? {}).length > 0;

    // 没有快照时：若有 REST 验证状态且流状态未知，用验证状态修正
    if (!snapHasData) {
      if (verifiedTaskStatus && streamState.status === "unknown") {
        return { ...streamState, status: verifiedTaskStatus };
      }
      return streamState;
    }

    // 合并：快照为基线，实时流数据覆盖（实时流更新时优先）
    const mergedAgentStatus = {
      ...(restoredSnapshot!.agentStatus ?? {}),
      ...streamState.agentStatus,
    };

    // 日志：实时流有条目时覆盖，否则保留快照
    const mergedAgentLogs: Record<string, import("@/lib/sse/event-reducer").TaskLogEntry[]> = {
      ...(restoredSnapshot!.agentLogs ?? {}),
    };
    for (const [aid, logs] of Object.entries(streamState.agentLogs)) {
      if (logs.length > 0) mergedAgentLogs[aid] = logs;
    }

    const mergedAgentThinkingDone = {
      ...(restoredSnapshot!.agentThinkingDone ?? {}),
      ...streamState.agentThinkingDone,
    };

    const liveHasStatus = streamState.status !== "unknown";
    const liveHasProgress = streamState.progress > 0;

    // 优先级：SSE 实时状态 > REST 验证状态 > 快照状态
    // verifiedTaskStatus 在 refreshCanvas 时从 REST 获取，解决 backlog 重置后"永远运行中"问题
    const resolvedStatus =
      liveHasStatus
        ? streamState.status
        : (verifiedTaskStatus ?? restoredSnapshot!.status);

    return {
      status: resolvedStatus,
      progress: liveHasProgress ? streamState.progress : restoredSnapshot!.progress,
      canvas: streamState.canvas,           // canvas 由 REST 单独加载
      logs: streamState.logs,               // 日志不持久化
      agentStatus: mergedAgentStatus,
      lastEventAt: streamState.lastEventAt ?? restoredSnapshot!.lastEventAt,
      error: streamState.error ?? restoredSnapshot!.error,
      videoAsyncState:
        streamState.videoAsyncState !== "idle"
          ? streamState.videoAsyncState
          : (restoredSnapshot!.videoAsyncState ?? "idle"),
      agentThinking: streamState.agentThinking,  // 不持久化（太大）
      agentThinkingDone: mergedAgentThinkingDone,
      agentLogs: mergedAgentLogs,
    };
  }, [streamState, restoredSnapshot, verifiedTaskStatus]);

  // 任务已完成但 SSE 画布仍空（例如 canvas 事件被截断）：再拉一次完整 canvas 覆盖兜底。
  useEffect(() => {
    if (!taskId) return;
    if (effectiveStreamState.status !== "completed") return;
    const modules = effectiveStreamState.canvas?.modules;
    if (Array.isArray(modules) && modules.length > 0) return;

    let cancelled = false;
    void (async () => {
      const res = await apiGet<CanvasSchema>(
        `/tasks/${encodeURIComponent(taskId)}/canvas`,
        { withAuth: true },
      );
      if (cancelled || !res.ok || res.data.task_id !== taskId) return;
      const m = res.data.modules;
      if (Array.isArray(m) && m.length > 0) {
        setFallbackCanvas(res.data);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [taskId, effectiveStreamState.status, effectiveStreamState.canvas]);

  // 兜底拉取一次 canvas + 任务状态：taskId 变化且 SSE 还没推事件时
  useEffect(() => {
    if (!taskId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFallbackCanvas(null);
      return;
    }
    let cancelled = false;
    const pull = async () => {
      const [canvasRes, taskRes] = await Promise.all([
        apiGet<CanvasSchema>(`/tasks/${encodeURIComponent(taskId)}/canvas`, { withAuth: true }),
        apiGet<{ task_id: string; status: string }>(`/tasks/${encodeURIComponent(taskId)}`, { withAuth: true }),
      ]);
      if (cancelled) return;
      if (taskRes.ok && taskRes.data.task_id === taskId) {
        setVerifiedTaskStatus(taskRes.data.status);
      }
      if (canvasRes.ok && canvasRes.data.task_id === taskId) {
        setFallbackCanvas(canvasRes.data);
      }
    };
    void pull();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  const startTask = useCallback((id: string, rawInput: string) => {
    setTaskIdState(id);
    setLastUserInput(rawInput);
    setFallbackCanvas(null);
    // 任务执行中不自动展开画布；仅在任务完成后由页面层控制展示。
    setCanvasCollapsedState(true);
    setBusyModuleIds(new Set());
  }, []);

  const resetWorkspace = useCallback(() => {
    setTaskIdState(null);
    setLastUserInput(null);
    setConversationIdState(null);
    setMessages([]);
    setFallbackCanvas(null);
    setCanvasCollapsedState(true);
    setBusyModuleIds(new Set());
  }, []);

  const setTaskId = useCallback((id: string | null, rawInput?: string | null) => {
    setTaskIdState(id);
    setFallbackCanvas(null);
    setBusyModuleIds(new Set());
    if (id) {
      setLastUserInput(rawInput ?? null);
      // 恢复/切换任务时默认收起，避免任务进行中提前展示画布。
      setCanvasCollapsedState(true);
    } else {
      setLastUserInput(null);
      setCanvasCollapsedState(true);
    }
  }, []);

  const setConversation = useCallback((id: string | null, nextMessages: ChatMessage[] = []) => {
    setConversationIdState(id);
    setMessages(nextMessages);
  }, []);

  const appendConversationMessages = useCallback((nextMessages: ChatMessage[]) => {
    setMessages((prev) => {
      const indexById = new Map(prev.map((message, index) => [message.message_id, index]));
      const merged = [...prev];
      for (const message of nextMessages) {
        const existingIndex = indexById.get(message.message_id);
        if (existingIndex === undefined) {
          indexById.set(message.message_id, merged.length);
          merged.push(message);
        } else {
          merged[existingIndex] = message;
        }
      }
      return merged;
    });
  }, []);

  const replaceConversationMessage = useCallback((messageId: string, message: ChatMessage) => {
    setMessages((prev) => prev.map((item) => (item.message_id === messageId ? message : item)));
  }, []);

  const replaceConversationMessages = useCallback((nextMessages: ChatMessage[]) => {
    setMessages(nextMessages);
  }, []);

  const setCanvasCollapsed = useCallback((value: boolean) => {
    setCanvasCollapsedState(value);
  }, []);

  const toggleCanvasCollapsed = useCallback(() => {
    setCanvasCollapsedState((v) => !v);
  }, []);

  const setModuleBusy = useCallback((moduleId: string, busy: boolean) => {
    setBusyModuleIds((prev) => {
      const next = new Set(prev);
      if (busy) next.add(moduleId);
      else next.delete(moduleId);
      return next;
    });
  }, []);

  const patchCanvasModule = useCallback(
    (module: CanvasModule) => {
      setFallbackCanvas((prev) => {
        const base = prev ?? effectiveStreamState.canvas;
        if (base && base.task_id !== taskId) return prev;
        return base ? applyModulePatch(base, module) : prev;
      });
    },
    [effectiveStreamState.canvas, taskId],
  );

  const refreshCanvas = useCallback(async () => {
    if (!taskId) return null;
    // 并发拉取 canvas + 任务状态（任务状态用于纠正 SSE backlog 清空后的幽灵 "running"）
    const [canvasRes, taskRes] = await Promise.all([
      apiGet<CanvasSchema>(`/tasks/${encodeURIComponent(taskId)}/canvas`, { withAuth: true }),
      apiGet<{ task_id: string; status: string }>(`/tasks/${encodeURIComponent(taskId)}`, { withAuth: true }),
    ]);
    if (taskRes.ok && taskRes.data.task_id === taskId) {
      setVerifiedTaskStatus(taskRes.data.status);
    }
    if (canvasRes.ok && canvasRes.data.task_id === taskId) {
      setFallbackCanvas(canvasRes.data);
      return canvasRes.data;
    }
    return null;
  }, [taskId]);

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      taskId,
      lastUserInput,
      conversationId,
      messages,
      canvasCollapsed,
      busyModuleIds,
      fallbackCanvas,
      streamState: effectiveStreamState,
      connectionStatus,
      streamError,
      startTask,
      resetWorkspace,
      setTaskId,
      setConversation,
      appendConversationMessages,
      replaceConversationMessage,
      replaceConversationMessages,
      setCanvasCollapsed,
      toggleCanvasCollapsed,
      setModuleBusy,
      reconnectStream: reconnect,
      patchCanvasModule,
      refreshCanvas,
    }),
    [
      taskId,
      lastUserInput,
      conversationId,
      messages,
      canvasCollapsed,
      busyModuleIds,
      fallbackCanvas,
      effectiveStreamState,
      connectionStatus,
      streamError,
      startTask,
      resetWorkspace,
      setTaskId,
      setConversation,
      appendConversationMessages,
      replaceConversationMessage,
      replaceConversationMessages,
      setCanvasCollapsed,
      toggleCanvasCollapsed,
      setModuleBusy,
      reconnect,
      patchCanvasModule,
      refreshCanvas,
    ],
  );

  return <WorkspaceCtx.Provider value={value}>{children}</WorkspaceCtx.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceCtx);
  if (!ctx) {
    throw new Error("useWorkspace 必须在 <WorkspaceProvider> 内使用");
  }
  return ctx;
}
