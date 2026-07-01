"use client";

import { useEffect, useRef, useState } from "react";

import type { TaskLogEntry, TaskStreamState } from "@/lib/sse/event-reducer";

interface AgentTimelineProps {
  state: TaskStreamState;
  taskId: string | null;
  /** 任务类型（来自 TaskHandoff.task_type），决定用哪套步骤目录；不传时沿用爆文默认目录 */
  taskType?: string;
}

type AgentStepDef = {
  id: string;
  label: string;
  caption: string;
  code: string;
  hasLLM: boolean;
};

const XHS_AUTH_NOT_BOUND_CODE = "AUTH_XHS_NOT_BOUND";
const XHS_CAPTCHA_CODE = "CRAWLER_CAPTCHA";
const XHS_COOKIE_EXPIRED_CODE = "AUTH_COOKIE_EXPIRED";

const XHS_AUTH_REQUIRED_CODES = new Set([
  XHS_AUTH_NOT_BOUND_CODE,
  XHS_CAPTCHA_CODE,
  XHS_COOKIE_EXPIRED_CODE,
]);

export const AGENT_STEPS: AgentStepDef[] = [
  { id: "InputParserAgent",  label: "意图解析",   caption: "拆解需求、关键词与任务参数",            code: "01", hasLLM: true  },
  { id: "XhsAuthAgent",      label: "数据源授权", caption: "检查小红书 Cookie 与访问权限",           code: "02", hasLLM: false },
  { id: "CrawlerAgent",      label: "小红书采集", caption: "采集行业池、竞品、互动 TOP 与 SERP 样本", code: "03", hasLLM: false },
  { id: "ImageAnalysisAgent",label: "图文分析",   caption: "提取封面、标题、压字与内容结构",          code: "04", hasLLM: true  },
  { id: "VideoAnalysisAgent",label: "视频分析",   caption: "分析视频封面、节奏、镜头与音画同步",       code: "05", hasLLM: true  },
  { id: "ViralModelAgent",   label: "爆文模型",   caption: "聚合样本并生成爆文模型矩阵",              code: "06", hasLLM: true  },
  { id: "Sheet2NarrativeAgent", label: "分类叙事", caption: "为爆文模型各要素分类生成 playbook 文案", code: "07", hasLLM: true  },
  { id: "InsightAgent",      label: "洞察提炼",   caption: "沉淀痛点、SEO 与创作策略",               code: "08", hasLLM: true  },
  { id: "RAGAgent",          label: "知识增强",   caption: "检索业务知识库并补充约束",               code: "09", hasLLM: true  },
  { id: "CanvasRenderAgent", label: "画布渲染",   caption: "组织 Canvas 模块与可编辑产物",           code: "10", hasLLM: false },
];

/** 评论分析 Skill 专用步骤目录，对应 comment_pipeline.py 的 7 个阶段 agent_id */
export const COMMENT_AGENT_STEPS: AgentStepDef[] = [
  { id: "CommentInputParser", label: "意图解析",     caption: "拆解关键词与采集范围",               code: "01", hasLLM: true  },
  { id: "CommentCrawler",     label: "笔记采集",     caption: "按关键词搜索并补全笔记详情",           code: "02", hasLLM: false },
  { id: "CommentFetcher",     label: "评论采集",     caption: "逐笔记拉取全量评论（含子评论）",        code: "03", hasLLM: false },
  { id: "CommentDim1",        label: "舆情分类",     caption: "识别评论中的核心讨论类别",             code: "04", hasLLM: true  },
  { id: "CommentDim2",        label: "类别深度分析", caption: "逐类别拆解正/中/负情感与代表性评论",    code: "05", hasLLM: true  },
  { id: "CommentDim3",        label: "总体洞察",     caption: "从评论中提炼行动导向的创作建议",        code: "06", hasLLM: true  },
  { id: "CommentReport",      label: "生成报告",     caption: "汇总生成 Excel 数据表与 Markdown 报告", code: "07", hasLLM: false },
];

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function AgentTimeline({ state, taskId, taskType }: AgentTimelineProps) {
  const isTerminal = TERMINAL_STATUSES.has(state.status);
  const isRunning = state.status === "running" || state.status === "queued";
  const activeSteps = taskType === "comment_analysis" ? COMMENT_AGENT_STEPS : AGENT_STEPS;

  // ─── 问题1：实时计时器（每秒本地更新，不依赖 SSE 事件频率）─────────────────
  const [now, setNow] = useState(() => Date.now());
  const startTsRef = useRef<number | null>(null);

  useEffect(() => {
    // 首条事件到达时记录起始时间戳
    if (state.logs[0]?.timestamp && startTsRef.current === null) {
      startTsRef.current = Date.parse(state.logs[0].timestamp);
    }
  }, [state.logs]);

  useEffect(() => {
    if (!isRunning) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isRunning]);

  const elapsedSec =
    startTsRef.current != null
      ? Math.max(0, Math.round((isRunning ? now : Date.parse(state.lastEventAt ?? "")) - startTsRef.current) / 1000)
      : null;

  if (!taskId) return null;

  const errorStepId = pickErrorStepId(state, activeSteps);

  // ─── 问题2：步骤可见性——有日志就要显示，不能等 agentStatus ────────────────
  const visibleSteps = activeSteps.filter(
    (step) =>
      state.agentStatus[step.id] ||
      state.agentThinking[step.id] ||
      (state.agentLogs[step.id]?.length ?? 0) > 0,
  );

  // 当前正在运行的步骤（用于 running 状态时显示具体步骤名）
  const currentRunningStep = activeSteps.find((step) => {
    const entry = state.agentStatus[step.id];
    return entry && !entry.done;
  });

  return (
    <section className="space-y-0.5">
      {/* 顶部状态行：执行中时 sticky 固定，完成后随内容正常流动 */}
      <div className={`flex items-center gap-2 pb-3 pt-0.5 ${isRunning ? "sticky top-0 z-10 bg-papyrus/[0.96] backdrop-blur-sm" : ""}`}>
        {isRunning ? (
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-dew opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-dew" />
          </span>
        ) : (
          <span
            className={`h-2 w-2 shrink-0 rounded-full ${
              state.status === "completed"
                ? "bg-moss"
                : state.status === "failed"
                  ? "bg-[#C05A5C]"
                  : "bg-obsidian/20"
            }`}
          />
        )}
        <span className="text-[13px] text-obsidian/55">
          {timelineSummaryLine(state.status, elapsedSec, currentRunningStep?.label)}
        </span>
      </div>

      {/* 步骤列表（动态出现） */}
      <div className="space-y-1">
        {visibleSteps.map((step) => (
          <StepRow
            key={step.id}
            step={step}
            state={state}
            isTerminal={isTerminal}
            errorStepId={errorStepId}
          />
        ))}
      </div>

      {/* 视频异步状态徽章 */}
      {state.videoAsyncState !== "idle" ? (
        <VideoAsyncBadge value={state.videoAsyncState} />
      ) : null}

      {/* 错误信息展示 */}
      {state.error ? (
        <div className="mt-2 rounded-xl border border-[#E8CFC8] bg-[#FFFBFA] px-3 py-2.5 text-[12px] leading-relaxed text-[#9A5558]">
          <div className="font-semibold">{state.error.code}</div>
          <div>{state.error.message || "任务执行失败，请查看日志后重试。"}</div>
          {XHS_AUTH_REQUIRED_CODES.has(state.error.code) ? (
            <a
              href="/settings#xhs-credential"
              className="mt-2 inline-flex rounded-full bg-obsidian px-3 py-1.5 text-[11px] font-semibold text-papyrus"
            >
              前往数据源授权
            </a>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

// ─────────────────────────────────────────
// 步骤行组件
// ─────────────────────────────────────────

interface StepRowProps {
  step: AgentStepDef;
  state: TaskStreamState;
  isTerminal: boolean;
  errorStepId: string | null;
}

function StepRow({ step, state, isTerminal, errorStepId }: StepRowProps) {
  const entry = state.agentStatus[step.id];
  const isStepRunning = Boolean(entry && !entry.done && !isTerminal);
  // 用于图标/样式的"视觉完成"（含 thinkingDone）
  const isStepDone = Boolean(entry?.done || isTerminal || state.agentThinkingDone[step.id]);
  // 仅用于自动收回：必须等 agent entry.done 确认，避免子步骤 thinking 结束就触发收回
  const isStepActuallyDone = Boolean(entry?.done || isTerminal);
  const isError = errorStepId === step.id;

  const thinkingText = state.agentThinking[step.id] ?? "";
  const thinkingDone = state.agentThinkingDone[step.id] ?? false;
  const agentLogs = state.agentLogs[step.id] ?? [];
  const hasThinking = thinkingText.length > 0;
  const hasLogs = agentLogs.length > 0;

  const canExpand = hasThinking || thinkingDone || hasLogs;

  // 执行中自动展开，完成后自动收回；用户点击可手动覆盖
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (isStepRunning && canExpand) {
      setOpen(true);              // 步骤开始执行 → 展开
    } else if (isStepActuallyDone) {
      setOpen(false);             // agent 真正完成 → 自动收回
    }
  }, [isStepRunning, isStepActuallyDone, canExpand]);

  // 思考框自动滚到底部（流式输出时跟随）
  const thinkScrollRef = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (thinkScrollRef.current) {
      thinkScrollRef.current.scrollTop = thinkScrollRef.current.scrollHeight;
    }
  }, [thinkingText]);

  return (
    <div className="rounded-lg overflow-hidden">
      {/* 步骤头行 */}
      <div
        className={`flex items-center gap-2 px-2 py-1.5 rounded-lg transition-colors ${
          canExpand ? "cursor-pointer hover:bg-obsidian/[0.03] select-none" : ""
        }`}
        onClick={canExpand ? () => setOpen((v) => !v) : undefined}
        role={canExpand ? "button" : undefined}
        aria-expanded={canExpand ? open : undefined}
      >
        <StepIcon
          isRunning={isStepRunning}
          isDone={isStepDone}
          isError={isError}
          code={step.code}
        />
        <span className="flex-1 min-w-0">
          <span
            className={`block text-[13px] font-medium leading-snug ${
              isError
                ? "text-[#9A5558]"
                : isStepRunning
                  ? "text-obsidian/90"
                  : isStepDone
                    ? "text-obsidian/70"
                    : "text-obsidian/55"
            }`}
          >
            {step.label}
          </span>
          {/* 运行中副标题：展示该阶段最新一条 AGENT_PROGRESS 文案，如"库中已有87条笔记，需再采集113条…" */}
          {isStepRunning && entry?.message && (
            <span className="block truncate text-[11px] leading-snug text-obsidian/40">
              {entry.message}
            </span>
          )}
        </span>
        {entry?.lastAt && (
          <span className="shrink-0 text-[10px] text-obsidian/28">
            {formatTime(entry.lastAt)}
          </span>
        )}
        {canExpand && (
          <ChevronIcon
            className={`h-3.5 w-3.5 shrink-0 text-obsidian/25 transition-transform duration-150 ${
              open ? "rotate-180" : ""
            }`}
          />
        )}
      </div>

      {/* 展开内容区 */}
      {open && (
        <div className="px-2 pb-2 space-y-1.5">
          {/* ─── 问题3：LLM 流式思考内容（实时更新，立即可见）─────────── */}
          {(hasThinking || thinkingDone) && (
            <div className="ml-7 rounded-lg border border-obsidian/[0.07] bg-obsidian/[0.025]">
              {hasThinking ? (
                <pre
                  ref={thinkScrollRef}
                  className="max-h-52 overflow-y-auto whitespace-pre-wrap break-words px-3 py-2.5 font-mono text-[11px] leading-relaxed text-obsidian/60"
                >
                  {thinkingText}
                  {isStepRunning && !thinkingDone && <BlinkingCursor />}
                </pre>
              ) : (
                <p className="px-3 py-2 text-[11px] text-obsidian/30 italic">
                  思考记录已过期（页面刷新后不再保留）
                </p>
              )}
            </div>
          )}

          {/* 执行日志（次级折叠，默认展开以实时查看） */}
          {hasLogs && <LogsCollapse logs={agentLogs} isRunning={isStepRunning} />}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────
// 执行日志折叠（运行中默认展开）
// ─────────────────────────────────────────

function LogsCollapse({ logs, isRunning }: { logs: TaskLogEntry[]; isRunning: boolean }) {
  // 运行中挂载时默认展开；父层 StepRow 会在步骤完成后整体收回
  const [open, setOpen] = useState(isRunning);

  // 日志列表自动滚到底部
  const logsScrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (logsScrollRef.current) {
      logsScrollRef.current.scrollTop = logsScrollRef.current.scrollHeight;
    }
  }, [logs.length]);

  return (
    <div className="ml-7">
      <button
        type="button"
        className="flex items-center gap-1 text-[11px] text-obsidian/35 hover:text-obsidian/55 transition-colors select-none"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronIcon
          className={`h-3 w-3 transition-transform duration-150 ${open ? "rotate-180" : ""}`}
        />
        执行日志 · {logs.length} 条
      </button>
      {open && (
        <div
          ref={logsScrollRef}
          className="mt-1.5 max-h-44 overflow-y-auto space-y-1 rounded-lg border border-obsidian/[0.06] bg-obsidian/[0.015] px-2.5 py-2"
        >
          {logs.map((log) => (
            <div
              key={log.event_id || `${log.timestamp}-${log.message}`}
              className="flex gap-2 text-[10.5px] leading-relaxed"
            >
              <span
                className={`shrink-0 font-medium ${
                  log.level === "error"
                    ? "text-[#B65356]"
                    : log.level === "warn"
                      ? "text-[#8B6914]"
                      : "text-obsidian/30"
                }`}
              >
                {log.level}
              </span>
              <span className="min-w-0 flex-1 break-words text-obsidian/55">{log.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────
// 辅助组件
// ─────────────────────────────────────────

function BlinkingCursor() {
  return (
    <span className="inline-block w-[7px] h-[1em] bg-dew/70 align-text-bottom animate-[blink_1s_step-end_infinite] ml-0.5" />
  );
}

function StepIcon({
  isRunning,
  isDone,
  isError,
  code,
}: {
  isRunning: boolean;
  isDone: boolean;
  isError: boolean;
  code: string;
}) {
  if (isRunning) {
    return (
      <span className="relative flex h-5 w-5 shrink-0 items-center justify-center">
        <span className="absolute h-5 w-5 animate-ping rounded-full bg-dew/30" />
        <span className="relative flex h-5 w-5 items-center justify-center rounded-full bg-dew text-[9px] font-bold text-white shadow-sm">
          {code}
        </span>
      </span>
    );
  }
  if (isDone && !isError) {
    return (
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-moss/80 text-[10px] font-bold text-white shadow-sm">
        ✓
      </span>
    );
  }
  if (isError) {
    return (
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-[#E8CFC8] bg-[#FFF5F3] text-[9px] font-semibold text-[#9A5558]">
        !
      </span>
    );
  }
  return (
    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-black/[0.08] bg-white text-[9px] font-semibold text-obsidian/32">
      {code}
    </span>
  );
}

function ChevronIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      aria-hidden
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

function VideoAsyncBadge({ value }: { value: TaskStreamState["videoAsyncState"] }) {
  const map: Record<TaskStreamState["videoAsyncState"], { label: string; cls: string }> = {
    idle: { label: "", cls: "" },
    pending: { label: "视频异步支路仍在后台分析", cls: "border-dew/45 bg-dew/10 text-obsidian/55" },
    completed: { label: "视频异步分析已完成", cls: "border-moss bg-moss/40 text-[#49715A]" },
    partial: { label: "视频异步分析部分完成", cls: "border-[#F1E3B6] bg-[#FFF8E6] text-[#8B6914]" },
    failed: { label: "视频异步分析失败", cls: "border-[#E8CFC8] bg-[#FFF5F3] text-[#9A5558]" },
  };
  const item = map[value];
  if (!item.label) return null;
  return (
    <div className={`mt-2 rounded-xl border px-3 py-2 text-[11px] font-semibold ${item.cls}`}>
      {item.label}
    </div>
  );
}

// ─────────────────────────────────────────
// 工具函数
// ─────────────────────────────────────────

function pickErrorStepId(state: TaskStreamState, steps: AgentStepDef[]): string | null {
  if (state.status !== "failed" && !state.error) return null;
  if (state.error?.code && XHS_AUTH_REQUIRED_CODES.has(state.error.code) && steps.some((s) => s.id === "XhsAuthAgent")) {
    return "XhsAuthAgent";
  }
  const lastAgentId = state.logs
    .slice()
    .reverse()
    .find((log) => typeof log.agent_id === "string")?.agent_id;
  if (lastAgentId && steps.some((step) => step.id === lastAgentId)) return lastAgentId;
  const lastEntry = steps.slice().reverse().find((step) => state.agentStatus[step.id]);
  return lastEntry?.id ?? steps[0]?.id ?? null;
}

function timelineSummaryLine(
  status: TaskStreamState["status"],
  elapsedSec: number | null,
  currentStepLabel?: string,
): string {
  const fmt = (s: number) => {
    const r = Math.round(s);
    if (r < 60) return `${r} 秒`;
    const m = Math.floor(r / 60);
    const sec = r % 60;
    return sec > 0 ? `${m} 分 ${sec} 秒` : `${m} 分钟`;
  };
  if (status === "completed" && elapsedSec != null) return `已完成，用时 ${fmt(elapsedSec)}`;
  if (status === "completed") return "已完成";
  if (status === "failed") return "执行失败";
  if (status === "cancelled") return "已取消";
  if (status === "paused") return "已暂停";
  if (status === "queued") return "排队中…";
  if (status === "pending") return "准备执行…";
  if (status === "running" && currentStepLabel && elapsedSec != null)
    return `正在执行 ${currentStepLabel}，已用 ${fmt(elapsedSec)}`;
  if (status === "running" && elapsedSec != null) return `执行中，已用 ${fmt(elapsedSec)}`;
  return "执行中…";
}

function formatTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
