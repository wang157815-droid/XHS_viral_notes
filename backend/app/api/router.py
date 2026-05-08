from fastapi import APIRouter

from .routes import auth, conversations, health, history, knowledge, metrics, settings, tasks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(conversations.router)
api_router.include_router(tasks.router)
api_router.include_router(history.router)
api_router.include_router(knowledge.router)
api_router.include_router(metrics.router)
api_router.include_router(settings.router)

