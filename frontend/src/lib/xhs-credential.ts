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

// ---------------------------------------------------------------------------
// Phase 3: 虚拟手机号 / SMS 自动登录
// ---------------------------------------------------------------------------

export type SmsLoginStatus =
  | "initializing"
  | "acquiring_phone"
  | "sending_sms"
  | "waiting_sms"
  | "submitting_sms"
  | "extracting_cookies"
  | "success"
  | "error"
  | "cancelled"
  | "expired";

export interface SmsLoginSessionDto {
  session_id: string;
  redmuse_user_id: string;
  status: SmsLoginStatus;
  phone: string | null;
  phone_country: string | null;
  order_id: string | null;
  error_code: string | null;
  error_message: string | null;
  is_terminal: boolean;
  cookies_ready: boolean;
  created_at: string;
  expires_at: string;
  updated_at: string;
}

export async function createSmsLoginSession() {
  return apiPost<SmsLoginSessionDto>("/xhs-auth/sms-login/session", {}, { withAuth: true });
}

export async function fetchSmsLoginSession(sessionId: string) {
  return apiGet<SmsLoginSessionDto>(
    `/xhs-auth/sms-login/session/${encodeURIComponent(sessionId)}`,
    { withAuth: true },
  );
}

export async function cancelSmsLoginSession(sessionId: string) {
  return apiDelete<{ session_id: string; cancelled: boolean }>(
    `/xhs-auth/sms-login/session/${encodeURIComponent(sessionId)}`,
    { withAuth: true },
  );
}

export async function bindFromSmsLoginSession(sessionId: string) {
  return apiPost<XhsBindResultDto>(
    `/xhs-auth/sms-login/session/${encodeURIComponent(sessionId)}/bind`,
    {},
    { withAuth: true },
  );
}

export function describeSmsLoginStatus(status: SmsLoginStatus): {
  label: string;
  progress: number; // 0~100，用于进度条
  tone: "info" | "ok" | "err" | "warn";
} {
  switch (status) {
    case "initializing":
      return { label: "启动浏览器…", progress: 5, tone: "info" };
    case "acquiring_phone":
      return { label: "申请虚拟手机号…", progress: 20, tone: "info" };
    case "sending_sms":
      return { label: "填入手机号并请求验证码…", progress: 40, tone: "info" };
    case "waiting_sms":
      return { label: "等待验证码到达（最长 5 分钟）…", progress: 55, tone: "info" };
    case "submitting_sms":
      return { label: "自动提交验证码…", progress: 75, tone: "info" };
    case "extracting_cookies":
      return { label: "抓取登录 Cookie…", progress: 90, tone: "info" };
    case "success":
      return { label: "登录成功，可绑定到当前账号", progress: 100, tone: "ok" };
    case "error":
      return { label: "自动登录失败", progress: 100, tone: "err" };
    case "cancelled":
      return { label: "已取消", progress: 100, tone: "warn" };
    case "expired":
      return { label: "会话超时", progress: 100, tone: "err" };
    default:
      return { label: String(status), progress: 0, tone: "info" };
  }
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
