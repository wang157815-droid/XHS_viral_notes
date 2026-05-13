"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { apiGet } from "@/lib/api-client";
import { clearAuthSession, getAuthToken } from "@/lib/auth-storage";
import type { Role } from "@/lib/rbac";
import { useSession } from "@/lib/session-context";

/**
 * Phase 4b：登录页只负责 RedMuse 账号密码登录。
 * 小红书数据源（扫码 / SMS / 粘贴 Cookie）授权统一收口到「设置 → 数据源授权」。
 */
export default function LoginPage() {
  const router = useRouter();
  const { loginWithCredentials } = useSession();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [credLoading, setCredLoading] = useState(false);
  const [credError, setCredError] = useState<string | null>(null);

  // 已登录则直接跳转工作台
  useEffect(() => {
    let cancelled = false;
    const checkExistingSession = async () => {
      const token = getAuthToken();
      if (!token) return;
      const me = await apiGet<{ user_id: string; nickname: string; role: Role }>(
        "/auth/me",
        { withAuth: true },
      );
      if (cancelled) return;
      if (me.ok && !me.data.user_id.startsWith("fallback_")) {
        router.replace("/workspace");
        return;
      }
      clearAuthSession();
    };
    void checkExistingSession();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const handleCredentialsSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (credLoading) return;
    setCredError(null);
    if (!username.trim() || !password) {
      setCredError("请输入用户名和密码");
      return;
    }
    setCredLoading(true);
    try {
      await loginWithCredentials(username, password);
      router.replace("/workspace");
    } catch (error) {
      const message = error instanceof Error ? error.message : "登录失败";
      setCredError(message);
    } finally {
      setCredLoading(false);
    }
  };

  return (
    <div className="grid min-h-screen grid-cols-1 bg-[radial-gradient(circle_at_16%_16%,rgba(185,206,209,0.3),transparent_34%),linear-gradient(135deg,rgba(255,255,255,0.82),rgba(247,247,245,0.96))] text-obsidian lg:grid-cols-[minmax(320px,0.55fr)_minmax(680px,0.95fr)]">
      <section className="hidden items-center justify-center border-r border-black/[0.05] bg-[radial-gradient(circle_at_48%_30%,rgba(232,240,232,0.74),transparent_36%),linear-gradient(145deg,rgba(255,255,255,0.72),rgba(185,206,209,0.22))] p-8 lg:flex">
        <div className="max-w-md text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-[22px] border border-black/[0.06] bg-obsidian text-2xl font-bold text-papyrus shadow-[0_18px_48px_rgba(26,26,26,0.18)]">
            R
          </div>
          <h1 className="font-serif text-4xl font-semibold tracking-[-0.04em]">RedMuse</h1>
          <p className="mt-4 text-sm leading-7 text-obsidian/45">
            智能分析小红书爆款笔记
            <br />
            一句话生成专属爆文创作模型
          </p>
        </div>
      </section>

      <section className="flex items-center justify-center px-8 py-10">
        <div className="w-full max-w-md rounded-[30px] border border-black/[0.05] bg-white/78 p-8 shadow-[0_24px_70px_rgba(26,26,26,0.08)] backdrop-blur-xl">
          <div className="mb-3 text-[10px] font-bold uppercase tracking-[0.24em] text-obsidian/28">Account Access</div>
          <h2 className="font-serif text-[30px] font-semibold tracking-[-0.04em]">欢迎使用 RedMuse</h2>
          <p className="mt-2 text-sm leading-6 text-obsidian/45">
            使用 RedMuse 账号登录系统。
            <br />
            小红书数据源授权请在登录后到「设置 → 数据源授权」完成。
          </p>

          <form className="mt-6 space-y-4" onSubmit={handleCredentialsSubmit}>
            <div>
              <label className="mb-1 block text-xs font-semibold text-obsidian/55">用户名</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="w-full rounded-full border border-black/[0.08] bg-white/85 px-4 py-2 text-sm outline-none placeholder:text-obsidian/24 focus:border-dew"
                placeholder="例如 admin"
                disabled={credLoading}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-obsidian/55">密码</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full rounded-full border border-black/[0.08] bg-white/85 px-4 py-2 text-sm outline-none placeholder:text-obsidian/24 focus:border-dew"
                placeholder="请输入密码"
                disabled={credLoading}
              />
            </div>

            {credError ? (
              <p className="rounded-2xl border border-[#E8CFC8] bg-[#FFF5F3] px-3 py-2 text-xs text-[#9A5558]">{credError}</p>
            ) : (
              <p className="text-xs leading-5 text-obsidian/34">
                首次部署如未创建账号，请管理员通过环境变量
                <code className="mx-1 rounded-full bg-fog px-2 py-0.5 text-[11px] text-obsidian/55">
                  REDMUSE_BOOTSTRAP_ADMIN_PASSWORD
                </code>
                引导首个 admin。
              </p>
            )}

            <button
              type="submit"
              disabled={credLoading || !username.trim() || !password}
              className="w-full rounded-full border border-obsidian bg-obsidian px-4 py-2.5 text-sm font-semibold text-papyrus transition hover:bg-obsidian/86 disabled:cursor-not-allowed disabled:border-fog disabled:bg-fog disabled:text-obsidian/24"
            >
              {credLoading ? "登录中..." : "登录"}
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}
