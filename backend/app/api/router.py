from fastapi import APIRouter

from .routes import (
    auth,
    conversations,
    health,
    history,
    knowledge,
    metrics,
    settings,
    tasks,
    wxwork_aibot,
    wxwork_session,
    wxwork_webhook,
    xhs_auth,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(xhs_auth.router)
api_router.include_router(conversations.router)
api_router.include_router(tasks.router)
api_router.include_router(history.router)
api_router.include_router(knowledge.router)
api_router.include_router(metrics.router)
api_router.include_router(settings.router)
# 企业微信自建应用消息回调
api_router.include_router(wxwork_webhook.router)
# 企业微信智能机器人消息回调（数据与智能专区）
api_router.include_router(wxwork_aibot.router)
# 企业微信会话内容存档（拉取 + 解密 + 本地存储）
api_router.include_router(wxwork_session.router)

