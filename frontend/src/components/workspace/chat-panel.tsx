"use client";

import { Fragment, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

import type { ChatMessage, CookieHealth, KnowledgeCitation } from "@/lib/contracts";
import type { SseClientStatus } from "@/lib/sse/event-source-client";
import type { TaskStreamState } from "@/lib/sse/event-reducer";
import { AgentTimeline } from "./agent-timeline";
import { PromptComposer } from "./prompt-composer";

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
  canWriteConversation: boolean;
  canWriteTask: boolean;
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
  failed: { bg: "rgba(242, 142, 130, 0.14)", color: "#c2716b", label: "推送异常" },
};

const XHS_AUTH_SETTINGS_HREF = "/settings#xhs-credential";

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
  canWriteConversation,
  canWriteTask,
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
  const isInitialState = !hasTask && !hasMessages;
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

  if (isInitialState) {
    return (
      <div className="flex h-full w-full flex-col overflow-hidden">
        <PromptComposer
          value={input}
          advanced={advanced}
          expanded={advOpen}
          disabled={!canWriteConversation}
          busy={creating}
          onValueChange={setInput}
          onAdvancedChange={setAdvanced}
          onToggleExpanded={() => setAdvOpen((v) => !v)}
          onSubmit={handleSend}
          onPickSuggestion={handleSuggestion}
          notice={
            <>
              {cookieBlocked ? (
                <div className="rounded-2xl border border-[#E8CFC8] bg-white/80 px-4 py-3 text-[12px] leading-6 text-[#9A5558] shadow-sm backdrop-blur">
                  小红书 Cookie 已过期。普通问答仍可继续；如需发起采集任务，请先到
                  <a href={XHS_AUTH_SETTINGS_HREF} className="mx-1 font-semibold underline underline-offset-2">
                    数据源授权
                  </a>
                  重新授权。
                </div>
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
    );
  }

  return (
    <div
      style={!canvasCollapsed ? { width: "var(--workspace-chat-width, 380px)" } : undefined}
      className={`flex h-full flex-shrink-0 flex-col border-r border-black/[0.06] bg-white/78 backdrop-blur-xl ${
        canvasCollapsed ? "w-full" : "w-auto"
      }`}
    >
      <header className="flex items-center justify-between gap-2 border-b border-black/[0.06] px-[18px] py-[14px] text-[14px] font-bold">
        <span className="font-serif text-[17px] font-semibold tracking-[-0.02em]">Muse 对话</span>
        <div className="flex items-center gap-2">
          {hasTask && canWriteTask ? (
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

      <div ref={scrollAreaRef} className="flex-1 overflow-y-auto px-[18px] py-[18px]">
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

      <AdvancedBar open={advOpen} onToggle={() => setAdvOpen((v) => !v)} value={advanced} onChange={setAdvanced} />

      <div className="border-t border-[#F0EEEB] px-[18px] py-[14px]">
        {cookieBlocked ? (
          <div className="mb-2 rounded border border-[#FDD8D8] bg-[#FFF2F2] px-2 py-1.5 text-[11px] text-[#C62828]">
            小红书 Cookie 已过期。普通问答仍可继续；如需发起采集任务，请先到
            <a href={XHS_AUTH_SETTINGS_HREF} className="mx-0.5 font-semibold underline underline-offset-2">
              数据源授权
            </a>
            重新授权。
          </div>
        ) : null}
        {!canWriteConversation ? (
          <div className="mb-2 rounded border border-[#E8E5E0] bg-[#FAFAF8] px-2 py-1.5 text-[11px] text-[#8A8580]">
            当前账号为只读成员，可查看历史与画布，但不能发送消息或发起分析。
          </div>
        ) : null}
        <div className="flex items-end gap-2">
          <textarea
            className="h-[40px] max-h-[120px] min-h-[40px] flex-1 resize-none rounded-xl border-[1.5px] border-black/[0.08] bg-white/80 px-[14px] py-[10px] text-[13px] leading-[1.5] outline-none placeholder:text-obsidian/24 focus:border-dew"
            placeholder="问我问题，或描述你想分析的品类/品牌..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={creating || !canWriteConversation}
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
            disabled={creating || !canWriteConversation || !input.trim()}
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-dew text-white transition hover:bg-[#9CBBC0] disabled:cursor-not-allowed disabled:bg-fog disabled:text-obsidian/20"
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

      {taskId ? <AgentTimeline state={streamState} taskId={taskId} /> : null}

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
          <span className="h-2 w-2 animate-ping rounded-full bg-dew" />
        </span>
        <span>{label}</span>
      </div>
    );
  }

  return (
    <div>
      <MarkdownContent content={message.content} />
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
            className="text-[#c2716b] underline decoration-dew/50 underline-offset-2"
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
      ? { background: "rgba(242, 142, 130, 0.14)", color: "#c2716b" }
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
        className="h-8 rounded-[7px] border border-black/[0.08] bg-white/75 px-2.5 text-[12px] text-obsidian outline-none focus:border-dew"
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
