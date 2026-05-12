import { normalizeRole, type Role } from "@/lib/rbac";

const TOKEN_KEY = "redmuse_access_token";
const USER_KEY = "redmuse_user_profile";

export type AuthUser = {
  user_id: string;
  nickname: string;
  role: Role;
};

export function saveAuthSession(token: string, user: AuthUser): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify({ ...user, role: normalizeRole(user.role) }));
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
    const parsed = JSON.parse(raw) as AuthUser;
    return { ...parsed, role: normalizeRole(parsed.role) };
  } catch {
    return null;
  }
}

export function clearAuthSession(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

