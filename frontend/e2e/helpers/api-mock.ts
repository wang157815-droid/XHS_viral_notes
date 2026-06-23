import type { Page, Route, Request } from "@playwright/test";

import {
  DEFAULT_COOKIE_HEALTH,
  DEFAULT_TOKEN,
  DEFAULT_USER,
  makeConversation,
  nowIso,
  type AuthUser,
  type ChatMessage,
  type Conversation,
  type CookieHealth,
} from "./mock-data";

export interface ConversationDetail {
  conversation: Conversation;
  messages: ChatMessage[];
}

export interface MockApiOptions {
  user?: AuthUser;
  token?: string;
  cookieHealth?: CookieHealth;
  /** GET /conversations 列表（侧边栏 / 历史） */
  conversations?: Array<Record<string, unknown>>;
  /** GET /conversations/{id} 明细，按 id 索引 */
  conversationDetails?: Record<string, ConversationDetail>;
  /** POST /conversations 返回的新会话 */
  createdConversation?: Conversation;
  /** POST /conversations/{id}/messages/stream 的 SSE 文本，或基于请求体动态生成 */
  conversationStream?: string | ((body: Record<string, unknown>) => string);
  /** GET /tasks/{id} 状态，按 id 索引 */
  tasks?: Record<string, { task_id: string; status: string; raw_input?: string }>;
  /** GET /tasks/{id}/canvas，按 id 索引（CanvasSchema 对象） */
  canvases?: Record<string, Record<string, unknown>>;
  /** GET /tasks/{id}/stream 的 SSE 文本，按 id 索引 */
  taskStreams?: Record<string, string>;
  /** GET /tasks 列表（历史页） */
  taskList?: Array<Record<string, unknown>>;
}

const CORS_HEADERS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
  "Access-Control-Allow-Headers": "*",
};

function jsonOk(route: Route, data: unknown) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    headers: CORS_HEADERS,
    body: JSON.stringify({ ok: true, data }),
  });
}

function jsonError(route: Route, status: number, code: string, message: string) {
  return route.fulfill({
    status,
    contentType: "application/json",
    headers: CORS_HEADERS,
    body: JSON.stringify({ ok: false, error: { code, message, details: {} } }),
  });
}

function sse(route: Route, body: string) {
  return route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    headers: { ...CORS_HEADERS, "Cache-Control": "no-cache" },
    body,
  });
}

/** 把任意 url 归一化为 /api/v1 之后的 path（去掉协议/host/query）。 */
function apiPath(request: Request): string {
  const url = new URL(request.url());
  const idx = url.pathname.indexOf("/api/v1");
  return idx >= 0 ? url.pathname.slice(idx + "/api/v1".length) : url.pathname;
}

/**
 * 在页面注入 localStorage 鉴权态（在任何导航前调用）。
 * 与 `frontend/src/lib/auth-storage.ts` 的 key 对齐。
 */
export async function seedAuth(
  page: Page,
  user: AuthUser = DEFAULT_USER,
  token: string = DEFAULT_TOKEN,
): Promise<void> {
  await page.addInitScript(
    ([t, u]) => {
      window.localStorage.setItem("redmuse_access_token", t as string);
      window.localStorage.setItem("redmuse_user_profile", JSON.stringify(u));
    },
    [token, user] as const,
  );
}

/**
 * 拦截所有 /api/v1/** 请求并以 mock 响应。返回一个收集器，便于断言调用情况。
 */
export async function installApiMock(page: Page, opts: MockApiOptions = {}) {
  const user = opts.user ?? DEFAULT_USER;
  const cookieHealth = opts.cookieHealth ?? DEFAULT_COOKIE_HEALTH;
  const calls: Array<{ method: string; path: string }> = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const method = request.method();
    const path = apiPath(request);
    calls.push({ method, path });

    if (method === "OPTIONS") {
      await route.fulfill({ status: 204, headers: CORS_HEADERS, body: "" });
      return;
    }

    // ── auth ──
    if (path === "/auth/me" && method === "GET") return jsonOk(route, user);
    if (path === "/auth/login" && method === "POST") {
      return jsonOk(route, { token: opts.token ?? DEFAULT_TOKEN, user });
    }
    if (path === "/auth/logout" && method === "POST") return jsonOk(route, { logout: true });

    // ── settings ──
    if (path.startsWith("/settings/cookie-health")) return jsonOk(route, cookieHealth);

    // ── conversations ──
    if (path.startsWith("/conversations")) {
      // POST /conversations/{id}/messages/stream
      if (path.endsWith("/messages/stream") && method === "POST") {
        const provider = opts.conversationStream;
        let body: Record<string, unknown> = {};
        try {
          body = (request.postDataJSON() as Record<string, unknown>) ?? {};
        } catch {
          body = {};
        }
        const text = typeof provider === "function" ? provider(body) : (provider ?? "");
        return sse(route, text);
      }
      // GET /conversations/{id}/messages
      if (path.endsWith("/messages") && method === "GET") {
        const id = path.split("/")[2] ?? "";
        const detail = opts.conversationDetails?.[id];
        return jsonOk(route, { items: detail?.messages ?? [], has_more: false });
      }
      // GET /conversations?... 列表
      if ((path === "/conversations" || path.startsWith("/conversations?")) && method === "GET") {
        return jsonOk(route, {
          items: opts.conversations ?? [],
          include_all_effective: false,
        });
      }
      // POST /conversations 新建
      if (path === "/conversations" && method === "POST") {
        return jsonOk(route, opts.createdConversation ?? makeConversation());
      }
      // archive / restore
      if (path.endsWith("/archive") || path.endsWith("/restore")) {
        const id = path.split("/")[2] ?? "";
        return jsonOk(route, makeConversation({ conversation_id: id }));
      }
      // GET/PATCH/DELETE /conversations/{id}
      const id = path.replace(/^\/conversations\//, "").split("/")[0] ?? "";
      if (method === "GET") {
        const detail = opts.conversationDetails?.[id];
        if (!detail) return jsonError(route, 404, "NOT_FOUND", "会话不存在");
        return jsonOk(route, detail);
      }
      if (method === "PATCH" || method === "DELETE") {
        return jsonOk(route, makeConversation({ conversation_id: id }));
      }
    }

    // ── tasks ──
    if (path.startsWith("/tasks")) {
      // GET /tasks/{id}/stream
      if (path.endsWith("/stream") && method === "GET") {
        const id = path.split("/")[2] ?? "";
        return sse(route, opts.taskStreams?.[id] ?? "");
      }
      // GET /tasks/{id}/canvas
      if (path.endsWith("/canvas") && method === "GET") {
        const id = path.split("/")[2] ?? "";
        const canvas = opts.canvases?.[id];
        if (!canvas) return jsonError(route, 404, "NOT_FOUND", "画布不存在");
        return jsonOk(route, canvas);
      }
      // GET /tasks 列表
      if ((path === "/tasks" || path.startsWith("/tasks?")) && method === "GET") {
        return jsonOk(route, { items: opts.taskList ?? [] });
      }
      // 控制类 POST（pause/resume/cancel/retry/regenerate/...）
      if (method === "POST") {
        return jsonOk(route, { accepted: true });
      }
      // GET /tasks/{id}
      const id = path.replace(/^\/tasks\//, "").split("/")[0] ?? "";
      if (method === "GET") {
        const task = opts.tasks?.[id];
        if (!task) return jsonError(route, 404, "NOT_FOUND", "任务不存在");
        return jsonOk(route, task);
      }
    }

    // ── knowledge（最小兜底，避免知识库 @ 提及触发真实请求） ──
    if (path.startsWith("/knowledge/documents")) {
      return jsonOk(route, { items: [], total: 0 });
    }

    // 默认兜底
    return jsonOk(route, {});
  });

  return {
    calls,
    countCalls: (predicate: (c: { method: string; path: string }) => boolean) =>
      calls.filter(predicate).length,
  };
}

/** 收集页面 console.error 文本（用于回归断言：不得出现重复 key 等告警）。 */
export function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });
  page.on("pageerror", (err) => {
    errors.push(String(err?.message ?? err));
  });
  return errors;
}

/** 从 console 文本里筛出 React 重复 key 告警。 */
export function duplicateKeyWarnings(errors: string[]): string[] {
  return errors.filter((t) => /same key|duplicate key|two children with the same key/i.test(t));
}

export const fixtures = {
  nowIso,
};
