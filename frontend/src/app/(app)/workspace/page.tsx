"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useSession } from "@/lib/session-context";
import { useWorkspace } from "@/lib/workspace-context";
import { CanvasView } from "@/components/workspace/canvas-view";
import { ChatPanel, type AdvancedConfig } from "@/components/workspace/chat-panel";
import { adaptCanvas } from "@/components/workspace/canvas-adapter";
import {
  DEMO_CANVAS,
  type PrototypeAction,
  type PrototypeCanvasModel,
  type PrototypeModule,
} from "@/components/workspace/mock-canvas-data";
import {
  API_BASE,
  apiGet,
  apiPost,
  generateIdempotencyKey,
} from "@/lib/api-client";
import { getAuthToken } from "@/lib/auth-storage";
import { createConversation, getConversation, sendConversationMessageStream } from "@/lib/conversation-api";
import type { CanvasSchema, ChatMessage, ConversationStreamEvent } from "@/lib/contracts";

/** 优先使用模块更完整的画布，避免 SSE 截断/空 payload 覆盖 REST 兜底。 */
function pickCanvas(
  stream: CanvasSchema | null | undefined,
  fallback: CanvasSchema | null | undefined,
  expectedTaskId: string | null,
): CanvasSchema | null {
  const activeStream = stream?.task_id === expectedTaskId ? stream : null;
  const activeFallback = fallback?.task_id === expectedTaskId ? fallback : null;
  if (!activeStream && !activeFallback) return null;
  const sm = activeStream?.modules;
  const fm = activeFallback?.modules;
  const streamN = Array.isArray(sm) ? sm.length : -1;
  const fallbackN = Array.isArray(fm) ? fm.length : -1;
  const streamOk = !!(
    activeStream &&
    typeof activeStream.task_id === "string" &&
    Array.isArray(activeStream.modules)
  );
  if (!streamOk && activeFallback) return activeFallback;
  if (streamOk && activeStream && activeFallback && (activeFallback.canvas_version ?? 0) > (activeStream.canvas_version ?? 0)) {
    return activeFallback;
  }
  if (streamOk && activeFallback && fallbackN > streamN) return activeFallback;
  if (streamOk && activeStream && activeFallback) {
    const streamVersions = new Map((activeStream.modules ?? []).map((module) => [module.module_id, module.version]));
    const fallbackHasNewerModule = (activeFallback.modules ?? []).some(
      (module) => (streamVersions.get(module.module_id) ?? -1) < module.version,
    );
    if (fallbackHasNewerModule) return activeFallback;
  }
  if (streamOk) return activeStream;
  return activeFallback ?? null;
}

export default function WorkspacePage() {
  const {
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
    setConversation,
    appendConversationMessages,
    replaceConversationMessage,
    setCanvasCollapsed,
    toggleCanvasCollapsed,
    setModuleBusy,
    reconnectStream,
    patchCanvasModule,
    refreshCanvas,
  } = useWorkspace();
  const { cookieHealth, can } = useSession();
  const canWriteConversation = can("conversation.write_own");
  const canWriteTask = can("task.write_own");

  // 纯 UI 本地状态（切页丢失无所谓）
  const [creating, setCreating] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [canvasRefreshing, setCanvasRefreshing] = useState(false);
  const [toast, setToast] = useState<{ type: "ok" | "err"; message: string } | null>(null);
  const regenAnchorRef = useRef<{ moduleId: string; paragraphId: string } | null>(null);
  const registerRegenerateAnchor = useCallback((moduleId: string, paragraphId: string) => {
    regenAnchorRef.current = { moduleId, paragraphId };
  }, []);
  const autoExpandedTaskRef = useRef<string | null>(null);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 3500);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  // 画布优先级：SSE 实时推送 > Provider 里的 fallback > 演示数据
  const { canvasModel, realtime } = useMemo<{
    canvasModel: PrototypeCanvasModel;
    realtime: boolean;
  }>(() => {
    if (!taskId) {
      return { canvasModel: DEMO_CANVAS, realtime: false };
    }
    const merged = pickCanvas(streamState.canvas, fallbackCanvas, taskId);
    if (merged) {
      return { canvasModel: adaptCanvas(merged), realtime: true };
    }
    return { canvasModel: DEMO_CANVAS, realtime: false };
  }, [taskId, streamState.canvas, fallbackCanvas]);

  const paragraphEnv = useMemo(
    () =>
      taskId && realtime && canWriteTask
        ? {
            taskId,
            realtime,
            onToast: (type: "ok" | "err", message: string) => setToast({ type, message }),
            registerRegenerateAnchor,
            onModulePatched: patchCanvasModule,
            refreshCanvas,
          }
        : undefined,
    [taskId, realtime, registerRegenerateAnchor, patchCanvasModule, refreshCanvas, canWriteTask],
  );
  const taskInProgress = Boolean(
    taskId && ["pending", "queued", "running", "paused"].includes(streamState.status),
  );
  // 历史任务查看时，stream 可能是 unknown/closed，但 fallbackCanvas 已可渲染。
  // 只要非进行中且存在真实画布数据，就允许展示画布与右上角按钮。
  const canShowCanvas = Boolean(taskId && realtime && !taskInProgress);

  // 画布可展示时，每个 task 仅自动展开一次，避免用户手动收起后被强制展开。
  useEffect(() => {
    if (!taskId) {
      autoExpandedTaskRef.current = null;
      return;
    }
    if (!canShowCanvas) {
      autoExpandedTaskRef.current = null;
      return;
    }
    if (autoExpandedTaskRef.current === taskId) return;
    if (canShowCanvas && canvasCollapsed) {
      setCanvasCollapsed(false);
      autoExpandedTaskRef.current = taskId;
    }
  }, [taskId, canShowCanvas, canvasCollapsed, setCanvasCollapsed]);

  const handleRefreshCanvas = useCallback(async () => {
    if (!taskId || !realtime) {
      setToast({ type: "err", message: "请先发起一次真实分析再刷新画布" });
      return;
    }
    setCanvasRefreshing(true);
    try {
      const nextCanvas = await refreshCanvas();
      if (nextCanvas) {
        setToast({ type: "ok", message: "画布已刷新" });
      } else {
        setToast({ type: "err", message: "未获取到最新画布，请稍后重试" });
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setToast({ type: "err", message: `刷新画布失败：${message}` });
    } finally {
      setCanvasRefreshing(false);
    }
  }, [taskId, realtime, refreshCanvas]);

  const waitForCanvasModuleUpdate = useCallback(
    async (moduleId: string, beforeVersion: number | null) => {
      const delays = [1200, 2000, 3000, 4000, 5000, 5000, 5000];
      for (const delay of delays) {
        await new Promise((resolve) => window.setTimeout(resolve, delay));
        const nextCanvas = await refreshCanvas();
        const nextModule = nextCanvas?.modules.find((module) => module.module_id === moduleId);
        if (nextModule && (beforeVersion === null || nextModule.version > beforeVersion)) return true;
      }
      return false;
    },
    [refreshCanvas],
  );

  const handleSubmit = useCallback(
    async ({ rawInput, keywords, advanced }: {
      rawInput: string;
      keywords: string[];
      advanced: AdvancedConfig;
    }) => {
      if (!canWriteConversation) {
        setToast({ type: "err", message: "只读成员不能发送消息或发起分析" });
        return;
      }
      setCreating(true);
      setToast(null);
      const clientMessageId = generateIdempotencyKey();
      let currentConversationId = conversationId ?? "pending_conversation";
      try {
        if (!conversationId) {
          const created = await createConversation(rawInput.slice(0, 24) || "新对话");
          if (!created.ok) {
            setToast({
              type: "err",
              message: `创建会话失败：${created.error.code} · ${created.error.message}`,
            });
            return;
          }
          currentConversationId = created.data.conversation_id;
          setConversation(currentConversationId, []);
        }

        const optimisticMessage: ChatMessage = {
          message_id: clientMessageId,
          conversation_id: currentConversationId,
          role: "user",
          content: rawInput,
          intent: "unknown",
          intent_confidence: 0,
          clarification_needed: false,
          clarification_question: null,
          citations: [],
          task_handoff: null,
          linked_task_id: taskId,
          debug: { optimistic: true },
          created_at: new Date().toISOString(),
        };
        appendConversationMessages([optimisticMessage]);

        let assistantMessageId = `stream_${clientMessageId}`;
        let assistantContent = "";
        let assistantInserted = false;
        const makeAssistantMessage = (patch: Partial<ChatMessage> = {}): ChatMessage => ({
          message_id: assistantMessageId,
          conversation_id: currentConversationId,
          role: "assistant",
          content: assistantContent,
          intent: "unknown",
          intent_confidence: 0,
          clarification_needed: false,
          clarification_question: null,
          citations: [],
          task_handoff: null,
          linked_task_id: taskId,
          debug: { streaming: true, status: "thinking" },
          created_at: new Date().toISOString(),
          ...patch,
        });
        const upsertAssistant = (message: ChatMessage) => {
          if (assistantInserted) {
            replaceConversationMessage(assistantMessageId, message);
          } else {
            appendConversationMessages([message]);
            assistantInserted = true;
          }
          assistantMessageId = message.message_id;
        };
        const handleFinalAssistant = (message: ChatMessage) => {
          upsertAssistant(message);
          const handoff = message.task_handoff;
          if (handoff?.task_id) {
            startTask(handoff.task_id, rawInput);
          }
          const debug = message.debug as
            | { regeneration_started?: unknown; module_id?: unknown }
            | null
            | undefined;
          const regenModuleId =
            debug?.regeneration_started === true && typeof debug.module_id === "string"
              ? debug.module_id
              : null;
          if (regenModuleId) {
            const beforeVersion =
              canvasModel.modules.find((module) => module.moduleId === regenModuleId)?.version ?? null;
            setModuleBusy(regenModuleId, true);
            void waitForCanvasModuleUpdate(regenModuleId, beforeVersion).finally(() =>
              setModuleBusy(regenModuleId, false),
            );
          }
        };

        await sendConversationMessageStream(
          {
            conversationId: currentConversationId,
            content: rawInput,
            keywords,
            advancedConfig: advanced as unknown as Record<string, unknown>,
            activeTaskId: taskId,
            clientMessageId,
          },
          (event: ConversationStreamEvent) => {
            if (event.type === "user_message") {
              replaceConversationMessage(clientMessageId, event.user_message);
              return;
            }
            if (event.type === "status") {
              const label =
                event.status === "retrieving_knowledge"
                  ? "正在检索知识库..."
                  : event.message || "正在生成回答...";
              upsertAssistant(makeAssistantMessage({ content: "", debug: { streaming: true, status: event.status, label } }));
              return;
            }
            if (event.type === "tool_selected") {
              upsertAssistant(makeAssistantMessage({ content: "", debug: { streaming: true, status: "tool_selected", tool: event.tool } }));
              return;
            }
            if (event.type === "message_start") {
              upsertAssistant(makeAssistantMessage({ message_id: event.message_id, content: "" }));
              return;
            }
            if (event.type === "message_delta") {
              assistantContent += event.delta;
              upsertAssistant(makeAssistantMessage({ message_id: event.message_id, content: assistantContent }));
              return;
            }
            if (event.type === "message_done") {
              if (event.assistant_message) {
                handleFinalAssistant(event.assistant_message);
                return;
              }
              assistantContent = event.content || assistantContent;
              upsertAssistant(makeAssistantMessage({ message_id: event.message_id, content: assistantContent, debug: { streaming: false } }));
              return;
            }
            if (event.type === "assistant_message") {
              handleFinalAssistant(event.assistant_message);
              return;
            }
            if (event.type === "message_error") {
              assistantContent = `生成失败：${event.code} · ${event.message}`;
              upsertAssistant(makeAssistantMessage({ message_id: event.message_id, content: assistantContent, debug: { streaming: false, error: event } }));
            }
          },
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        appendConversationMessages([
          {
            message_id: `error_${clientMessageId}`,
            conversation_id: currentConversationId,
            role: "assistant",
            content: `发送失败：${message}`,
            intent: "unknown",
            intent_confidence: 0,
            clarification_needed: false,
            clarification_question: null,
            citations: [],
            task_handoff: null,
            debug: { error: message },
            created_at: new Date().toISOString(),
          },
        ]);
        setToast({ type: "err", message: `发送消息失败：${message}` });
      } finally {
        setCreating(false);
      }
    },
    [
      appendConversationMessages,
      conversationId,
      replaceConversationMessage,
      setConversation,
      setModuleBusy,
      startTask,
      taskId,
      canvasModel.modules,
      waitForCanvasModuleUpdate,
      canWriteConversation,
    ],
  );

  const handleNewAnalysis = useCallback(() => {
    resetWorkspace();
  }, [resetWorkspace]);

  const handleModuleAction = useCallback(
    async (module: PrototypeModule, action: PrototypeAction) => {
      if (!canWriteTask) {
        setToast({ type: "err", message: "只读成员不能修改画布模块" });
        return;
      }
      if (!taskId || !realtime) {
        setToast({
          type: "ok",
          message: `演示模式 · 模块「${module.title}」已 ${action.label}`,
        });
        return;
      }

      if (
        action.id === "regenerate" ||
        action.id === "regenerate_cascade" ||
        action.id === "delete" ||
        action.id === "restore"
      ) {
        const isRegen = action.id === "regenerate" || action.id === "regenerate_cascade";
        const path =
          isRegen
            ? `/tasks/${encodeURIComponent(taskId)}/modules/${encodeURIComponent(module.moduleId)}/regenerate`
            : action.id === "delete"
              ? `/tasks/${encodeURIComponent(taskId)}/modules/${encodeURIComponent(module.moduleId)}/delete`
              : `/tasks/${encodeURIComponent(taskId)}/modules/${encodeURIComponent(module.moduleId)}/restore`;

        setModuleBusy(module.moduleId, true);
        try {
          const anchor = regenAnchorRef.current;
          const paragraphId =
            isRegen && anchor && anchor.moduleId === module.moduleId ? anchor.paragraphId : undefined;
          const cascade = action.id === "regenerate_cascade";
          const res = await apiPost(
            path,
            isRegen
              ? { instruction: "", cascade, paragraph_id: paragraphId, feedback_hint: "" }
              : { reason: "user_action" },
            {
              withAuth: true,
              idempotencyKey: generateIdempotencyKey(),
              ifMatch: module.version,
            },
          );

          if (!res.ok) {
            setToast({ type: "err", message: `${action.label}失败：${res.error.code} · ${res.error.message}` });
            return;
          }

          if (isRegen) {
            setToast({ type: "ok", message: `模块「${module.title}」已开始重生，完成后会自动刷新` });
            const updated = await waitForCanvasModuleUpdate(module.moduleId, module.version);
            if (updated) {
              setToast({ type: "ok", message: `模块「${module.title}」已刷新到最新结果` });
            } else {
              setToast({ type: "ok", message: `模块「${module.title}」已提交，可手动刷新确认最新结果` });
            }
          } else {
            await refreshCanvas();
            setToast({ type: "ok", message: `模块「${module.title}」已 ${action.label}` });
          }
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          setToast({ type: "err", message: `${action.label}异常：${message}` });
        } finally {
          setModuleBusy(module.moduleId, false);
        }
      } else {
        setToast({ type: "ok", message: `模块「${module.title}」· ${action.label}` });
      }
    },
    [taskId, realtime, setModuleBusy, refreshCanvas, waitForCanvasModuleUpdate, canWriteTask],
  );

  const handlePause = useCallback(async () => {
    if (!taskId) return;
    if (!canWriteTask) {
      setToast({ type: "err", message: "只读成员不能控制任务" });
      return;
    }
    setControlBusy(true);
    try {
      const res = await apiPost(`/tasks/${encodeURIComponent(taskId)}/pause`, {}, { withAuth: true });
      if (!res.ok) setToast({ type: "err", message: `暂停失败：${res.error.code} · ${res.error.message}` });
      else setToast({ type: "ok", message: "已暂停" });
    } finally {
      setControlBusy(false);
    }
  }, [taskId, canWriteTask]);

  const handleResume = useCallback(async () => {
    if (!taskId) return;
    if (!canWriteTask) {
      setToast({ type: "err", message: "只读成员不能控制任务" });
      return;
    }
    setControlBusy(true);
    try {
      const res = await apiPost(`/tasks/${encodeURIComponent(taskId)}/resume`, {}, { withAuth: true });
      if (!res.ok) setToast({ type: "err", message: `恢复失败：${res.error.code} · ${res.error.message}` });
      else setToast({ type: "ok", message: "已恢复" });
    } finally {
      setControlBusy(false);
    }
  }, [taskId, canWriteTask]);

  const handleCancel = useCallback(async () => {
    if (!taskId) return;
    if (!canWriteTask) {
      setToast({ type: "err", message: "只读成员不能控制任务" });
      return;
    }
    setControlBusy(true);
    try {
      const res = await apiPost(`/tasks/${encodeURIComponent(taskId)}/cancel`, {}, { withAuth: true });
      if (!res.ok) setToast({ type: "err", message: `取消失败：${res.error.code} · ${res.error.message}` });
      else setToast({ type: "ok", message: "任务已取消" });
    } finally {
      setControlBusy(false);
    }
  }, [taskId, canWriteTask]);

  const downloadExport = useCallback(
    async (format: "excel" | "json") => {
      if (!taskId || !realtime) {
        setToast({ type: "err", message: "请先发起一次真实分析再导出（当前为演示画布）" });
        return;
      }
      try {
        const token = getAuthToken();
        const res = await fetch(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/export/${format}`, {
          method: "GET",
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        });
        if (!res.ok) {
          const text = await res.text();
          setToast({ type: "err", message: `导出失败：${res.status} ${text}` });
          return;
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${taskId}.${format === "excel" ? "xlsx" : "json"}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setToast({ type: "ok", message: `导出成功：${format.toUpperCase()}` });
      } catch (e) {
        setToast({ type: "err", message: `导出异常：${String(e)}` });
      }
    },
    [taskId, realtime],
  );

  const toastBanner = useMemo(() => {
    if (!toast) return null;
    return (
      <div
        className={`pointer-events-auto fixed right-6 top-6 z-50 rounded-md border px-3 py-2 text-[12px] shadow-lg ${toast.type === "ok"
          ? "border-[#D7EAD9] bg-[#F0FAF1] text-[#3D8C40]"
          : "border-[#FDD8D8] bg-[#FFF2F2] text-[#C62828]"
          }`}
      >
        {toast.message}
      </div>
    );
  }, [toast]);

  return (
    <>
      <WorkspaceUrlSync />
      {toastBanner}
      <div className="flex flex-1 overflow-hidden">
        <ChatPanel
          cookieHealth={cookieHealth}
          taskId={taskId}
          userInput={lastUserInput}
          messages={messages}
          streamState={streamState}
          connectionStatus={connectionStatus}
          creating={creating}
          controlBusy={controlBusy}
          streamError={streamError}
          canvasCollapsed={canvasCollapsed}
          showCanvasToggle={canShowCanvas}
          canWriteConversation={canWriteConversation}
          canWriteTask={canWriteTask}
          onToggleCanvas={toggleCanvasCollapsed}
          onSubmit={handleSubmit}
          onNewAnalysis={handleNewAnalysis}
          onReconnect={reconnectStream}
          onPause={handlePause}
          onResume={handleResume}
          onCancel={handleCancel}
        />
        <CanvasView
          canvas={canvasModel}
          collapsed={!canShowCanvas || canvasCollapsed}
          onToggleCollapsed={() => setCanvasCollapsed(!canvasCollapsed)}
          onRefresh={() => void handleRefreshCanvas()}
          refreshBusy={canvasRefreshing}
          refreshDisabled={!taskId || !realtime}
          onExportExcel={() => downloadExport("excel")}
          onExportJson={() => downloadExport("json")}
          onModuleAction={canWriteTask ? handleModuleAction : undefined}
          busyModuleIds={busyModuleIds}
          paragraphEnv={paragraphEnv}
        />
      </div>
    </>
  );
}

/**
 * URL 同步：
 * - `?new=<ts>` → 触发 reset，然后清 query（侧边栏"新建分析"按钮用）
 * - `?task=<id>` → 一次性加载该任务后立即清除 URL 参数（历史任务页跳转用）
 * - `?conversation=<id>` → 恢复历史对话；若绑定 active_task_id，同时恢复 Canvas
 *
 * URL 参数消费后会清除，避免刷新时重复触发。
 */
function WorkspaceUrlSync() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { resetWorkspace, setTaskId, setConversation } = useWorkspace();

  const newFlag = searchParams.get("new");
  const urlTask = searchParams.get("task");
  const urlConversation = searchParams.get("conversation");

  // ?new=<ts> → reset 回初始界面
  useEffect(() => {
    if (!newFlag) return;
    resetWorkspace();
    router.replace("/workspace");
  }, [newFlag, resetWorkspace, router]);

  // ?conversation=<id> → 恢复对话；有关联任务时同步恢复 Canvas
  useEffect(() => {
    if (newFlag || !urlConversation) return;
    let cancelled = false;
    void (async () => {
      const res = await getConversation(urlConversation);
      if (cancelled) return;
      if (res.ok) {
        setConversation(res.data.conversation.conversation_id, res.data.messages);
        if (res.data.conversation.active_task_id) {
          setTaskId(res.data.conversation.active_task_id);
        } else {
          setTaskId(null);
        }
      }
      router.replace("/workspace");
    })();
    return () => {
      cancelled = true;
    };
  }, [urlConversation, newFlag, setConversation, setTaskId, router]);

  // ?task=<id> → 一次性加载任务，然后清 URL 参数
  useEffect(() => {
    if (newFlag || urlConversation || !urlTask) return;
    let cancelled = false;
    void (async () => {
      const res = await apiGet<{ raw_input?: string }>(
        `/tasks/${encodeURIComponent(urlTask)}`,
        { withAuth: true },
      );
      if (cancelled) return;
      setTaskId(urlTask, res.ok ? res.data.raw_input ?? null : null);
      router.replace("/workspace");
    })();
    return () => {
      cancelled = true;
    };
  }, [urlTask, urlConversation, newFlag, setTaskId, router]);

  return null;
}
