"use client";

import Image from "next/image";
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import type {
  AdvancedConfig,
  ChatMessage,
  ConversationAttachmentPayload,
  CookieHealth,
  KnowledgeCitation,
  KnowledgeRefPayload,
} from "@/lib/contracts";
import { DEFAULT_ADVANCED } from "@/lib/contracts";
import type { SseClientStatus } from "@/lib/sse/event-source-client";
import type { TaskStreamState } from "@/lib/sse/event-reducer";
import { motion, useReducedMotion } from "motion/react";
import { BrandLink } from "@/components/layout/brand-link";
import { AgentTimeline } from "./agent-timeline";
import {
  AdvancedParamsMenuSection,
  advancedOverridesDefault,
  changedAdvancedLabels,
  SlidersIcon,
} from "./composer-shared";
import {
  getActiveKbMention,
  KnowledgeMentionList,
  useKbDocuments,
  type KbDocRow,
} from "./knowledge-mention";
import { PromptComposer, type LocalMediaDraft } from "./prompt-composer";
import { ConversationUserAttachments, type AttachmentRecord } from "./conversation-attachments";
import {
  ComposerDraftThumbnailsRow,
  isAllowedComposerAttachmentFile,
} from "./composer-draft-thumbnails";
import { uploadConversationFile, type ConversationUploadResult } from "@/lib/conversation-api";
import { Square } from "lucide-react";

function newLocalDraftId(): string {
  return `lm_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

interface ChatPanelProps {
  cookieHealth: CookieHealth | null;
  taskId: string | null;
  userInput: string | null;
  /** 恢复当前会话的草稿内容（切换会话时保留） */
  initialInput?: string;
  /** 每次输入内容变化时回调，供父级持久化草稿 */
  onDraftChange?: (draft: string) => void;
  messages: ChatMessage[];
  streamState: TaskStreamState;
  connectionStatus: SseClientStatus;
  creating: boolean;
  streamError: { code: string; message: string } | null;
  canvasCollapsed: boolean;
  showCanvasToggle: boolean;
  canWriteConversation: boolean;
  /** 对话态下 ChatPanel 宽度（px），来自父级可拖拽状态；初始态不限制 */
  chatWidth?: number;
  onToggleCanvas: () => void;
  onSubmit: (input: {
    rawInput: string;
    keywords: string[];
    advanced: AdvancedConfig;
    attachments?: ConversationAttachmentPayload[];
    knowledgeRefs?: KnowledgeRefPayload[];
    localMediaFiles?: File[];
  }) => Promise<boolean | void>;
  onReconnect: () => void;
  onStop: () => Promise<void> | void;
  canReadKnowledge: boolean;
}

export type { AdvancedConfig } from "@/lib/contracts";

const STATUS_BADGE: Record<SseClientStatus, { bg: string; color: string; label: string }> = {
  idle: { bg: "#F5F3F0", color: "#8A8580", label: "未连接" },
  connecting: { bg: "#FFF8E6", color: "#8B6914", label: "连接中" },
  open: { bg: "#F0FAF0", color: "#3D8C40", label: "已接入推送" },
  reconnecting: { bg: "#FFF8E6", color: "#8B6914", label: "重连中" },
  closed: { bg: "#F5F3F0", color: "#8A8580", label: "已完成" },
  failed: { bg: "rgba(242, 142, 130, 0.14)", color: "#c2716b", label: "推送异常" },
};

const XHS_AUTH_SETTINGS_HREF = "/settings#xhs-credential";

const LOCAL_MEDIA_ACCEPT = "image/*,.pdf,.txt,.md,.doc,.docx";

/** 底部对话输入框最大高度（超出后内部滚动） */
const MAX_FOOTER_TEXTAREA_HEIGHT = 200;


export function ChatPanel({
  cookieHealth,
  taskId,
  userInput,
  initialInput,
  onDraftChange,
  messages,
  streamState,
  connectionStatus,
  creating,
  streamError,
  canvasCollapsed,
  showCanvasToggle,
  canWriteConversation,
  chatWidth,
  onToggleCanvas,
  onSubmit,
  onReconnect,
  onStop,
  canReadKnowledge,
}: ChatPanelProps) {
  const reduceMotion = useReducedMotion();
  const [input, setInput] = useState(initialInput ?? "");
  // 文字接近行末（>75% 行宽）时展开，清空时收起，中间不回收（单向）
  // 初始值从 initialInput 派生：草稿含换行符则直接以展开态挂载，避免切回会话时出现折叠闪烁
  const [isFooterExpandedByContent, setIsFooterExpandedByContent] = useState(
    (initialInput ?? "").includes("\n"),
  );
  // 用 ref 持有回调，避免 useEffect 依赖项频繁变化
  const onDraftChangeRef = useRef(onDraftChange);
  onDraftChangeRef.current = onDraftChange;
  // input 变化时通知父级保存草稿（key 切换时 initialInput 已恢复，首次同步无副作用）
  useEffect(() => {
    onDraftChangeRef.current?.(input);
  }, [input]);
  const [advanced, setAdvanced] = useState<AdvancedConfig>(DEFAULT_ADVANCED);
  const [knowledgeRefs, setKnowledgeRefs] = useState<KnowledgeRefPayload[]>([]);
  const [localMediaDrafts, setLocalMediaDrafts] = useState<LocalMediaDraft[]>([]);
  const [inputCaret, setInputCaret] = useState(0);
  const [plusFooterOpen, setPlusFooterOpen] = useState(false);
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const localMediaInputRef = useRef<HTMLInputElement | null>(null);
  const plusFooterRef = useRef<HTMLDivElement | null>(null);
  const footerInputRef = useRef<HTMLTextAreaElement | null>(null);
  // 自动跟随滚动相关
  const autoFollowRef = useRef(true);
  const [showBackToBottom, setShowBackToBottom] = useState(false);
  const scrollThrottleRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const msgContainerRef = useRef<HTMLDivElement | null>(null);

  const hasComposerExtras =
    knowledgeRefs.length > 0 ||
    localMediaDrafts.length > 0 ||
    advancedOverridesDefault(advanced);
  // 文字接近行末 或 有标签 → 展开双行；清空 → 收起单行
  const isFooterExpanded = hasComposerExtras || isFooterExpandedByContent;

  const cookieBlocked = cookieHealth?.status === "expired";
  const hasTask = !!taskId;
  const hasMessages = messages.length > 0;
  // 任务执行中（pending/queued/running）时显示停止按钮；用宽松判断避免 creating→task 过渡闪烁
  const isTaskRunning = !!(taskId && !["completed", "failed", "cancelled"].includes(streamState.status));
  // 全局"正在生成"：对话流式中 OR 任务执行中
  const isGenerating = creating || isTaskRunning;
  const isInitialState = !hasTask && !hasMessages;
  const lastMessageId = messages[messages.length - 1]?.message_id;
  const badge = STATUS_BADGE[connectionStatus];

  const parseKeywords = (text: string): string[] => {
    const matches = text.match(/「([^」]+)」|「(.+?)」/g) ?? [];
    return matches.map((m) => m.replace(/[「」]/g, "")).slice(0, 5);
  };

  const kbMention = useMemo(() => getActiveKbMention(input, inputCaret), [input, inputCaret]);
  const kbMentionLoad = kbMention !== null && canReadKnowledge;
  const { docs: kbDocRows, loading: kbDocsLoading } = useKbDocuments(kbMentionLoad);

  useEffect(() => {
    if (!plusFooterOpen) return;
    const onDoc = (e: MouseEvent) => {
      const el = plusFooterRef.current;
      if (el && !el.contains(e.target as Node)) setPlusFooterOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [plusFooterOpen]);

  const removeKnowledgeRef = (docId: string) => {
    setKnowledgeRefs((prev) => prev.filter((r) => r.doc_id !== docId));
  };

  const removeLocalMediaDraft = (id: string) => {
    setLocalMediaDrafts((prev) => prev.filter((d) => d.id !== id));
  };

  const appendLocalMediaDrafts = useCallback((files: File[]) => {
    const allowed = files.filter(isAllowedComposerAttachmentFile);
    if (!allowed.length) return;
    const newDrafts: LocalMediaDraft[] = allowed.map((file) => ({
      id: newLocalDraftId(),
      file,
      uploadStatus: "uploading" as const,
    }));
    setLocalMediaDrafts((prev) => [...prev, ...newDrafts].slice(0, 20));

    // 预上传：选择文件后立即上传，发送消息时直接使用已上传结果
    for (const draft of newDrafts) {
      uploadConversationFile(draft.file)
        .then((result) => {
          if (result.ok) {
            setLocalMediaDrafts((prev) =>
              prev.map((d) =>
                d.id === draft.id
                  ? {
                      ...d,
                      uploadStatus: "done" as const,
                      uploaded: result.data,
                      parsed: result.data.parsed,
                    }
                  : d,
              ),
            );
          } else {
            setLocalMediaDrafts((prev) =>
              prev.map((d) =>
                d.id === draft.id
                  ? { ...d, uploadStatus: "error" as const, uploadError: result.error.message }
                  : d,
              ),
            );
          }
        })
        .catch((err: Error) => {
          setLocalMediaDrafts((prev) =>
            prev.map((d) =>
              d.id === draft.id
                ? { ...d, uploadStatus: "error" as const, uploadError: err.message }
                : d,
            ),
          );
        });
    }
  }, []);

  const [composerDragActive, setComposerDragActive] = useState(false);
  const composerDragDepth = useRef(0);

  const onComposerDragEnter = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (creating || !canWriteConversation) return;
      composerDragDepth.current += 1;
      setComposerDragActive(true);
    },
    [creating, canWriteConversation],
  );

  const onComposerDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    composerDragDepth.current -= 1;
    if (composerDragDepth.current <= 0) {
      composerDragDepth.current = 0;
      setComposerDragActive(false);
    }
  }, []);

  const onComposerDragOver = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!creating && canWriteConversation) e.dataTransfer.dropEffect = "copy";
    },
    [creating, canWriteConversation],
  );

  const onComposerDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      composerDragDepth.current = 0;
      setComposerDragActive(false);
      if (creating || !canWriteConversation) return;
      appendLocalMediaDrafts(Array.from(e.dataTransfer.files ?? []));
    },
    [creating, canWriteConversation, appendLocalMediaDrafts],
  );

  const resetAdvancedField = (key: keyof AdvancedConfig) => {
    setAdvanced((prev) => ({ ...prev, [key]: DEFAULT_ADVANCED[key] }));
  };

  const onLocalMediaInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    const fileArray = files ? Array.from(files) : [];
    e.target.value = "";
    if (!fileArray.length) return;
    appendLocalMediaDrafts(fileArray);
  };

  const toggleKbDoc = (doc: KbDocRow) => {
    setKnowledgeRefs((prev) => {
      const exists = prev.some((r) => r.doc_id === doc.doc_id);
      if (exists) return prev.filter((r) => r.doc_id !== doc.doc_id);
      if (prev.length >= 12) return prev;
      return [...prev, { doc_id: doc.doc_id, title: doc.name }];
    });
  };

  const insertKbMentionAtFooter = () => {
    if (!canReadKnowledge || creating || !canWriteConversation) return;
    setPlusFooterOpen(false);
    const el = footerInputRef.current;
    const caret = el?.selectionStart ?? input.length;
    const next = input.slice(0, caret) + "@" + input.slice(caret);
    setInput(next);
    const pos = caret + 1;
    setInputCaret(pos);
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(pos, pos);
    });
  };

  const handleFooterInputChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    setInputCaret(e.target.selectionStart ?? e.target.value.length);
    footerAutoResize(e.target);
  };

  const handleKbMentionPickFooter = (doc: KbDocRow) => {
    const m = getActiveKbMention(input, inputCaret);
    if (!m || !canReadKnowledge) return;
    const end = inputCaret;
    toggleKbDoc(doc);
    const next = input.slice(0, m.start) + input.slice(end);
    setInput(next);
    const pos = m.start;
    setInputCaret(pos);
    requestAnimationFrame(() => {
      const inp = footerInputRef.current;
      inp?.focus();
      inp?.setSelectionRange(pos, pos);
    });
  };

  const handleSend = async () => {
    const text = input.trim();
    if (creating) return;
    if (!text && !localMediaDrafts.length) return;

    // 发送新消息：强制滚到底部并开启自动跟随
    autoFollowRef.current = true;
    setShowBackToBottom(false);
    bottomRef.current?.scrollIntoView({ behavior: "instant", block: "end" });

    const kw = parseKeywords(text);
    const refs = [...knowledgeRefs];

    // 预上传：选择文件时已自动上传，发送时直接使用已成功上传的附件
    // 上传中的文件不等待，未上传的文件会被丢弃
    const uploadedAttachments: ConversationAttachmentPayload[] = localMediaDrafts
      .filter((d) => d.uploaded)
      .map((d) => d.uploaded!);

    if (!text && !uploadedAttachments.length) return;

    // 移除上传失败的草稿
    const hasErrors = localMediaDrafts.some((d) => d.uploadStatus === "error");
    if (hasErrors) {
      setLocalMediaDrafts((prev) => prev.filter((d) => d.uploadStatus !== "error"));
    }

    // 发送前立即清空 composer，避免发送期间显示"正在上传"
    setInput("");
    setInputCaret(0);
    setKnowledgeRefs([]);
    setLocalMediaDrafts([]);
    // 立即重置底部 textarea 高度，无需等待 effect
    if (footerInputRef.current) {
      footerInputRef.current.style.height = "auto";
      footerInputRef.current.style.overflowY = "hidden";
    }

    const ok = await onSubmit({
      rawInput: text,
      keywords: kw,
      advanced,
      attachments: uploadedAttachments.length ? uploadedAttachments : [],
      knowledgeRefs: refs,
      localMediaFiles: undefined,
    });
    // 发送失败不恢复输入（让用户重新输入或附件已在预上传中可再次选择）
  };

  const handleSuggestion = async (text: string) => {
    const kw = parseKeywords(text);
    const refs = [...knowledgeRefs];
    const uploadedAttachments: ConversationAttachmentPayload[] = localMediaDrafts
      .filter((d) => d.uploaded)
      .map((d) => d.uploaded!);

    // 发送新消息：强制滚到底部并开启自动跟随
    autoFollowRef.current = true;
    setShowBackToBottom(false);
    bottomRef.current?.scrollIntoView({ behavior: "instant", block: "end" });

    // 发送前立即清空 composer
    setInput("");
    setInputCaret(0);
    setKnowledgeRefs([]);
    setLocalMediaDrafts([]);

    const ok = await onSubmit({
      rawInput: text,
      keywords: kw,
      advanced,
      attachments: uploadedAttachments.length ? uploadedAttachments : [],
      knowledgeRefs: refs,
      localMediaFiles: undefined,
    });
    // 发送失败不恢复输入
    if (ok === false) return;
  };

  // ── 自动滚动辅助函数 ──────────────────────────────────────────
  const BOTTOM_THRESHOLD = 80;
  const SCROLL_THROTTLE_MS = 100;

  const isNearBottom = useCallback(() => {
    const el = scrollAreaRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD;
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    bottomRef.current?.scrollIntoView({ behavior, block: "end" });
  }, []);

  const scheduleScrollToBottom = useCallback(() => {
    if (!autoFollowRef.current) return;
    if (scrollThrottleRef.current) return;
    scrollThrottleRef.current = setTimeout(() => {
      scrollThrottleRef.current = null;
      if (autoFollowRef.current) scrollToBottom("smooth");
    }, SCROLL_THROTTLE_MS);
  }, [scrollToBottom]);

  const handleScrollAreaScroll = useCallback(() => {
    const atBottom = isNearBottom();
    if (atBottom) {
      autoFollowRef.current = true;
      setShowBackToBottom(false);
    } else {
      // 任何时候用户滚离底部均显示「回到底部」按钮
      if (isGenerating) {
        // AI 输出中额外关闭自动跟随，避免强制拉回
        autoFollowRef.current = false;
      }
      setShowBackToBottom(true);
    }
  }, [isNearBottom, isGenerating]);

  // ── Effect 1：注册滚动事件监听 ───────────────────────────────
  useEffect(() => {
    const el = scrollAreaRef.current;
    if (!el) return;
    el.addEventListener("scroll", handleScrollAreaScroll, { passive: true });
    return () => el.removeEventListener("scroll", handleScrollAreaScroll);
  }, [handleScrollAreaScroll]);

  // ── Effect 2：流式数据变化 → 节流滚底 ──────────────────────
  useEffect(() => {
    if (!hasTask && !hasMessages) return;
    scheduleScrollToBottom();
  }, [hasTask, hasMessages, messages.length, lastMessageId, streamState.lastEventAt, streamState.status, scheduleScrollToBottom]);

  // ── Effect 3：ResizeObserver 监听消息容器高度变化 ───────────
  useEffect(() => {
    const inner = msgContainerRef.current;
    if (!inner) return;
    const ro = new ResizeObserver(() => { scheduleScrollToBottom(); });
    ro.observe(inner);
    return () => ro.disconnect();
  }, [scheduleScrollToBottom]);

  // ── Effect 4：组件挂载时（切换会话触发 remount）立即滚到底部 ─
  // ChatPanel 以 key={conversationId} 挂载，切换会话整体重新挂载，
  // 此 effect 每次挂载只运行一次，无需监听 conversationId。
  useEffect(() => {
    if (!hasMessages) return;
    autoFollowRef.current = true;
    setShowBackToBottom(false);
    bottomRef.current?.scrollIntoView({ behavior: "instant", block: "end" });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 底部输入框自动伸缩，同时用 canvas 测量文字宽度决定是否触发展开
  const footerAutoResize = useCallback((el: HTMLTextAreaElement | null) => {
    if (!el) return;
    el.style.height = "auto";
    const scrollH = el.scrollHeight;
    const newH = Math.min(scrollH, MAX_FOOTER_TEXTAREA_HEIGHT);
    el.style.height = `${newH}px`;
    el.style.overflowY = scrollH > MAX_FOOTER_TEXTAREA_HEIGHT ? "auto" : "hidden";

    const value = el.value;
    if (!value) {
      // 完全清空 → 收起
      setIsFooterExpandedByContent(false);
      return;
    }
    setIsFooterExpandedByContent((prev) => {
      if (prev) return true; // 已展开则保持（单向：只有清空才收起，避免振荡）
      // 含换行符（如粘贴多行或草稿恢复）直接展开，不依赖宽度测量
      if (value.includes("\n")) return true;
      // 单行：检测文字宽度是否接近行末（>85%）
      const style = getComputedStyle(el);
      const paddingLeft = parseFloat(style.paddingLeft) || 0;
      const paddingRight = parseFloat(style.paddingRight) || 0;
      const innerWidth = el.clientWidth - paddingLeft - paddingRight;
      if (innerWidth <= 0) return false;
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d");
      if (!ctx) return false;
      ctx.font = style.font;
      const maxLineWidth = value
        .split("\n")
        .reduce((max, line) => Math.max(max, ctx.measureText(line).width), 0);
      return maxLineWidth > innerWidth * 0.85;
    });
  }, []);

  useEffect(() => {
    footerAutoResize(footerInputRef.current);
  }, [input, footerAutoResize]);

  // 布局展开/收起切换后恢复焦点，避免 textarea remount 丢失输入焦点
  const prevIsFooterExpandedRef = useRef<boolean | null>(null);
  useEffect(() => {
    if (prevIsFooterExpandedRef.current !== null && prevIsFooterExpandedRef.current !== isFooterExpanded) {
      requestAnimationFrame(() => {
        const el = footerInputRef.current;
        if (el) {
          el.focus();
          const len = el.value.length;
          el.setSelectionRange(len, len);
        }
      });
    }
    prevIsFooterExpandedRef.current = isFooterExpanded;
  }, [isFooterExpanded]);

  const kbSelected = new Set(knowledgeRefs.map((r) => r.doc_id));

  const plusFooterBlock = (
    <div className="relative flex-shrink-0" ref={plusFooterRef}>
      <button
        type="button"
        onClick={() => setPlusFooterOpen((v) => !v)}
        disabled={creating || !canWriteConversation}
        className={`flex h-10 w-10 items-center justify-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-50 ${plusFooterOpen ? "bg-obsidian text-papyrus" : "bg-fog text-obsidian/55 hover:bg-moss hover:text-obsidian"}`}
        title="高级配置、知识库或上传照片和文件"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
      {plusFooterOpen ? (
        <div className="absolute bottom-full left-0 z-40 mb-2 w-[min(calc(100vw-32px),288px)] overflow-visible rounded-[10px] border border-black/[0.08] bg-white py-0 text-[13px] shadow-lg">
          <AdvancedParamsMenuSection advanced={advanced} onChange={setAdvanced} />
          <button
            type="button"
            disabled={!canReadKnowledge}
            title={!canReadKnowledge ? "无知识库读取权限" : undefined}
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-obsidian/80 hover:bg-moss/40 disabled:cursor-not-allowed disabled:text-obsidian/25"
            onClick={() => insertKbMentionAtFooter()}
          >
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-black/[0.1] text-[11px] font-bold text-obsidian/50">
              @
            </span>
            知识库
          </button>
          <button
            type="button"
            disabled={creating || !canWriteConversation}
            className="flex w-full items-center gap-2.5 border-t border-black/[0.05] px-3 py-2.5 text-left text-obsidian/80 hover:bg-moss/40 disabled:opacity-50"
            onClick={() => {
              setPlusFooterOpen(false);
              localMediaInputRef.current?.click();
            }}
          >
            <svg
              className="h-[18px] w-[18px] shrink-0 text-obsidian/45"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden
            >
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19A4 4 0 0116.85 3h0a4 4 0 013.54 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
            </svg>
            上传照片和文件
          </button>
        </div>
      ) : null}
    </div>
  );

  const chatFooterSendButton = (
    <button
      type="button"
      onClick={() => {
        if (isGenerating) void onStop();
        else void handleSend();
      }}
      disabled={!isGenerating && (!canWriteConversation || (!input.trim() && localMediaDrafts.length === 0))}
      className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full bg-dew text-white transition hover:bg-[#9CBBC0] disabled:cursor-not-allowed disabled:bg-fog disabled:text-obsidian/20"
      title={isGenerating ? "停止" : "发送"}
    >
      {isGenerating ? (
        <Square size={16} />
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M13 5l7 7-7 7M5 12h14" />
        </svg>
      )}
    </button>
  );

  const shellTransition = reduceMotion ? { duration: 0 } : { duration: 0.5, ease: [0.4, 0, 0.2, 1] as const };

  const sharedChrome = (
    <>
      <input
        ref={localMediaInputRef}
        type="file"
        className="hidden"
        multiple
        accept={LOCAL_MEDIA_ACCEPT}
        onChange={onLocalMediaInputChange}
      />
    </>
  );

  if (isInitialState) {
    return (
      <>
        {sharedChrome}
        <div className="flex h-full w-full flex-col overflow-hidden bg-[#F7F7F7]">
          <PromptComposer
            value={input}
            advanced={advanced}
            disabled={!canWriteConversation}
            busy={creating}
            onValueChange={setInput}
            onAdvancedChange={setAdvanced}
            onSubmit={handleSend}
            onPickSuggestion={handleSuggestion}
            knowledgeRefs={knowledgeRefs}
            localMediaDrafts={localMediaDrafts}
            onRemoveKnowledgeRef={removeKnowledgeRef}
            onRemoveLocalMediaDraft={removeLocalMediaDraft}
            onResetAdvancedField={resetAdvancedField}
            canReadKnowledge={canReadKnowledge}
            onKbDocToggle={toggleKbDoc}
            onTriggerLocalMedia={() => localMediaInputRef.current?.click()}
            onPickLocalFiles={appendLocalMediaDrafts}
            notice={
            <>
              {cookieBlocked ? (
                <p className="text-center text-[11px] leading-relaxed text-[#9A5558]">
                  小红书 Cookie 已过期，普通问答仍可继续；如需采集任务，请先到
                  <a href={XHS_AUTH_SETTINGS_HREF} className="mx-1 font-semibold underline underline-offset-2">
                    数据源授权
                  </a>
                  重新授权。
                </p>
              ) : null}
              {!canWriteConversation ? (
                <div className="mt-2 rounded-2xl border border-black/[0.06] bg-white/75 px-4 py-3 text-[12px] text-obsidian/45 shadow-sm backdrop-blur">
                  当前账号为只读成员，可查看历史与画布，但不能发送消息或发起分析。
                </div>
              ) : null}
            </>
          }
        />
        </div>
      </>
    );
  }

  return (
    <>
      {sharedChrome}
      <div
        style={!canvasCollapsed ? { width: chatWidth ?? "var(--workspace-chat-width, 380px)" } : undefined}
        className={`flex h-full flex-shrink-0 flex-col border-r border-black/[0.06] bg-papyrus ${
          canvasCollapsed ? "w-full" : "w-auto"
        }`}
      >
      <header className="flex shrink-0 items-center justify-between gap-3 bg-papyrus/95 px-3 py-3 backdrop-blur-sm sm:px-4">
        <BrandLink />
        <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
          {hasTask ? (
            <span
              className="rounded px-2 py-[3px] text-[11px]"
              style={{ background: badge.bg, color: badge.color }}
            >
              {badge.label}
            </span>
          ) : null}
          {showCanvasToggle ? (
            <button
              type="button"
              onClick={onToggleCanvas}
              title="收起/展开画布"
              className="flex h-[30px] w-[30px] items-center justify-center rounded-full border border-black/[0.06] bg-white text-obsidian/42 transition hover:bg-moss hover:text-obsidian"
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className={`transition ${canvasCollapsed ? "rotate-180" : ""}`}
              >
                <path d="M9 3h6M9 21h6M3 9v6M21 9v6M9 9h6v6H9z" />
              </svg>
            </button>
          ) : null}
        </div>
      </header>

      <div ref={scrollAreaRef} className="custom-scrollbar relative flex-1 overflow-y-auto bg-papyrus pb-4">
        <div ref={msgContainerRef} className="mx-auto w-full max-w-4xl space-y-3 px-3 pt-4 sm:px-5">
        <ConversationView
          messages={messages}
          streamState={streamState}
          userMessage={userInput ?? ""}
          taskId={taskId}
        />

        {streamError ? (
          <div className="mt-3 rounded-md border border-[#FFE8E0] bg-[#FFF8F5] px-3 py-2 text-[11px] text-[#8B6914]">
            <div className="flex items-center justify-between gap-2">
              <span>
                推送异常：{streamError.code} · {streamError.message}
              </span>
              <button
                type="button"
                onClick={onReconnect}
                className="rounded border border-[#F1E3B6] bg-white px-2 py-0.5 text-[#8B6914] hover:bg-[#FFF8F0]"
              >
                重连
              </button>
            </div>
          </div>
        ) : null}
        <div ref={bottomRef} />
        </div>
        {showBackToBottom && (
          <div className="sticky bottom-4 flex justify-center" style={{ pointerEvents: "none" }}>
            <button
              type="button"
              onClick={() => {
                autoFollowRef.current = true;
                setShowBackToBottom(false);
                scrollToBottom("smooth");
              }}
              title="回到底部"
              className="flex h-8 w-8 items-center justify-center rounded-full border border-black/[0.08] bg-white text-obsidian/50 shadow-md transition hover:bg-moss hover:text-obsidian"
              style={{ pointerEvents: "auto" }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M12 5v14M5 12l7 7 7-7" />
              </svg>
            </button>
          </div>
        )}
      </div>

      <div className="relative z-10 flex-shrink-0 bg-gradient-to-b from-papyrus/0 to-moss/20 backdrop-blur-md">
        <div className="mx-auto w-full max-w-4xl px-3 pt-0 sm:px-5">
        {!canWriteConversation ? (
          <div className="mb-2 rounded-2xl border border-black/[0.06] bg-white/80 px-3 py-2 text-[11px] text-obsidian/45 shadow-sm">
            当前账号为只读成员，可查看历史与画布，但不能发送消息或发起分析。
          </div>
        ) : null}

        <motion.div
          layout
          initial={false}
          animate={{
            borderRadius: hasComposerExtras ? 24 : 30,
          }}
          transition={shellTransition}
          onDragEnter={onComposerDragEnter}
          onDragLeave={onComposerDragLeave}
          onDragOver={onComposerDragOver}
          onDrop={onComposerDrop}
          className={`relative z-10 flex min-h-[60px] w-full flex-col overflow-visible border border-black/[0.05] bg-white px-3 py-2 shadow-[0_20px_56px_rgba(26,26,26,0.085)] ${composerDragActive ? "ring-2 ring-dew ring-offset-2 ring-offset-papyrus" : ""}`}
        >
          <ComposerDraftThumbnailsRow
            drafts={localMediaDrafts}
            disabled={!canWriteConversation}
            onRemove={removeLocalMediaDraft}
            showDropHint={canWriteConversation}
          />
          {isFooterExpanded ? (
            <>
              {/* 展开态：全宽输入区 + 底部工具栏；有标签时工具栏显示 chips */}
              <div className="relative min-h-10 w-full min-w-0 px-0.5 pt-0.5">
                <KnowledgeMentionList
                  open={kbMention !== null}
                  placement="above"
                  loading={kbDocsLoading}
                  docs={kbDocRows}
                  query={kbMention?.query ?? ""}
                  selectedIds={kbSelected}
                  onPick={handleKbMentionPickFooter}
                  canRead={canReadKnowledge}
                />
                <textarea
                  ref={footerInputRef}
                  rows={1}
                  placeholder="输入指令，或让 RED MUSE 发现热点…"
                  value={input}
                  onChange={handleFooterInputChange}
                  onSelect={(e) => setInputCaret(e.currentTarget.selectionStart ?? input.length)}
                  onClick={(e) => setInputCaret(e.currentTarget.selectionStart ?? input.length)}
                  onKeyUp={(e) => setInputCaret(e.currentTarget.selectionStart ?? input.length)}
                  disabled={creating || !canWriteConversation}
                  className="textarea-scrollbar min-h-[40px] w-full min-w-0 resize-none overflow-hidden bg-transparent py-[9px] text-[15px] leading-[1.6] text-obsidian outline-none placeholder:text-obsidian/22 disabled:cursor-not-allowed disabled:opacity-60"
                  onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => {
                    const el = e.currentTarget;
                    const c = el.selectionStart ?? input.length;
                    if (e.key === "Escape") {
                      const m = getActiveKbMention(input, c);
                      if (m) {
                        e.preventDefault();
                        const next = input.slice(0, m.start) + input.slice(c);
                        setInput(next);
                        setInputCaret(m.start);
                        requestAnimationFrame(() => {
                          el.focus();
                          el.setSelectionRange(m.start, m.start);
                        });
                        return;
                      }
                    }
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      void handleSend();
                    }
                  }}
                />
              </div>
              <div className="mt-1.5 flex w-full items-end justify-between gap-2 pt-1">
                <div className="composer-chip-strip flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                  <div className="flex h-10 shrink-0 items-center">{plusFooterBlock}</div>
                  {knowledgeRefs.map((r) => (
                    <span
                      key={r.doc_id}
                      className="inline-flex max-w-[160px] shrink-0 items-center gap-1 rounded-full bg-dew/15 px-2.5 py-1 text-[11px] font-medium text-obsidian/75"
                    >
                      <span className="truncate">@{r.title || r.doc_id.slice(0, 6)}</span>
                      <button
                        type="button"
                        className="shrink-0 rounded-full px-0.5 text-obsidian/40 hover:text-obsidian"
                        onClick={() => removeKnowledgeRef(r.doc_id)}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  {changedAdvancedLabels(advanced).map((row) => (
                    <span
                      key={row.key}
                      className="inline-flex max-w-[180px] shrink-0 items-center gap-1 rounded-full bg-obsidian/[0.06] px-2.5 py-1 text-[11px] text-obsidian/70"
                    >
                      <SlidersIcon className="h-3.5 w-3.5 shrink-0 text-obsidian/40" />
                      <span className="truncate">
                        {row.label}：{row.value}
                      </span>
                      <button
                        type="button"
                        className="shrink-0 text-obsidian/40 hover:text-obsidian"
                        onClick={() => resetAdvancedField(row.key as keyof AdvancedConfig)}
                        aria-label={`恢复${row.label}默认`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex h-10 shrink-0 items-center pb-px">{chatFooterSendButton}</div>
              </div>
            </>
          ) : (
            /* 收起态：空内容且无标签时恢复紧凑单行 */
            <div className="flex w-full items-center gap-2 py-1">
              <div className="flex h-10 shrink-0 items-center">{plusFooterBlock}</div>
              <div className="relative min-h-10 min-w-0 flex-1 flex items-center">
                <KnowledgeMentionList
                  open={kbMention !== null}
                  placement="above"
                  loading={kbDocsLoading}
                  docs={kbDocRows}
                  query={kbMention?.query ?? ""}
                  selectedIds={kbSelected}
                  onPick={handleKbMentionPickFooter}
                  canRead={canReadKnowledge}
                />
                <textarea
                  ref={footerInputRef}
                  rows={1}
                  placeholder="输入指令，或让 RED MUSE 发现热点…"
                  value={input}
                  onChange={handleFooterInputChange}
                  onSelect={(e) => setInputCaret(e.currentTarget.selectionStart ?? input.length)}
                  onClick={(e) => setInputCaret(e.currentTarget.selectionStart ?? input.length)}
                  onKeyUp={(e) => setInputCaret(e.currentTarget.selectionStart ?? input.length)}
                  disabled={creating || !canWriteConversation}
                  className="textarea-scrollbar min-h-[40px] w-full min-w-0 resize-none overflow-hidden bg-transparent pt-[10px] pb-[8px] text-[15px] leading-[1.6] text-obsidian outline-none placeholder:text-obsidian/22 disabled:cursor-not-allowed disabled:opacity-60"
                  onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => {
                    const el = e.currentTarget;
                    const c = el.selectionStart ?? input.length;
                    if (e.key === "Escape") {
                      const m = getActiveKbMention(input, c);
                      if (m) {
                        e.preventDefault();
                        const next = input.slice(0, m.start) + input.slice(c);
                        setInput(next);
                        setInputCaret(m.start);
                        requestAnimationFrame(() => {
                          el.focus();
                          el.setSelectionRange(m.start, m.start);
                        });
                        return;
                      }
                    }
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      void handleSend();
                    }
                  }}
                />
              </div>
              <div className="flex h-10 shrink-0 items-center">{chatFooterSendButton}</div>
            </div>
          )}
        </motion.div>
        {cookieBlocked ? (
          <p className="mt-2 text-center text-[11px] leading-relaxed text-[#9A5558]">
            小红书 Cookie 已过期，普通问答仍可继续；如需采集任务，请先到
            <a href={XHS_AUTH_SETTINGS_HREF} className="mx-0.5 font-semibold underline underline-offset-2">数据源授权</a>
            重新授权。
          </p>
        ) : null}
        <p className="mt-[10px] pb-[8px] text-center text-[12px] text-[#8c8c8c]">
          内容由 AI 生成，可能有误，请注意核查
        </p>
        </div>
      </div>
    </div>
    </>
  );
}

function parseKnowledgeRefsFromDebug(debug: Record<string, unknown> | null | undefined): Array<{ doc_id: string; title?: string }> {
  const raw = debug?.["knowledge_refs"];
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: Array<{ doc_id: string; title?: string }> = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const docId = String(o.doc_id ?? "").trim();
    if (!docId || seen.has(docId)) continue;
    seen.add(docId);
    const title = typeof o.title === "string" ? o.title : undefined;
    out.push({ doc_id: docId, title });
  }
  return out;
}

/** 用户消息中展示「本条携带的知识库」（来自 debug.knowledge_refs，与请求体一致） */
function UserKnowledgeRefsStrip({ debug }: { debug?: Record<string, unknown> | null }) {
  const refs = parseKnowledgeRefsFromDebug(debug);
  if (!refs.length) return null;
  return (
    <div className="mb-2 flex flex-wrap justify-end gap-1.5">
      {refs.map((r) => (
        <span
          key={r.doc_id}
          className="inline-flex max-w-[220px] items-center rounded-full bg-dew/12 px-2.5 py-0.5 text-[11px] font-medium text-obsidian/70"
          title={r.doc_id}
        >
          <span className="truncate">@{r.title || r.doc_id.slice(0, 12)}</span>
        </span>
      ))}
    </div>
  );
}

function ConversationView({
  messages,
  streamState,
  userMessage,
  taskId,
}: {
  messages: ChatMessage[];
  streamState: TaskStreamState;
  userMessage: string;
  taskId: string | null;
}) {
  const firstMsg = userMessage || "正在分析…";
  const showCompletionHint = !!taskId && streamState.status === "completed" && messages.length === 0;

  const renderMessage = (message: ChatMessage) => {
    // 跳过 content 为空的 assistant 消息，但保留流式进行中的消息（有动画占位）
    const isStreaming = (message.debug as { streaming?: boolean } | null)?.streaming === true;
    if (message.role !== "user" && !message.content?.trim() && !isStreaming) return null;
    return (
    <Message
      key={message.message_id}
      role={message.role === "user" ? "user" : "ai"}
      bubble={
        <div>
          {message.role === "user" ? <UserKnowledgeRefsStrip debug={message.debug} /> : null}
          <MessageContent message={message} />
          {message.citations.length > 0 ? <CitationList citations={message.citations} /> : null}
        </div>
      }
      time={formatMessageTime(message.created_at)}
    />
    );
  };

  if (!taskId) {
    return (
      <div className="flex w-full flex-col gap-4">
        {messages.map(renderMessage)}
      </div>
    );
  }

  // 三级策略统一定位任务发起消息：
  // 1. 内容精确匹配（live 模式或缓存恢复的会话）
  // 2. task_handoff 反查（历史恢复或内容匹配失败时）
  // 3. 降级取最后一条 user 消息
  const isLiveSession = !!userMessage;
  let taskMsgIndex = -1;

  if (userMessage) {
    const trimmed = userMessage.trim();
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user" && messages[i].content?.trim() === trimmed) {
        taskMsgIndex = i;
        break;
      }
    }
  }

  if (taskMsgIndex === -1) {
    const handoffIdx = messages.findIndex(
      (m) => m.task_handoff?.task_id === taskId
    );
    if (handoffIdx > 0) {
      taskMsgIndex = handoffIdx - 1;
    } else if (handoffIdx === 0) {
      taskMsgIndex = 0;
    }
  }

  if (taskMsgIndex === -1) {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") { taskMsgIndex = i; break; }
    }
  }

  // 基于统一的 taskMsgIndex 三段切分：前置历史 / 任务触发消息 / 后续消息
  const preTaskMessages = taskMsgIndex > 0 ? messages.slice(0, taskMsgIndex) : [];
  const taskMessage = taskMsgIndex >= 0 ? messages[taskMsgIndex] : null;
  const postTaskMessages = taskMsgIndex >= 0 ? messages.slice(taskMsgIndex + 1) : messages;

  // task_type 决定 AgentTimeline 用哪套步骤目录渲染；直接按 task_handoff.task_id
  // 精确匹配携带该字段的助手消息，不依赖上面用于切分历史的 taskMsgIndex（后者可能
  // 落在触发消息本身而非携带 task_handoff 的助手回复上）。
  const handoffMessage = messages.find((m) => m.task_handoff?.task_id === taskId) ?? null;
  const taskType = handoffMessage?.task_handoff?.task_type;

  // 携带 task_handoff 的助手确认回复（如"好的，已为「xxx」启动评论分析任务..."）
  // 语义上是对触发消息的即时应答，应提到 AgentTimeline 之前展示，而不是和"分析完成"
  // 等真正的后续消息一起堆在时间线下方。
  const handoffIdxInPost = handoffMessage ? postTaskMessages.indexOf(handoffMessage) : -1;
  const beforeHandoffMessages = handoffIdxInPost > 0 ? postTaskMessages.slice(0, handoffIdxInPost) : [];
  const afterHandoffMessages =
    handoffIdxInPost >= 0 ? postTaskMessages.slice(handoffIdxInPost + 1) : postTaskMessages;

  return (
    <div className="flex w-full flex-col gap-4">
      {/* 任务发起前的历史对话 */}
      {preTaskMessages.map(renderMessage)}

      {/* 任务发起消息：live 模式用 userMessage 乐观渲染，历史模式渲染 DB 消息 */}
      {isLiveSession ? (
        <Message role="user" bubble={firstMsg} time={taskId.slice(-6)} />
      ) : (
        taskMessage ? renderMessage(taskMessage) : null
      )}

      {/* 助手的任务启动确认回复，紧跟在触发消息后、时间线之前 */}
      {beforeHandoffMessages.map(renderMessage)}
      {handoffMessage ? renderMessage(handoffMessage) : null}

      <AgentTimeline state={streamState} taskId={taskId} taskType={taskType} />

      {afterHandoffMessages.map(renderMessage)}

      {showCompletionHint ? (
        <Message
          role="ai"
          bubble={
            <span>
              分析完成，右侧画布已生成。
              <br />
              你可以继续调整：
              <br />
              · "展开封面分析的详情"
              <br />
              · "加上竞品XX的对比"
              <br />
              · "导出 Excel"
            </span>
          }
          time=""
        />
      ) : null}
    </div>
  );
}

function ThinkBox({ content, durationMs, live }: { content: string; durationMs: number; live?: boolean }) {
  const [open, setOpen] = useState(live ?? false);
  const secs = durationMs > 0 ? `${(durationMs / 1000).toFixed(1)}s` : "";

  // 思考进行中时自动展开
  if (live && !open) setOpen(true);

  return (
    <div className="mb-3 rounded-xl border border-obsidian/10 bg-obsidian/[0.03]">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[12px] text-obsidian/45 transition-colors hover:text-obsidian/70"
      >
        <svg
          className={`h-3 w-3 shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
          viewBox="0 0 12 12"
          fill="currentColor"
        >
          <path d="M4 2l5 4-5 4V2z" />
        </svg>
        <span>思考过程</span>
        {live ? (
          <span className="ml-1 inline-flex items-center gap-1 text-obsidian/35">
            <span className="h-1.5 w-1.5 animate-ping rounded-full bg-dew/70" />
            <span>思考中...</span>
          </span>
        ) : secs ? (
          <span className="ml-1 text-obsidian/30">{secs}</span>
        ) : null}
      </button>
      {open && content ? (
        <div className="border-t border-obsidian/8 px-3 py-2.5 text-[13px] leading-relaxed text-obsidian/55 whitespace-pre-wrap">
          {content}
        </div>
      ) : null}
    </div>
  );
}

function MessageContent({ message }: { message: ChatMessage }) {
  const debug = message.debug ?? {};
  const streaming = debug["streaming"] === true;
  const thinkingLive = debug["thinking_live"] === true;
  const label = typeof debug["label"] === "string" ? debug["label"] : "正在生成回答...";
  const thinkContent = typeof debug["think_content"] === "string" ? debug["think_content"] : "";
  const thinkDurationMs = typeof debug["think_duration_ms"] === "number" ? debug["think_duration_ms"] : 0;

  const userAttachments: AttachmentRecord[] =
    message.role === "user" && Array.isArray(message.attachments)
      ? (message.attachments as AttachmentRecord[])
      : [];

  // 思考中（尚无正式回答内容）
  if (streaming && !message.content) {
    return (
      <div>
        {thinkContent || thinkingLive ? (
          <ThinkBox content={thinkContent} durationMs={thinkDurationMs} live={thinkingLive} />
        ) : null}
        {!thinkingLive ? (
          <div className="flex items-center gap-2 text-[15px] text-obsidian/48">
            <span className="inline-flex h-4 w-4 items-center justify-center">
              <span className="h-2 w-2 animate-ping rounded-full bg-dew" />
            </span>
            <span>{thinkContent ? "正在生成回答..." : label}</span>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div>
      {userAttachments.length > 0 ? <ConversationUserAttachments items={userAttachments} /> : null}
      {thinkContent ? <ThinkBox content={thinkContent} durationMs={thinkDurationMs} live={false} /> : null}
      {message.content.trim() ? <MarkdownContent content={message.content} /> : null}
      {streaming ? <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-dew align-[-2px]" /> : null}
    </div>
  );
}

type MarkdownBlock =
  | { type: "text"; content: string }
  | { type: "code"; lang: string; content: string };

function MarkdownContent({ content }: { content: string }) {
  const blocks = splitMarkdownBlocks(content);
  return (
    <div className="space-y-2.5 text-left text-[15px] leading-[1.75] text-obsidian/90">
      {blocks.map((block, index) =>
        block.type === "code" ? (
          <pre
            key={`code-${index}`}
            className="overflow-x-auto rounded-lg border border-[#E8E5E0] bg-[#2D2A26] px-3 py-2 text-[13px] leading-[1.65] text-white"
          >
            <code>{block.content}</code>
          </pre>
        ) : (
          <Fragment key={`text-${index}`}>{renderMarkdownText(block.content, index)}</Fragment>
        ),
      )}
    </div>
  );
}

function splitMarkdownBlocks(content: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const pattern = /```([A-Za-z0-9_-]*)\s*\n?([\s\S]*?)```/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(content)) !== null) {
    if (match.index > cursor) {
      blocks.push({ type: "text", content: content.slice(cursor, match.index) });
    }
    blocks.push({ type: "code", lang: match[1] || "", content: match[2].replace(/\n$/, "") });
    cursor = pattern.lastIndex;
  }
  if (cursor < content.length) {
    blocks.push({ type: "text", content: content.slice(cursor) });
  }
  return blocks.length ? blocks : [{ type: "text", content }];
}

function renderMarkdownText(content: string, blockIndex: number): ReactNode[] {
  const nodes: ReactNode[] = [];
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const text = paragraph.join(" ").trim();
    if (text) {
      nodes.push(
        <p key={`${blockIndex}-p-${nodes.length}`} className="leading-[1.75]">
          {renderInlineMarkdown(text)}
        </p>,
      );
    }
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      continue;
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      const level = heading[1].length;
      const Tag = level === 1 ? "h3" : "h4";
      const headingClass =
        level === 1
          ? "mt-2.5 text-[1.125rem] font-semibold leading-snug text-[#2D2A26] first:mt-0"
          : "mt-2 text-[15px] font-semibold leading-snug text-[#2D2A26] first:mt-0";
      nodes.push(
        <Tag key={`${blockIndex}-h-${nodes.length}`} className={headingClass}>
          {renderInlineMarkdown(heading[2])}
        </Tag>,
      );
      continue;
    }

    if (/^[-*_]{3,}$/.test(trimmed)) {
      flushParagraph();
      nodes.push(<hr key={`${blockIndex}-hr-${nodes.length}`} className="border-[#E8E5E0]" />);
      continue;
    }

    const unordered = trimmed.match(/^[-*]\s+(.+)$/);
    if (unordered) {
      flushParagraph();
      const items: string[] = [unordered[1]];
      while (i + 1 < lines.length) {
        const next = lines[i + 1].trim().match(/^[-*]\s+(.+)$/);
        if (!next) break;
        items.push(next[1]);
        i += 1;
      }
      nodes.push(
        <ul key={`${blockIndex}-ul-${nodes.length}`} className="list-disc space-y-1 pl-5">
          {items.map((item, idx) => (
            <li key={idx}>{renderInlineMarkdown(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    const ordered = trimmed.match(/^\d+\.\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      const items: string[] = [ordered[1]];
      while (i + 1 < lines.length) {
        const next = lines[i + 1].trim().match(/^\d+\.\s+(.+)$/);
        if (!next) break;
        items.push(next[1]);
        i += 1;
      }
      nodes.push(
        <ol key={`${blockIndex}-ol-${nodes.length}`} className="list-decimal space-y-1 pl-5">
          {items.map((item, idx) => (
            <li key={idx}>{renderInlineMarkdown(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    const quote = trimmed.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      nodes.push(
        <blockquote
          key={`${blockIndex}-quote-${nodes.length}`}
          className="border-l-2 border-[#FFD6CC] pl-3 text-[#5A5550]"
        >
          {renderInlineMarkdown(quote[1])}
        </blockquote>,
      );
      continue;
    }

    paragraph.push(trimmed);
  }

  flushParagraph();
  return nodes;
}

/** /api/v1/... 相对路径补全为后端完整 URL */
function resolveHref(href: string): string {
  if (/^(https?:\/\/|mailto:)/i.test(href)) return href;
  if (href.startsWith("/api/")) {
    const base = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8100/api/v1").replace(/\/api\/v1\/?$/, "");
    return `${base}${href}`;
  }
  if (/^\/[^/]/.test(href)) return href;
  return "";
}

/**
 * 带 JWT 认证的文件下载：适用于后端 API 导出链接。
 * fetch 时携带 Authorization 头，响应以 Blob 触发浏览器下载。
 */
async function downloadWithAuth(url: string, filename: string): Promise<void> {
  try {
    const { getAuthToken } = await import("@/lib/auth-storage");
    const token = getAuthToken();
    const res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      console.error(`[download] 请求失败 ${res.status}`, await res.text().catch(() => ""));
      alert(`下载失败（${res.status}），请确认已登录`);
      return;
    }
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(blobUrl);
  } catch (err) {
    console.error("[download] 异常", err);
    alert("下载失败，请稍后重试");
  }
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(
        <strong key={`strong-${match.index}`} className="font-semibold">
          {token.slice(2, -2)}
        </strong>,
      );
    } else if (token.startsWith("`")) {
      nodes.push(
        <code key={`code-${match.index}`} className="rounded bg-[#F0EEEB] px-1 py-0.5 text-[13px] text-[#8B6914]">
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const rawHref = link?.[2] ?? "";
      const safeHref = resolveHref(rawHref);
      const isApiDownload = rawHref.startsWith("/api/");
      nodes.push(
        safeHref ? (
          isApiDownload ? (
            <button
              key={`link-${match.index}`}
              type="button"
              onClick={() => {
                const rawSuffix = rawHref.split("/").pop() ?? "";
                const filename = rawSuffix === "comment_excel"
                  ? "评论分析报告.xlsx"
                  : rawSuffix === "comment_md"
                  ? "评论舆情分析报告.md"
                  : rawSuffix.endsWith(".xlsx") ? rawSuffix : rawSuffix || "report.xlsx";
                void downloadWithAuth(safeHref, filename);
              }}
              className="cursor-pointer text-[#c2716b] underline decoration-dew/50 underline-offset-2 bg-transparent border-0 p-0 font-inherit text-inherit"
            >
              {link?.[1]}
            </button>
          ) : (
            <a
              key={`link-${match.index}`}
              href={safeHref}
              target="_blank"
              rel="noreferrer"
              className="text-[#c2716b] underline decoration-dew/50 underline-offset-2"
            >
              {link?.[1]}
            </a>
          )
        ) : (
          link?.[1] ?? token
        ),
      );
    }
    cursor = pattern.lastIndex;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

function formatMessageTime(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function CitationList({ citations }: { citations: KnowledgeCitation[] }) {
  const citationFiles = Array.from(
    new Map(
      citations.map((citation) => [
        citation.title || citation.doc_id,
        {
          key: citation.title || citation.doc_id,
          title: citation.title || citation.doc_id,
        },
      ]),
    ).values(),
  );
  return (
    <details className="mt-2 rounded-lg border border-[#F0EEEB] bg-white/70 px-3 py-2 text-left">
      <summary className="cursor-pointer text-[12px] font-medium text-[#8A8580]">
        知识库引用 · {citationFiles.length} 个文件
      </summary>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {citationFiles.map((file) => (
          <div
            key={file.key}
            className="rounded-full border border-[#E8E5E0] bg-white px-2 py-1 text-[11px] font-medium leading-tight text-[#5A5550]"
          >
            {file.title}
          </div>
        ))}
      </div>
    </details>
  );
}

function Message({
  role,
  bubble,
  time,
}: {
  role: "ai" | "user";
  bubble: React.ReactNode;
  time: string;
}) {
  const isUser = role === "user";
  const bubbleClass = isUser
    ? "max-w-[min(100%,28rem)] rounded-xl border border-black/[0.07] bg-white px-4 py-3 text-[15px] leading-[1.75] text-obsidian/90 shadow-[0_2px_14px_rgba(26,26,26,0.05)]"
    : "w-full min-w-0 max-w-full rounded-xl border-0 bg-transparent px-0 py-2 text-[15px] leading-[1.75] text-obsidian/88 shadow-none";
  return (
    <div className={`flex w-full flex-col gap-1 ${isUser ? "items-end" : "items-start"}`}>
      <div className={bubbleClass}>{bubble}</div>
      {time ? (
        <div className={`text-[11px] text-obsidian/32 ${isUser ? "pr-0.5" : "pl-0"}`}>{time}</div>
      ) : null}
    </div>
  );
}

function TaskControls({
  status,
  busy,
  onPause,
  onResume,
  onCancel,
  onNew,
}: {
  status: TaskStreamState["status"];
  busy: boolean;
  onPause: () => Promise<void> | void;
  onResume: () => Promise<void> | void;
  onCancel: () => Promise<void> | void;
  onNew: () => void;
}) {
  const isRunning = status === "running" || status === "queued" || status === "pending";
  const isPaused = status === "paused";
  const isTerminal = status === "completed" || status === "failed" || status === "cancelled";

  const btn = "rounded-md border border-[#E8E5E0] bg-transparent px-2.5 py-1 text-[11px] text-[#8A8580] transition hover:bg-[#F5F3F0] hover:text-[#5A5550] disabled:cursor-not-allowed disabled:opacity-50";

  if (isTerminal) {
    return (
      <button type="button" onClick={onNew} className={btn}>
        + 新建
      </button>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      {isRunning ? (
        <button type="button" onClick={() => void onPause()} disabled={busy} className={btn}>
          暂停
        </button>
      ) : null}
      {isPaused ? (
        <button type="button" onClick={() => void onResume()} disabled={busy} className={btn}>
          继续
        </button>
      ) : null}
      <button
        type="button"
        onClick={() => {
          if (typeof window !== "undefined" && !window.confirm("确定取消当前任务？")) return;
          void onCancel();
        }}
        disabled={busy}
        className={`rounded-md border border-[#e8cfc8] bg-[#fff5f3] px-2.5 py-1 text-[11px] text-[#c2716b] transition hover:bg-[#fff0ed] disabled:cursor-not-allowed disabled:opacity-50`}
      >
        取消
      </button>
      <button type="button" onClick={onNew} className={btn} disabled={busy}>
        + 新建
      </button>
    </div>
  );
}
