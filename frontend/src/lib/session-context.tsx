"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { apiGet, apiPost } from "@/lib/api-client";
import {
  clearAuthSession,
  getAuthToken,
  getAuthUser,
  saveAuthSession,
  type AuthUser,
} from "@/lib/auth-storage";
import type { CookieHealth } from "@/lib/contracts";
import { canRole, normalizeRole, type PermissionAction } from "@/lib/rbac";

export interface SessionState {
  ready: boolean;
  user: AuthUser | null;
  cookieHealth: CookieHealth | null;
  refreshCookie: (force?: boolean) => Promise<boolean>;
  refreshMe: () => Promise<void>;
  can: (action: PermissionAction) => boolean;
  logout: () => Promise<void>;
  /** 登录成功后立即把 token/user 注入 Provider,避免跨页跳转 Provider state 不刷新。 */
  loginWithSession: (token: string, user: AuthUser) => void;
  /** Phase 0: 用户名 + 密码登录。失败抛 Error，由调用方捕获展示。 */
  loginWithCredentials: (username: string, password: string) => Promise<AuthUser>;
}

const SessionCtx = createContext<SessionState | null>(null);

const COOKIE_REFRESH_INTERVAL_MS = 30_000;

function isFallbackUser(userId?: string | null): boolean {
  return Boolean(userId?.startsWith("fallback_"));
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [cookieHealth, setCookieHealth] = useState<CookieHealth | null>(null);
  const [ready, setReady] = useState(false);
  const timerRef = useRef<number | null>(null);

  const refreshCookie = useCallback(async (force: boolean = false): Promise<boolean> => {
    const path = force ? "/settings/cookie-health?force=true" : "/settings/cookie-health";
    try {
      const res = await apiGet<CookieHealth>(path, { withAuth: true });
      if (res.ok && res.data && typeof (res.data as CookieHealth).status === "string") {
        setCookieHealth(res.data);
        return true;
      }
      const msg = res.ok === false ? res.error.message : "Cookie 状态返回异常";
      setCookieHealth((prev) => ({
        status: "unknown",
        saved_days: prev?.saved_days ?? 0,
        last_checked_at: new Date().toISOString(),
        message: msg,
      }));
      return false;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setCookieHealth((prev) => ({
        status: "unknown",
        saved_days: prev?.saved_days ?? 0,
        last_checked_at: new Date().toISOString(),
        message: `校验请求失败：${msg}`,
      }));
      return false;
    }
  }, []);

  const refreshMe = useCallback(async () => {
    const token = getAuthToken();
    if (!token) return;
    const res = await apiGet<AuthUser>("/auth/me", { withAuth: true });
    if (res.ok) {
      setUser((prev) => {
        const next: AuthUser = {
          user_id: res.data.user_id,
          nickname: res.data.nickname,
          role: normalizeRole(res.data.role),
        };
        if (prev && prev.user_id === next.user_id) return { ...prev, ...next };
        return next;
      });
      saveAuthSession(token, {
        user_id: res.data.user_id,
        nickname: res.data.nickname,
        role: normalizeRole(res.data.role),
      });
    }
  }, []);

  const can = useCallback((action: PermissionAction) => canRole(user?.role, action), [user?.role]);

  const logout = useCallback(async () => {
    try {
      await apiPost<{ logout: boolean }>("/auth/logout", {}, { withAuth: true });
    } catch {}
    clearAuthSession();
    setUser(null);
    setCookieHealth(null);
    setReady(true);
    router.replace("/login");
  }, [router]);

  const loginWithSession = useCallback((token: string, u: AuthUser) => {
    const normalized = { ...u, role: normalizeRole(u.role) };
    saveAuthSession(token, normalized);
    setUser(normalized);
    setReady(true);
    // 后台静默刷新 cookie 健康,失败不影响登录体验
    void (async () => {
      const res = await apiGet<CookieHealth>("/settings/cookie-health", { withAuth: true });
      if (res.ok) setCookieHealth(res.data);
    })();
  }, []);

  const loginWithCredentials = useCallback(
    async (username: string, password: string): Promise<AuthUser> => {
      const res = await apiPost<{ token: string; user: AuthUser }, { username: string; password: string }>(
        "/auth/login",
        { username: username.trim(), password },
      );
      if (!res.ok) {
        throw new Error(res.error.message || "登录失败");
      }
      const { token, user: payloadUser } = res.data;
      const u: AuthUser = {
        user_id: payloadUser.user_id,
        nickname: payloadUser.nickname || payloadUser.user_id,
        role: normalizeRole(payloadUser.role),
      };
      saveAuthSession(token, u);
      setUser(u);
      setReady(true);
      // 后台静默刷新 cookie 健康；当前 RedMuse 用户可能尚未绑定 XHS Cookie。
      void (async () => {
        const cookieRes = await apiGet<CookieHealth>("/settings/cookie-health", { withAuth: true });
        if (cookieRes.ok) setCookieHealth(cookieRes.data);
      })();
      return u;
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;

    const bootstrap = async () => {
      const token = getAuthToken();
      if (!token) {
        if (!cancelled) {
          setReady(true);
        }
        return;
      }

      // 有 token 且有 cached user 时先放行渲染,避免 /auth/me 响应慢导致
      // AuthGate 卡在"正在验证登录状态..."(尤其是扫码刚完成时后端事件循环繁忙)。
      // 后台继续校验 /auth/me,失败时再 clearSession + 跳 /login。
      const cached = getAuthUser();
      if (cached && isFallbackUser(cached.user_id)) {
        clearAuthSession();
        if (!cancelled) {
          setUser(null);
          setReady(true);
        }
        return;
      }
      if (cached && !cancelled) {
        setUser(cached);
        setReady(true);
      }

      const meRes = await apiGet<AuthUser>("/auth/me", { withAuth: true });
      if (cancelled) return;

      if (!meRes.ok || isFallbackUser(meRes.ok ? meRes.data.user_id : null)) {
        clearAuthSession();
        setUser(null);
        setReady(true);
        return;
      }

      const u: AuthUser = {
        user_id: meRes.data.user_id,
        nickname: meRes.data.nickname,
        role: normalizeRole(meRes.data.role),
      };
      saveAuthSession(token, u);
      setUser(u);

      const cookieRes = await apiGet<CookieHealth>("/settings/cookie-health", {
        withAuth: true,
      });
      if (!cancelled && cookieRes.ok) setCookieHealth(cookieRes.data);

      if (!cancelled) setReady(true);
    };

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  // 定时刷新 cookie 健康状态（登录后才启动）
  useEffect(() => {
    if (!user) return;
    timerRef.current = window.setInterval(() => {
      void refreshCookie(false);
    }, COOKIE_REFRESH_INTERVAL_MS);
    return () => {
      if (timerRef.current) {
        window.clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [user, refreshCookie]);

  return (
    <SessionCtx.Provider
      value={{
        ready,
        user,
        cookieHealth,
        refreshCookie,
        refreshMe,
        can,
        logout,
        loginWithSession,
        loginWithCredentials,
      }}
    >
      {children}
    </SessionCtx.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionCtx);
  if (!ctx) {
    throw new Error("useSession 必须在 <SessionProvider> 内使用");
  }
  return ctx;
}

/**
 * 需要登录的页面用这个 Gate 包裹。
 * - 未 ready 时显示全屏极简加载态
 * - 无用户时 replace 到 /login
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { ready, user } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (ready && !user) {
      router.replace("/login");
    }
  }, [ready, user, router]);

  if (!ready) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_50%_20%,rgba(185,206,209,0.24),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.78),rgba(247,247,245,0.96))] text-obsidian/42">
        <span className="text-sm">正在验证登录状态...</span>
      </div>
    );
  }
  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_50%_20%,rgba(185,206,209,0.24),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.78),rgba(247,247,245,0.96))] text-obsidian/42">
        <span className="text-sm">即将跳转登录...</span>
      </div>
    );
  }
  return <>{children}</>;
}
