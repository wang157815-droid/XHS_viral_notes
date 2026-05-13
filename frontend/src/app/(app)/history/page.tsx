"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { API_BASE, apiGet, apiPost } from "@/lib/api-client";
import { getAuthToken } from "@/lib/auth-storage";
import { listConversations } from "@/lib/conversation-api";
import type { ConversationIntent, ConversationSummary, TaskStatus } from "@/lib/contracts";
import { useSession } from "@/lib/session-context";

interface HistoryTaskItem {
  task_id: string;
  owner_user_id: string;
  status: TaskStatus;
  raw_input: string;
  keywords: string[];
  competitor_keywords: string[];
  progress: number;
  collected_count: number;
  duration_seconds: number;
  created_at: string;
  updated_at: string;
}

type ListResponse = {
  items: HistoryTaskItem[];
  include_all_effective: boolean;
};

type HistoryTab = "all" | "tasks" | "conversations";
type HistoryTimelineItem =
  | { type: "task"; key: string; updated_at: string; task: HistoryTaskItem }
  | { type: "conversation"; key: string; updated_at: string; conversation: ConversationSummary };

const HISTORY_TABS: Array<{ value: HistoryTab; label: string }> = [
  { value: "all", label: "全部" },
  { value: "tasks", label: "分析任务" },
  { value: "conversations", label: "对话" },
];

const STATUS_FILTERS: Array<{ value: string; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "completed", label: "已完成" },
  { value: "running", label: "分析中" },
  { value: "paused", label: "已暂停" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
];

const TIME_FILTERS: Array<{ value: string; label: string; days: number | null }> = [
  { value: "7d", label: "最近 7 天", days: 7 },
  { value: "30d", label: "最近 30 天", days: 30 },
  { value: "all", label: "全部", days: null },
];

const HISTORY_TIME_BUCKETS = ["today", "yesterday", "within_week", "older"] as const;
type HistoryTimeBucket = (typeof HISTORY_TIME_BUCKETS)[number];

const HISTORY_BUCKET_LABELS: Record<HistoryTimeBucket, string> = {
  today: "今天",
  yesterday: "昨天",
  within_week: "本周",
  older: "更早",
};

function getHistoryTimeBucket(updatedAtIso: string): HistoryTimeBucket {
  const t = Date.parse(updatedAtIso);
  if (Number.isNaN(t)) return "older";
  const itemDay = new Date(t);
  itemDay.setHours(0, 0, 0, 0);
  const startToday = new Date();
  startToday.setHours(0, 0, 0, 0);
  const diffDays = Math.round((startToday.getTime() - itemDay.getTime()) / 86_400_000);
  if (diffDays < 0) return "today";
  if (diffDays === 0) return "today";
  if (diffDays === 1) return "yesterday";
  if (diffDays >= 2 && diffDays <= 6) return "within_week";
  return "older";
}

export default function HistoryPage() {
  const { can } = useSession();
  const canReadAll = can("task.read_all");
  const canWriteTask = can("task.write_own");
  const [tasks, setTasks] = useState<HistoryTaskItem[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [includeAll, setIncludeAll] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [activeTab, setActiveTab] = useState<HistoryTab>("all");
  const [statusFilter, setStatusFilter] = useState("all");
  // 默认「全部」：避免换账号或久未登录时误以为列表被清空（仍可手动选最近 7/30 天）
  const [timeFilter, setTimeFilter] = useState("all");
  const [toast, setToast] = useState<{ type: "ok" | "err"; message: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (includeAll) params.set("include_all", "true");
    params.set("limit", "100");
    const [taskRes, conversationRes] = await Promise.all([
      apiGet<ListResponse>(`/tasks?${params.toString()}`, { withAuth: true }),
      listConversations({ includeAll, limit: 100 }),
    ]);
    if (taskRes.ok) {
      setTasks(taskRes.data.items);
    }
    if (conversationRes.ok) {
      setConversations(conversationRes.data.items);
    }
    if (!taskRes.ok || !conversationRes.ok) {
      const failed = !taskRes.ok ? taskRes.error : conversationRes.ok ? null : conversationRes.error;
      if (failed) {
        setToast({ type: "err", message: `加载失败：${failed.code} · ${failed.message}` });
      }
    }
    setLoading(false);
  }, [includeAll]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  const filtered = useMemo<HistoryTimelineItem[]>(() => {
    const kw = keyword.trim().toLowerCase();
    const timeEntry = TIME_FILTERS.find((t) => t.value === timeFilter);
    // eslint-disable-next-line react-hooks/purity
    const nowMs = Date.now();
    const cutoffMs = timeEntry?.days ? nowMs - timeEntry.days * 86_400_000 : null;
    const taskItems: HistoryTimelineItem[] = tasks.map((task) => ({
      type: "task",
      key: `task:${task.task_id}`,
      updated_at: task.updated_at || task.created_at,
      task,
    }));
    const conversationItems: HistoryTimelineItem[] = conversations.map((conversation) => ({
      type: "conversation",
      key: `conversation:${conversation.conversation_id}`,
      updated_at: conversation.updated_at || conversation.created_at,
      conversation,
    }));
    return [...taskItems, ...conversationItems]
      .filter((item) => {
        if (activeTab === "tasks" && item.type !== "task") return false;
        if (activeTab === "conversations" && item.type !== "conversation") return false;
        if (statusFilter !== "all" && item.type !== "task") return false;
        if (item.type === "task" && statusFilter !== "all" && item.task.status !== statusFilter) return false;
        if (cutoffMs) {
          const ts = Date.parse(item.updated_at);
          if (!Number.isNaN(ts) && ts < cutoffMs) return false;
        }
        if (!kw) return true;
        const hay =
          item.type === "task"
            ? [
                item.task.raw_input,
                ...item.task.keywords,
                ...item.task.competitor_keywords,
                item.task.task_id,
              ].join(" ")
            : [
                item.conversation.title,
                item.conversation.summary,
                item.conversation.last_message_preview,
                item.conversation.last_intent,
                item.conversation.active_task_id,
                item.conversation.conversation_id,
                ...(item.conversation.metadata.recent_keywords || []),
              ].join(" ");
        return hay.toLowerCase().includes(kw);
      })
      .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  }, [tasks, conversations, keyword, activeTab, statusFilter, timeFilter]);

  const counts = useMemo(
    () => ({
      all: tasks.length + conversations.length,
      tasks: tasks.length,
      conversations: conversations.length,
    }),
    [tasks.length, conversations.length],
  );

  const groupedTimeline = useMemo(() => {
    const buckets = new Map<HistoryTimeBucket, HistoryTimelineItem[]>();
    for (const b of HISTORY_TIME_BUCKETS) buckets.set(b, []);
    for (const item of filtered) {
      buckets.get(getHistoryTimeBucket(item.updated_at))!.push(item);
    }
    return HISTORY_TIME_BUCKETS.filter((b) => (buckets.get(b)?.length ?? 0) > 0).map((bucket) => ({
      bucket,
      label: HISTORY_BUCKET_LABELS[bucket],
      items: buckets.get(bucket)!,
    }));
  }, [filtered]);

  const handleCancel = useCallback(async (taskId: string) => {
    if (typeof window !== "undefined" && !window.confirm("确定取消该任务？")) return;
    const res = await apiPost(`/tasks/${encodeURIComponent(taskId)}/cancel`, {}, { withAuth: true });
    if (!res.ok) {
      setToast({ type: "err", message: `取消失败：${res.error.code} · ${res.error.message}` });
      return;
    }
    setToast({ type: "ok", message: "任务已取消" });
    await load();
  }, [load]);

  const handlePause = useCallback(async (taskId: string) => {
    const res = await apiPost(`/tasks/${encodeURIComponent(taskId)}/pause`, {}, { withAuth: true });
    if (!res.ok) {
      setToast({ type: "err", message: `暂停失败：${res.error.code} · ${res.error.message}` });
      return;
    }
    setToast({ type: "ok", message: "已暂停" });
    await load();
  }, [load]);

  const handleRetry = useCallback(
    async (taskId: string) => {
      const res = await apiPost<{ new_task_id: string; idempotent_hit?: boolean }>(
        `/tasks/${encodeURIComponent(taskId)}/retry`,
        {},
        { withAuth: true },
      );
      if (!res.ok) {
        setToast({ type: "err", message: `重试失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      const msg = res.data.idempotent_hit
        ? `已复用历史任务 ${res.data.new_task_id}`
        : `已提交新任务 ${res.data.new_task_id}`;
      setToast({ type: "ok", message: msg });
      await load();
    },
    [load],
  );

  const handleExport = useCallback(async (taskId: string, format: "excel" | "json" = "excel") => {
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
      setToast({ type: "ok", message: "已导出" });
    } catch (e) {
      setToast({ type: "err", message: `导出异常：${String(e)}` });
    }
  }, []);

  return (
    <>
      <PageHeader
        title="历史中心"
        actions={
          canReadAll ? (
            <label className="flex items-center gap-2 rounded-full border border-black/[0.05] bg-white/70 px-3 py-1.5 text-[12px] font-semibold text-obsidian/50">
              <input
                type="checkbox"
                checked={includeAll}
                onChange={(e) => setIncludeAll(e.target.checked)}
                className="h-3.5 w-3.5 accent-obsidian"
              />
              管理员视图：全部记录
            </label>
          ) : null
        }
      />

      <main className="relative flex-1 overflow-y-auto bg-[radial-gradient(circle_at_82%_6%,rgba(185,206,209,0.22),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.7),rgba(247,247,245,0.94))] px-6 py-6 lg:px-8">
        {toast ? (
          <div
            className={`pointer-events-auto fixed right-6 top-6 z-50 rounded-2xl border px-4 py-2 text-[12px] font-semibold shadow-[0_18px_48px_rgba(26,26,26,0.12)] backdrop-blur ${
              toast.type === "ok"
                ? "border-[#D7EAD9] bg-moss/90 text-[#49715A]"
                : "border-[#E8CFC8] bg-[#FFF5F3]/90 text-[#9A5558]"
            }`}
          >
            {toast.message}
          </div>
        ) : null}

        <div className="mx-auto mb-5 flex max-w-[1180px] flex-wrap items-center gap-2.5 rounded-[28px] border border-black/[0.05] bg-white/74 p-3 shadow-[0_18px_48px_rgba(26,26,26,0.055)] backdrop-blur-xl">
          <input
            placeholder="搜索任务、对话、关键词..."
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            className="h-[38px] w-[260px] rounded-full border border-black/[0.08] bg-white/85 px-[14px] text-[13px] text-obsidian outline-none placeholder:text-obsidian/24 focus:border-dew"
          />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="h-[38px] cursor-pointer rounded-full border border-black/[0.08] bg-white/85 px-3 text-[13px] text-obsidian/64 outline-none focus:border-dew"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
          <select
            value={timeFilter}
            onChange={(e) => setTimeFilter(e.target.value)}
            className="h-[38px] cursor-pointer rounded-full border border-black/[0.08] bg-white/85 px-3 text-[13px] text-obsidian/64 outline-none focus:border-dew"
          >
            {TIME_FILTERS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
          <div className="flex rounded-full border border-black/[0.06] bg-white/85 p-0.5">
            {HISTORY_TABS.map((tab) => (
              <button
                key={tab.value}
                type="button"
                onClick={() => setActiveTab(tab.value)}
                className={`rounded-md px-3 py-1.5 text-[12px] transition ${
                  activeTab === tab.value
                    ? "bg-obsidian font-semibold text-papyrus"
                    : "text-obsidian/50 hover:bg-moss hover:text-obsidian"
                }`}
              >
                {tab.label} {counts[tab.value]}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => void load()}
            className="h-[38px] rounded-full border border-black/[0.06] bg-white/85 px-4 text-[13px] font-semibold text-obsidian/50 transition hover:bg-moss hover:text-obsidian"
          >
            刷新
          </button>
        </div>

        {loading && !filtered.length ? (
          <div className="py-20 text-center text-[14px] text-obsidian/32">加载中...</div>
        ) : filtered.length === 0 ? (
          <EmptyState activeTab={activeTab} />
        ) : (
          <div className="mx-auto flex max-w-[1180px] flex-col gap-10">
            {groupedTimeline.map((group) => (
              <section key={group.bucket} aria-labelledby={`history-bucket-${group.bucket}`}>
                <div className="mb-3 flex items-end gap-3 px-1">
                  <h2 id={`history-bucket-${group.bucket}`} className="font-serif text-[15px] font-semibold tracking-tight text-obsidian/72">
                    {group.label}
                  </h2>
                  <span className="pb-0.5 text-[11px] font-medium tabular-nums text-obsidian/28">{group.items.length} 条</span>
                  <span className="mb-1 ml-1 h-px min-w-[48px] flex-1 bg-gradient-to-r from-dew/50 to-transparent" aria-hidden />
                </div>
                <div className="flex flex-col gap-3">
                  {group.items.map((item) =>
                    item.type === "task" ? (
                      <TaskCard
                        key={item.key}
                        task={item.task}
                        isAdmin={canReadAll}
                        canWriteTask={canWriteTask}
                        onCancel={handleCancel}
                        onPause={handlePause}
                        onExport={handleExport}
                        onRetry={handleRetry}
                      />
                    ) : (
                      <ConversationCard key={item.key} conversation={item.conversation} isAdmin={canReadAll} />
                    ),
                  )}
                </div>
              </section>
            ))}
          </div>
        )}
      </main>
    </>
  );
}

function TaskCard({
  task,
  isAdmin,
  canWriteTask,
  onCancel,
  onPause,
  onExport,
  onRetry,
}: {
  task: HistoryTaskItem;
  isAdmin: boolean;
  canWriteTask: boolean;
  onCancel: (taskId: string) => void;
  onPause: (taskId: string) => void;
  onExport: (taskId: string, format?: "excel" | "json") => void;
  onRetry: (taskId: string) => void;
}) {
  const statusMeta = getStatusMeta(task.status);
  const displayTitle = task.raw_input?.trim() || task.task_id;
  const allKeywords = [...(task.keywords || []), ...(task.competitor_keywords || [])];
  const createdLabel = formatTime(task.created_at);

  const isRunning = task.status === "running" || task.status === "queued" || task.status === "pending";
  const isPaused = task.status === "paused";
  const isFinished = task.status === "completed";
  const isFailed = task.status === "failed" || task.status === "cancelled";

  return (
    <div className="flex items-center gap-5 rounded-[24px] border border-black/[0.05] bg-white/78 px-6 py-5 shadow-[0_18px_48px_rgba(26,26,26,0.05)] backdrop-blur transition hover:-translate-y-0.5 hover:shadow-[0_24px_70px_rgba(26,26,26,0.075)]">
      <span
        className="h-[10px] w-[10px] flex-shrink-0 rounded-full"
        style={{
          background: statusMeta.color,
          animation: statusMeta.animated ? "pulse 1.5s infinite" : undefined,
        }}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate font-serif text-[18px] font-semibold tracking-[-0.03em] text-obsidian" title={displayTitle}>
          {displayTitle}
        </div>
        {allKeywords.length > 0 ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {allKeywords.map((kw) => (
              <span
                key={kw}
                className="rounded-full bg-dew/12 px-2.5 py-1 text-[11px] font-medium text-obsidian/50"
              >
                {kw}
              </span>
            ))}
          </div>
        ) : null}
        <div className="mt-2 flex flex-wrap gap-4 text-[12px] text-obsidian/34">
          <span>{createdLabel}</span>
          <span>{statusMeta.detail(task)}</span>
          {task.collected_count > 0 ? <span>{task.collected_count} 篇笔记</span> : null}
          {isAdmin ? <span>归属: {task.owner_user_id}</span> : null}
        </div>
      </div>

      <div className="flex items-center gap-5">
        <Stat value={isFinished ? String(task.progress || 100) : "--"} label="完成度" />
        <Stat value={task.collected_count > 0 ? formatK(task.collected_count) : "--"} label="已采集" />
      </div>

      <div className="flex items-center gap-2">
        {isFinished || isRunning || isPaused ? (
          <Link
            href={`/workspace?task=${encodeURIComponent(task.task_id)}`}
            prefetch
            className="rounded-full border border-obsidian bg-obsidian px-3.5 py-1.5 text-[12px] font-semibold text-papyrus transition hover:bg-obsidian/86"
          >
            查看结果
          </Link>
        ) : null}
        {isFinished ? (
          <button
            type="button"
            onClick={() => onExport(task.task_id, "excel")}
            className="rounded-full border border-black/[0.06] bg-white/65 px-3.5 py-1.5 text-[12px] font-semibold text-obsidian/50 transition hover:bg-moss hover:text-obsidian"
          >
            导出
          </button>
        ) : null}
        {isRunning && canWriteTask ? (
          <>
            <button
              type="button"
              onClick={() => onPause(task.task_id)}
              className="rounded-full border border-black/[0.06] bg-white/65 px-3.5 py-1.5 text-[12px] font-semibold text-obsidian/50 transition hover:bg-moss hover:text-obsidian"
            >
              暂停
            </button>
            <button
              type="button"
              onClick={() => onCancel(task.task_id)}
              className="rounded-full border border-[#E8CFC8] bg-[#FFF5F3] px-3.5 py-1.5 text-[12px] font-semibold text-[#9A5558] transition hover:bg-[#FFF0EE]"
            >
              取消
            </button>
          </>
        ) : null}
        {isPaused && canWriteTask ? (
          <button
            type="button"
            onClick={() => onCancel(task.task_id)}
            className="rounded-full border border-[#E8CFC8] bg-[#FFF5F3] px-3.5 py-1.5 text-[12px] font-semibold text-[#9A5558] transition hover:bg-[#FFF0EE]"
          >
            取消
          </button>
        ) : null}
        {isFailed && canWriteTask ? (
          <button
            type="button"
            onClick={() => onRetry(task.task_id)}
            className="rounded-full border border-black/[0.06] bg-white/65 px-3.5 py-1.5 text-[12px] font-semibold text-obsidian/50 transition hover:bg-moss hover:text-obsidian"
          >
            重试
          </button>
        ) : null}
      </div>
    </div>
  );
}

function ConversationCard({ conversation, isAdmin }: { conversation: ConversationSummary; isAdmin: boolean }) {
  const intentMeta = getIntentMeta(conversation.last_intent);
  const recentKeywords = conversation.metadata.recent_keywords || [];
  const preview = conversation.last_message_preview || conversation.summary || "暂无消息摘要";

  return (
    <div className="flex items-center gap-5 rounded-[24px] border border-black/[0.05] bg-white/78 px-6 py-5 shadow-[0_18px_48px_rgba(26,26,26,0.05)] backdrop-blur transition hover:-translate-y-0.5 hover:shadow-[0_24px_70px_rgba(26,26,26,0.075)]">
      <span className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-2xl bg-dew/16 text-[13px] font-bold text-[#6F9095]">
        对
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <div className="truncate font-serif text-[18px] font-semibold tracking-[-0.03em] text-obsidian" title={conversation.title}>
            {conversation.title || "未命名对话"}
          </div>
          <span
            className="flex-shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium"
            style={{ background: intentMeta.bg, color: intentMeta.color }}
          >
            {intentMeta.label}
          </span>
          {conversation.active_task_id ? (
            <span className="flex-shrink-0 rounded-full bg-moss px-2.5 py-1 text-[11px] font-medium text-[#49715A]">
              已关联分析任务
            </span>
          ) : null}
        </div>
        <p className="mt-1 line-clamp-2 text-[13px] leading-5 text-obsidian/58">{preview}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px] text-obsidian/34">
          <span>{formatTime(conversation.updated_at || conversation.created_at)}</span>
          <span>{conversation.message_count} 条消息</span>
          {conversation.citations_count ? <span>{conversation.citations_count} 条引用</span> : null}
          {isAdmin ? <span>归属: {conversation.owner_user_id}</span> : null}
        </div>
        {recentKeywords.length > 0 ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {recentKeywords.slice(0, 6).map((kw) => (
              <span
                key={kw}
                className="rounded-full bg-fog px-2.5 py-1 text-[11px] font-medium text-obsidian/42"
              >
                {kw}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="flex items-center gap-2">
        {conversation.active_task_id ? (
          <Link
            href={`/workspace?conversation=${encodeURIComponent(conversation.conversation_id)}`}
            prefetch
            className="rounded-full border border-obsidian bg-obsidian px-3.5 py-1.5 text-[12px] font-semibold text-papyrus transition hover:bg-obsidian/86"
          >
            继续并查看任务
          </Link>
        ) : (
          <Link
            href={`/workspace?conversation=${encodeURIComponent(conversation.conversation_id)}`}
            prefetch
            className="rounded-full border border-black/[0.06] bg-white/65 px-3.5 py-1.5 text-[12px] font-semibold text-obsidian/50 transition hover:bg-moss hover:text-obsidian"
          >
            继续对话
          </Link>
        )}
      </div>
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="text-center">
      <div className="font-serif text-[18px] font-semibold text-obsidian">{value}</div>
      <div className="text-[11px] text-obsidian/32">{label}</div>
    </div>
  );
}

function EmptyState({ activeTab }: { activeTab: HistoryTab }) {
  const label = activeTab === "tasks" ? "历史任务" : activeTab === "conversations" ? "历史对话" : "历史记录";
  return (
    <div className="mx-auto flex max-w-[1180px] flex-col items-center justify-center rounded-[28px] border border-dashed border-black/[0.08] bg-white/58 py-20 text-obsidian/34 backdrop-blur">
      <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="mb-4 text-obsidian/18">
        <path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <p className="text-[14px]">暂无匹配的{label}</p>
      <Link
        href="/workspace"
        prefetch
        className="mt-4 rounded-full border border-black/[0.06] bg-white/75 px-4 py-2 text-[12px] font-semibold text-obsidian/50 transition hover:bg-moss hover:text-obsidian"
      >
        去分析工作台发起新任务
      </Link>
    </div>
  );
}

function getStatusMeta(status: TaskStatus): {
  color: string;
  label: string;
  animated: boolean;
  detail: (task: HistoryTaskItem) => string;
} {
  switch (status) {
    case "completed":
      return {
        color: "#49715A",
        label: "已完成",
        animated: false,
        detail: (t) => (t.duration_seconds ? `耗时 ${t.duration_seconds}s` : "已完成"),
      };
    case "running":
    case "queued":
    case "pending":
      return {
        color: "#E8A84C",
        label: "进行中",
        animated: true,
        detail: (t) => `进行中 · ${t.progress || 0}%`,
      };
    case "paused":
      return { color: "#E8A84C", label: "已暂停", animated: false, detail: () => "已暂停" };
    case "failed":
      return { color: "#9A5558", label: "失败", animated: false, detail: () => "失败，查看详情" };
    case "cancelled":
      return { color: "#A8A4A0", label: "已取消", animated: false, detail: () => "已取消" };
    default:
      return { color: "#A8A4A0", label: String(status), animated: false, detail: () => String(status) };
  }
}

function getIntentMeta(intent: ConversationIntent): { label: string; bg: string; color: string } {
  switch (intent) {
    case "knowledge_qa":
      return { label: "知识库问答", bg: "#EEF5FF", color: "#2F6EB8" };
    case "xhs_analysis":
      return { label: "爆文分析", bg: "#FFF5F3", color: "#9A5558" };
    case "refine_canvas":
      return { label: "画布追问", bg: "#F6F0FF", color: "#7B4DC4" };
    case "export":
      return { label: "导出指令", bg: "#E8F0E8", color: "#49715A" };
    case "general_qa":
      return { label: "普通问答", bg: "#FAF8F5", color: "#5A5550" };
    default:
      return { label: "对话", bg: "#FAF8F5", color: "#8A8580" };
  }
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
  } catch {
    return iso;
  }
}

function formatK(n: number): string {
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)}w`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}
