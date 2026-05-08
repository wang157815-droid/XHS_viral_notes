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

export interface SessionState {
  ready: boolean;
  user: AuthUser | null;
  cookieHealth: CookieHealth | null;
  refreshCookie: (force?: boolean) => Promise<void>;
  refreshMe: () => Promise<void>;
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

function clearFallbackSession() {
  clearAuthSession();
  window.localStorage.removeItem("redmuse_last_xhs_user_id");
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [cookieHealth, setCookieHealth] = useState<CookieHealth | null>(null);
  const [ready, setReady] = useState(false);
  const timerRef = useRef<number | null>(null);

  const refreshCookie = useCallback(async (force: boolean = false) => {
    const path = force
      ? "/settings/cookie-health?force=true"
      : "/settings/cookie-health";
    const res = await apiGet<CookieHealth>(path, { withAuth: true });
    if (res.ok) setCookieHealth(res.data);
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
          role: res.data.role,
        };
        if (prev && prev.user_id === next.user_id) return { ...prev, ...next };
        return next;
      });
      saveAuthSession(token, {
        user_id: res.data.user_id,
        nickname: res.data.nickname,
        role: res.data.role,
      });
      if (!isFallbackUser(res.data.user_id)) {
        window.localStorage.setItem("redmuse_last_xhs_user_id", res.data.user_id);
      }
    }
  }, []);

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
    saveAuthSession(token, u);
    if (!isFallbackUser(u.user_id)) {
      window.localStorage.setItem("redmuse_last_xhs_user_id", u.user_id);
    } else {
      window.localStorage.removeItem("redmuse_last_xhs_user_id");
    }
    setUser(u);
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
        role: payloadUser.role,
      };
      // Phase 0: RedMuse 用户的 user_id 与 XHS user_id 不同名，
      // 不要写入 redmuse_last_xhs_user_id（那是给扫码续登的小红书身份）。
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
        clearFallbackSession();
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
        if (meRes.ok) {
          window.localStorage.removeItem("redmuse_last_xhs_user_id");
        }
        setUser(null);
        setReady(true);
        return;
      }

      const u: AuthUser = {
        user_id: meRes.data.user_id,
        nickname: meRes.data.nickname,
        role: meRes.data.role,
      };
      saveAuthSession(token, u);
      if (!isFallbackUser(u.user_id)) {
        window.localStorage.setItem("redmuse_last_xhs_user_id", u.user_id);
      }
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
      <div className="flex min-h-screen items-center justify-center bg-[#FAFAF8] text-[#8A8580]">
        <span className="text-sm">正在验证登录状态...</span>
      </div>
    );
  }
  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#FAFAF8] text-[#8A8580]">
        <span className="text-sm">即将跳转登录...</span>
      </div>
    );
  }
  return <>{children}</>;
}
