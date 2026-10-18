"use client";

import * as React from "react";
import { ArrowUp, Check, ChevronRight, Sparkles, Square, X } from "lucide-react";

export type ThinkingStatus = "idle" | "thinking" | "answering" | "done";
export type ThinkingStep = { id: string; text: string; detail?: string; status?: "active" | "complete" | "error" };
export type ThinkingSubmitOptions = { think: boolean };
export type ThinkingPromptInputProps = {
  status?: ThinkingStatus;
  prompt?: string;
  steps?: ThinkingStep[];
  answer?: string;
  elapsedSeconds?: number;
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  think?: boolean;
  defaultThink?: boolean;
  onThinkChange?: (think: boolean) => void;
  onSubmit?: (value: string, options: ThinkingSubmitOptions) => void;
  onStop?: () => void;
  placeholder?: string;
  followUpPlaceholder?: string;
  answerNote?: string;
  disabled?: boolean;
  className?: string;
};

const EMPTY_STEPS: ThinkingStep[] = [];
const EASE = "cubic-bezier(0.22, 1, 0.36, 1)";

function usePrefersReducedMotion() {
  const [reduced, setReduced] = React.useState(true);
  React.useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return reduced;
}

export type ThinkingTraceProps = {
  /** true = 思考进行中（折叠按钮显示流光文字 + 计时，步骤区强制展开） */
  live: boolean;
  steps: ThinkingStep[];
  elapsedSeconds?: number;
  liveLabel?: string;
  doneLabel?: (seconds: number) => string;
  /** 步骤列表之后的附加内容（如执行计划、确认卡） */
  children?: React.ReactNode;
  className?: string;
};

/** 可复用的"思考过程"折叠区：流光标题 + 计时 + 步骤时间线。 */
export function ThinkingTrace({
  live,
  steps,
  elapsedSeconds = 0,
  liveLabel = "Thinking it through",
  doneLabel = (s) => `Thought for ${s}s`,
  children,
  className = "",
}: ThinkingTraceProps) {
  const [expanded, setExpanded] = React.useState(false);
  const reduced = usePrefersReducedMotion();
  const shimmer = React.useRef(null as HTMLSpanElement | null);
  const body = React.useRef(null as HTMLDivElement | null);
  const regionId = React.useId();
  const open = live || expanded;
  const seconds = Number.isFinite(elapsedSeconds) ? Math.max(0, Math.round(elapsedSeconds)) : 0;

  React.useEffect(() => {
    if (live) setExpanded(false);
  }, [live]);

  React.useEffect(() => {
    const element = shimmer.current;
    if (!live || reduced || !element) return;
    let disposed = false;
    let animation = null as Animation | null;
    queueMicrotask(() => {
      if (disposed || typeof element.animate !== "function") return;
      animation = element.animate([
        { backgroundPosition: "100% 0" },
        { backgroundPosition: "0% 0" },
      ], { duration: 1800, iterations: Infinity, easing: "linear" });
    });
    return () => { disposed = true; animation?.cancel(); };
  }, [live, reduced]);

  React.useLayoutEffect(() => {
    const node = body.current;
    if (!node || !open || reduced) return;
    let disposed = false;
    const animations: Animation[] = [];
    queueMicrotask(() => {
      if (disposed) return;
      node.querySelectorAll("[data-step]").forEach((row, index) => {
        if (!(row instanceof HTMLElement) || row.dataset.entered || typeof row.animate !== "function") return;
        row.dataset.entered = "true";
        animations.push(row.animate([
          { opacity: 0, transform: "translateY(10px)" },
          { opacity: 1, transform: "translateY(0px)" },
        ], { duration: 420, delay: index * 45, easing: EASE, fill: "backwards" }));
      });
    });
    return () => { disposed = true; animations.forEach((animation) => animation.cancel()); };
  }, [steps, open, reduced]);

  return (
    <div data-slot="thinking-trace" className={`min-w-0 ${className}`}>
      <button
        type="button"
        data-thought-toggle
        aria-expanded={open}
        aria-controls={regionId}
        disabled={live}
        onClick={() => setExpanded(!expanded)}
        className={`flex max-w-full items-center gap-2 rounded-full text-xs outline-none transition-colors motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-ring ${live ? "h-7 w-full text-muted-foreground" : "border border-border bg-muted/60 px-2.5 py-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"}`}
      >
        <Sparkles className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
        {live ? (
          <span ref={shimmer} data-shimmer className={reduced ? "text-muted-foreground" : "bg-[linear-gradient(90deg,var(--color-muted-foreground)_0%,var(--color-muted-foreground)_42%,var(--color-foreground)_50%,var(--color-muted-foreground)_58%,var(--color-muted-foreground)_100%)] bg-[length:250%_100%] bg-clip-text text-transparent"}>{liveLabel}</span>
        ) : <span>{doneLabel(seconds)}</span>}
        {live ? <span className="ml-auto font-mono text-[11px] tabular-nums text-muted-foreground">{seconds}s</span> : <ChevronRight className={`size-3.5 transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`} aria-hidden="true" />}
      </button>
      <div ref={body} id={regionId} data-reasoning hidden={!open} className="pt-3">
        {steps.length > 0 && (
          <ol className="relative ml-[7px] border-l border-border pl-5">
            {steps.map((step) => (
              <li key={step.id} data-step={step.id} className="relative flex min-h-9 items-center gap-3 py-2 text-xs leading-5">
                <span className="absolute -left-[26px] flex size-[11px] items-center justify-center rounded-full bg-background ring-4 ring-background">
                  {step.status === "complete" ? (
                    <Check className="size-3 text-foreground" aria-label="Complete" />
                  ) : step.status === "error" ? (
                    <X className="size-3 text-[#B65356]" aria-label="Failed" />
                  ) : (
                    <span className="size-1.5 rounded-full bg-primary motion-safe:animate-pulse" aria-label="In progress" />
                  )}
                </span>
                <span className="min-w-0">
                  <span className={step.status === "active" ? "text-foreground" : step.status === "error" ? "text-[#9A5558]" : "text-muted-foreground"}>{step.text}</span>
                  {step.detail ? <span className="block break-words text-[11px] leading-4 text-muted-foreground/80">{step.detail}</span> : null}
                </span>
              </li>
            ))}
          </ol>
        )}
        {children}
      </div>
    </div>
  );
}

/** Controlled conversation UI. Supply public progress summaries and your own response stream. */
export function ThinkingPromptInput({
  status = "idle",
  prompt = "",
  steps = EMPTY_STEPS,
  answer = "",
  elapsedSeconds = 0,
  value,
  defaultValue = "",
  onValueChange,
  think,
  defaultThink = true,
  onThinkChange,
  onSubmit,
  onStop,
  placeholder = "Ask anything",
  followUpPlaceholder = "Ask a follow-up",
  answerNote,
  disabled = false,
  className = "",
}: ThinkingPromptInputProps) {
  const [draft, setDraft] = React.useState(defaultValue);
  const [localThink, setLocalThink] = React.useState(defaultThink);
  const reduced = usePrefersReducedMotion();
  const transcript = React.useRef(null as HTMLDivElement | null);
  const transcriptBody = React.useRef(null as HTMLDivElement | null);
  const textarea = React.useRef(null as HTMLTextAreaElement | null);
  const question = React.useRef(null as HTMLDivElement | null);
  const inputRect = React.useRef(null as DOMRect | null);
  const previousTurn = React.useRef({ status, prompt });
  const inputValue = value ?? draft;
  const thinkingEnabled = think ?? localThink;
  const busy = status === "thinking" || status === "answering";
  const inputPlaceholder = status === "idle" ? placeholder : followUpPlaceholder;
  const paragraphs = answer.split("\n").filter(Boolean);
  const lastBoundary = [...answer.matchAll(/[.!?。！？](?=\s|$)|\n/g)].at(-1);
  const completeAnswer = status === "done" ? answer : answer.slice(0, lastBoundary ? lastBoundary.index + 1 : 0).trim();

  function scrollToLatest() {
    const panel = transcript.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }

  // Capture the previous input rectangle before resizing the now-empty composer.
  React.useLayoutEffect(() => {
    const previous = previousTurn.current;
    previousTurn.current = { status, prompt };
    const newTurn = status !== "idle" && (previous.status === "idle" || previous.prompt !== prompt || (previous.status === "done" && busy));
    const from = inputRect.current;
    const bubble = question.current;
    const panel = transcript.current;
    if (!newTurn || !from || !bubble || !panel || reduced) return;
    let disposed = false;
    let animation = null as Animation | null;
    queueMicrotask(() => {
      if (disposed || typeof bubble.animate !== "function") return;
      const to = bubble.getBoundingClientRect();
      if (!to.width || !to.height) return;
      // Let the bubble cross the gap from the composer, then restore scrolling.
      panel.style.overflow = "visible";
      animation = bubble.animate([
        { transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) scale(${from.width / to.width}, ${from.height / to.height})` },
        { transform: "translate(0px, 0px) scale(1, 1)" },
      ], { duration: 560, easing: EASE });
      animation.onfinish = () => { panel.style.overflow = ""; scrollToLatest(); };
    });
    return () => { disposed = true; animation?.cancel(); panel.style.overflow = ""; };
  }, [status, prompt, busy, reduced]);

  // Only the composer grows for multiline drafts; the transcript remains above it.
  React.useLayoutEffect(() => {
    const input = textarea.current;
    if (!input) return;
    let disposed = false;
    let animation = null as Animation | null;
    queueMicrotask(() => {
      if (disposed) return;
      const before = input.getBoundingClientRect().height;
      input.style.height = "auto";
      const height = Math.max(64, Math.min(input.scrollHeight, 120));
      input.style.height = `${height}px`;
      if (!reduced && Math.abs(before - height) > 1 && typeof input.animate === "function") {
        animation = input.animate([{ height: `${before}px` }, { height: `${height}px` }], { duration: 200, easing: EASE });
      }
      const rect = input.getBoundingClientRect();
      const style = getComputedStyle(input);
      const left = Number.parseFloat(style.paddingLeft);
      const top = Number.parseFloat(style.paddingTop);
      inputRect.current = new DOMRect(rect.left + left, rect.top + top, rect.width - left - Number.parseFloat(style.paddingRight), height - top - Number.parseFloat(style.paddingBottom));
    });
    return () => { disposed = true; animation?.cancel(); };
  }, [inputValue, reduced]);

  React.useLayoutEffect(() => {
    const panel = transcript.current;
    const body = transcriptBody.current;
    if (!panel || !body) return;
    let disposed = false;
    const scrollToBottom = () => {
      if (!disposed) scrollToLatest();
    };
    queueMicrotask(scrollToBottom);
    const observer = new ResizeObserver(scrollToBottom);
    observer.observe(body);
    observer.observe(panel);
    return () => { disposed = true; observer.disconnect(); };
  }, [status, prompt, steps, answer, answerNote]);

  function changeValue(next: string) {
    if (value === undefined) setDraft(next);
    onValueChange?.(next);
  }

  function submit() {
    const message = inputValue.trim();
    if (!message || busy || disabled || !onSubmit) return;
    onSubmit(message, { think: thinkingEnabled });
    changeValue("");
  }

  return (
    <div data-slot="thinking-prompt-input" data-state={status} className={`flex h-full min-h-0 w-full max-w-[480px] flex-col text-foreground ${className}`}>
      {status !== "idle" && (
        <div ref={transcript} data-transcript role="region" aria-label="Conversation" tabIndex={0} className="mb-4 min-h-0 flex-1 overflow-y-auto rounded-lg outline-none [overflow-anchor:none] focus-visible:ring-2 focus-visible:ring-ring">
          <div ref={transcriptBody} className="space-y-5 px-1 py-1">
            <div ref={question} data-question className="ml-auto w-fit max-w-[94%] origin-top-left rounded-2xl rounded-br-sm bg-muted px-3.5 py-2.5 text-[13px] leading-5 text-foreground">
              <p className="break-words">{prompt}</p>
            </div>
            <div data-assistant className="min-w-0">
              {(steps.length > 0 || status === "thinking") && (
                <ThinkingTrace live={status === "thinking"} steps={steps} elapsedSeconds={elapsedSeconds} />
              )}
              {(status === "answering" || status === "done") && (
                <div data-answer role="region" aria-label="Response" aria-live="off" className="mt-4 space-y-2.5 break-words text-sm leading-6 text-foreground">
                  {paragraphs.map((paragraph, index) => (
                    <p key={index}>{paragraph}{status === "answering" && index === paragraphs.length - 1 && <span data-cursor aria-hidden="true" className="ml-1 inline-block h-3.5 w-1 translate-y-0.5 rounded-full bg-primary motion-safe:animate-pulse" />}</p>
                  ))}
                  {status === "answering" && paragraphs.length === 0 && <span data-cursor aria-hidden="true" className="inline-block h-3.5 w-1 rounded-full bg-primary motion-safe:animate-pulse" />}
                  {answerNote && status === "done" && <p className="text-[11px] leading-4 text-muted-foreground">{answerNote}</p>}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      <form data-composer className="relative mt-auto shrink-0 rounded-3xl border border-border bg-card p-4 shadow-xl shadow-primary/5" onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <textarea
          ref={textarea}
          aria-label={inputPlaceholder}
          placeholder={inputPlaceholder}
          value={inputValue}
          disabled={disabled}
          rows={2}
          onChange={(event) => changeValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
          className="block h-16 min-h-16 w-full resize-none rounded-lg bg-transparent p-1 text-[17px] leading-7 tracking-tight text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        />
        <div className="mt-3 flex items-center justify-between">
          <button
            type="button"
            aria-pressed={thinkingEnabled}
            disabled={disabled || busy}
            onClick={() => {
              if (think === undefined) setLocalThink(!thinkingEnabled);
              onThinkChange?.(!thinkingEnabled);
            }}
            className={`inline-flex h-8 items-center gap-2 rounded-full border px-3 text-xs font-medium outline-none transition-colors motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${thinkingEnabled ? "border-primary/15 bg-primary/5 text-foreground" : "border-border bg-card text-muted-foreground"}`}
          >
            <Sparkles className="size-3.5" aria-hidden="true" />
            Think
            {thinkingEnabled && <Check className="size-3" aria-hidden="true" />}
          </button>
          <button
            type={busy ? "button" : "submit"}
            data-stop={busy ? "" : undefined}
            aria-label={busy ? "Stop" : "Send prompt"}
            onClick={busy ? (event) => { event.preventDefault(); onStop?.(); } : undefined}
            disabled={disabled || (busy ? !onStop : !inputValue.trim() || !onSubmit)}
            className="relative inline-flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card disabled:opacity-30"
          >
            <span data-action-icon="send" aria-hidden="true" className={`absolute inset-0 flex items-center justify-center transition-[opacity,scale] duration-200 motion-reduce:transition-none ${busy ? "scale-75 opacity-0" : "scale-100 opacity-100"}`}><ArrowUp className="size-4" strokeWidth={2} /></span>
            <span data-action-icon="stop" aria-hidden="true" className={`absolute inset-0 flex items-center justify-center transition-[opacity,scale] duration-200 motion-reduce:transition-none ${busy ? "scale-100 opacity-100" : "scale-75 opacity-0"}`}><Square className="size-3 fill-current" /></span>
          </button>
        </div>
      </form>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true" data-announcement>{status === "thinking" ? `Thinking. ${steps.at(-1)?.text ?? ""}` : completeAnswer}</div>
    </div>
  );
}
