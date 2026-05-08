const TOKEN_KEY = "redmuse_access_token";
const USER_KEY = "redmuse_user_profile";
/** 上一次扫码登录成功的小红书 user_id，用于国内/国际不同号合并历史任务（请求头带给后端做 link） */
const LAST_XHS_USER_ID_KEY = "redmuse_last_xhs_user_id";

export type AuthUser = {
  user_id: string;
  nickname: string;
  role: "admin" | "user";
};

export function saveAuthSession(token: string, user: AuthUser): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  if (!user.user_id.startsWith("fallback_")) {
    window.localStorage.setItem(LAST_XHS_USER_ID_KEY, user.user_id);
  } else {
    window.localStorage.removeItem(LAST_XHS_USER_ID_KEY);
  }
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getAuthUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function clearAuthSession(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  // 保留 LAST_XHS_USER_ID_KEY，下次扫码可把新旧账号链到一起
}

