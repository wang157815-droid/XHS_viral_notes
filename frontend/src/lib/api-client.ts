import type { ApiResponse } from "@/lib/contracts";
import { getAuthToken } from "@/lib/auth-storage";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8100/api/v1";

type RequestOptions = {
  withAuth?: boolean;
  idempotencyKey?: string;
  ifMatch?: string | number;
  signal?: AbortSignal;
};

export function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  const rnd = Math.random().toString(36).slice(2);
  return `idem-${Date.now().toString(36)}-${rnd}`;
}

function buildHeaders(options: RequestOptions): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (options.withAuth) {
    const token = getAuthToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
  }
  if (options.idempotencyKey) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }
  if (options.ifMatch !== undefined && options.ifMatch !== null) {
    headers["If-Match"] = String(options.ifMatch);
  }
  return headers;
}

async function normalizeResponse<T>(response: Response): Promise<ApiResponse<T>> {
  const text = await response.text();
  let parsed: unknown = {};
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { raw: text };
    }
  }

  if (response.ok) {
    if (parsed && typeof parsed === "object" && "ok" in parsed) {
      return parsed as ApiResponse<T>;
    }
    return {
      ok: true,
      data: (parsed as T) ?? ({} as T),
    };
  }

  if (parsed && typeof parsed === "object" && "ok" in parsed) {
    return parsed as ApiResponse<T>;
  }

  const detail =
    parsed && typeof parsed === "object" && "detail" in parsed
      ? (parsed as { detail?: unknown }).detail
      : undefined;

  if (detail && typeof detail === "object" && "code" in (detail as Record<string, unknown>)) {
    const detailObj = detail as Record<string, unknown>;
    return {
      ok: false,
      error: {
        code: String(detailObj.code ?? `HTTP_${response.status}`),
        message: String(detailObj.message ?? response.statusText ?? ""),
        details: (detailObj.details as Record<string, unknown>) ?? {},
        trace_id: detailObj.trace_id ? String(detailObj.trace_id) : undefined,
      },
    };
  }

  const message =
    typeof detail === "string"
      ? detail
      : response.statusText || `请求失败（${response.status}）`;

  return {
    ok: false,
    error: {
      code: `HTTP_${response.status}`,
      message,
      details: parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {},
    },
  };
}

export async function apiGet<T>(path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "GET",
    headers: buildHeaders(options),
    cache: "no-store",
    signal: options.signal,
  });
  return normalizeResponse<T>(response);
}

export async function apiPost<T, B = Record<string, unknown>>(
  path: string,
  body: B,
  options: RequestOptions = {},
): Promise<ApiResponse<T>> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers: buildHeaders(options),
    body: JSON.stringify(body),
    signal: options.signal,
  });
  return normalizeResponse<T>(response);
}

export async function apiPut<T, B = Record<string, unknown>>(
  path: string,
  body: B,
  options: RequestOptions = {},
): Promise<ApiResponse<T>> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "PUT",
    headers: buildHeaders(options),
    body: JSON.stringify(body),
    signal: options.signal,
  });
  return normalizeResponse<T>(response);
}

export async function apiDelete<T>(path: string, options: RequestOptions = {}): Promise<ApiResponse<T>> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "DELETE",
    headers: buildHeaders(options),
    signal: options.signal,
  });
  return normalizeResponse<T>(response);
}

/**
 * Multipart 上传。与 apiPost 区别：
 * - 不设置 Content-Type（让浏览器自动带上 boundary）
 * - body 是 FormData
 */
export async function apiUpload<T>(
  path: string,
  formData: FormData,
  options: Omit<RequestOptions, "idempotencyKey" | "ifMatch"> = {},
): Promise<ApiResponse<T>> {
  const headers: Record<string, string> = {};
  if (options.withAuth) {
    const token = getAuthToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    headers,
    body: formData,
    signal: options.signal,
  });
  return normalizeResponse<T>(response);
}

export const API_BASE = API_BASE_URL;
