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
import type { TaskStreamState } from "@/lib/sse/event-reducer";
import { useTaskStream } from "@/lib/sse/use-task-stream";

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

  const {
    state: streamState,
    connectionStatus,
    lastError: streamError,
    reconnect,
  } = useTaskStream(taskId);

  // 任务已完成但 SSE 画布仍空（例如 canvas 事件被截断）：再拉一次完整 canvas 覆盖兜底。
  useEffect(() => {
    if (!taskId) return;
    if (streamState.status !== "completed") return;
    const modules = streamState.canvas?.modules;
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
  }, [taskId, streamState.status, streamState.canvas]);

  // 兜底拉取一次 canvas：taskId 变化且 SSE 还没推 canvas_schema_updated 时
  useEffect(() => {
    if (!taskId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFallbackCanvas(null);
      return;
    }
    let cancelled = false;
    const pull = async () => {
      const res = await apiGet<CanvasSchema>(
        `/tasks/${encodeURIComponent(taskId)}/canvas`,
        { withAuth: true },
      );
      if (!cancelled && res.ok && res.data.task_id === taskId) {
        setFallbackCanvas(res.data);
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
        const base = prev ?? streamState.canvas;
        if (base && base.task_id !== taskId) return prev;
        return base ? applyModulePatch(base, module) : prev;
      });
    },
    [streamState.canvas, taskId],
  );

  const refreshCanvas = useCallback(async () => {
    if (!taskId) return null;
    const res = await apiGet<CanvasSchema>(`/tasks/${encodeURIComponent(taskId)}/canvas`, {
      withAuth: true,
    });
    if (res.ok && res.data.task_id === taskId) {
      setFallbackCanvas(res.data);
      return res.data;
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
      streamState,
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
      streamState,
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
