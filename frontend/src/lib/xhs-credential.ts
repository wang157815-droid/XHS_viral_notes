/**
 * Phase 1+2-A 后端凭据接口的前端封装。
 *
 * 后端路由全部在 `/api/v1/xhs-auth/*`，详见
 * `backend/app/api/routes/xhs_auth.py`。
 */

import { apiDelete, apiGet, apiPost } from "@/lib/api-client";

export type XhsCredentialStatus =
  | "active"
  | "expired"
  | "expiring_soon"
  | "unknown"
  | "unbound";

export interface XhsCredentialPublic {
  redmuse_user_id: string;
  is_bound: boolean;
  status: XhsCredentialStatus;
  status_message: string;
  xhs_user_id: string | null;
  xhs_nickname: string | null;
  last_validated_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface XhsBindResultDto {
  success: boolean;
  redmuse_user_id: string;
  xhs_user_id?: string;
  xhs_nickname?: string;
  credential?: XhsCredentialPublic;
}

export interface XhsCredentialStatusDto {
  redmuse_user_id: string;
  status: XhsCredentialStatus;
  message: string;
  is_bound: boolean;
  last_validated_at: string | null;
}

export async function fetchMyXhsCredential() {
  return apiGet<XhsCredentialPublic>("/xhs-auth/credential", { withAuth: true });
}

export async function probeMyXhsCredentialStatus(force = false) {
  const path = `/xhs-auth/credential/status${force ? "?force=true" : ""}`;
  return apiGet<XhsCredentialStatusDto>(path, { withAuth: true });
}

export async function unbindMyXhsCredential() {
  return apiDelete<{ redmuse_user_id: string; unbound: boolean }>(
    "/xhs-auth/credential",
    { withAuth: true },
  );
}

export async function bindXhsWithCookies(cookiesStr: string) {
  return apiPost<XhsBindResultDto>(
    "/xhs-auth/bind/cookies",
    { cookies_str: cookiesStr },
    { withAuth: true },
  );
}

export async function bindXhsFromQrSession(sessionId: string) {
  return apiPost<XhsBindResultDto>(
    "/xhs-auth/bind/from-session",
    { session_id: sessionId },
    { withAuth: true },
  );
}

/** 仅 admin：列出所有用户的凭据状态。 */
export async function listAllXhsCredentials() {
  return apiGet<{ credentials: XhsCredentialPublic[] }>(
    "/xhs-auth/credentials",
    { withAuth: true },
  );
}

export function describeXhsCredentialStatus(status: XhsCredentialStatus): {
  label: string;
  tone: "ok" | "warn" | "err" | "muted";
} {
  switch (status) {
    case "active":
      return { label: "已授权", tone: "ok" };
    case "expiring_soon":
      return { label: "即将过期", tone: "warn" };
    case "expired":
      return { label: "已过期", tone: "err" };
    case "unbound":
      return { label: "未绑定", tone: "err" };
    default:
      return { label: "状态未知", tone: "muted" };
  }
}
