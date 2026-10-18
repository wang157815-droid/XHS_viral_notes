"use client";

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { ThinkingTrace, type ThinkingStep } from "@/components/ui/thinking-prompt-input";
import { resolveAgentConfirmation } from "@/lib/conversation-api";
import type { AgentConfirmation, AgentPlanStep, AgentRunStep } from "@/lib/contracts";

const AGENT_TOOL_LABELS: Record<string, string> = {
  search_notes: "搜索笔记",
  collect_notes: "采集笔记",
  fetch_note_detail: "获取笔记详情",
  fetch_notes_details: "批量补全详情",
  fetch_comments: "采集评论",
  query_dataset: "查询工作记忆",
  list_working_memory: "盘点工作记忆",
  set_plan: "制定计划",
  update_plan: "更新计划",
  present_artifact: "生成结构化产物",
  export_report: "导出报告",
  search_knowledge: "检索知识库",
  web_search: "联网检索",
  analyze_image: "图像分析",
  analyze_video: "视频分析",
  run_viral_analysis: "爆文分析流水线",
  run_comment_analysis: "评论分析流水线",
  create_analysis_task: "创建分析任务",
  parse_task_input: "意图解析",
  crawl_task_notes: "小红书采集",
  analyze_task_images: "图文分析",
  analyze_task_videos: "视频分析",
  build_viral_model: "爆文模型",
  generate_task_insights: "洞察提炼",
  retrieve_task_knowledge: "知识增强",
  render_task_canvas: "画布渲染",
  get_task_progress: "查看任务进度",
  list_my_tasks: "查询历史任务",
  get_task_detail: "查看任务详情",
  get_task_canvas: "读取任务画布",
  pause_task: "暂停任务",
  resume_task: "恢复任务",
  cancel_task: "取消任务",
  retry_task: "重试任务",
  list_knowledge_documents: "查询知识库文档",
  get_metrics_summary: "运营指标汇总",
  get_history_timeline: "历史时间线",
  get_cookie_health: "Cookie 健康度",
  get_xhs_auth_status: "小红书授权状态",
  get_crawler_status: "爬虫状态",
  get_focus_keywords: "关注关键词",
  get_system_settings: "系统设置",
};

export const agentToolLabel = (tool: string) => AGENT_TOOL_LABELS[tool] || tool || "工具";

const PLAN_DOT: Record<AgentPlanStep["status"], string> = {
  pending: "border border-obsidian/25 bg-transparent",
  in_progress: "bg-dew animate-pulse",
  done: "bg-moss",
  skip: "bg-obsidian/20",
};

function toThinkingSteps(steps: AgentRunStep[]): ThinkingStep[] {
  return steps.map((step) => ({
    id: step.id,
    text: agentToolLabel(step.tool),
    thought: step.thought,
    detail: step.summary,
    status: step.status === "running" ? "active" : step.status === "error" ? "error" : "complete",
  }));
}

/** 运行中每秒刷新的计时；结束后取固定时长 */
function useElapsedSeconds(live: boolean, startedAt?: number, durationMs?: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live]);
  if (!live && durationMs) return durationMs / 1000;
  return startedAt ? (now - startedAt) / 1000 : 0;
}

export function AgentThinking({
  steps,
  plan,
  live,
  startedAt,
  durationMs,
  confirmation,
  conversationId,
  label,
  thinkContent,
}: {
  steps: AgentRunStep[];
  plan: AgentPlanStep[];
  live: boolean;
  startedAt?: number;
  durationMs?: number;
  confirmation?: AgentConfirmation | null;
  conversationId: string;
  /** 进行中的标题（默认"正在思考与执行"） */
  label?: string;
  /** 模型推理过程文本（reasoning），展示在步骤之后 */
  thinkContent?: string;
}) {
  const seconds = useElapsedSeconds(live, startedAt, durationMs);
  if (!steps.length && !plan.length && !live && !confirmation && !thinkContent) return null;

  return (
    <div className="mb-3">
      <ThinkingTrace
        live={live}
        steps={toThinkingSteps(steps)}
        elapsedSeconds={seconds}
        liveLabel={confirmation?.state === "pending" ? "等待你确认后继续" : label || "正在思考与执行"}
        doneLabel={(s) => {
          const head = s > 0 ? `已思考 ${s} 秒` : "已完成思考";
          return steps.length ? `${head} · ${steps.length} 步` : head;
        }}
      >
        {thinkContent ? (
          <p className="whitespace-pre-wrap border-l border-border pl-3 text-xs leading-5 text-muted-foreground">
            {thinkContent}
          </p>
        ) : null}
        {plan.length ? (
          <div className="mt-2 rounded-[12px] border border-border bg-card/70 px-3 py-2.5">
            <div className="mb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground">执行计划</div>
            <ol className="space-y-1">
              {plan.map((item) => (
                <li key={item.id} className="flex items-start gap-2 text-xs leading-5">
                  <span className={`mt-[6px] size-2 shrink-0 rounded-full ${PLAN_DOT[item.status] ?? PLAN_DOT.pending}`} />
                  <span className={item.status === "done" || item.status === "skip" ? "text-muted-foreground line-through decoration-obsidian/20" : "text-foreground/80"}>
                    {item.title}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </ThinkingTrace>
      {confirmation ? (
        <AgentConfirmationCard confirmation={confirmation} conversationId={conversationId} />
      ) : null}
    </div>
  );
}

function formatArgs(args?: Record<string, unknown>): string {
  if (!args) return "";
  return Object.entries(args)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join("、") : typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join("；");
}

const DECISION_TEXT: Record<string, string> = {
  approved: "已确认，继续执行",
  rejected: "已取消，该步骤未执行",
  timeout: "等待超时，该步骤未执行",
};

function AgentConfirmationCard({
  confirmation,
  conversationId,
}: {
  confirmation: AgentConfirmation;
  conversationId: string;
}) {
  const [submitting, setSubmitting] = useState<null | boolean>(null);
  const [error, setError] = useState("");
  const pending = confirmation.state === "pending";
  const args = formatArgs(confirmation.arguments);

  async function decide(approved: boolean) {
    setSubmitting(approved);
    setError("");
    const res = await resolveAgentConfirmation(conversationId, confirmation.id, approved);
    if (!res.ok) {
      setSubmitting(null);
      setError(res.error.message || "提交失败，请重试");
    }
  }

  return (
    <div
      data-agent-confirmation={confirmation.state}
      className="mt-3 rounded-[16px] border border-border bg-card px-3.5 py-3 shadow-[var(--redmuse-soft-shadow)]"
    >
      <div className="flex items-start gap-2.5">
        <ShieldCheck className="mt-0.5 size-4 shrink-0 text-obsidian/60" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-medium text-foreground">
            {pending ? "需要你确认：" : ""}
            {agentToolLabel(confirmation.tool)}
          </div>
          {confirmation.message ? (
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{confirmation.message}</p>
          ) : null}
          {args ? <p className="mt-1 break-words font-mono text-[11px] leading-4 text-muted-foreground">{args}</p> : null}
          {pending ? (
            <div className="mt-2.5 flex items-center gap-2">
              <button
                type="button"
                disabled={submitting !== null}
                onClick={() => void decide(true)}
                className="inline-flex h-8 items-center rounded-full bg-primary px-3.5 text-xs font-medium text-primary-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              >
                {submitting === true ? "提交中…" : "确认执行"}
              </button>
              <button
                type="button"
                disabled={submitting !== null}
                onClick={() => void decide(false)}
                className="inline-flex h-8 items-center rounded-full border border-border bg-card px-3.5 text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              >
                {submitting === false ? "提交中…" : "取消"}
              </button>
            </div>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">{DECISION_TEXT[confirmation.state] ?? ""}</p>
          )}
          {error ? <p className="mt-1.5 text-xs text-[#9A5558]">{error}</p> : null}
        </div>
      </div>
    </div>
  );
}
