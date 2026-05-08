"use client";

import { useCallback, useEffect, useState, type CSSProperties } from "react";


import { PageHeader } from "@/components/layout/page-header";
import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api-client";
import { useSession } from "@/lib/session-context";

interface SystemSettings {
  text_model: string;
  vision_model: string;
  embedding_model: string;
  video_analysis_enabled: boolean;
  crawler_schedule: {
    enabled?: boolean;
    interval_hours?: number;
    hot_keywords_top_n?: number;
  };
}

interface ModelGovernance {
  providers: Array<{
    name: string;
    base_url: string;
    kind: string;
    api_key_configured: boolean;
    usable: boolean;
  }>;
  profiles: Array<{
    profile_id: string;
    provider: string;
    model_name: string;
    modality: string;
    temperature: number;
    max_tokens: number;
    timeout_seconds: number;
    max_retries: number;
  }>;
  policy: Array<{
    agent_id: string;
    modality: string;
    profile_id: string;
  }>;
}

interface CrawlerStatus {
  last_run: {
    status: string;
    at: string;
    collected_count: number;
  } | null;
  total_records: number;
  keyword_count: number;
}

interface ManagedUser {
  user_id: string;
  nickname: string;
  xhs_id_masked: string;
  username: string;
  role: "admin" | "user";
  source?: string;
  last_login_at?: string;
}

interface MaintenanceStats {
  video_cache_bytes: number;
  analysis_cache_bytes: number;
  browser_data_bytes: number;
}

interface MetricsSummary {
  window_hours: number;
  tasks: {
    total: number;
    completed: number;
    failed: number;
    cancelled: number;
    failure_rate: number;
    p50_ms: number;
    p95_ms: number;
  };
  models: {
    calls: number;
    failed_calls: number;
    tokens_in: number;
    tokens_out: number;
    avg_duration_ms: number;
  };
  top_errors: Array<{ error_code: string; count: number }>;
  recent_eval_runs: Array<{
    suite: string;
    mode: string;
    status: string;
    started_at: string;
    finished_at?: string | null;
    metrics: Record<string, unknown>;
  }>;
}

export default function SettingsPage() {
  const { user, cookieHealth, refreshCookie, logout } = useSession();
  const isAdmin = user?.role === "admin";

  const [system, setSystem] = useState<SystemSettings | null>(null);
  const [focusKeywords, setFocusKeywords] = useState<string[]>([]);
  const [kwInput, setKwInput] = useState("");
  const [governance, setGovernance] = useState<ModelGovernance | null>(null);
  const [crawlerStatus, setCrawlerStatus] = useState<CrawlerStatus | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [maintenance, setMaintenance] = useState<MaintenanceStats | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [cleaningTarget, setCleaningTarget] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "ok" | "err"; message: string } | null>(null);

  const loadSystem = useCallback(async () => {
    const res = await apiGet<SystemSettings>("/settings/system", { withAuth: true });
    if (res.ok) setSystem(res.data);
  }, []);

  const loadFocusKeywords = useCallback(async () => {
    const res = await apiGet<{ items: string[] }>("/settings/focus-keywords", { withAuth: true });
    if (res.ok) setFocusKeywords(res.data.items ?? []);
  }, []);

  const loadGovernance = useCallback(async () => {
    const res = await apiGet<ModelGovernance>("/settings/model-governance", { withAuth: true });
    if (res.ok) setGovernance(res.data);
  }, []);

  const loadCrawlerStatus = useCallback(async () => {
    const res = await apiGet<CrawlerStatus>("/settings/crawler-status", { withAuth: true });
    if (res.ok) setCrawlerStatus(res.data);
  }, []);

  const loadUsers = useCallback(async () => {
    const res = await apiGet<{ items: ManagedUser[] }>("/settings/users", { withAuth: true });
    if (res.ok) setUsers(res.data.items ?? []);
  }, []);

  const loadMaintenance = useCallback(async () => {
    const res = await apiGet<MaintenanceStats>("/settings/maintenance", { withAuth: true });
    if (res.ok) setMaintenance(res.data);
  }, []);

  const loadMetrics = useCallback(async () => {
    const res = await apiGet<MetricsSummary>("/metrics/summary?window_hours=24", { withAuth: true });
    if (res.ok) setMetrics(res.data);
  }, []);

  useEffect(() => {
    void loadSystem();
    void loadFocusKeywords();
    void loadCrawlerStatus();
    if (isAdmin) {
      void loadGovernance();
      void loadUsers();
      void loadMaintenance();
      void loadMetrics();
    }
  }, [
    loadSystem,
    loadFocusKeywords,
    loadCrawlerStatus,
    loadGovernance,
    loadUsers,
    loadMaintenance,
    loadMetrics,
    isAdmin,
  ]);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  const handleSaveSystem = useCallback(
    async (patch: Partial<SystemSettings>) => {
      if (!system) return;
      const next: SystemSettings = { ...system, ...patch };
      const res = await apiPut<SystemSettings, SystemSettings>("/settings/system", next, {
        withAuth: true,
      });
      if (!res.ok) {
        setToast({ type: "err", message: `保存失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setSystem(res.data);
      setToast({ type: "ok", message: "已保存" });
    },
    [system],
  );

  const handleSaveCrawlerSchedule = useCallback(
    async (crawlerSchedule: SystemSettings["crawler_schedule"]) => {
      const res = await apiPut<SystemSettings, SystemSettings["crawler_schedule"]>(
        "/settings/crawler-schedule",
        crawlerSchedule,
        { withAuth: true },
      );
      if (!res.ok) {
        setToast({ type: "err", message: `保存失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setSystem(res.data);
      setToast({ type: "ok", message: "已保存" });
    },
    [],
  );

  const handleAddKeyword = useCallback(async () => {
    const v = kwInput.trim();
    if (!v) return;
    if (focusKeywords.includes(v)) return;
    const next = [...focusKeywords, v];
    const res = await apiPut<{ items: string[] }, { items: string[] }>("/settings/focus-keywords", {
      items: next,
    }, { withAuth: true });
    if (!res.ok) {
      setToast({ type: "err", message: `保存失败：${res.error.code} · ${res.error.message}` });
      return;
    }
    setFocusKeywords(res.data.items);
    setKwInput("");
  }, [kwInput, focusKeywords]);

  const handleRemoveKeyword = useCallback(
    async (kw: string) => {
      const next = focusKeywords.filter((k) => k !== kw);
      const res = await apiPut<{ items: string[] }, { items: string[] }>("/settings/focus-keywords", {
        items: next,
      }, { withAuth: true });
      if (!res.ok) {
        setToast({ type: "err", message: `保存失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setFocusKeywords(res.data.items);
    },
    [focusKeywords],
  );

  const handleTriggerCrawler = useCallback(async () => {
    setTriggering(true);
    try {
      const res = await apiPost<{ message?: string }>("/settings/crawler/run-now", {}, { withAuth: true });
      if (!res.ok) {
        setToast({ type: "err", message: `触发失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setToast({ type: "ok", message: res.data.message || "已触发立即采集" });
      await loadCrawlerStatus();
    } finally {
      setTriggering(false);
    }
  }, [loadCrawlerStatus]);

  const handleUpdateUserRole = useCallback(
    async (userId: string, role: "admin" | "user") => {
      const res = await apiPut<ManagedUser, { role: string }>(
        `/settings/users/${encodeURIComponent(userId)}`,
        { role },
        { withAuth: true },
      );
      if (!res.ok) {
        setToast({ type: "err", message: `更新失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setToast({ type: "ok", message: "角色已更新" });
      await loadUsers();
    },
    [loadUsers],
  );

  const handleDeleteUser = useCallback(
    async (userId: string) => {
      if (typeof window !== "undefined" && !window.confirm("确定删除该用户？")) return;
      const res = await apiDelete(`/settings/users/${encodeURIComponent(userId)}`, { withAuth: true });
      if (!res.ok) {
        setToast({ type: "err", message: `删除失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setToast({ type: "ok", message: "已删除" });
      await loadUsers();
    },
    [loadUsers],
  );

  const handleCleanCache = useCallback(
    async (target: "video_cache" | "analysis_cache" | "browser_data") => {
      setCleaningTarget(target);
      try {
        const res = await apiPost<{ freed_bytes?: number; message?: string }, { target: string }>(
          "/settings/maintenance/clean",
          { target },
          { withAuth: true },
        );
        if (!res.ok) {
          setToast({ type: "err", message: `清理失败：${res.error.code} · ${res.error.message}` });
          return;
        }
        setToast({ type: "ok", message: res.data.message || "清理任务已下发" });
        await loadMaintenance();
      } finally {
        setCleaningTarget(null);
      }
    },
    [loadMaintenance],
  );

  return (
    <>
      <PageHeader title="系统设置" />
      <main className="flex-1 overflow-y-auto px-8 py-6">
        {toast ? (
          <div
            className={`pointer-events-auto fixed right-6 top-6 z-50 rounded-md border px-3 py-2 text-[12px] shadow-lg ${
              toast.type === "ok"
                ? "border-[#D7EAD9] bg-[#F0FAF1] text-[#3D8C40]"
                : "border-[#FDD8D8] bg-[#FFF2F2] text-[#C62828]"
            }`}
          >
            {toast.message}
          </div>
        ) : null}

        <AccountSection
          user={user}
          cookieHealth={cookieHealth}
          onRecheck={() => refreshCookie(true)}
          onReauth={() => void logout()}
        />

        <AIModelSection
          system={system}
          governance={governance}
          isAdmin={!!isAdmin}
          onUpdateSystem={handleSaveSystem}
        />

        <CrawlerScheduleSection
          system={system}
          status={crawlerStatus}
          triggering={triggering}
          onUpdate={handleSaveCrawlerSchedule}
          onTrigger={handleTriggerCrawler}
        />

        <FocusKeywordsSection
          keywords={focusKeywords}
          input={kwInput}
          onInputChange={setKwInput}
          onAdd={handleAddKeyword}
          onRemove={handleRemoveKeyword}
        />

        {isAdmin ? (
          <SystemObservabilitySection metrics={metrics} onRefresh={loadMetrics} />
        ) : null}

        {isAdmin ? (
          <UserManagementSection
            users={users}
            currentUserId={user?.user_id ?? ""}
            onUpdateRole={handleUpdateUserRole}
            onDelete={handleDeleteUser}
          />
        ) : null}

        {isAdmin ? (
          <SystemMaintenanceSection
            stats={maintenance}
            cleaningTarget={cleaningTarget}
            onClean={handleCleanCache}
          />
        ) : null}

        <div className="mb-8">
          <button
            type="button"
            onClick={() => void logout()}
            className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-5 py-2.5 text-[14px] text-[#E04040] transition hover:bg-[#FFE8E0]"
          >
            退出登录
          </button>
        </div>
      </main>
    </>
  );
}

function SectionTitle({ title, desc }: { title: string; desc?: string }) {
  return (
    <div className="mb-4">
      <div className="text-[16px] font-bold">{title}</div>
      {desc ? <div className="mt-1 text-[13px] text-[#8A8580]">{desc}</div> : null}
    </div>
  );
}

function Card({ children, style }: { children: React.ReactNode; style?: CSSProperties }) {
  return (
    <div className="overflow-hidden rounded-[12px] border border-[#F0EEEB] bg-white" style={style}>
      {children}
    </div>
  );
}

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-[#F5F3F0] px-5 py-4 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="text-[14px] font-semibold">{label}</div>
        {hint ? <div className="mt-0.5 text-[12px] text-[#A8A4A0]">{hint}</div> : null}
      </div>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

function AccountSection({
  user,
  cookieHealth,
  onRecheck,
  onReauth,
}: {
  user: { user_id: string; nickname: string; role: "admin" | "user" } | null;
  cookieHealth: { status?: string; saved_days?: number; last_checked_at?: string | null; message?: string } | null;
  onRecheck: () => void;
  onReauth: () => void;
}) {
  const status = cookieHealth?.status ?? "unknown";
  const statusMeta = (() => {
    switch (status) {
      case "valid":
        return { dot: "#3D8C40", text: "有效", color: "#3D8C40" };
      case "expiring_soon":
        return { dot: "#E8A84C", text: "即将过期（建议重新登录）", color: "#B8860B" };
      case "expired":
        return { dot: "#E04040", text: "已过期（无法采集）", color: "#E04040" };
      default:
        return { dot: "#A8A4A0", text: "状态未知", color: "#5A5550" };
    }
  })();

  return (
    <section className="mb-8">
      <SectionTitle title="账号信息" desc="当前登录的小红书账号信息" />
      <Card>
        <Row label="小红书昵称" hint="绑定的小红书账号">
          <span className="text-[13px] text-[#5A5550]">{user?.nickname ?? "-"}</span>
        </Row>
        <Row label="用户 ID" hint="小红书平台 ID">
          <span className="font-mono text-[12px] text-[#5A5550]">{user?.user_id ?? "-"}</span>
        </Row>
        <Row label="系统角色" hint="决定可访问的功能范围">
          <span
            className={`rounded px-2.5 py-0.5 text-[11px] font-semibold ${
              user?.role === "admin"
                ? "bg-[#FFF0EE] text-[#FF4757]"
                : "bg-[#F5F3F0] text-[#5A5550]"
            }`}
          >
            {user?.role === "admin" ? "管理员" : "普通用户"}
          </span>
        </Row>
        <Row label="Cookie 状态" hint="小红书登录凭证有效性">
          <span className="flex items-center gap-2 text-[13px]" style={{ color: statusMeta.color }}>
            <span className="h-2 w-2 rounded-full" style={{ background: statusMeta.dot }} />
            {statusMeta.text}
          </span>
        </Row>
        <Row label="Cookie 保存时间" hint="超过 5 天建议重新登录">
          <span className="text-[13px] text-[#5A5550]">已保存 {cookieHealth?.saved_days ?? 0} 天</span>
        </Row>
        <Row label="健康检查" hint="后台定时自动验证 Cookie 是否仍然有效">
          <span className="text-[13px] text-[#5A5550]">
            上次检查：
            {cookieHealth?.last_checked_at ? formatTime(cookieHealth.last_checked_at) : "暂无"}
          </span>
          <button
            type="button"
            onClick={onRecheck}
            className="rounded-md border border-[#E8E5E0] bg-transparent px-3.5 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
          >
            立即检查
          </button>
        </Row>
        <Row label="重新登录" hint="Cookie 失效或即将过期时需要重新扫码">
          <button
            type="button"
            onClick={onReauth}
            className="rounded-md border border-[#E8E5E0] bg-transparent px-3.5 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
          >
            重新扫码登录
          </button>
        </Row>
      </Card>
    </section>
  );
}

function AIModelSection({
  system,
  governance,
  isAdmin,
  onUpdateSystem,
}: {
  system: SystemSettings | null;
  governance: ModelGovernance | null;
  isAdmin: boolean;
  onUpdateSystem: (patch: Partial<SystemSettings>) => void;
}) {
  return (
    <section className="mb-8">
      <SectionTitle title="AI 模型配置" desc="控制各环节使用的 AI 模型和 API" />
      <Card>
        <Row label="文本分析模型" hint="用于语义分析、策略生成、综合推理">
          <span className="text-[13px] text-[#5A5550]">{system?.text_model ?? "-"}</span>
        </Row>
        <Row label="多模态模型" hint="用于封面分析、图文对齐、视频理解">
          <span className="text-[13px] text-[#5A5550]">{system?.vision_model ?? "-"}</span>
        </Row>
        <Row label="向量化模型" hint="用于知识库检索和语义搜索">
          <span className="text-[13px] text-[#5A5550]">{system?.embedding_model ?? "-"}</span>
        </Row>
        <Row label="视频分析" hint="是否启用视频笔记的深度分析（耗时较长）">
          <Toggle
            on={!!system?.video_analysis_enabled}
            onToggle={() =>
              onUpdateSystem({
                video_analysis_enabled: !system?.video_analysis_enabled,
              })
            }
          />
        </Row>
      </Card>

      {isAdmin && governance ? (
        <div className="mt-6">
          <SectionTitle title="Agent 模型路由（管理员）" desc="每个 Agent 使用哪个 ModelProfile" />
          <Card>
            <div className="max-h-[300px] overflow-y-auto">
              <table className="w-full border-collapse">
                <thead className="sticky top-0 bg-white">
                  <tr>
                    <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                      Agent
                    </th>
                    <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                      模态
                    </th>
                    <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                      模型档案
                    </th>
                    <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                      Provider
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {governance.policy.map((p) => {
                    const profile = governance.profiles.find((pf) => pf.profile_id === p.profile_id);
                    return (
                      <tr key={`${p.agent_id}-${p.modality}`}>
                        <td className="border-b border-[#F5F3F0] px-4 py-3 text-[13px] font-semibold">
                          {p.agent_id}
                        </td>
                        <td className="border-b border-[#F5F3F0] px-4 py-3 text-[13px] text-[#5A5550]">
                          {p.modality}
                        </td>
                        <td className="border-b border-[#F5F3F0] px-4 py-3 text-[13px] text-[#5A5550]">
                          {p.profile_id}{" "}
                          {profile ? (
                            <span className="text-[#A8A4A0]">· {profile.model_name}</span>
                          ) : null}
                        </td>
                        <td className="border-b border-[#F5F3F0] px-4 py-3 text-[13px] text-[#5A5550]">
                          {profile?.provider ?? "-"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      ) : null}
    </section>
  );
}

function CrawlerScheduleSection({
  system,
  status,
  triggering,
  onUpdate,
  onTrigger,
}: {
  system: SystemSettings | null;
  status: CrawlerStatus | null;
  triggering: boolean;
  onUpdate: (crawlerSchedule: SystemSettings["crawler_schedule"]) => void;
  onTrigger: () => void;
}) {
  const schedule = system?.crawler_schedule ?? {};
  const enabled = schedule.enabled ?? false;
  const intervalHours = schedule.interval_hours ?? 6;
  const topN = schedule.hot_keywords_top_n ?? 50;

  const lastRun = status?.last_run;
  const lastRunMeta = (() => {
    if (!lastRun) {
      return { color: "#A8A4A0", dot: "#A8A4A0", text: "暂无执行记录" };
    }
    const tsLabel = formatTime(lastRun.at);
    if (lastRun.status === "success") {
      return {
        color: "#3D8C40",
        dot: "#3D8C40",
        text: `成功 · ${tsLabel} · 采集 ${lastRun.collected_count} 条`,
      };
    }
    if (lastRun.status === "failed") {
      return { color: "#E04040", dot: "#E04040", text: `失败 · ${tsLabel}` };
    }
    return { color: "#E8A84C", dot: "#E8A84C", text: `${lastRun.status} · ${tsLabel}` };
  })();

  return (
    <section className="mb-8">
      <SectionTitle
        title="定时爬虫预热"
        desc="后台自动抓取热榜关键词，预热数据到向量库，减少用户等待时间"
      />
      <Card>
        <Row label="启用定时爬虫" hint="开启后系统会按设定频率自动采集热搜关键词数据">
          <Toggle
            on={enabled}
            onToggle={() => onUpdate({ ...schedule, enabled: !enabled })}
          />
        </Row>
        <Row label="爬取频率" hint="每隔多长时间执行一次热榜采集">
          <select
            value={intervalHours}
            onChange={(e) =>
              onUpdate({ ...schedule, interval_hours: Number(e.target.value) })
            }
            className="h-9 cursor-pointer rounded-lg border border-[#E8E5E0] bg-[#FAFAF8] px-3 text-[13px] outline-none"
          >
            <option value={3}>每 3 小时</option>
            <option value={6}>每 6 小时</option>
            <option value={12}>每 12 小时</option>
            <option value={24}>每 24 小时</option>
          </select>
        </Row>
        <Row label="热榜采集数量" hint="每次抓取热搜 Top N 关键词">
          <select
            value={topN}
            onChange={(e) =>
              onUpdate({ ...schedule, hot_keywords_top_n: Number(e.target.value) })
            }
            className="h-9 cursor-pointer rounded-lg border border-[#E8E5E0] bg-[#FAFAF8] px-3 text-[13px] outline-none"
          >
            <option value={20}>Top 20</option>
            <option value={50}>Top 50</option>
            <option value={100}>Top 100</option>
          </select>
        </Row>
        <Row label="上次执行" hint="最近一次定时爬虫的执行情况">
          <span className="flex items-center gap-2 text-[13px]" style={{ color: lastRunMeta.color }}>
            <span className="h-2 w-2 rounded-full" style={{ background: lastRunMeta.dot }} />
            {lastRunMeta.text}
          </span>
        </Row>
        <Row label="预热数据量" hint="ChromaDB 中由定时爬虫写入的数据总量">
          <span className="text-[13px] text-[#5A5550]">
            {status ? `${status.total_records.toLocaleString()} 条 · 覆盖 ${status.keyword_count} 个关键词` : "-"}
          </span>
        </Row>
        <Row label="立即执行" hint="手动触发一次热榜采集（不影响定时计划）">
          <button
            type="button"
            onClick={onTrigger}
            disabled={triggering}
            className="rounded-md border border-[#E8E5E0] bg-transparent px-3.5 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {triggering ? "下发中..." : "立即采集"}
          </button>
        </Row>
      </Card>
    </section>
  );
}

function UserManagementSection({
  users,
  currentUserId,
  onUpdateRole,
  onDelete,
}: {
  users: ManagedUser[];
  currentUserId: string;
  onUpdateRole: (userId: string, role: "admin" | "user") => void;
  onDelete: (userId: string) => void;
}) {
  return (
    <section className="mb-8">
      <SectionTitle title="用户管理" desc="管理系统中的所有用户（仅管理员可见）" />
      <Card>
        {!users.length ? (
          <div className="py-10 text-center text-[13px] text-[#A8A4A0]">暂无其他用户记录</div>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                  用户
                </th>
                <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                  小红书 ID
                </th>
                <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                  角色
                </th>
                <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                  最后登录
                </th>
                <th className="border-b border-[#F0EEEB] px-4 py-3 text-left text-[12px] font-semibold text-[#8A8580]">
                  操作
                </th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const isCurrent = u.user_id === currentUserId;
                return (
                  <tr key={u.user_id}>
                    <td className="border-b border-[#F5F3F0] px-4 py-[14px] text-[13px] font-semibold">
                      {u.nickname}
                      {isCurrent ? (
                        <span className="ml-2 rounded bg-[#F5F3F0] px-1.5 py-0.5 text-[10px] text-[#8A8580]">
                          当前
                        </span>
                      ) : null}
                    </td>
                    <td className="border-b border-[#F5F3F0] px-4 py-[14px] font-mono text-[12px] text-[#5A5550]">
                      {u.xhs_id_masked}
                    </td>
                    <td className="border-b border-[#F5F3F0] px-4 py-[14px]">
                      <span
                        className={`rounded px-2.5 py-0.5 text-[11px] font-semibold ${
                          u.role === "admin"
                            ? "bg-[#FFF0EE] text-[#FF4757]"
                            : "bg-[#F5F3F0] text-[#5A5550]"
                        }`}
                      >
                        {u.role === "admin" ? "管理员" : "普通用户"}
                      </span>
                    </td>
                    <td className="border-b border-[#F5F3F0] px-4 py-[14px] text-[12px] text-[#5A5550]">
                      {u.last_login_at ? formatTime(u.last_login_at) : "-"}
                    </td>
                    <td className="border-b border-[#F5F3F0] px-4 py-[14px]">
                      {isCurrent ? (
                        <span className="text-[12px] text-[#A8A4A0]">--</span>
                      ) : (
                        <div className="flex gap-1.5">
                          <button
                            type="button"
                            onClick={() => onUpdateRole(u.user_id, u.role === "admin" ? "user" : "admin")}
                            className="rounded-md border border-[#E8E5E0] bg-transparent px-3 py-1 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
                          >
                            {u.role === "admin" ? "降为普通用户" : "设为管理员"}
                          </button>
                          <button
                            type="button"
                            onClick={() => onDelete(u.user_id)}
                            className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-3 py-1 text-[12px] text-[#E04040] transition hover:bg-[#FFE8E0]"
                          >
                            删除
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </section>
  );
}

function SystemObservabilitySection({
  metrics,
  onRefresh,
}: {
  metrics: MetricsSummary | null;
  onRefresh: () => void;
}) {
  const statItems = [
    { label: "24h 任务数", value: String(metrics?.tasks.total ?? 0), hint: "创建任务" },
    { label: "失败率", value: formatPercent(metrics?.tasks.failure_rate ?? 0), hint: "failed / total" },
    { label: "P95", value: `${metrics?.tasks.p95_ms ?? 0} ms`, hint: "任务运行耗时" },
    { label: "模型调用", value: String(metrics?.models.calls ?? 0), hint: "LLM / embedding" },
  ];

  return (
    <section className="mb-8">
      <SectionTitle title="系统观测" desc="最近 24 小时的任务、模型调用和 Eval 摘要（仅管理员可见）" />
      <Card>
        <div className="grid grid-cols-2 gap-0 border-b border-[#F5F3F0] md:grid-cols-4">
          {statItems.map((item) => (
            <div key={item.label} className="border-r border-[#F5F3F0] px-5 py-4 last:border-r-0">
              <div className="text-[12px] text-[#8A8580]">{item.label}</div>
              <div className="mt-1 text-[22px] font-bold text-[#2B2723]">{item.value}</div>
              <div className="mt-1 text-[11px] text-[#A8A4A0]">{item.hint}</div>
            </div>
          ))}
        </div>
        <Row label="模型 Token" hint="输入 / 输出 token 估算">
          <span className="text-[13px] text-[#5A5550]">
            {metrics?.models.tokens_in ?? 0} / {metrics?.models.tokens_out ?? 0}
          </span>
        </Row>
        <Row label="模型平均耗时" hint="ModelGateway 记录的平均调用耗时">
          <span className="text-[13px] text-[#5A5550]">{metrics?.models.avg_duration_ms ?? 0} ms</span>
        </Row>
        <Row label="Top 错误码" hint="按最近窗口内出现次数排序">
          <span className="max-w-[420px] truncate text-[13px] text-[#5A5550]">
            {metrics?.top_errors.length
              ? metrics.top_errors.map((e) => `${e.error_code}(${e.count})`).join(" · ")
              : "暂无错误"}
          </span>
        </Row>
        <Row label="最近 Eval" hint="最近一次 Eval 运行状态">
          <span className="text-[13px] text-[#5A5550]">
            {metrics?.recent_eval_runs[0]
              ? `${metrics.recent_eval_runs[0].suite} · ${metrics.recent_eval_runs[0].status}`
              : "暂无 Eval 记录"}
          </span>
          <button
            type="button"
            onClick={() => void onRefresh()}
            className="rounded-md border border-[#E8E5E0] bg-transparent px-3.5 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
          >
            刷新
          </button>
        </Row>
      </Card>
    </section>
  );
}

function SystemMaintenanceSection({
  stats,
  cleaningTarget,
  onClean,
}: {
  stats: MaintenanceStats | null;
  cleaningTarget: string | null;
  onClean: (target: "video_cache" | "analysis_cache" | "browser_data") => void;
}) {
  const items: Array<{
    target: "video_cache" | "analysis_cache" | "browser_data";
    label: string;
    hint: string;
    bytes?: number;
  }> = [
    {
      target: "video_cache",
      label: "视频缓存",
      hint: "已下载的视频临时文件",
      bytes: stats?.video_cache_bytes,
    },
    {
      target: "analysis_cache",
      label: "分析缓存",
      hint: "历史分析结果临时文件",
      bytes: stats?.analysis_cache_bytes,
    },
    {
      target: "browser_data",
      label: "浏览器数据",
      hint: "Playwright 浏览器持久化目录",
      bytes: stats?.browser_data_bytes,
    },
  ];

  return (
    <section className="mb-8">
      <SectionTitle title="系统维护" desc="清理视频、分析和浏览器缓存临时文件" />
      <Card>
        {items.map((item) => (
          <Row key={item.target} label={item.label} hint={item.hint}>
            <span className="text-[13px] text-[#5A5550]">
              {item.bytes != null ? formatBytes(item.bytes) : "-"}
            </span>
            <button
              type="button"
              onClick={() => onClean(item.target)}
              disabled={cleaningTarget === item.target}
              className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-3.5 py-1.5 text-[12px] text-[#E04040] transition hover:bg-[#FFE8E0] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {cleaningTarget === item.target ? "清理中..." : "清理"}
            </button>
          </Row>
        ))}
      </Card>
    </section>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(0)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function FocusKeywordsSection({
  keywords,
  input,
  onInputChange,
  onAdd,
  onRemove,
}: {
  keywords: string[];
  input: string;
  onInputChange: (v: string) => void;
  onAdd: () => void;
  onRemove: (kw: string) => void;
}) {
  return (
    <section className="mb-8">
      <SectionTitle
        title="重点关注词"
        desc="手动配置需要定期预热的关键词，系统会每 24 小时自动采集这些词的最新数据"
      />
      <Card>
        <div className="flex flex-wrap gap-2 border-b border-[#F5F3F0] px-5 py-4">
          {keywords.length ? (
            keywords.map((kw) => (
              <span
                key={kw}
                className="flex items-center gap-1.5 rounded-md border border-[#FFD6CC] bg-[#FFF0EE] px-3 py-[5px] text-[13px] text-[#FF4757]"
              >
                {kw}
                <button
                  type="button"
                  onClick={() => onRemove(kw)}
                  className="text-[#A8A4A0] hover:text-[#E04040]"
                  aria-label={`删除 ${kw}`}
                >
                  ×
                </button>
              </span>
            ))
          ) : (
            <span className="text-[12px] text-[#A8A4A0]">暂无重点关注词</span>
          )}
        </div>
        <div className="flex gap-2 px-5 py-4">
          <input
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onAdd();
              }
            }}
            placeholder="输入关键词，回车添加"
            className="h-9 flex-1 rounded-lg border border-[#E8E5E0] bg-white px-3 text-[13px] outline-none focus:border-[#FF4757]"
          />
          <button
            type="button"
            onClick={onAdd}
            className="rounded-md border border-[#FF4757] bg-[#FF4757] px-3.5 py-1.5 text-[12px] text-white transition hover:bg-[#E8404F]"
          >
            添加
          </button>
        </div>
      </Card>
    </section>
  );
}

function Toggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={on}
      className="relative h-6 w-11 cursor-pointer rounded-full transition"
      style={{ background: on ? "#FF4757" : "#E8E5E0" }}
    >
      <span
        className="absolute top-[2px] h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.1)] transition-all"
        style={{ left: on ? "22px" : "2px" }}
      />
    </button>
  );
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}-${dd} ${hh}:${mi}`;
  } catch {
    return iso;
  }
}
