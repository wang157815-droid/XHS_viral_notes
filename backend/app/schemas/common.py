from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CookieHealth(BaseModel):
    status: str = Field(default="unknown")
    saved_days: int = Field(default=0)
    last_checked_at: Optional[datetime] = None
    message: str = Field(default="pending_check")


class UserBrief(BaseModel):
    user_id: str
    nickname: str
    role: str


class TaskSummary(BaseModel):
    task_id: str
    keywords: List[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime
    progress: int = Field(default=0)
    collected_count: int = Field(default=0)
    duration_seconds: int = Field(default=0)


class ApiSuccess(BaseModel):
    ok: bool = True
    data: Dict[str, Any] = Field(default_factory=dict)


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)


class ApiError(BaseModel):
    ok: bool = False
    error: ApiErrorDetail

