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
    <div className="grid min-h-screen grid-cols-1 bg-[#fbf8f5] text-[#2d2a26] lg:grid-cols-[minmax(320px,0.55fr)_minmax(680px,0.95fr)]">
      <section className="hidden items-center justify-center bg-gradient-to-br from-[#fff5f0] via-[#ffe8e0] to-[#ffd6cc] p-8 lg:flex">
        <div className="max-w-md text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-[#ff4757] text-2xl font-bold text-white">
            R
          </div>
          <h1 className="text-3xl font-semibold">RedMuse</h1>
          <p className="mt-4 text-sm leading-7 text-[#8a8580]">
            智能分析小红书爆款笔记
            <br />
            一句话生成专属爆文创作模型
          </p>
        </div>
      </section>

      <section className="flex items-center justify-center bg-white px-8 py-10">
        <div className="w-full max-w-md">
          <h2 className="text-2xl font-semibold">欢迎使用 RedMuse</h2>
          <p className="mt-2 text-sm leading-6 text-[#8a8580]">
            使用 RedMuse 账号登录系统。
            <br />
            小红书数据源授权请在登录后到「设置 → 数据源授权」完成。
          </p>

          <form className="mt-6 space-y-4" onSubmit={handleCredentialsSubmit}>
            <div>
              <label className="mb-1 block text-xs font-medium text-[#5a5550]">用户名</label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                className="w-full rounded-lg border border-[#e8e5e0] px-3 py-2 text-sm outline-none focus:border-[#ff4757]"
                placeholder="例如 admin"
                disabled={credLoading}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-[#5a5550]">密码</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                className="w-full rounded-lg border border-[#e8e5e0] px-3 py-2 text-sm outline-none focus:border-[#ff4757]"
                placeholder="请输入密码"
                disabled={credLoading}
              />
            </div>

            {credError ? (
              <p className="text-xs text-[#e04040]">{credError}</p>
            ) : (
              <p className="text-xs text-[#a8a4a0]">
                首次部署如未创建账号，请管理员通过环境变量
                <code className="mx-1 rounded bg-[#f5f3f0] px-1 py-0.5 text-[11px]">
                  REDMUSE_BOOTSTRAP_ADMIN_PASSWORD
                </code>
                引导首个 admin。
              </p>
            )}

            <button
              type="submit"
              disabled={credLoading || !username.trim() || !password}
              className="w-full rounded-lg bg-[#ff4757] px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-[#ffb6bd]"
            >
              {credLoading ? "登录中..." : "登录"}
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}
