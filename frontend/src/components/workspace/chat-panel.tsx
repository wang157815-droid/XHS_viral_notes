"use client";

import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import type { ChatMessage, CookieHealth, KnowledgeCitation } from "@/lib/contracts";
import type { SseClientStatus } from "@/lib/sse/event-source-client";
import type { TaskStreamState } from "@/lib/sse/event-reducer";
import {
  SUGGESTION_TAGS,
} from "./mock-canvas-data";

interface ChatPanelProps {
  cookieHealth: CookieHealth | null;
  taskId: string | null;
  userInput: string | null;
  messages: ChatMessage[];
  streamState: TaskStreamState;
  connectionStatus: SseClientStatus;
  creating: boolean;
  controlBusy: boolean;
  streamError: { code: string; message: string } | null;
  canvasCollapsed: boolean;
  showCanvasToggle: boolean;
  onToggleCanvas: () => void;
  onSubmit: (input: { rawInput: string; keywords: string[]; advanced: AdvancedConfig }) => Promise<void>;
  onNewAnalysis: () => void;
  onReconnect: () => void;
  onPause: () => Promise<void> | void;
  onResume: () => Promise<void> | void;
  onCancel: () => Promise<void> | void;
}

export interface AdvancedConfig {
  note_type: string;
  time_range: string;
  sample_count: string;
  viral_ratio: string;
}

const DEFAULT_ADVANCED: AdvancedConfig = {
  note_type: "不限",
  time_range: "不限",
  sample_count: "100",
  viral_ratio: "前50%",
};

const STATUS_BADGE: Record<SseClientStatus, { bg: string; color: string; label: string }> = {
  idle: { bg: "#F5F3F0", color: "#8A8580", label: "未连接" },
  connecting: { bg: "#FFF8E6", color: "#8B6914", label: "连接中" },
  open: { bg: "#F0FAF0", color: "#3D8C40", label: "已接入推送" },
  reconnecting: { bg: "#FFF8E6", color: "#8B6914", label: "重连中" },
  closed: { bg: "#F5F3F0", color: "#8A8580", label: "已完成" },
  failed: { bg: "#FFF0EE", color: "#FF4757", label: "推送异常" },
};

export function ChatPanel({
  cookieHealth,
  taskId,
  userInput,
  messages,
  streamState,
  connectionStatus,
  creating,
  controlBusy,
  streamError,
  canvasCollapsed,
  showCanvasToggle,
  onToggleCanvas,
  onSubmit,
  onNewAnalysis,
  onReconnect,
  onPause,
  onResume,
  onCancel,
}: ChatPanelProps) {
  const [input, setInput] = useState("");
  const [advOpen, setAdvOpen] = useState(false);
  const [advanced, setAdvanced] = useState<AdvancedConfig>(DEFAULT_ADVANCED);
  const scrollAreaRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const cookieBlocked = cookieHealth?.status === "expired";
  const hasTask = !!taskId;
  const hasMessages = messages.length > 0;
  const lastMessageId = messages[messages.length - 1]?.message_id;
  const badge = STATUS_BADGE[connectionStatus];

  const parseKeywords = (text: string): string[] => {
    const matches = text.match(/「([^」]+)」|「(.+?)」/g) ?? [];
    return matches.map((m) => m.replace(/[「」]/g, "")).slice(0, 5);
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || creating) return;
    const kw = parseKeywords(text);
    setInput("");
    await onSubmit({ rawInput: text, keywords: kw, advanced });
  };

  const handleSuggestion = async (text: string) => {
    setInput(text);
    const kw = parseKeywords(text);
    setInput("");
    await onSubmit({ rawInput: text, keywords: kw, advanced });
  };

  useEffect(() => {
    if (!hasTask && !hasMessages) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [hasTask, hasMessages, messages.length, lastMessageId, streamState.lastEventAt, streamState.status]);

  return (
    <div
      style={!canvasCollapsed ? { width: "var(--workspace-chat-width, 380px)" } : undefined}
      className={`flex h-full flex-shrink-0 flex-col border-r border-[#F0EEEB] bg-white ${
        canvasCollapsed ? "w-full" : "w-auto"
      }`}
    >
      <header className="flex items-center justify-between gap-2 border-b border-[#F0EEEB] px-[18px] py-[14px] text-[14px] font-bold">
        <span>对话</span>
        <div className="flex items-center gap-2">
          {hasTask ? (
            <TaskControls
              status={streamState.status}
              busy={controlBusy}
              onPause={onPause}
              onResume={onResume}
              onCancel={onCancel}
              onNew={onNewAnalysis}
            />
          ) : null}
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
              className="flex h-[30px] w-[30px] items-center justify-center rounded-lg border border-[#E8E5E0] bg-white text-[#8A8580] transition hover:bg-[#F5F3F0] hover:text-[#5A5550]"
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

      <div ref={scrollAreaRef} className="flex-1 overflow-y-auto px-[18px] py-[18px]">
        {hasTask || hasMessages ? (
          <ConversationView
            messages={messages}
            streamState={streamState}
            userMessage={userInput ?? ""}
            taskId={taskId}
          />
        ) : (
          <WelcomeView
            onPick={handleSuggestion}
          />
        )}

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
        {streamState.error?.code === "AUTH_XHS_NOT_BOUND" ? (
          <div className="mt-3 rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-3 py-2 text-[12px] text-[#C62828]">
            <div className="flex items-center justify-between gap-2">
              <span>
                小红书数据源尚未授权 · {streamState.error.message || "请先在「数据源授权」绑定 Cookie"}
              </span>
              <a
                href="/settings"
                className="rounded border border-[#FF4757] bg-[#FF4757] px-2 py-0.5 text-white hover:bg-[#E03B4A]"
              >
                前往授权
              </a>
            </div>
          </div>
        ) : null}
        <div ref={bottomRef} />
      </div>

      <AdvancedBar open={advOpen} onToggle={() => setAdvOpen((v) => !v)} value={advanced} onChange={setAdvanced} />

      <div className="border-t border-[#F0EEEB] px-[18px] py-[14px]">
        {cookieBlocked ? (
          <div className="mb-2 rounded border border-[#FDD8D8] bg-[#FFF2F2] px-2 py-1.5 text-[11px] text-[#C62828]">
            Cookie 已过期。普通问答仍可继续；如需发起小红书采集任务，请先到“系统设置”页重新登录。
          </div>
        ) : null}
        <div className="flex items-end gap-2">
          <textarea
            className="h-[40px] max-h-[120px] min-h-[40px] flex-1 resize-none rounded-xl border-[1.5px] border-[#E8E5E0] px-[14px] py-[10px] text-[13px] leading-[1.5] outline-none placeholder:text-[#B8B4B0] focus:border-[#FF4757]"
            placeholder="问我问题，或描述你想分析的品类/品牌..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={creating}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void handleSend();
              }
            }}
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={creating || !input.trim()}
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[10px] bg-[#FF4757] text-white transition hover:bg-[#E8404F] disabled:cursor-not-allowed disabled:bg-[#FFB6BD]"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M13 5l7 7-7 7M5 12h14" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

function WelcomeView({
  onPick,
}: {
  onPick: (text: string) => void | Promise<void>;
}) {
  return (
    <div>
      <div className="pt-10 pb-5 text-center">
        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-[14px] bg-gradient-to-br from-[#FFE8E0] to-[#FFD6CC]">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#FF4757" strokeWidth="2">
            <path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
          </svg>
        </div>
        <h3 className="text-[16px] font-semibold">告诉我你想分析什么</h3>
        <p className="mt-2 text-[13px] leading-[1.6] text-[#8A8580]">
          描述你关注的品类、品牌或内容方向，
          <br />
          我会自动完成数据采集、多维洞察和爆文模型生成。
        </p>
      </div>
      <div className="flex flex-col gap-2">
        {SUGGESTION_TAGS.map((text) => (
          <button
            key={text}
            type="button"
            onClick={() => onPick(text)}
            className="rounded-[10px] border border-[#F0EEEB] bg-[#FAFAF8] px-[14px] py-[10px] text-left text-[12px] leading-[1.5] text-[#5A5550] transition hover:border-[#FFD6CC] hover:bg-[#FFF8F5]"
          >
            {text}
          </button>
        ))}
      </div>
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
  const milestoneBubbles = useMemo(() => buildTaskMilestoneBubbles(streamState), [streamState]);
  const firstMsg = userMessage || "正在分析…";
  const taskIsActive = taskId && ["pending", "queued", "running", "paused", "unknown"].includes(streamState.status);
  const showCompletionHint = !!taskId && streamState.status === "completed" && messages.length === 0;

  return (
    <div className="flex flex-col gap-4">
      {messages.length > 0 ? (
        messages.map((message) => (
          <Message
            key={message.message_id}
            role={message.role === "user" ? "user" : "ai"}
            bubble={
              <div>
                <MessageContent message={message} />
                {message.citations.length > 0 ? <CitationList citations={message.citations} /> : null}
              </div>
            }
            time={formatMessageTime(message.created_at)}
          />
        ))
      ) : taskId ? (
        <Message role="user" bubble={firstMsg} time={taskId.slice(-6)} />
      ) : null}

      {taskId
        ? milestoneBubbles.map((item) => (
            <Message
              key={item.id}
              role="ai"
              bubble={
                <div className="flex items-center gap-2">
                  <MilestoneDot tone={item.tone} loading={item.loading} />
                  <span>{item.text}</span>
                </div>
              }
              time={item.time}
            />
          ))
        : null}

      {showCompletionHint ? (
        <Message
          role="ai"
          bubble={
            <span>
              分析完成，右侧画布已生成。
              <br />
              你可以继续调整：
              <br />
              · “展开封面分析的详情”
              <br />
              · “加上竞品XX的对比”
              <br />
              · “导出 Excel”
            </span>
          }
          time=""
        />
      ) : null}
    </div>
  );
}

function MessageContent({ message }: { message: ChatMessage }) {
  const debug = message.debug ?? {};
  const streaming = debug["streaming"] === true;
  const label = typeof debug["label"] === "string" ? debug["label"] : "正在生成回答...";

  if (streaming && !message.content) {
    return (
      <div className="flex items-center gap-2 text-[#8A8580]">
        <span className="inline-flex h-4 w-4 items-center justify-center">
          <span className="h-2 w-2 animate-ping rounded-full bg-[#FF4757]" />
        </span>
        <span>{label}</span>
      </div>
    );
  }

  return (
    <div>
      <MarkdownContent content={message.content} />
      {streaming ? <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-[#FF4757] align-[-2px]" /> : null}
    </div>
  );
}

type MarkdownBlock =
  | { type: "text"; content: string }
  | { type: "code"; lang: string; content: string };

function MarkdownContent({ content }: { content: string }) {
  const blocks = splitMarkdownBlocks(content);
  return (
    <div className="space-y-2 text-left">
      {blocks.map((block, index) =>
        block.type === "code" ? (
          <pre
            key={`code-${index}`}
            className="overflow-x-auto rounded-lg border border-[#E8E5E0] bg-[#2D2A26] px-3 py-2 text-[12px] leading-[1.6] text-white"
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
        <p key={`${blockIndex}-p-${nodes.length}`} className="leading-[1.7]">
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
      const Tag = heading[1].length === 1 ? "h3" : "h4";
      nodes.push(
        <Tag key={`${blockIndex}-h-${nodes.length}`} className="mt-2 font-semibold text-[#2D2A26] first:mt-0">
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
        <code key={`code-${match.index}`} className="rounded bg-[#F0EEEB] px-1 py-0.5 text-[12px] text-[#8B6914]">
          {token.slice(1, -1)}
        </code>,
      );
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = link?.[2] ?? "";
      const safeHref = /^(https?:\/\/|mailto:)/i.test(href) ? href : "";
      nodes.push(
        safeHref ? (
          <a
            key={`link-${match.index}`}
            href={safeHref}
            target="_blank"
            rel="noreferrer"
            className="text-[#FF4757] underline decoration-[#FFD6CC] underline-offset-2"
          >
            {link?.[1]}
          </a>
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
      <summary className="cursor-pointer text-[11px] font-medium text-[#8A8580]">
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
  const avatarStyle: CSSProperties =
    role === "ai"
      ? { background: "#FFF0EE", color: "#FF4757" }
      : { background: "#F0EEEB", color: "#5A5550" };
  const bubbleStyle: CSSProperties =
    role === "ai"
      ? { background: "#FAFAF8", border: "1px solid #F0EEEB" }
      : { background: "#FFF5F3", border: "1px solid #FFE8E0" };

  return (
    <div className={`flex gap-2.5 ${role === "user" ? "flex-row-reverse" : ""}`}>
      <div
        className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg text-[12px] font-semibold"
        style={avatarStyle}
      >
        {role === "ai" ? "R" : "我"}
      </div>
      <div className={`max-w-[85%] ${role === "user" ? "text-right" : ""}`}>
        <div
          className="rounded-xl px-4 py-3 text-[13px] leading-[1.7] text-[#2D2A26]"
          style={bubbleStyle}
        >
          {bubble}
        </div>
        {time ? <div className="mt-1 text-[10px] text-[#A8A4A0]">{time}</div> : null}
      </div>
    </div>
  );
}


type MilestoneBubble = {
  id: string;
  text: string;
  tone: "info" | "success" | "warn" | "error";
  loading?: boolean;
  time: string;
};

function buildTaskMilestoneBubbles(state: TaskStreamState): MilestoneBubble[] {
  const out: MilestoneBubble[] = [];
  const ts = state.lastEventAt
    ? new Date(state.lastEventAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : "";

  const has = (agentId: string) => Boolean(state.agentStatus[agentId]);
  const isTerminal = ["completed", "failed", "cancelled"].includes(state.status);
  const done = (agentId: string, nextAgentIds: string[] = []) =>
    Boolean(
      state.agentStatus[agentId]?.done ||
        isTerminal ||
        nextAgentIds.some((id) => has(id)),
    );

  if (["pending", "queued"].includes(state.status)) {
    out.push({
      id: "task-created",
      text: "任务已创建，正在进入执行队列。",
      tone: "info",
      loading: true,
      time: ts,
    });
  }
  if (state.status === "running") {
    out.push({
      id: "task-running",
      text: "任务开始执行，正在持续接收进度。",
      tone: "info",
      loading: true,
      time: ts,
    });
  }
  if (state.status === "paused") {
    out.push({ id: "task-paused", text: "任务已暂停，可在右上角继续。", tone: "warn", time: ts });
  }

  if (has("CrawlerAgent")) {
    const isDone = done("CrawlerAgent", ["ImageAnalysisAgent", "VideoAnalysisAgent", "ViralModelAgent"]);
    out.push({
      id: "crawler-progress",
      text: isDone ? "数据采集完成，进入图文/视频分析。" : "正在采集行业池、竞品、互动TOP与 SERP 数据。",
      tone: isDone ? "success" : "info",
      loading: !isDone,
      time: ts,
    });
  }

  const imageHas = has("ImageAnalysisAgent");
  const videoHas = has("VideoAnalysisAgent");
  if (imageHas || videoHas) {
    const isDone =
      done("ImageAnalysisAgent", ["ViralModelAgent", "InsightAgent", "CanvasRenderAgent"]) &&
      done("VideoAnalysisAgent", ["ViralModelAgent", "InsightAgent", "CanvasRenderAgent"]);
    out.push({
      id: "multimodal-progress",
      text: isDone ? "图文/视频 6 要素标注完成。" : "正在进行图文/视频 6 要素标注。",
      tone: isDone ? "success" : "info",
      loading: !isDone,
      time: ts,
    });
  }

  if (has("ViralModelAgent")) {
    const isDone = done("ViralModelAgent", ["InsightAgent", "RAGAgent", "CanvasRenderAgent"]);
    out.push({
      id: "modeling-progress",
      text: isDone ? "爆文模型矩阵生成完成。" : "正在生成爆文模型矩阵（混合聚类）。",
      tone: isDone ? "success" : "info",
      loading: !isDone,
      time: ts,
    });
  }

  if (has("InsightAgent") || has("RAGAgent")) {
    const insightDone =
      done("InsightAgent", ["CanvasRenderAgent"]) &&
      done("RAGAgent", ["CanvasRenderAgent"]);
    out.push({
      id: "insight-progress",
      text: insightDone ? "洞察与业务约束检索完成。" : "正在生成洞察并检索业务约束知识。",
      tone: insightDone ? "success" : "info",
      loading: !insightDone,
      time: ts,
    });
  }

  if (has("CanvasRenderAgent")) {
    const isDone = done("CanvasRenderAgent");
    out.push({
      id: "canvas-progress",
      text: isDone ? "Canvas 渲染完成。" : "正在渲染 Canvas 模块。",
      tone: isDone ? "success" : "info",
      loading: !isDone,
      time: ts,
    });
  }

  if (state.videoAsyncState === "pending") {
    out.push({
      id: "video-async-pending",
      text: "视频异步分析仍在后台进行，结果会自动补全。",
      tone: "info",
      loading: true,
      time: ts,
    });
  } else if (state.videoAsyncState === "partial") {
    out.push({
      id: "video-async-partial",
      text: "视频异步分析部分完成，已有结果已写入画布。",
      tone: "warn",
      time: ts,
    });
  } else if (state.videoAsyncState === "failed") {
    out.push({
      id: "video-async-failed",
      text: "视频异步分析失败，可稍后重试相关模块。",
      tone: "error",
      time: ts,
    });
  }

  if (state.status === "completed") {
    out.push({
      id: "task-completed",
      text: "分析完成，右侧画布已可查看与编辑。",
      tone: "success",
      time: ts,
    });
  } else if (state.status === "failed") {
    out.push({ id: "task-failed", text: "任务执行失败，请查看日志后重试。", tone: "error", time: ts });
  } else if (state.status === "cancelled") {
    out.push({ id: "task-cancelled", text: "任务已取消。", tone: "warn", time: ts });
  }

  return out;
}

function MilestoneDot({ tone, loading }: { tone: MilestoneBubble["tone"]; loading?: boolean }) {
  if (loading) {
    return (
      <span className="inline-flex h-4 w-4 items-center justify-center">
        <span className="h-2 w-2 animate-ping rounded-full bg-[#FF4757]" />
      </span>
    );
  }
  if (tone === "success") return <span className="h-[8px] w-[8px] flex-shrink-0 rounded-full bg-[#3D8C40]" />;
  if (tone === "warn") return <span className="h-[8px] w-[8px] flex-shrink-0 rounded-full bg-[#E8A84C]" />;
  if (tone === "error") return <span className="h-[8px] w-[8px] flex-shrink-0 rounded-full bg-[#E04040]" />;
  return <span className="h-[8px] w-[8px] flex-shrink-0 rounded-full bg-[#8A8580]" />;
}

function AdvancedBar({
  open,
  onToggle,
  value,
  onChange,
}: {
  open: boolean;
  onToggle: () => void;
  value: AdvancedConfig;
  onChange: (v: AdvancedConfig) => void;
}) {
  return (
    <div className="border-t border-[#F0EEEB] px-[18px]">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center justify-center gap-1 py-2 text-[11px] text-[#A8A4A0] transition hover:text-[#5A5550]"
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className={`transition ${open ? "rotate-180" : ""}`}
        >
          <path d="M19 9l-7 7-7-7" />
        </svg>
        高级配置
      </button>
      {open ? (
        <div className="pb-3">
          <div className="grid grid-cols-2 gap-2.5">
            <Field
              label="笔记类型"
              value={value.note_type}
              options={["不限", "视频", "图文"]}
              onChange={(v) => onChange({ ...value, note_type: v })}
            />
            <Field
              label="时间范围"
              value={value.time_range}
              options={["不限", "一天内", "一周内", "半年内"]}
              onChange={(v) => onChange({ ...value, time_range: v })}
            />
            <Field
              label="采集数量"
              value={value.sample_count}
              options={["100", "200", "500"]}
              onChange={(v) => onChange({ ...value, sample_count: v })}
            />
            <Field
              label="爆款比例"
              value={value.viral_ratio}
              options={["前50%", "前30%", "前20%"]}
              onChange={(v) => onChange({ ...value, viral_ratio: v })}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Field({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-semibold text-[#A8A4A0]">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 rounded-[7px] border border-[#E8E5E0] bg-[#FAFAF8] px-2.5 text-[12px] text-[#2D2A26] outline-none focus:border-[#FF4757]"
      >
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    </label>
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
        className={`rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-2.5 py-1 text-[11px] text-[#FF4757] transition hover:bg-[#FFE8E0] disabled:cursor-not-allowed disabled:opacity-50`}
      >
        取消
      </button>
      <button type="button" onClick={onNew} className={btn} disabled={busy}>
        + 新建
      </button>
    </div>
  );
}
