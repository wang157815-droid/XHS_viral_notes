"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useSession } from "@/lib/session-context";
import { useWorkspace } from "@/lib/workspace-context";
import { CanvasView } from "@/components/workspace/canvas-view";
import { ChatPanel, type AdvancedConfig } from "@/components/workspace/chat-panel";
import { ResizeDivider } from "@/components/workspace/resize-divider";
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
import {
  createConversation,
  getConversation,
  sendConversationMessageStream,
  uploadConversationFile,
} from "@/lib/conversation-api";
import type {
  CanvasSchema,
  ChatMessage,
  ConversationStreamEvent,
  ConversationSurface,
} from "@/lib/contracts";

const SURFACE_QUERY_VALUES = new Set<string>(["insight", "hotspot", "post_investment"]);

function surfaceFromQuery(raw: string | null): ConversationSurface | undefined {
  if (!raw || !SURFACE_QUERY_VALUES.has(raw)) return undefined;
  return raw as ConversationSurface;
}

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
  const searchParams = useSearchParams();
  const router = useRouter();
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

  // 各会话草稿内容（key = conversationId），切换会话时保留、切回时恢复
  const conversationDraftsRef = useRef<Record<string, string>>({});
  // 对话流 AbortController，点停止按钮时 abort
  const convStreamAbortRef = useRef<AbortController | null>(null);

  // 纯 UI 本地状态（切页丢失无所谓）
  const [creating, setCreating] = useState(false);
  const [, setControlBusy] = useState(false);
  const [canvasRefreshing, setCanvasRefreshing] = useState(false);
  const [toast, setToast] = useState<{ type: "ok" | "err"; message: string } | null>(null);
  /** 可拖拽分隔器控制 ChatPanel 宽度 [300, 768] px */
  const [chatWidth, setChatWidth] = useState(380);
  const handleResizeDrag = useCallback((dx: number) => {
    setChatWidth((w) => Math.min(768, Math.max(300, w + dx)));
  }, []);
  const regenAnchorRef = useRef<{ moduleId: string; paragraphId: string } | null>(null);
  const registerRegenerateAnchor = useCallback((moduleId: string, paragraphId: string) => {
    regenAnchorRef.current = { moduleId, paragraphId };
  }, []);
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
    async ({
      rawInput,
      keywords,
      advanced,
      attachments = [],
      knowledgeRefs = [],
      localMediaFiles,
    }: {
      rawInput: string;
      keywords: string[];
      advanced: AdvancedConfig;
      attachments?: Array<{
        file_id: string;
        filename: string;
        mime_type: string;
        size_bytes: number;
        storage_subpath: string;
      }>;
      knowledgeRefs?: Array<{ doc_id: string; title?: string }>;
      localMediaFiles?: File[];
    }) => {
      if (!canWriteConversation) {
        setToast({ type: "err", message: "只读成员不能发送消息或发起分析" });
        return false;
      }

      const mergedAttachments: Array<{
        file_id: string;
        filename: string;
        mime_type: string;
        size_bytes: number;
        storage_subpath: string;
      }> = [...(attachments ?? [])];
      // 兼容旧逻辑：如果 attachments 已包含预上传结果，直接使用；否则从 localMediaFiles 上传
      if (!mergedAttachments.length && localMediaFiles?.length) {
        for (const file of localMediaFiles) {
          const uploaded = await uploadConversationFile(file);
          if (!uploaded.ok) {
            setToast({
              type: "err",
              message: `「${file.name}」上传失败：${uploaded.error.message}`,
            });
            return false;
          }
          mergedAttachments.push(uploaded.data);
        }
      }

      setCreating(true);
      setToast(null);
      const clientMessageId = generateIdempotencyKey();
      let currentConversationId = conversationId ?? "pending_conversation";
      const streamCtrl = new AbortController();
      convStreamAbortRef.current = streamCtrl;
      // 提前声明，catch 块（AbortError 处理）需要访问
      let assistantMessageId = `stream_${clientMessageId}`;
      let assistantContent = "";
      let assistantInserted = false;
      let thinkContent = "";
      let thinkDurationMs = 0;
      try {
        if (!conversationId) {
          const surface = surfaceFromQuery(searchParams.get("surface")) ?? "insight";
          const created = await createConversation(
            rawInput.trim().slice(0, 24) || (localMediaFiles?.[0]?.name ?? "").slice(0, 24) || "新对话",
            { surface },
          );
          if (!created.ok) {
            setToast({
              type: "err",
              message: `创建会话失败：${created.error.code} · ${created.error.message}`,
            });
            return false;
          }
          currentConversationId = created.data.conversation_id;
          setConversation(currentConversationId, []);
          // 显式同步 URL，避免 WorkspaceUrlSync Effect 2（URL 反写）与 Effect 3（加载）打架
          const newConvParams = new URLSearchParams();
          newConvParams.set("conversation", currentConversationId);
          const newConvSurface = surfaceFromQuery(searchParams.get("surface"));
          if (newConvSurface) newConvParams.set("surface", newConvSurface);
          router.replace(`/workspace?${newConvParams.toString()}`, { scroll: false });
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
          debug: { optimistic: true, knowledge_refs: knowledgeRefs },
          attachments: mergedAttachments as unknown as Array<Record<string, unknown>>,
          created_at: new Date().toISOString(),
        };
        appendConversationMessages([optimisticMessage]);

        // 积累本轮思考内容（供最终消息渲染思考框用）
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
          // 将本轮积累的思考内容合并进 debug，供渲染思考框使用
          const enriched: ChatMessage =
            thinkContent
              ? { ...message, debug: { ...(message.debug ?? {}), think_content: thinkContent, think_duration_ms: thinkDurationMs } }
              : message;
          upsertAssistant(enriched);
          const handoff = message.task_handoff;
          if (handoff?.task_id) {
            startTask(handoff.task_id, rawInput);
            // 同步更新会话缓存中的 activeTaskId，避免切走再切回时 taskId 被还原为 null
            // （convCacheRef 在首次加载会话时写入，但 startTask 后 active_task_id 已变更）
            if (currentConversationId) {
              const existing = convCacheRef.current.get(currentConversationId);
              convCacheRef.current.set(currentConversationId, {
                messages: existing?.messages ?? [],
                activeTaskId: handoff.task_id,
                lastUserInput: rawInput,
              });
            }
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
            attachments: mergedAttachments as unknown as Array<Record<string, unknown>>,
            knowledgeRefs,
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
            if (event.type === "thinking_done") {
              thinkContent = event.think || thinkContent;
              thinkDurationMs = event.duration_ms || 0;
              upsertAssistant(makeAssistantMessage({ debug: { streaming: true, thinking_live: false, think_content: thinkContent, think_duration_ms: thinkDurationMs } }));
              return;
            }
            if (event.type === "thinking_start") {
              thinkContent = "";
              upsertAssistant(makeAssistantMessage({ debug: { streaming: true, thinking_live: true, think_content: "", think_duration_ms: 0 } }));
              return;
            }
            if (event.type === "thinking_delta") {
              thinkContent += event.delta;
              upsertAssistant(makeAssistantMessage({ debug: { streaming: true, thinking_live: true, think_content: thinkContent, think_duration_ms: 0 } }));
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
            if (event.type === "conversation_updated") {
              // LLM 标题生成完毕，此时才刷新侧边栏（避免先出现旧标题再闪变）
              if (typeof window !== "undefined") {
                window.dispatchEvent(new Event("redmuse-conversations-refresh"));
              }
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
          streamCtrl.signal,
        );
        return true;
      } catch (error) {
        // 用户主动停止：静默处理，保留已积累的内容并移除流式指示器
        if (error instanceof Error && error.name === "AbortError") {
          if (assistantInserted) {
            replaceConversationMessage(assistantMessageId, {
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
              debug: { streaming: false },
              created_at: new Date().toISOString(),
            });
          }
          return false;
        }
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
        return false;
      } finally {
        convStreamAbortRef.current = null;
        setCreating(false);
        if (typeof window !== "undefined") {
          window.dispatchEvent(new Event("redmuse-conversations-refresh"));
        }
      }
    },
    [
      appendConversationMessages,
      conversationId,
      replaceConversationMessage,
      searchParams,
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
    const surface = surfaceFromQuery(searchParams.get("surface"));
    router.replace(
      surface ? `/workspace?surface=${encodeURIComponent(surface)}` : "/workspace",
      { scroll: false },
    );
  }, [resetWorkspace, router, searchParams]);

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
        action.id === "regen_sheet2_narrative" ||
        action.id === "rename_models" ||
        action.id === "regenerate" ||
        action.id === "regenerate_cascade" ||
        action.id === "delete" ||
        action.id === "restore"
      ) {
        const isRegen =
          action.id === "regen_sheet2_narrative" ||
          action.id === "rename_models" ||
          action.id === "regenerate" ||
          action.id === "regenerate_cascade";
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
          const actionHint =
            action.id === "regen_sheet2_narrative" ? "regen_sheet2_narrative" :
            action.id === "rename_models" ? "rename_models" : "";
          const res = await apiPost(
            path,
            isRegen
              ? { instruction: "", cascade, paragraph_id: paragraphId, feedback_hint: "", action_hint: actionHint }
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

  // 统一停止：对话流式中 → abort fetch；任务执行中 → cancel 任务
  const handleStop = useCallback(async () => {
    if (creating) {
      convStreamAbortRef.current?.abort();
    } else if (taskId) {
      await handleCancel();
    }
  }, [creating, taskId, handleCancel]);

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
        // 从响应头解析服务端生成的文件名，解析不到则降级
        const cd = res.headers.get("Content-Disposition") ?? "";
        const nameMatch = cd.match(/filename\*=UTF-8''([^;]+)/i)
          ?? cd.match(/filename="([^"]+)"/i);
        const filename = nameMatch
          ? decodeURIComponent(nameMatch[1])
          : `${taskId}.${format === "excel" ? "xlsx" : "json"}`;
        a.download = filename;
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
          key={conversationId ?? "new"}
          cookieHealth={cookieHealth}
          taskId={taskId}
          userInput={lastUserInput}
          initialInput={conversationDraftsRef.current[conversationId ?? ""] ?? ""}
          onDraftChange={(draft) => {
            if (conversationId) conversationDraftsRef.current[conversationId] = draft;
          }}
          messages={messages}
          streamState={streamState}
          connectionStatus={connectionStatus}
          creating={creating}
          streamError={streamError}
          canvasCollapsed={canvasCollapsed}
          showCanvasToggle={canShowCanvas}
          canWriteConversation={canWriteConversation}
          canReadKnowledge={can("knowledge.read")}
          chatWidth={chatWidth}
          onToggleCanvas={toggleCanvasCollapsed}
          onSubmit={handleSubmit}
          onReconnect={reconnectStream}
          onStop={handleStop}
        />
        {!canvasCollapsed && canShowCanvas ? (
          <>
            <ResizeDivider onDrag={handleResizeDrag} />
            <div className="min-w-[504px] flex-1 flex flex-col overflow-hidden">
              <CanvasView
                canvas={canvasModel}
                collapsed={false}
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
        ) : null}
      </div>
    </>
  );
}

/**
 * URL 同步：
 * - `?new=<ts>` → 触发 reset，然后清 query（侧边栏「新建」用）
 * - `?task=<id>` → 一次性加载该任务后清除 URL 参数（历史任务页跳转用）
 * - `?conversation=<id>` → 拉取并恢复对话；**保留** `conversation` 在地址栏，便于刷新、分享、收藏
 * - 当上下文已有 `conversationId` 但与地址栏不一致时，写入 `?conversation=`（新建会话后等）
 */
function WorkspaceUrlSync() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { resetWorkspace, setTaskId, setConversation, conversationId, messages, taskId, lastUserInput } = useWorkspace();

  const newFlag = searchParams.get("new");
  const urlTask = searchParams.get("task");
  const urlConversation = searchParams.get("conversation");
  const surfaceQ = searchParams.get("surface");

  // conversationId / messages / taskId 通过 ref 读取，避免加入依赖导致 Effect 3 频繁重建
  const conversationIdRef = useRef(conversationId);
  conversationIdRef.current = conversationId;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const taskIdRef = useRef(taskId);
  taskIdRef.current = taskId;
  const lastUserInputRef = useRef(lastUserInput);
  lastUserInputRef.current = lastUserInput;

  // 会话数据缓存（同 session 内复用，切回已访问的会话零延迟）
  const convCacheRef = useRef(
    new Map<string, { messages: import("@/lib/contracts").ChatMessage[]; activeTaskId: string | null; lastUserInput: string | null }>(),
  );
  // 当前在途请求的 AbortController，下次切换时手动 abort（不放在 cleanup 里）
  const abortRef = useRef<AbortController | null>(null);

  // ?new=<ts> → reset 回初始界面（保留 surface 供新建会话 metadata 使用）
  useEffect(() => {
    if (!newFlag) return;
    resetWorkspace();
    const surface = surfaceFromQuery(surfaceQ);
    router.replace(surface ? `/workspace?surface=${encodeURIComponent(surface)}` : "/workspace", { scroll: false });
  }, [newFlag, surfaceQ, resetWorkspace, router]);

  // 上下文会话 id 与地址栏不一致时，补上 `conversation`（创建会话、继续对话等）
  // 注意：若 URL 里已有 conversation 参数（用户主动导航），不干预 — Effect 3 会处理加载
  useEffect(() => {
    if (newFlag) return;
    if (!conversationId) return;
    if (searchParams.get("conversation") === conversationId) return;
    // URL 里已有某个会话 ID（与当前不同）→ 说明用户刚通过侧边栏/链接切换了会话
    // Effect 3 会负责加载，这里不能覆盖回旧 ID，否则两个 effect 会互相打架陷入死循环
    if (searchParams.get("conversation")) return;
    const params = new URLSearchParams(searchParams.toString());
    params.delete("new");
    params.delete("task");
    params.set("conversation", conversationId);
    const qs = params.toString();
    router.replace(qs ? `/workspace?${qs}` : "/workspace", { scroll: false });
  }, [conversationId, newFlag, router, searchParams]);

  // ?conversation=<id> → 恢复对话
  // conversationId 故意不加入依赖：通过 ref 读取，避免 setConversation 触发 cleanup abort 在途请求
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (newFlag || !urlConversation) return;
    // 已是当前会话，无需重新加载
    if (conversationIdRef.current === urlConversation) return;

    // 切走前把当前会话最新的 messages 和 taskId 写回缓存，
    // 保证切回时（缓存命中）拿到的是最新状态而非首次加载的快照
    const outgoingId = conversationIdRef.current;
    if (outgoingId) {
      convCacheRef.current.set(outgoingId, {
        messages: messagesRef.current,
        activeTaskId: taskIdRef.current,
        lastUserInput: lastUserInputRef.current,
      });
    }

    // 取消上一个在途请求（防止慢→快切换时旧数据覆盖新数据）
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    // ① 缓存命中：立即填充，零延迟
    const cached = convCacheRef.current.get(urlConversation);
    if (cached) {
      setConversation(urlConversation, cached.messages);
      setTaskId(cached.activeTaskId, cached.lastUserInput);
      return;
    }

    // ② 缓存未命中：立即清空消息让 UI 秒响应；taskId 不动，SSE 保持连接不断任务
    setConversation(urlConversation, []);

    void (async () => {
      const res = await getConversation(urlConversation);
      if (ctrl.signal.aborted) return;

      const surface = surfaceFromQuery(surfaceQ);
      const fallbackPath = surface ? `/workspace?surface=${encodeURIComponent(surface)}` : "/workspace";
      if (!res.ok) {
        router.replace(fallbackPath, { scroll: false });
        return;
      }
      const { conversation, messages } = res.data;
      const activeTaskId = conversation.active_task_id ?? null;

      // ③ 写缓存，下次切回零延迟（lastUserInput 首次加载时未知，置 null；startTask 时会更新）
      convCacheRef.current.set(conversation.conversation_id, { messages, activeTaskId, lastUserInput: null });

      // ④ 填充消息并切换到正确 taskId（此时才改 taskId，SSE 切换到新任务）
      setConversation(conversation.conversation_id, messages);
      setTaskId(activeTaskId);

      const params = new URLSearchParams();
      params.set("conversation", conversation.conversation_id);
      if (surface) params.set("surface", surface);
      router.replace(`/workspace?${params.toString()}`, { scroll: false });
    })();
    // 不在 cleanup 里 abort：由下次切换时手动 abort，避免 conversationId 变化触发误 abort
  }, [urlConversation, newFlag, surfaceQ, setConversation, setTaskId, router]);

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
      router.replace("/workspace", { scroll: false });
    })();
    return () => {
      cancelled = true;
    };
  }, [urlTask, urlConversation, newFlag, setTaskId, router]);

  return null;
}
