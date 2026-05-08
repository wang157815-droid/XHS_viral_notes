"""Legacy RedMuse entrypoint stub.

阶段 4.5 起，主应用已迁移到 `backend.app.main`（默认端口 8100）和
Next.js 前端。此文件只保留旧启动命令 `python viral_app.py` 的兼容行为：

- `/` 返回维护页
- `/health` 返回 stub 状态
- 旧 `/api/*` 返回 410，并指向新版 `/api/v1/*` 契约

不要在本文件继续添加业务逻辑。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse


app = FastAPI(
    title="RedMuse Legacy Stub",
    description="旧 viral_app.py 已在阶段 4.5 下线，请使用 backend.app.main。",
    version="4.5-legacy-stub",
)


MIGRATION_HINTS: Dict[str, str] = {
    "/api/auth": "/api/v1/auth",
    "/api/admin/users": "/api/v1/settings/users",
    "/api/viral/cookie/status": "/api/v1/settings/cookie-health",
    "/api/viral/search": "/api/v1/tasks 或 /api/v1/conversations/{conversation_id}/messages",
    "/api/viral/status": "/api/v1/tasks/{task_id} 或 /api/v1/tasks/{task_id}/stream",
    "/api/viral/history": "/api/v1/tasks + /api/v1/conversations",
    "/api/viral/export": "/api/v1/tasks/{task_id}/export/excel 或 /export/json",
    "/api/tasks": "/api/v1/tasks",
    "/api/knowledge": "/api/v1/knowledge",
    "/api/documents": "/api/v1/knowledge/documents",
    "/api/cleanup": "/api/v1/settings/maintenance",
    "/api/qrcode": "/api/v1/auth/xhs-login/session",
}


def _maintenance_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta http-equiv="refresh" content="3;url=/login" />
    <title>RedMuse 已迁移</title>
    <style>
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: linear-gradient(135deg, #fffdfb 0%, #fff8f5 100%);
        color: #2d2a26;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      }
      main {
        width: min(560px, calc(100vw - 40px));
        padding: 40px;
        border: 1px solid #f0eeeb;
        border-radius: 22px;
        background: rgba(255, 255, 255, 0.94);
        box-shadow: 0 20px 60px rgba(45, 42, 38, 0.08);
      }
      .badge {
        display: inline-flex;
        padding: 6px 10px;
        border-radius: 999px;
        background: #fff0ee;
        color: #ff4757;
        font-size: 12px;
        font-weight: 700;
      }
      h1 { margin: 18px 0 12px; font-size: 30px; line-height: 1.2; }
      p { margin: 0; color: #8a8580; font-size: 15px; line-height: 1.8; }
      .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 28px; }
      a {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 40px;
        padding: 0 16px;
        border-radius: 10px;
        text-decoration: none;
        font-size: 14px;
        font-weight: 700;
      }
      .primary { background: #ff4757; color: #fff; }
      .secondary { border: 1px solid #f0eeeb; color: #2d2a26; }
      .hint { margin-top: 18px; font-size: 12px; }
    </style>
  </head>
  <body>
    <main>
      <span class="badge">Legacy app retired</span>
      <h1>RedMuse 已迁移到新版工作台</h1>
      <p>旧版 viral_app.py 入口已停止承载业务功能。请使用新版 RedMuse 登录、对话中枢、历史中心、知识库与 Canvas 工作台。</p>
      <div class="actions">
        <a class="primary" href="/login">进入新版 RedMuse</a>
        <a class="secondary" href="/api/v1/health">查看新后端健康状态</a>
      </div>
      <p class="hint">页面将在 3 秒后自动跳转到新版登录页。</p>
    </main>
  </body>
</html>"""


def _lookup_hint(path: str) -> str:
    for prefix, replacement in MIGRATION_HINTS.items():
        if path.startswith(prefix):
            return replacement
    return "/api/v1"


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _maintenance_html()


@app.get("/health")
async def health_check() -> Dict[str, Any]:
        return {
        "status": "legacy_stub",
        "service": "RedMuse Legacy Stub",
        "message": "viral_app.py 已下线，请使用 backend.app.main",
        "new_backend": "python backend/run.py --no-reload --host 0.0.0.0 --port 8100",
    }


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def legacy_api_gone(path: str, request: Request) -> JSONResponse:
    legacy_path = f"/api/{path}"
        return JSONResponse(
        status_code=410,
        content={
            "ok": False,
            "error": {
                "code": "LEGACY_API_GONE",
                "message": "旧 viral_app.py API 已在阶段 4.5 下线，请使用新版 RedMuse API。",
                "details": {
                    "legacy_path": legacy_path,
                    "method": request.method,
                    "replacement": _lookup_hint(legacy_path),
                    "api_prefix": "/api/v1",
                    "deprecation_doc": "docs/phase4_deprecation_list.md",
                },
            },
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("viral_app:app", host="0.0.0.0", port=8000)
