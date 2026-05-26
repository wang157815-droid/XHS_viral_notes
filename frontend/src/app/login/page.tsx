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
    <div className="grid h-screen grid-cols-1 overflow-hidden bg-[radial-gradient(ellipse_at_75%_50%,rgba(185,215,205,0.45),transparent_55%),linear-gradient(to_right,rgba(255,255,255,1)_0%,rgba(248,251,249,0.97)_100%)] text-obsidian lg:grid-cols-[3fr_2fr]">
      <section className="hidden items-center justify-center p-12 lg:flex">
        <picture>
          <source srcSet="/login-banner.webp" type="image/webp" />
          <img
            src="/login-banner.png"
            alt="RedMuse"
            className="max-h-[70vh] w-auto max-w-full object-contain"
          />
        </picture>
      </section>

      <section className="flex items-center justify-start pl-8 pr-12 py-10">
        <div className="w-full max-w-[420px] rounded-[30px] border border-black/[0.05] bg-white/78 p-8 shadow-[0_24px_70px_rgba(26,26,26,0.08)] backdrop-blur-xl">
          <h2 className="font-serif text-[30px] font-semibold tracking-[-0.04em]">欢迎使用 RedMuse</h2>

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
            ) : null}

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
