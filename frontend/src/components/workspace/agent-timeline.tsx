"use client";

import type { TaskLogEntry, TaskStreamState } from "@/lib/sse/event-reducer";

interface AgentTimelineProps {
  state: TaskStreamState;
  taskId: string | null;
}

type AgentTone = "pending" | "running" | "done" | "warn" | "error";

const XHS_AUTH_NOT_BOUND_CODE = "AUTH_XHS_NOT_BOUND";

const AGENT_STEPS: Array<{
  id: string;
  label: string;
  caption: string;
  code: string;
}> = [
  { id: "InputParserAgent", label: "意图解析", caption: "拆解需求、关键词与任务参数", code: "01" },
  { id: "XhsAuthAgent", label: "数据源授权", caption: "检查小红书 Cookie 与访问权限", code: "02" },
  { id: "CrawlerAgent", label: "小红书采集", caption: "采集行业池、竞品、互动 TOP 与 SERP 样本", code: "03" },
  { id: "ImageAnalysisAgent", label: "图文分析", caption: "提取封面、标题、压字与内容结构", code: "04" },
  { id: "VideoAnalysisAgent", label: "视频分析", caption: "分析视频封面、节奏、镜头与音画同步", code: "05" },
  { id: "ViralModelAgent", label: "爆文模型", caption: "聚合样本并生成爆文模型矩阵", code: "06" },
  { id: "InsightAgent", label: "洞察提炼", caption: "沉淀痛点、SEO 与创作策略", code: "07" },
  { id: "RAGAgent", label: "知识增强", caption: "检索业务知识库并补充约束", code: "08" },
  { id: "CanvasRenderAgent", label: "画布渲染", caption: "组织 Canvas 模块与可编辑产物", code: "09" },
];

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function AgentTimeline({ state, taskId }: AgentTimelineProps) {
  if (!taskId) return null;

  const latestLogTime = formatTime(state.lastEventAt);
  const errorStepId = pickErrorStepId(state);
  const lastStartedIndex = AGENT_STEPS.reduce((latest, step, index) => {
    if (state.agentStatus[step.id]) return index;
    return latest;
  }, -1);
  const isTerminal = TERMINAL_STATUSES.has(state.status);
  const activeIndex = isTerminal ? -1 : Math.max(lastStartedIndex, 0);
  const recentLogs = state.logs.slice(-4);

  return (
    <section className="rounded-[24px] border border-black/[0.05] bg-[#F7F7F5] p-4 shadow-[0_18px_48px_rgba(26,26,26,0.055)]">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-obsidian/28">Agent Orchestration</div>
          <div className="mt-1 font-serif text-[20px] font-semibold tracking-[-0.03em] text-obsidian">执行时间线</div>
          <div className="mt-1 text-[12px] leading-5 text-obsidian/42">任务 {taskId.slice(-8)} · {statusLabel(state.status)}</div>
        </div>
        <div className="rounded-full border border-black/[0.05] bg-white px-3 py-1.5 text-[12px] font-semibold text-obsidian/55">
          {Math.round(state.progress || 0)}%
        </div>
      </header>

      <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-obsidian/[0.06]">
        <div
          className="h-full rounded-full bg-dew transition-[width] duration-500"
          style={{ width: `${Math.max(0, Math.min(100, state.progress || 0))}%` }}
        />
      </div>

      <div className="space-y-1.5">
        {AGENT_STEPS.map((step, index) => {
          const entry = state.agentStatus[step.id];
          const tone = resolveTone({ state, stepId: step.id, index, activeIndex, lastStartedIndex, errorStepId });
          return (
            <div key={step.id} className="grid grid-cols-[28px_1fr] gap-3">
              <div className="flex flex-col items-center">
                <AgentNode tone={tone} label={step.code} />
                {index < AGENT_STEPS.length - 1 ? <div className="my-1 h-full min-h-5 w-px bg-black/[0.06]" /> : null}
              </div>
              <div className={`rounded-2xl border px-3.5 py-3 transition ${panelClass(tone)}`}>
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-semibold text-obsidian">{step.label}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${pillClass(tone)}`}>{toneLabel(tone)}</span>
                    </div>
                    <div className="mt-1 text-[11px] leading-5 text-obsidian/40">{entry?.message || step.caption}</div>
                  </div>
                  <div className="hidden flex-shrink-0 text-[10px] text-obsidian/28 sm:block">{entry ? formatTime(entry.lastAt) : ""}</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {state.videoAsyncState !== "idle" ? <VideoAsyncBadge value={state.videoAsyncState} /> : null}

      {state.error ? (
        <div className="mt-4 rounded-2xl border border-[#E8CFC8] bg-white px-4 py-3 text-[12px] leading-6 text-[#9A5558]">
          <div className="font-semibold">{state.error.code}</div>
          <div>{state.error.message || "任务执行失败，请查看日志后重试。"}</div>
          {state.error.code === XHS_AUTH_NOT_BOUND_CODE ? (
            <a href="/settings#xhs-credential" className="mt-2 inline-flex rounded-full bg-obsidian px-3 py-1.5 text-[11px] font-semibold text-papyrus">
              前往数据源授权
            </a>
          ) : null}
        </div>
      ) : null}

      {recentLogs.length > 0 ? <RecentLogs logs={recentLogs} latestLogTime={latestLogTime} /> : null}
    </section>
  );
}

function resolveTone({
  state,
  stepId,
  index,
  activeIndex,
  lastStartedIndex,
  errorStepId,
}: {
  state: TaskStreamState;
  stepId: string;
  index: number;
  activeIndex: number;
  lastStartedIndex: number;
  errorStepId: string | null;
}): AgentTone {
  if (errorStepId === stepId) return "error";
  if (state.status === "cancelled" && index <= Math.max(lastStartedIndex, 0)) return "warn";
  if (state.status === "completed") return "done";
  const entry = state.agentStatus[stepId];
  if (entry?.done) return "done";
  if (entry) return "running";
  if (index < activeIndex) return "done";
  if (index === activeIndex && ["pending", "queued", "running", "unknown"].includes(state.status)) return "running";
  return "pending";
}

function pickErrorStepId(state: TaskStreamState): string | null {
  if (state.status !== "failed" && !state.error) return null;
  if (state.error?.code === XHS_AUTH_NOT_BOUND_CODE) return "XhsAuthAgent";
  const lastAgentId = state.logs
    .slice()
    .reverse()
    .find((log) => typeof log.agent_id === "string")?.agent_id;
  if (lastAgentId && AGENT_STEPS.some((step) => step.id === lastAgentId)) return lastAgentId;
  const lastEntry = AGENT_STEPS.slice().reverse().find((step) => state.agentStatus[step.id]);
  return lastEntry?.id ?? "InputParserAgent";
}

function AgentNode({ tone, label }: { tone: AgentTone; label: string }) {
  if (tone === "running") {
    return (
      <span className="relative flex h-7 w-7 items-center justify-center rounded-full bg-dew text-[10px] font-bold text-white shadow-sm">
        <span className="absolute h-7 w-7 animate-ping rounded-full bg-dew/45" />
        <span className="relative">{label}</span>
      </span>
    );
  }
  return <span className={`flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-bold ${nodeClass(tone)}`}>{label}</span>;
}

function RecentLogs({ logs, latestLogTime }: { logs: TaskLogEntry[]; latestLogTime: string }) {
  return (
    <details className="mt-4 rounded-2xl border border-black/[0.05] bg-white/65 px-4 py-3 text-[11px] text-obsidian/48">
      <summary className="cursor-pointer select-none font-semibold text-obsidian/55">最近事件 {latestLogTime ? `· ${latestLogTime}` : ""}</summary>
      <div className="mt-3 space-y-2">
        {logs.map((log) => (
          <div key={log.event_id || `${log.timestamp}-${log.message}`} className="flex gap-2">
            <span className={log.level === "error" ? "text-[#B65356]" : log.level === "warn" ? "text-[#8B6914]" : "text-obsidian/30"}>{log.level}</span>
            <span className="min-w-0 flex-1 break-words">{log.message}</span>
          </div>
        ))}
      </div>
    </details>
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
  return <div className={`mt-4 rounded-full border px-3 py-2 text-[11px] font-semibold ${item.cls}`}>{item.label}</div>;
}

function nodeClass(tone: AgentTone): string {
  if (tone === "done") return "bg-obsidian text-papyrus";
  if (tone === "warn") return "bg-[#FFF8E6] text-[#8B6914]";
  if (tone === "error") return "bg-[#9A5558] text-white";
  return "bg-obsidian/[0.06] text-obsidian/28";
}

function panelClass(tone: AgentTone): string {
  if (tone === "running") return "border-dew/55 bg-white shadow-sm";
  if (tone === "done") return "border-black/[0.04] bg-white/70";
  if (tone === "warn") return "border-[#F1E3B6] bg-[#FFF8E6]/75";
  if (tone === "error") return "border-[#E8CFC8] bg-[#FFF5F3]";
  return "border-black/[0.04] bg-white/35";
}

function pillClass(tone: AgentTone): string {
  if (tone === "running") return "bg-dew/18 text-[#6F9095]";
  if (tone === "done") return "bg-moss/60 text-[#49715A]";
  if (tone === "warn") return "bg-[#FFF8E6] text-[#8B6914]";
  if (tone === "error") return "bg-[#FFF0EE] text-[#9A5558]";
  return "bg-obsidian/[0.04] text-obsidian/30";
}

function toneLabel(tone: AgentTone): string {
  if (tone === "running") return "运行中";
  if (tone === "done") return "完成";
  if (tone === "warn") return "注意";
  if (tone === "error") return "失败";
  return "等待";
}

function statusLabel(status: TaskStreamState["status"]): string {
  const map: Record<TaskStreamState["status"], string> = {
    unknown: "等待事件",
    pending: "待执行",
    queued: "排队中",
    running: "运行中",
    paused: "已暂停",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return map[status] ?? status;
}

function formatTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}
