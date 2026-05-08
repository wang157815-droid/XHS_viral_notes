/* eslint-disable @next/next/no-img-element */
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { apiGet, apiPost } from "@/lib/api-client";
import { clearAuthSession, getAuthToken } from "@/lib/auth-storage";
import { useSession } from "@/lib/session-context";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8100/api/v1";

type QrSessionCreateResponse = {
  session_id: string;
  status: string;
  expires_in?: number;
  expires_at?: string | null;
};

type QrSessionStatusResponse = {
  session_id: string;
  status: string;
  qrcode_base64?: string | null;
  screenshot_version?: number;
  error_message?: string | null;
  token?: string | null;
  user?: {
    user_id: string;
    nickname: string;
    role: "admin" | "user";
  } | null;
};

const POLL_INTERVAL_MS = 1200;
const POLL_FIRST_DELAY_MS = 300;
const FINAL_STATES = new Set(["success", "expired", "error", "cancelled"]);
const CLIENT_DEVICE_ID_KEY = "redmuse_client_device_id";

function getClientDeviceId(): string {
  if (typeof window === "undefined") return "";
  const existing = window.localStorage.getItem(CLIENT_DEVICE_ID_KEY);
  if (existing) return existing;

  const generated =
    typeof window.crypto?.randomUUID === "function"
      ? window.crypto.randomUUID()
      : `device_${Date.now()}_${Math.random().toString(36).slice(2)}`;
  window.localStorage.setItem(CLIENT_DEVICE_ID_KEY, generated);
  return generated;
}

function cancelLoginSession(sessionId: string, keepalive = false): void {
  void fetch(`${API_BASE_URL}/auth/xhs-login/session/${sessionId}`, {
    method: "DELETE",
    cache: "no-store",
    keepalive,
  }).catch(() => {
    // 页面刷新/关闭时取消请求可能被浏览器中断，后端仍有超时兜底。
  });
}

export default function LoginPage() {
  const router = useRouter();
  const { loginWithSession } = useSession();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("idle");
  const [qrCodeBase64, setQrCodeBase64] = useState<string | null>(null);
  const [screenshotVersion, setScreenshotVersion] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [smsCode, setSmsCode] = useState("");
  const [isSubmittingSms, setIsSubmittingSms] = useState(false);
  const [debugMessage, setDebugMessage] = useState<string>("准备获取二维码");
  const activeSessionIdRef = useRef<string | null>(null);
  const createSeqRef = useRef(0);
  const isCreatingRef = useRef(false);

  useEffect(() => {
    let cancelled = false;

    const checkExistingSession = async () => {
      const token = getAuthToken();
      if (!token) return;

      const me = await apiGet<{ user_id: string; nickname: string; role: "admin" | "user" }>("/auth/me", {
        withAuth: true,
      });

      if (!cancelled && me.ok && !me.data.user_id.startsWith("fallback_")) {
        router.replace("/workspace");
        return;
      }

      if (!cancelled && me.ok) {
        clearAuthSession();
        window.localStorage.removeItem("redmuse_last_xhs_user_id");
        return;
      }

      if (!cancelled) {
        clearAuthSession();
      }
    };

    void checkExistingSession();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const statusLabel = useMemo(() => {
    if (qrCodeBase64 && status === "initializing") {
      return "等待扫码中...";
    }
    switch (status) {
      case "initializing":
        return "初始化扫码环境中...";
      case "waiting_scan":
        return "等待扫码中...";
      case "scanned":
        return "已扫码，等待手机确认...";
      case "need_sms_code":
        return "需要短信验证码，请在下方输入";
      case "confirmed":
        return "已确认，正在生成系统会话...";
      case "success":
        return "登录成功，正在跳转...";
      case "expired":
        return "二维码已过期，请刷新";
      case "error":
        return "登录失败，请重试";
      case "cancelled":
        return "会话已取消";
      default:
        return "点击按钮开始扫码登录";
    }
  }, [qrCodeBase64, status]);

  useEffect(() => {
    if (!sessionId) return;

    const pollingSessionId = sessionId;
    let timer: number | null = null;
    let stopped = false;
    let isFirstPoll = true;

    const pollStatus = async () => {
      try {
        const prevXhsUserId =
          typeof window !== "undefined"
            ? window.localStorage.getItem("redmuse_last_xhs_user_id")
            : null;
        const response = await fetch(
          `${API_BASE_URL}/auth/xhs-login/session/${pollingSessionId}`,
          {
            method: "GET",
            headers: {
              "Content-Type": "application/json",
              ...(prevXhsUserId ? { "X-Redmuse-Previous-User-Id": prevXhsUserId } : {}),
            },
            cache: "no-store",
          },
        );

        if (response.status === 404) {
          stopped = true;
          activeSessionIdRef.current = null;
          setStatus("expired");
          setErrorMessage("扫码会话已过期或后端已重启，请刷新后重新扫码");
          setDebugMessage("会话已失效");
          return;
        }

        if (!response.ok) {
          const text = await response.text();
          throw new Error(text || `查询扫码状态失败: ${response.status}`);
        }

        const payload = (await response.json()) as {
          ok: boolean;
          data?: QrSessionStatusResponse;
        };

        if (!payload.ok || !payload.data) {
          throw new Error("服务返回无效响应");
        }

        const data = payload.data;
        if (
          stopped ||
          activeSessionIdRef.current !== pollingSessionId ||
          data.session_id !== pollingSessionId
        ) {
          return;
        }
        setStatus(data.status);
        if (data.qrcode_base64) {
          setQrCodeBase64(data.qrcode_base64);
          setScreenshotVersion((current) => data.screenshot_version ?? current + 1);
        }
        setErrorMessage(data.error_message ?? null);

        if (data.status === "success" && data.token && data.user) {
          // 直接注入 SessionProvider,同步 token/user + ready=true,
          // 跨页跳转时 AuthGate 立即放行,不再出现"正在验证登录状态..."卡顿
          loginWithSession(data.token, data.user);
          setDebugMessage("会话建立成功，即将跳转工作台");
          window.setTimeout(() => router.push("/workspace"), 400);
          stopped = true;
          if (timer) {
            window.clearTimeout(timer);
            timer = null;
          }
          return;
        }

        if (FINAL_STATES.has(data.status)) {
          stopped = true;
          if (timer) {
            window.clearTimeout(timer);
            timer = null;
          }
          return;
        }

        if (!stopped) {
          timer = window.setTimeout(pollStatus, POLL_INTERVAL_MS);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "扫码状态轮询失败";
        setErrorMessage(message);
        setStatus("error");
      }
    };

    const firstDelay = isFirstPoll ? POLL_FIRST_DELAY_MS : POLL_INTERVAL_MS;
    isFirstPoll = false;
    timer = window.setTimeout(pollStatus, firstDelay);
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [sessionId, router, loginWithSession]);

  const handleStartLogin = async () => {
    if (isCreatingRef.current) return;
    isCreatingRef.current = true;
    const createSeq = createSeqRef.current + 1;
    createSeqRef.current = createSeq;
    setIsCreating(true);
    setErrorMessage(null);
    setDebugMessage("正在创建扫码会话...");

    try {
      const lastXhsUserId =
        typeof window !== "undefined"
          ? window.localStorage.getItem("redmuse_last_xhs_user_id")
          : null;
      const expectedUserId =
        lastXhsUserId && !lastXhsUserId.startsWith("fallback_") ? lastXhsUserId : null;
      const clientDeviceId = getClientDeviceId();

      const response = await apiPost<
        QrSessionCreateResponse,
        { scene: string; expected_user_id?: string; client_device_id: string }
      >(
        "/auth/xhs-login/session",
        {
          scene: "dashboard_login",
          client_device_id: clientDeviceId,
          ...(expectedUserId ? { expected_user_id: expectedUserId } : {}),
        },
      );

      if (!response.ok) {
        throw new Error(response.error.message);
      }

      if (createSeq !== createSeqRef.current) {
        return;
      }
      activeSessionIdRef.current = response.data.session_id;
      setSessionId(response.data.session_id);
      setStatus(response.data.status);
      setDebugMessage(`会话已创建：${response.data.session_id}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "创建扫码会话失败";
      setErrorMessage(message);
      setStatus("error");
    } finally {
      if (createSeq === createSeqRef.current) {
        isCreatingRef.current = false;
        setIsCreating(false);
      }
    }
  };

  const handleRefresh = async () => {
    const oldSessionId = activeSessionIdRef.current;
    createSeqRef.current += 1;
    if (oldSessionId) {
      cancelLoginSession(oldSessionId);
    }
    activeSessionIdRef.current = null;
    setSessionId(null);
    setStatus("idle");
    setQrCodeBase64(null);
    setScreenshotVersion(0);
    setErrorMessage(null);
    setDebugMessage("准备获取二维码");
    await handleStartLogin();
  };

  const handleSubmitSmsCode = async () => {
    if (!sessionId || !smsCode.trim() || isSubmittingSms) return;
    setIsSubmittingSms(true);
    setErrorMessage(null);
    try {
      const response = await apiPost<{ sms_submitted: boolean }, { sms_code: string }>(
        `/auth/xhs-login/session/${sessionId}/sms`,
        { sms_code: smsCode.trim() },
      );
      if (!response.ok) {
        throw new Error(response.error.message);
      }
      setDebugMessage("验证码已提交，等待验证结果...");
      setSmsCode("");
    } catch (error) {
      const message = error instanceof Error ? error.message : "提交验证码失败";
      setErrorMessage(message);
    } finally {
      setIsSubmittingSms(false);
    }
  };

  useEffect(() => {
    return () => {
      if (sessionId && activeSessionIdRef.current === sessionId) {
        activeSessionIdRef.current = null;
        cancelLoginSession(sessionId);
      }
    };
  }, [sessionId]);

  useEffect(() => {
    const cancelActiveSession = () => {
      const currentSessionId = activeSessionIdRef.current;
      if (!currentSessionId || FINAL_STATES.has(status)) return;
      activeSessionIdRef.current = null;
      cancelLoginSession(currentSessionId, true);
    };

    window.addEventListener("pagehide", cancelActiveSession);
    window.addEventListener("beforeunload", cancelActiveSession);
    return () => {
      window.removeEventListener("pagehide", cancelActiveSession);
      window.removeEventListener("beforeunload", cancelActiveSession);
    };
  }, [status]);

  const hasActiveLoginSession = Boolean(sessionId && !FINAL_STATES.has(status));

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
        <div className="w-full max-w-3xl">
          <h2 className="text-2xl font-semibold">欢迎使用 RedMuse</h2>
          <p className="mt-2 text-sm text-[#8a8580]">使用小红书账号登录，即刻开始分析</p>

          <div className="mt-6 rounded-xl border border-dashed border-[#e8e5e0] bg-[#fafaf8] p-3 text-center">
            {qrCodeBase64 ? (
              <div className="relative mx-auto mb-4 h-[min(74vh,720px)] w-full overflow-hidden rounded-lg border border-[#f0eeeb] bg-white">
                <img
                  key={`${sessionId ?? "login"}-${screenshotVersion}`}
                  src={`data:image/jpeg;base64,${qrCodeBase64}`}
                  alt="服务器登录页截图"
                  className="absolute left-1/2 top-1/2 h-full w-full max-w-none -translate-x-1/2 -translate-y-1/2 scale-[1.55] object-contain object-center sm:scale-[1.7] lg:scale-[1.85]"
                />
              </div>
            ) : status === "waiting_scan" || status === "scanned" || status === "confirmed" ? (
              <div className="mx-auto mb-4 flex h-44 w-44 flex-col items-center justify-center gap-2 rounded-lg bg-[#f5f3f0] text-xs text-[#5a5550]">
                <span className="text-2xl">🖥️</span>
                <span>浏览器已打开</span>
                <span className="text-[#a8a4a0]">请在弹出的窗口中完成登录</span>
              </div>
            ) : status === "initializing" ? (
              <div className="mx-auto mb-4 flex h-44 w-44 items-center justify-center rounded-lg bg-[#f5f3f0]">
                <div className="h-6 w-6 animate-spin rounded-full border-2 border-[#ff4757] border-t-transparent" />
              </div>
            ) : (
              <div className="mx-auto mb-4 flex h-44 w-44 items-center justify-center rounded-lg bg-[#f5f3f0] text-xs text-[#a8a4a0]">
                点击下方按钮开始
              </div>
            )}
            <p className="text-sm text-[#5a5550]">{statusLabel}</p>
            <p className="mt-2 text-xs text-[#a8a4a0]">{debugMessage}</p>
            {errorMessage ? <p className="mt-2 text-xs text-[#e04040]">{errorMessage}</p> : null}
          </div>

          <div className="mt-4 flex gap-3">
            <button
              type="button"
              onClick={handleStartLogin}
              disabled={isCreating || hasActiveLoginSession}
              className="flex-1 rounded-lg bg-[#ff4757] px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-[#ffb6bd]"
            >
              {isCreating ? "创建中..." : "开始扫码登录"}
            </button>
            <button
              type="button"
              onClick={handleRefresh}
              disabled={isCreating}
              className="rounded-lg border border-[#e8e5e0] px-4 py-2 text-sm text-[#5a5550] disabled:cursor-not-allowed disabled:opacity-50"
            >
              刷新
            </button>
          </div>

          {status === "need_sms_code" ? (
            <div className="mt-4 rounded-lg border border-[#f0eeeb] bg-[#fafaf8] p-3">
              <p className="mb-2 text-xs text-[#8a8580]">检测到需要短信验证码，请输入后提交</p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={smsCode}
                  onChange={(e) => setSmsCode(e.target.value)}
                  maxLength={6}
                  placeholder="输入短信验证码"
                  className="flex-1 rounded-lg border border-[#e8e5e0] px-3 py-2 text-sm outline-none focus:border-[#ff4757]"
                />
                <button
                  type="button"
                  onClick={handleSubmitSmsCode}
                  disabled={isSubmittingSms || smsCode.trim().length < 4}
                  className="rounded-lg bg-[#ff4757] px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-[#ffb6bd]"
                >
                  {isSubmittingSms ? "提交中..." : "提交"}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}

