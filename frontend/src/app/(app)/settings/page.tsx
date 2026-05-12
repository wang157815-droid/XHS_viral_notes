"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";


import { PageHeader } from "@/components/layout/page-header";
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "@/lib/api-client";
import { roleLabel, type Role } from "@/lib/rbac";
import { useSession } from "@/lib/session-context";
import {
  bindFromSmsLoginSession,
  bindXhsFromQrSession,
  bindXhsWithCookies,
  cancelSmsLoginSession,
  cancelXhsQrLoginSession,
  createSmsLoginSession,
  createXhsQrLoginSession,
  describeSmsLoginStatus,
  describeXhsCredentialStatus,
  describeXhsQrLoginStatus,
  fetchMyXhsCredential,
  fetchSmsLoginSession,
  fetchXhsQrLoginSession,
  probeMyXhsCredentialStatus,
  submitXhsQrLoginSms,
  unbindMyXhsCredential,
  XHS_QR_FINAL_STATUSES,
  type SmsLoginSessionDto,
  type XhsCredentialPublic,
  type XhsQrLoginSessionDto,
} from "@/lib/xhs-credential";

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
  xhs_id_masked?: string;
  username: string;
  role: Role;
  source?: string;
  last_login_at?: string;
}

interface CreateUserForm {
  username: string;
  password: string;
  nickname: string;
  role: Role;
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

type XhsCredentialAction = "probe" | "unbind" | "bind" | null;

export default function SettingsPage() {
  const { user, logout, can } = useSession();
  const canManageSystem = can("settings.system.write");
  const canManageUsers = can("settings.users.manage");
  const canReadObservability = can("settings.observability.read");
  const canRunMaintenance = can("settings.maintenance.write");
  const canManageCredential = can("xhs_credential.manage_self");

  const [system, setSystem] = useState<SystemSettings | null>(null);
  const [focusKeywords, setFocusKeywords] = useState<string[]>([]);
  const [kwInput, setKwInput] = useState("");
  const [governance, setGovernance] = useState<ModelGovernance | null>(null);
  const [crawlerStatus, setCrawlerStatus] = useState<CrawlerStatus | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [createUserForm, setCreateUserForm] = useState<CreateUserForm>({
    username: "",
    password: "",
    nickname: "",
    role: "analyst",
  });
  const [creatingUser, setCreatingUser] = useState(false);
  const [maintenance, setMaintenance] = useState<MaintenanceStats | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [cleaningTarget, setCleaningTarget] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "ok" | "err"; message: string } | null>(null);

  // Phase 2-C: XHS 数据源凭据
  const [xhsCred, setXhsCred] = useState<XhsCredentialPublic | null>(null);
  const [xhsCredAction, setXhsCredAction] = useState<XhsCredentialAction>(null);

  const loadXhsCredential = useCallback(async () => {
    const res = await fetchMyXhsCredential();
    if (res.ok) setXhsCred(res.data);
    else setToast({ type: "err", message: res.error?.message ?? "凭据加载失败" });
  }, []);

  const handleProbeXhsCredential = useCallback(async () => {
    setXhsCredAction("probe");
    try {
      const res = await probeMyXhsCredentialStatus(true);
      if (res.ok) {
        if (res.data.credential) {
          setXhsCred(res.data.credential);
        } else {
          await loadXhsCredential();
        }
        setToast({
          type: res.data.status === "active" ? "ok" : "err",
          message: `凭据检查完成：${res.data.message || res.data.status}`,
        });
      } else {
        setToast({ type: "err", message: res.error?.message ?? "凭据检查失败" });
      }
    } finally {
      setXhsCredAction(null);
    }
  }, [loadXhsCredential]);

  const handleUnbindXhsCredential = useCallback(async () => {
    if (!confirm("确定解绑当前小红书账号？解绑后任务无法采集，需要重新扫码。")) return;
    setXhsCredAction("unbind");
    try {
      const res = await unbindMyXhsCredential();
      if (res.ok) {
        setToast({ type: "ok", message: "已解绑小红书账号" });
        await loadXhsCredential();
      } else {
        setToast({ type: "err", message: res.error?.message ?? "解绑失败" });
      }
    } finally {
      setXhsCredAction(null);
    }
  }, [loadXhsCredential]);

  const handlePasteBindXhsCredential = useCallback(
    async (cookiesStr: string) => {
      if (!cookiesStr.trim()) {
        setToast({ type: "err", message: "Cookie 字符串为空" });
        return;
      }
      setXhsCredAction("bind");
      try {
        const res = await bindXhsWithCookies(cookiesStr.trim());
        if (res.ok) {
          setToast({
            type: "ok",
            message: `绑定成功：${res.data.xhs_nickname ?? res.data.xhs_user_id ?? ""}`,
          });
          await loadXhsCredential();
        } else {
          setToast({
            type: "err",
            message: res.error?.message ?? "绑定失败，请重试",
          });
        }
      } finally {
        setXhsCredAction(null);
      }
    },
    [loadXhsCredential],
  );

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
    const res = await apiGet<{ users: ManagedUser[] }>("/auth/users", { withAuth: true });
    if (res.ok) setUsers(res.data.users ?? []);
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
    void loadXhsCredential();
    if (canManageSystem) {
      void loadGovernance();
    }
    if (canManageUsers) {
      void loadUsers();
    }
    if (canRunMaintenance) {
      void loadMaintenance();
    }
    if (canReadObservability) {
      void loadMetrics();
    }
  }, [
    loadSystem,
    loadFocusKeywords,
    loadCrawlerStatus,
    loadXhsCredential,
    loadGovernance,
    loadUsers,
    loadMaintenance,
    loadMetrics,
    canManageSystem,
    canManageUsers,
    canRunMaintenance,
    canReadObservability,
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
    async (userId: string, role: Role) => {
      const res = await apiPatch<ManagedUser, { role: string }>(
        `/auth/users/${encodeURIComponent(userId)}/role`,
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

  const handleCreateUser = useCallback(async () => {
    const username = createUserForm.username.trim();
    const password = createUserForm.password.trim();
    const nickname = createUserForm.nickname.trim();
    if (!username || !password) {
      setToast({ type: "err", message: "用户名和密码不能为空" });
      return;
    }
    setCreatingUser(true);
    try {
      const res = await apiPost<ManagedUser, CreateUserForm>(
        "/auth/users",
        {
          username,
          password,
          nickname,
          role: createUserForm.role,
        },
        { withAuth: true },
      );
      if (!res.ok) {
        setToast({ type: "err", message: `创建失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setCreateUserForm({ username: "", password: "", nickname: "", role: "analyst" });
      setToast({ type: "ok", message: "用户已创建" });
      await loadUsers();
    } finally {
      setCreatingUser(false);
    }
  }, [createUserForm, loadUsers]);

  const handleDeleteUser = useCallback(
    async (userId: string) => {
      if (typeof window !== "undefined" && !window.confirm("确定删除该用户？")) return;
      const res = await apiDelete(`/auth/users/${encodeURIComponent(userId)}`, { withAuth: true });
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

        <AccountSection user={user} />

        <XhsCredentialSection
          credential={xhsCred}
          action={xhsCredAction}
          canManageCredential={canManageCredential}
          onRefresh={() => void loadXhsCredential()}
          onProbe={() => void handleProbeXhsCredential()}
          onUnbind={() => void handleUnbindXhsCredential()}
          onPasteBind={(cookies: string) => void handlePasteBindXhsCredential(cookies)}
        />

        <AIModelSection
          system={system}
          governance={governance}
          canManageSystem={canManageSystem}
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
          canEdit={canManageSystem}
          onInputChange={setKwInput}
          onAdd={handleAddKeyword}
          onRemove={handleRemoveKeyword}
        />

        {canReadObservability ? (
          <SystemObservabilitySection metrics={metrics} onRefresh={loadMetrics} />
        ) : null}

        {canManageUsers ? (
          <UserManagementSection
            users={users}
            currentUserId={user?.user_id ?? ""}
            createUserForm={createUserForm}
            creatingUser={creatingUser}
            onCreateFormChange={setCreateUserForm}
            onCreateUser={handleCreateUser}
            onUpdateRole={handleUpdateUserRole}
            onDelete={handleDeleteUser}
          />
        ) : null}

        {canRunMaintenance ? (
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
}: {
  user: { user_id: string; nickname: string; role: Role } | null;
}) {
  // 仅展示 RedMuse 系统账号本身的信息。
  // 小红书数据源（cookies / xhs_user_id / 健康检查 / 重新授权）
  // 全部归到下方的 XhsCredentialSection，避免与系统身份混淆。
  return (
    <section className="mb-8">
      <SectionTitle
        title="系统账号"
        desc="当前登录 RedMuse 的账号信息（与小红书数据源无关）"
      />
      <Card>
        <Row label="账号昵称" hint="RedMuse 系统账号显示名">
          <span className="text-[13px] text-[#5A5550]">{user?.nickname ?? "-"}</span>
        </Row>
        <Row label="用户 ID" hint="RedMuse 系统用户 ID">
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
            {roleLabel(user?.role)}
          </span>
        </Row>
      </Card>
    </section>
  );
}

function XhsCredentialSection({
  credential,
  action,
  canManageCredential,
  onRefresh,
  onProbe,
  onUnbind,
  onPasteBind,
}: {
  credential: XhsCredentialPublic | null;
  action: XhsCredentialAction;
  canManageCredential: boolean;
  onRefresh: () => void;
  onProbe: () => void;
  onUnbind: () => void;
  onPasteBind: (cookiesStr: string) => void;
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [cookieDraft, setCookieDraft] = useState("");

  const status = credential?.status ?? "unbound";
  const meta = describeXhsCredentialStatus(status);
  const isBound = !!credential?.is_bound && status !== "unbound";
  const busy = action !== null;

  const dotColor = (() => {
    switch (meta.tone) {
      case "ok":
        return "#3D8C40";
      case "warn":
        return "#E8A84C";
      case "err":
        return "#E04040";
      default:
        return "#A8A4A0";
    }
  })();
  const labelColor = meta.tone === "warn" ? "#B8860B" : dotColor;

  return (
    <section id="xhs-credential" className="mb-8 scroll-mt-6">
      <SectionTitle
        title="数据源授权"
        desc="为当前 RedMuse 账号绑定一份小红书 Cookie；任务前置 XhsAuthAgent 会以此校验授权状态。"
      />
      <Card>
        <Row label="授权状态" hint="未授权 / 已过期时所有任务会立即失败提示">
          <span className="flex items-center gap-2 text-[13px]" style={{ color: labelColor }}>
            <span className="h-2 w-2 rounded-full" style={{ background: dotColor }} />
            {meta.label}
          </span>
        </Row>
        <Row label="绑定的小红书账号" hint="来自 selfinfo 接口的最新昵称">
          <span className="text-[13px] text-[#5A5550]">
            {credential?.xhs_nickname ?? "-"}
          </span>
        </Row>
        <Row label="XHS user_id" hint="后端 store 中的 xhs_user_id">
          <span className="font-mono text-[12px] text-[#5A5550]">
            {credential?.xhs_user_id ?? "-"}
          </span>
        </Row>
        <Row label="状态描述" hint="后端 status_message">
          <span className="text-[13px] text-[#5A5550]">
            {credential?.status_message ?? "-"}
          </span>
        </Row>
        <Row label="上次校验" hint="健康检查时间（ISO）">
          <span className="text-[13px] text-[#5A5550]">
            {credential?.last_validated_at
              ? formatTime(credential.last_validated_at)
              : "暂无"}
          </span>
        </Row>
        <Row label="操作" hint="检查 / 解绑当前凭据">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onProbe}
              disabled={busy || !canManageCredential}
              className="rounded-md border border-[#E8E5E0] bg-transparent px-3.5 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0] disabled:opacity-60"
            >
              {action === "probe" ? "检查中…" : "立即检查"}
            </button>
            <button
              type="button"
              onClick={onUnbind}
              disabled={busy || !isBound || !canManageCredential}
              className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-3.5 py-1.5 text-[12px] text-[#E04040] transition hover:bg-[#FFE8E0] disabled:opacity-50"
            >
              {action === "unbind" ? "解绑中…" : "解绑"}
            </button>
          </div>
        </Row>
        <Row
          label="扫码登录绑定"
          hint="使用真机扫描小红书二维码；登录成功后绑定到当前 RedMuse 账号"
        >
          {canManageCredential ? (
            <XhsQrLoginPanel onBound={onRefresh} />
          ) : (
            <span className="text-[12px] text-[#A8A4A0]">只读成员不可重新授权</span>
          )}
        </Row>
        <Row
          label="自动 SMS 重新授权"
          hint="调 hero-sms 虚拟号 + Playwright 自动登录；需在 .env 配置 SMS_PROVIDER_API_KEY"
        >
          {canManageCredential ? (
            <SmsAutoLoginPanel onBound={onRefresh} />
          ) : (
            <span className="text-[12px] text-[#A8A4A0]">只读成员不可重新授权</span>
          )}
        </Row>
        <Row label="高级：粘贴 Cookie 绑定" hint="从浏览器 DevTools 复制完整 cookie，仅管理员排障使用">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            disabled={!canManageCredential}
            className="rounded-md border border-[#E8E5E0] bg-transparent px-3.5 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
          >
            {showAdvanced ? "收起" : "展开"}
          </button>
        </Row>
        {showAdvanced && canManageCredential ? (
          <div className="px-5 py-4 border-t border-[#F5F3F0] bg-[#FAFAF8]">
            <textarea
              value={cookieDraft}
              onChange={(e) => setCookieDraft(e.target.value)}
              placeholder="a1=...; web_session=...; webId=..."
              spellCheck={false}
              className="block w-full min-h-[96px] rounded-md border border-[#E8E5E0] bg-white px-3 py-2 font-mono text-[12px] text-[#3A3530] focus:border-[#FF4757] focus:outline-none"
            />
            <div className="mt-3 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setCookieDraft("")}
                disabled={busy || !cookieDraft}
                className="rounded-md border border-[#E8E5E0] bg-white px-3.5 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0] disabled:opacity-50"
              >
                清空
              </button>
              <button
                type="button"
                onClick={() => {
                  onPasteBind(cookieDraft);
                  setCookieDraft("");
                }}
                disabled={busy || !cookieDraft.trim()}
                className="rounded-md border border-[#FF4757] bg-[#FF4757] px-3.5 py-1.5 text-[12px] text-white transition hover:bg-[#E03B4A] disabled:opacity-50"
              >
                {action === "bind" ? "绑定中…" : "绑定到当前账号"}
              </button>
            </div>
            <p className="mt-2 text-[12px] text-[#8A8580]">
              系统会调用 selfinfo 校验 Cookie 有效性，成功后写入 XhsCredentialStore。失败原因会显示在右上角提示。
            </p>
          </div>
        ) : null}
      </Card>
    </section>
  );
}

function SmsAutoLoginPanel({ onBound }: { onBound: () => void }) {
  const [session, setSession] = useState<SmsLoginSessionDto | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bindMessage, setBindMessage] = useState<string | null>(null);

  // 轮询：非终态时每 1.5s 拉一次
  useEffect(() => {
    if (!session?.session_id) return;
    if (session.is_terminal) return;
    const timer = window.setInterval(async () => {
      const res = await fetchSmsLoginSession(session.session_id);
      if (res.ok) setSession(res.data);
      else setError(res.error?.message ?? "查询会话失败");
    }, 1500);
    return () => window.clearInterval(timer);
  }, [session?.session_id, session?.is_terminal]);

  const handleStart = useCallback(async () => {
    setBusy(true);
    setError(null);
    setBindMessage(null);
    try {
      const res = await createSmsLoginSession();
      if (res.ok) {
        setSession(res.data);
      } else {
        setError(res.error?.message ?? "启动会话失败");
      }
    } finally {
      setBusy(false);
    }
  }, []);

  const handleCancel = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    try {
      const res = await cancelSmsLoginSession(session.session_id);
      if (!res.ok) {
        setError(res.error?.message ?? "取消失败");
        return;
      }
      const refreshed = await fetchSmsLoginSession(session.session_id);
      if (refreshed.ok) setSession(refreshed.data);
    } finally {
      setBusy(false);
    }
  }, [session]);

  const handleBind = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    setError(null);
    setBindMessage(null);
    try {
      const res = await bindFromSmsLoginSession(session.session_id);
      if (!res.ok) {
        setError(res.error?.message ?? "绑定失败");
        return;
      }
      setBindMessage(
        `已绑定：${res.data.xhs_nickname ?? res.data.xhs_user_id ?? ""}`,
      );
      onBound();
      // 刷新一次以更新 cookies_ready=false
      const refreshed = await fetchSmsLoginSession(session.session_id);
      if (refreshed.ok) setSession(refreshed.data);
    } finally {
      setBusy(false);
    }
  }, [session, onBound]);

  if (!session) {
    return (
      <button
        type="button"
        onClick={() => void handleStart()}
        disabled={busy}
        className="rounded-md border border-[#FF4757] bg-[#FFF5F3] px-3.5 py-1.5 text-[12px] text-[#FF4757] transition hover:bg-[#FFE8E0] disabled:opacity-60"
      >
        {busy ? "启动中…" : "开始自动登录"}
      </button>
    );
  }

  const meta = describeSmsLoginStatus(session.status);
  const toneColor =
    meta.tone === "ok"
      ? "#3D8C40"
      : meta.tone === "err"
        ? "#E04040"
        : meta.tone === "warn"
          ? "#B8860B"
          : "#5A5550";

  return (
    <div className="flex w-full flex-col gap-2 text-[12px]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2" style={{ color: toneColor }}>
          <span
            className="h-2 w-2 rounded-full"
            style={{ background: toneColor }}
          />
          <span>{meta.label}</span>
        </div>
        <div className="flex items-center gap-2">
          {session.is_terminal ? (
            <button
              type="button"
              onClick={() => setSession(null)}
              className="rounded-md border border-[#E8E5E0] bg-white px-3 py-1 text-[#5A5550] hover:bg-[#F5F3F0]"
            >
              重新开始
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void handleCancel()}
              disabled={busy}
              className="rounded-md border border-[#E8E5E0] bg-white px-3 py-1 text-[#5A5550] hover:bg-[#F5F3F0] disabled:opacity-60"
            >
              取消
            </button>
          )}
          {session.status === "success" && session.cookies_ready ? (
            <button
              type="button"
              onClick={() => void handleBind()}
              disabled={busy}
              className="rounded-md border border-[#FF4757] bg-[#FF4757] px-3 py-1 text-white hover:bg-[#E03B4A] disabled:opacity-60"
            >
              绑定到当前账号
            </button>
          ) : null}
        </div>
      </div>

      <div className="h-1.5 w-full overflow-hidden rounded-full bg-[#F5F3F0]">
        <div
          className="h-full rounded-full transition-all"
          style={{
            width: `${meta.progress}%`,
            background:
              meta.tone === "ok"
                ? "#3D8C40"
                : meta.tone === "err"
                  ? "#E04040"
                  : "#FF4757",
          }}
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[#8A8580]">
        <span>会话：{session.session_id}</span>
        {session.phone ? (
          <span>
            手机号：{session.phone}
            {session.phone_country ? `（${session.phone_country}）` : ""}
          </span>
        ) : null}
        {session.phone_reused ? (
          <span
            className="rounded-md border border-[#D4ECD6] bg-[#F3F9F4] px-1.5 py-0.5 text-[10px] text-[#3D8C40]"
            title="本次复用上次失败的虚拟号（20 分钟内、未收过验证码），未重复扣费"
          >
            ♻ 复用
          </span>
        ) : null}
        {session.order_id ? <span>hero-sms 订单：{session.order_id}</span> : null}
      </div>

      {session.error_message ? (
        <div className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-2 py-1 text-[#C62828]">
          {session.error_code ? `[${session.error_code}] ` : ""}
          {session.error_message}
        </div>
      ) : null}
      {bindMessage ? (
        <div className="rounded-md border border-[#D4ECD6] bg-[#F3F9F4] px-2 py-1 text-[#3D8C40]">
          {bindMessage}
        </div>
      ) : null}
      {error ? (
        <div className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-2 py-1 text-[#C62828]">
          {error}
        </div>
      ) : null}
    </div>
  );
}

function XhsQrLoginPanel({ onBound }: { onBound: () => void }) {
  const [session, setSession] = useState<XhsQrLoginSessionDto | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bindMessage, setBindMessage] = useState<string | null>(null);
  const [smsCode, setSmsCode] = useState("");

  const isTerminal = session ? XHS_QR_FINAL_STATUSES.includes(session.status) : false;
  // 防重复触发自动绑定：session 进入 success 后轮询会多次返回 success，此 ref 保证只调一次 bindXhsFromQrSession。
  const autoBindTriggeredRef = useRef(false);

  // 卸载时取消活跃会话（用 ref 避免依赖 session 引发清理重建）
  const sessionRef = useRef<XhsQrLoginSessionDto | null>(null);
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);
  useEffect(() => {
    return () => {
      const cur = sessionRef.current;
      if (cur && !XHS_QR_FINAL_STATUSES.includes(cur.status)) {
        void cancelXhsQrLoginSession(cur.session_id);
      }
    };
  }, []);

  // session 被重置为 null 或重启一轮扫码时，释放自动绑定锁。
  useEffect(() => {
    if (!session) {
      autoBindTriggeredRef.current = false;
    }
  }, [session]);

  // 非终态时每 1.2s 拉一次最新状态
  useEffect(() => {
    if (!session?.session_id || isTerminal) return;
    const sid = session.session_id;
    const timer = window.setInterval(async () => {
      const res = await fetchXhsQrLoginSession(sid);
      if (res.ok) {
        setSession(res.data);
      } else {
        setError(res.error?.message ?? "查询会话失败");
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [session?.session_id, isTerminal]);

  const handleStart = useCallback(async () => {
    setBusy(true);
    setError(null);
    setBindMessage(null);
    try {
      const res = await createXhsQrLoginSession();
      if (!res.ok) {
        setError(res.error?.message ?? "启动会话失败");
        return;
      }
      setSession({
        session_id: res.data.session_id,
        status: res.data.status,
        qrcode_base64: null,
        screenshot_version: 0,
        error_message: null,
      });
    } finally {
      setBusy(false);
    }
  }, []);

  const handleCancel = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    try {
      await cancelXhsQrLoginSession(session.session_id);
      const refreshed = await fetchXhsQrLoginSession(session.session_id);
      if (refreshed.ok) setSession(refreshed.data);
    } finally {
      setBusy(false);
    }
  }, [session]);

  const handleRefresh = useCallback(async () => {
    if (session && !XHS_QR_FINAL_STATUSES.includes(session.status)) {
      void cancelXhsQrLoginSession(session.session_id);
    }
    setSession(null);
    setSmsCode("");
    setError(null);
    setBindMessage(null);
    await handleStart();
  }, [session, handleStart]);

  const handleSubmitSms = useCallback(async () => {
    if (!session || !smsCode.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await submitXhsQrLoginSms(session.session_id, smsCode.trim());
      if (!res.ok) {
        setError(res.error?.message ?? "提交验证码失败");
        return;
      }
      setSmsCode("");
    } finally {
      setBusy(false);
    }
  }, [session, smsCode, busy]);

  const handleBind = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    setError(null);
    setBindMessage(null);
    try {
      const res = await bindXhsFromQrSession(session.session_id);
      if (!res.ok) {
        setError(res.error?.message ?? "绑定失败");
        return;
      }
      setBindMessage(
        `已绑定：${res.data.xhs_nickname ?? res.data.xhs_user_id ?? "当前账号"}`,
      );
      onBound();
    } finally {
      setBusy(false);
    }
  }, [session, onBound]);

  // 会话进入 success 后自动调一次 handleBind，避免让用户手动点击「绑定到当前账号」。
  // autoBindTriggeredRef 保证轮询多次命中 success 也只发一次请求。
  useEffect(() => {
    if (!session || session.status !== "success" || autoBindTriggeredRef.current) return;
    autoBindTriggeredRef.current = true;
    void handleBind();
  }, [session, handleBind]);

  if (!session) {
    return (
      <button
        type="button"
        onClick={() => void handleStart()}
        disabled={busy}
        className="rounded-md border border-[#FF4757] bg-[#FFF5F3] px-3.5 py-1.5 text-[12px] text-[#FF4757] transition hover:bg-[#FFE8E0] disabled:opacity-60"
      >
        {busy ? "启动中…" : "开始扫码登录"}
      </button>
    );
  }

  const meta = describeXhsQrLoginStatus(session.status, !!session.qrcode_base64);
  const isBound = Boolean(bindMessage);
  const statusLabel = isBound ? "已保存到当前账号" : meta.label;
  const toneColor =
    meta.tone === "ok"
      ? "#3D8C40"
      : meta.tone === "err"
        ? "#E04040"
        : meta.tone === "warn"
          ? "#B8860B"
          : "#5A5550";
  const showSmsInput = session.status === "need_sms_code";

  return (
    <div className="flex w-full flex-col gap-2 text-[12px]">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2" style={{ color: toneColor }}>
          <span className="h-2 w-2 rounded-full" style={{ background: toneColor }} />
          <span>{statusLabel}</span>
        </div>
        <div className="flex items-center gap-2">
          {isTerminal ? (
            <button
              type="button"
              onClick={() => {
                setSession(null);
                setSmsCode("");
                setError(null);
                setBindMessage(null);
              }}
              className="rounded-md border border-[#E8E5E0] bg-white px-3 py-1 text-[#5A5550] hover:bg-[#F5F3F0]"
            >
              重置
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => void handleRefresh()}
                disabled={busy}
                className="rounded-md border border-[#E8E5E0] bg-white px-3 py-1 text-[#5A5550] hover:bg-[#F5F3F0] disabled:opacity-60"
              >
                刷新
              </button>
              <button
                type="button"
                onClick={() => void handleCancel()}
                disabled={busy}
                className="rounded-md border border-[#E8E5E0] bg-white px-3 py-1 text-[#5A5550] hover:bg-[#F5F3F0] disabled:opacity-60"
              >
                取消
              </button>
            </>
          )}
        </div>
      </div>

      {/* 绑定成功后隐藏截图：此时后端返回的是登录后的 explore 页面，不再是二维码，不该留在面板上。 */}
      {!isBound && session.qrcode_base64 ? (
        <div className="flex justify-center overflow-hidden rounded-md border border-[#F0EEEB] bg-white p-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            key={`${session.session_id}-${session.screenshot_version ?? 0}`}
            src={`data:image/jpeg;base64,${session.qrcode_base64}`}
            alt="小红书登录二维码"
            className="block max-h-[460px] w-auto max-w-full object-contain"
          />
        </div>
      ) : null}
      {!isBound && !session.qrcode_base64 ? (
        <div className="flex h-24 items-center justify-center rounded-md border border-dashed border-[#E8E5E0] bg-[#FAFAF8] text-[11px] text-[#A8A4A0]">
          {meta.tone === "err" ? "二维码生成失败" : "二维码加载中…"}
        </div>
      ) : null}

      {showSmsInput ? (
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={smsCode}
            onChange={(e) => setSmsCode(e.target.value.replace(/\D/g, ""))}
            maxLength={6}
            placeholder="输入短信验证码"
            className="h-8 flex-1 rounded-md border border-[#E8E5E0] bg-white px-2 text-[12px] outline-none focus:border-[#FF4757]"
            disabled={busy}
          />
          <button
            type="button"
            onClick={() => void handleSubmitSms()}
            disabled={busy || smsCode.trim().length < 4}
            className="rounded-md border border-[#FF4757] bg-[#FF4757] px-3 py-1 text-[12px] text-white hover:bg-[#E03B4A] disabled:opacity-60"
          >
            {busy ? "提交中…" : "提交"}
          </button>
        </div>
      ) : null}

      {!isBound ? (
        <div className="text-[11px] text-[#8A8580]">会话：{session.session_id}</div>
      ) : null}

      {session.error_message ? (
        <div className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-2 py-1 text-[#C62828]">
          {session.error_message}
        </div>
      ) : null}
      {bindMessage ? (
        <div className="rounded-md border border-[#D4ECD6] bg-[#F3F9F4] px-2 py-1 text-[#3D8C40]">
          {bindMessage}
        </div>
      ) : null}
      {error ? (
        <div className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-2 py-1 text-[#C62828]">
          {error}
        </div>
      ) : null}
    </div>
  );
}

function AIModelSection({
  system,
  governance,
  canManageSystem,
  onUpdateSystem,
}: {
  system: SystemSettings | null;
  governance: ModelGovernance | null;
  canManageSystem: boolean;
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
            disabled={!canManageSystem}
            onToggle={() =>
              onUpdateSystem({
                video_analysis_enabled: !system?.video_analysis_enabled,
              })
            }
          />
        </Row>
      </Card>

      {canManageSystem && governance ? (
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
  createUserForm,
  creatingUser,
  onCreateFormChange,
  onCreateUser,
  onUpdateRole,
  onDelete,
}: {
  users: ManagedUser[];
  currentUserId: string;
  createUserForm: CreateUserForm;
  creatingUser: boolean;
  onCreateFormChange: (form: CreateUserForm) => void;
  onCreateUser: () => void;
  onUpdateRole: (userId: string, role: Role) => void;
  onDelete: (userId: string) => void;
}) {
  return (
    <section className="mb-8">
      <SectionTitle title="用户管理" desc="管理系统中的所有用户（仅管理员可见）" />
      <Card>
        <div className="mb-5 rounded-xl border border-[#F0EEEB] bg-[#FAFAF8] p-4">
          <div className="mb-3 text-[13px] font-semibold text-[#2A2420]">添加系统用户</div>
          <div className="grid gap-3 md:grid-cols-[1fr_1fr_1fr_140px_auto]">
            <input
              value={createUserForm.username}
              onChange={(event) => onCreateFormChange({ ...createUserForm, username: event.target.value })}
              placeholder="用户名"
              className="h-9 rounded-lg border border-[#E8E5E0] bg-white px-3 text-[13px] outline-none focus:border-[#FF4757]"
            />
            <input
              value={createUserForm.password}
              onChange={(event) => onCreateFormChange({ ...createUserForm, password: event.target.value })}
              type="password"
              placeholder="初始密码"
              className="h-9 rounded-lg border border-[#E8E5E0] bg-white px-3 text-[13px] outline-none focus:border-[#FF4757]"
            />
            <input
              value={createUserForm.nickname}
              onChange={(event) => onCreateFormChange({ ...createUserForm, nickname: event.target.value })}
              placeholder="昵称（可选）"
              className="h-9 rounded-lg border border-[#E8E5E0] bg-white px-3 text-[13px] outline-none focus:border-[#FF4757]"
            />
            <select
              value={createUserForm.role}
              onChange={(event) => onCreateFormChange({ ...createUserForm, role: event.target.value as Role })}
              className="h-9 rounded-lg border border-[#E8E5E0] bg-white px-3 text-[13px] outline-none focus:border-[#FF4757]"
            >
              <option value="admin">管理员</option>
              <option value="analyst">分析师</option>
              <option value="viewer">只读成员</option>
            </select>
            <button
              type="button"
              disabled={creatingUser}
              onClick={onCreateUser}
              className="h-9 rounded-lg bg-[#FF4757] px-4 text-[13px] font-semibold text-white hover:bg-[#E8404F] disabled:cursor-not-allowed disabled:bg-[#FFB6BD]"
            >
              {creatingUser ? "创建中..." : "添加用户"}
            </button>
          </div>
        </div>
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
                        {roleLabel(u.role)}
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
                          <select
                            value={u.role}
                            onChange={(event) => onUpdateRole(u.user_id, event.target.value as Role)}
                            className="rounded-md border border-[#E8E5E0] bg-white px-2.5 py-1 text-[12px] text-[#5A5550] outline-none transition hover:bg-[#F5F3F0]"
                          >
                            <option value="admin">管理员</option>
                            <option value="analyst">分析师</option>
                            <option value="viewer">只读成员</option>
                          </select>
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
  canEdit,
  onInputChange,
  onAdd,
  onRemove,
}: {
  keywords: string[];
  input: string;
  canEdit: boolean;
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
                  disabled={!canEdit}
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
              if (e.key === "Enter" && canEdit) {
                e.preventDefault();
                onAdd();
              }
            }}
            disabled={!canEdit}
            placeholder="输入关键词，回车添加"
            className="h-9 flex-1 rounded-lg border border-[#E8E5E0] bg-white px-3 text-[13px] outline-none focus:border-[#FF4757]"
          />
          <button
            type="button"
            onClick={onAdd}
            disabled={!canEdit}
            className="rounded-md border border-[#FF4757] bg-[#FF4757] px-3.5 py-1.5 text-[12px] text-white transition hover:bg-[#E8404F]"
          >
            添加
          </button>
        </div>
      </Card>
    </section>
  );
}

function Toggle({ on, onToggle, disabled = false }: { on: boolean; onToggle: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={on}
      className="relative h-6 w-11 cursor-pointer rounded-full transition disabled:cursor-not-allowed disabled:opacity-60"
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
