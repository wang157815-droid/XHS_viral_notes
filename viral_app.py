"""
小红书爆文笔记生成Agent - FastAPI应用
提供Web API接口和界面
"""
import os
import sys
import asyncio

# Windows 兼容性：设置正确的事件循环策略（支持子进程，Playwright需要）
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path
from collections import deque

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from loguru import logger
from dotenv import load_dotenv
import re
import uuid
import random

# 导入认证模块
from viral_agent.auth import (
    init_auth, verify_token, verify_token_and_password_changed,
    verify_password, create_token, check_must_change_password, change_password,
    # 用户管理
    is_admin, create_user, delete_user, list_users, get_user_info, update_user_role
)

# 导入爆文Agent核心模块
from viral_agent.services.core.viral_collector import ViralNoteCollector
from viral_agent.services.core.feature_extractor import ViralFeatureExtractor
from viral_agent.services.core.viral_analyzer import ViralAnalyzer, AnalysisCancelled
from viral_agent.models.viral_note import ViralNote, ViralAnalysisResult

# 导入视频分析模块
from viral_agent.services.video.video_enhanced_analyzer import VideoEnhancedAnalyzer
from viral_agent.models.video_analysis_model import VideoAnalysisResult, VideoAnalysisBatch

# 导入知识库管理模块
from viral_agent.config.knowledge_loader import (
    get_knowledge_config,
    reload_knowledge_config
)

# 导入RAG和文档管理模块
from viral_agent.services.knowledge.rag_service import RAGService
from viral_agent.services.knowledge.document_parser import DocumentParser
from viral_agent.services.knowledge.knowledge_retriever import UnifiedKnowledgeRetriever
from viral_agent.models.document import KnowledgeDocument, DocumentMetadata

# 导入清理服务
from viral_agent.services.cleanup_service import CleanupService

# 导入用户数据隔离服务
from viral_agent.services.user_data_service import get_user_data_service, UserDataService

# 导入自动补采服务
from viral_agent.services.core.auto_resupply import AutoResupplyService, ResupplyResult

# 导入任务管理模块
from viral_agent.task import (
    TaskManager, get_task_manager,
    TaskState, TaskCheckpoint, TaskControlSignal
)

# 加载环境变量
load_dotenv()

# 创建FastAPI应用
app = FastAPI(
    title="小红书爆文笔记生成Agent",
    description="自动采集和分析小红书爆款笔记，生成爆文模型",
    version="1.0.0"
)

# 初始化认证系统（检查 JWT_SECRET，创建默认用户）
init_auth()

# 配置静态文件和模板
app.mount("/static", StaticFiles(directory="web/static", html=True), name="static")
templates = Jinja2Templates(directory="web/templates")

# 全局任务状态存储
task_status = {}

# 日志缓冲区配置
LOG_BUFFER_SIZE = 50  # 每个任务最多保存50条日志
_log_id_counter = 0  # 日志ID计数器


@app.on_event("startup")
async def startup_warmup_browser():
    """
    应用启动时后台预热浏览器（非阻塞）

    通过提前启动 Playwright 浏览器实例，将首次扫码登录时的等待时间
    从 13-24 秒优化到 6-8 秒（节省浏览器启动时间 3-5 秒）

    使用 asyncio.create_task 确保不阻塞 FastAPI 启动，
    避免影响健康检查和负载均衡器的就绪探测。
    """
    async def _do_warmup():
        try:
            from viral_agent.services.auth import get_qrcode_login_service
            service = get_qrcode_login_service()
            logger.info("🚀 后台预热 Playwright 浏览器...")
            await service.warmup()
            logger.info("✅ Playwright 浏览器预热完成，首次扫码登录将更快")
        except Exception as e:
            # 预热失败不影响应用运行，只记录警告
            logger.warning(f"⚠️ 浏览器预热失败（不影响正常使用）: {e}")

    # 非阻塞：后台执行预热，不阻塞 FastAPI 启动
    asyncio.create_task(_do_warmup())


def verify_task_ownership(task_id: str, username: str) -> None:
    """
    验证任务归属权限

    Args:
        task_id: 任务ID
        username: 当前用户名

    Raises:
        HTTPException: 任务不存在或无权访问
    """
    if task_id not in task_status:
        raise HTTPException(status_code=404, detail="任务不存在")

    task_owner = task_status[task_id].get("username")
    if task_owner and task_owner != username:
        # 检查是否是管理员（管理员可以访问所有任务）
        if not is_admin(username):
            raise HTTPException(
                status_code=403,
                detail="无权访问此任务"
            )


def add_task_log(task_id: str, message: str, level: str = "info") -> None:
    """
    添加任务日志

    Args:
        task_id: 任务ID
        message: 日志消息
        level: 日志级别 (info/success/warning/error)
    """
    global _log_id_counter

    if task_id not in task_status:
        return

    # 确保任务有日志队列
    if "logs" not in task_status[task_id]:
        task_status[task_id]["logs"] = deque(maxlen=LOG_BUFFER_SIZE)

    # 生成日志条目（使用毫秒精度避免同秒显示）
    _log_id_counter += 1
    now = datetime.now()
    log_entry = {
        "id": _log_id_counter,
        "time": now.strftime("%H:%M:%S") + f".{now.microsecond // 1000:03d}",
        "message": message,
        "level": level
    }

    task_status[task_id]["logs"].append(log_entry)
    # 更新最后日志ID，便于前端增量获取
    task_status[task_id]["last_log_id"] = _log_id_counter


# ==================== 请求模型定义 ====================

class ViralSearchRequest(BaseModel):
    """爆款搜索请求模型（支持多关键词并行采集 + 双模式筛选）"""
    # 多关键词支持（优先使用）
    keywords: Optional[List[str]] = Field(
        default=None,
        description="搜索关键词列表（最多5个）",
        json_schema_extra={"example": ["巧克力", "北欧", "Fazer"]}
    )
    # 单关键词（向后兼容）
    keyword: Optional[str] = Field(
        default=None,
        description="搜索关键词（向后兼容，优先使用keywords）",
        json_schema_extra={"example": "防脱精华"}
    )
    note_type: int = Field(default=0, description="笔记类型：0不限 1视频 2图文")
    time_range: int = Field(default=0, description="时间范围：0不限 1一天内 2一周内 3半年内")

    # ==================== 搜索模式（前端默认 ratio，后端保留 threshold 支持） ====================
    search_mode: str = Field(
        default="ratio",
        description="搜索模式：ratio=比例筛选 threshold=阈值筛选",
        pattern="^(ratio|threshold)$"
    )

    # ==================== 比例模式参数 ====================
    target_count: int = Field(
        default=200,
        description="采集目标数量",
        ge=30,
        le=1000
    )
    viral_ratio: float = Field(
        default=0.5,
        description="爆款筛选比例：取互动分数前N%的笔记",
        ge=0.1,
        le=1.0
    )
    min_sample_count: int = Field(
        default=60,
        description="期望分析样本量（用户设定的目标分析数量）",
        ge=5,
        le=200
    )

    # ==================== 阈值模式参数（后端保留，前端暂不暴露） ====================
    min_interaction: Optional[int] = Field(
        default=None,
        description="最低互动阈值（阈值模式必填）",
        ge=100,
        le=100000
    )
    max_collect_count: int = Field(
        default=500,
        description="阈值模式最大采集数量",
        ge=50,
        le=1000
    )
    enable_ai_expansion: bool = Field(
        default=True,
        description="是否启用AI关键词扩展"
    )

    def get_keywords(self) -> List[str]:
        """获取关键词列表（兼容单关键词和多关键词模式）"""
        if self.keywords:
            # 限制最多5个关键词
            return self.keywords[:5]
        elif self.keyword:
            return [self.keyword]
        else:
            return []

    def model_post_init(self, __context) -> None:
        """验证请求参数"""
        # 验证至少提供一个关键词
        if not self.keywords and not self.keyword:
            raise ValueError("必须提供至少一个关键词（keywords 或 keyword）")

        # 阈值模式必须指定 min_interaction
        if self.search_mode == "threshold" and self.min_interaction is None:
            raise ValueError("阈值模式必须指定 min_interaction（最低互动阈值）")


class AnalysisRequest(BaseModel):
    """分析请求模型"""
    task_id: str = Field(..., description="采集任务ID")
    use_ai: bool = Field(default=True, description="是否使用AI分析")
    analysis_type: str = Field(
        default="all",
        description="分析类型：image=仅图文 video=仅视频 all=全部",
        pattern="^(image|video|all)$"
    )
    video_source_mode: Optional[str] = Field(
        default="proxy",
        description="视频源模式：url=URL直传 proxy=本地下载（默认），大视频自动降级URL",
        pattern="^(url|proxy)$"
    )


class CookieRequest(BaseModel):
    """Cookie请求模型"""
    cookie: str = Field(..., description="小红书Cookie字符串")


# ==================== 认证请求模型 ====================

class LoginRequest(BaseModel):
    """登录请求模型"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class ChangePasswordRequest(BaseModel):
    """修改密码请求模型"""
    old_password: str = Field(..., description="原密码")
    new_password: str = Field(..., description="新密码", min_length=8)


class CreateUserRequest(BaseModel):
    """创建用户请求模型"""
    username: str = Field(
        ...,
        description="用户名（字母开头，3-20位，只能包含字母数字下划线）",
        pattern="^[a-zA-Z][a-zA-Z0-9_]{2,19}$"
    )
    password: str = Field(..., description="初始密码", min_length=8)
    role: str = Field(default="user", description="角色：admin 或 user", pattern="^(admin|user)$")


class UpdateUserRoleRequest(BaseModel):
    """更新用户角色请求模型"""
    role: str = Field(..., description="新角色：admin 或 user", pattern="^(admin|user)$")


# ==================== 知识库管理请求模型 ====================

class DomainCreateRequest(BaseModel):
    """创建领域请求模型"""
    id: str = Field(..., description="领域ID（英文，如eye_care）", pattern="^[a-z_]+$")
    name: str = Field(..., description="领域名称", json_schema_extra={"example": "眼部护理"})
    enabled: bool = Field(default=True, description="是否启用")
    priority: int = Field(default=0, description="优先级", ge=0, le=100)
    keywords: List[str] = Field(..., description="关键词列表", min_length=1)
    problems: List[str] = Field(..., description="常见问题列表")
    intro_ways: List[str] = Field(..., description="产品引出方式列表")
    embed_ways: List[str] = Field(..., description="产品植入方式列表")
    examples: List[Dict[str, str]] = Field(default=[], description="示例列表")


class DomainUpdateRequest(BaseModel):
    """更新领域请求模型"""
    name: Optional[str] = Field(None, description="领域名称")
    enabled: Optional[bool] = Field(None, description="是否启用")
    priority: Optional[int] = Field(None, description="优先级", ge=0, le=100)
    keywords: Optional[List[str]] = Field(None, description="关键词列表")
    problems: Optional[List[str]] = Field(None, description="常见问题列表")
    intro_ways: Optional[List[str]] = Field(None, description="产品引出方式列表")
    embed_ways: Optional[List[str]] = Field(None, description="产品植入方式列表")
    examples: Optional[List[Dict[str, str]]] = Field(None, description="示例列表")


class KeywordRequest(BaseModel):
    """关键词请求模型"""
    keyword: str = Field(..., description="关键词")


class TestDetectionRequest(BaseModel):
    """测试检测请求模型"""
    title: str = Field(..., description="测试标题")
    description: str = Field(default="", description="测试描述")


class CleanupRequest(BaseModel):
    """清理请求模型"""
    categories: List[str] = Field(
        default=["cover_cache", "video_cache", "logs", "av_sync_cache"],
        description="要清理的类别列表"
    )
    dry_run: bool = Field(default=True, description="是否仅预览（不实际删除）")


# ==================== Cookie管理 ====================
# Cookie 现已改为用户独立存储，见 UserDataService.save_cookie() / get_cookie()

# ==================== API路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页"""
    return templates.TemplateResponse("index.html", {"request": request})


# ==================== 认证API ====================

@app.post("/api/auth/login")
async def login(request: LoginRequest):
    """
    用户登录，返回 JWT Token

    Returns:
        token: JWT Token
        must_change_password: 是否需要强制修改密码
    """
    if not verify_password(request.username, request.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    # 检查是否需要强制修改密码
    must_change = check_must_change_password(request.username)

    # 获取用户角色
    user_info = get_user_info(request.username)
    user_role = user_info.get("role", "user") if user_info else "user"

    token = create_token(request.username)
    return {
        "status": "success",
        "username": request.username,
        "token": token,
        "token_type": "bearer",
        "expires_in": 24 * 3600,
        "must_change_password": must_change,
        "role": user_role  # 返回用户角色，前端用于显示管理员功能
    }


@app.get("/api/auth/me")
async def get_current_user(username: str = Depends(verify_token)):
    """获取当前登录用户信息（需要认证）"""
    return {
        "status": "success",
        "username": username
    }


@app.post("/api/auth/change-password")
async def api_change_password(
    request: ChangePasswordRequest,
    username: str = Depends(verify_token)  # 使用 verify_token 而非 verify_token_and_password_changed，允许需要改密的用户访问
):
    """修改密码"""
    if not verify_password(username, request.old_password):
        raise HTTPException(status_code=400, detail="原密码错误")

    change_password(username, request.new_password)
    return {"status": "success", "message": "密码修改成功"}


# ==================== 用户管理API（仅管理员） ====================

@app.get("/api/admin/users")
async def api_list_users(username: str = Depends(verify_token_and_password_changed)):
    """
    获取所有用户列表（仅管理员）

    Returns:
        用户列表
    """
    if not is_admin(username):
        raise HTTPException(status_code=403, detail="仅管理员可访问")

    users = list_users()
    return {
        "status": "success",
        "count": len(users),
        "users": users
    }


@app.post("/api/admin/users")
async def api_create_user(
    request: CreateUserRequest,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    创建新用户（仅管理员）

    新创建的用户首次登录需要修改密码。
    """
    if not is_admin(username):
        raise HTTPException(status_code=403, detail="仅管理员可创建用户")

    try:
        user_info = create_user(
            username=request.username,
            password=request.password,
            role=request.role,
            created_by=username
        )

        # 自动为新用户创建数据目录
        from viral_agent.services.user_data_service import get_user_data_service
        get_user_data_service(request.username)

        return {
            "status": "success",
            "message": f"用户 {request.username} 创建成功",
            "user": user_info
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/admin/users/{target_username}")
async def api_get_user(
    target_username: str,
    username: str = Depends(verify_token_and_password_changed)
):
    """获取单个用户详情（仅管理员）"""
    if not is_admin(username):
        raise HTTPException(status_code=403, detail="仅管理员可访问")

    user_info = get_user_info(target_username)
    if not user_info:
        raise HTTPException(status_code=404, detail=f"用户 {target_username} 不存在")

    # 获取用户数据使用情况
    from viral_agent.services.user_data_service import get_user_data_service
    user_data = get_user_data_service(target_username)
    user_info["storage_usage_mb"] = round(user_data.get_storage_usage_mb(), 2)
    user_info["analysis_count"] = len(user_data.list_analysis_files())

    return {
        "status": "success",
        "user": user_info
    }


@app.put("/api/admin/users/{target_username}/role")
async def api_update_user_role(
    target_username: str,
    request: UpdateUserRoleRequest,
    username: str = Depends(verify_token_and_password_changed)
):
    """更新用户角色（仅管理员）"""
    if not is_admin(username):
        raise HTTPException(status_code=403, detail="仅管理员可修改角色")

    try:
        if not update_user_role(target_username, request.role, username):
            raise HTTPException(status_code=404, detail=f"用户 {target_username} 不存在")

        return {
            "status": "success",
            "message": f"用户 {target_username} 角色已更新为 {request.role}"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/users/{target_username}")
async def api_delete_user(
    target_username: str,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    删除用户（仅管理员）

    注意：此操作不会删除用户的数据目录，需要手动清理。
    """
    if not is_admin(username):
        raise HTTPException(status_code=403, detail="仅管理员可删除用户")

    try:
        if not delete_user(target_username, username):
            raise HTTPException(status_code=404, detail=f"用户 {target_username} 不存在")

        return {
            "status": "success",
            "message": f"用户 {target_username} 已删除"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/viral/cookie")
async def save_cookie(
    request: CookieRequest,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    保存Cookie（需要认证，用户独立存储）

    Returns:
        保存状态
    """
    # 清理Cookie中的换行符和多余空白
    cookie = request.cookie.strip().replace('\n', '').replace('\r', '')

    # 基本验证
    if not cookie:
        return {"status": "error", "message": "Cookie不能为空"}

    # 检查是否包含关键字段
    if 'a1=' not in cookie:
        return {"status": "error", "message": "Cookie格式不正确，缺少a1字段"}

    # 保存到用户专属目录
    user_data = get_user_data_service(username)
    if user_data.save_cookie(cookie):
        logger.info(f"用户 {username} 的 Cookie 已更新，长度: {len(cookie)}")
        return {
            "status": "success",
            "message": "Cookie保存成功",
            "cookie_length": len(cookie)
        }
    else:
        return {"status": "error", "message": "Cookie保存失败"}


@app.get("/api/viral/cookie/status")
async def get_cookie_status(username: str = Depends(verify_token_and_password_changed)):
    """
    获取Cookie状态（需要认证，用户独立）

    Returns:
        Cookie配置状态（不返回Cookie内容预览，防止泄露）
    """
    # 优先使用用户专属Cookie
    user_data = get_user_data_service(username)
    user_cookie_info = user_data.get_cookie_info()

    if user_cookie_info and user_cookie_info.get("has_cookie"):
        return user_cookie_info

    # 回退：仅 admin 用户可使用环境变量中的全局 Cookie
    if is_admin(username):
        env_cookie = os.getenv("COOKIE") or os.getenv("COOKIES") or ""
        env_cookie = env_cookie.strip().replace('\n', '').replace('\r', '')

        if env_cookie:
            return {
                "has_cookie": True,
                "cookie_length": len(env_cookie),
                "source": "env_fallback",
                "message": "使用环境变量中的全局Cookie（仅管理员可用）"
            }

    return {
        "has_cookie": False,
        "cookie_length": 0,
        "source": None
    }


@app.post("/api/viral/search")
async def start_viral_search(
    request: ViralSearchRequest,
    background_tasks: BackgroundTasks,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    启动爆款笔记搜索任务（支持多关键词）

    Returns:
        任务ID和初始状态
    """
    # 获取关键词列表
    keywords = request.get_keywords()
    if not keywords:
        raise HTTPException(status_code=400, detail="必须提供至少一个关键词")

    # 生成任务ID（加随机后缀，避免秒级冲突）
    task_id = f"viral_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(100, 999)}"
    keyword_display = "、".join(keywords)  # 提前定义，用于日志和返回值

    # 初始化任务状态（支持多关键词 + 双模式，记录用户名用于数据隔离）
    task_status[task_id] = {
        "status": "running",
        "progress": 0,
        "collected": 0,
        "target": request.target_count if request.search_mode == "ratio" else request.max_collect_count,
        "keywords": keywords,  # 关键词列表
        "keyword": ", ".join(keywords),  # 兼容旧格式
        "current_keyword": "",  # 当前正在采集的关键词
        "keyword_progress": {kw: {"collected": 0, "status": "pending"} for kw in keywords},
        "min_sample_count": request.min_sample_count,
        "message": "正在初始化...",
        "start_time": datetime.now().isoformat(),
        "logs": deque(maxlen=LOG_BUFFER_SIZE),  # 日志队列
        "last_log_id": 0,  # 最后日志ID
        "username": username,  # 用户名（用于数据隔离）
        # 搜索模式参数（后端保留）
        "search_mode": request.search_mode,
        "min_interaction": request.min_interaction,
        "enable_ai_expansion": request.enable_ai_expansion
    }

    # 同步注册到 TaskManager（支持暂停/恢复/持久化）
    manager = get_task_manager()
    manager.create_task(
        task_id=task_id,
        username=username,
        keywords=keywords,
        target_count=request.target_count,
        viral_ratio=request.viral_ratio,
        note_type=request.note_type,
        time_range=request.time_range,
        min_sample_count=request.min_sample_count,
        # 搜索模式参数
        search_mode=request.search_mode,
        min_interaction=request.min_interaction,
        max_collect_count=request.max_collect_count,
        enable_ai_expansion=request.enable_ai_expansion,
    )
    manager.start_task(task_id)  # 启动任务（创建控制信号）

    # 添加初始日志
    add_task_log(task_id, f"🚀 任务启动：搜索「{keyword_display}」", "info")
    manager.add_log(task_id, f"🚀 任务启动：搜索「{keyword_display}」", "info")

    # 启动后台任务
    background_tasks.add_task(
        collect_viral_notes_task,
        task_id,
        keywords,
        request.target_count,
        request.viral_ratio,
        request.note_type,
        request.time_range,
        request.min_sample_count,
        # 搜索模式参数
        request.search_mode,
        request.min_interaction,
        request.max_collect_count,
        request.enable_ai_expansion
    )

    return {
        "task_id": task_id,
        "status": "started",
        "keywords": keywords,
        "message": f"开始搜索「{keyword_display}」相关爆款笔记"
    }


@app.get("/api/viral/status/{task_id}")
async def get_task_status(
    task_id: str,
    log_cursor: Optional[int] = None,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    获取任务状态（需要认证）

    Args:
        task_id: 任务ID
        log_cursor: 日志游标，传入后只返回该ID之后的新日志

    Returns:
        任务当前状态信息（包含增量日志）
    """
    # 验证任务归属（防止跨用户访问）
    verify_task_ownership(task_id, username)

    status = task_status[task_id].copy()

    # 处理日志：将 deque 转为 list，并支持增量获取
    logs_deque = status.get("logs", deque())
    if log_cursor is not None:
        # 只返回 cursor 之后的新日志
        new_logs = [log for log in logs_deque if log["id"] > log_cursor]
        status["logs"] = new_logs
    else:
        # 返回全部日志
        status["logs"] = list(logs_deque)

    return status


@app.post("/api/viral/analyze")
async def analyze_viral_notes(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    分析爆款笔记生成爆文模型（需要认证）
    改为后台任务模式，支持实时日志更新

    Returns:
        任务状态（前端轮询获取分析进度）
    """
    task_id = request.task_id

    # 验证任务归属（防止跨用户访问）
    verify_task_ownership(task_id, username)

    current_status = task_status[task_id]["status"]

    # 检查是否已在分析中
    if current_status == "analyzing":
        return {
            "status": "already_running",
            "task_id": task_id,
            "message": "分析任务已在运行中"
        }

    # 允许从以下状态启动分析：
    # - completed: 采集完成，首次分析
    # - analyzed: 分析完成，允许重新分析
    # - analysis_failed: 分析失败，允许重试
    if current_status not in ("completed", "analyzed", "analysis_failed"):
        raise HTTPException(
            status_code=400,
            detail=f"当前状态为 {current_status}，无法启动分析。需要采集完成后才能分析"
        )

    # 加载采集的数据
    data_file = task_status[task_id].get("data_file")
    if not data_file or not os.path.exists(data_file):
        raise HTTPException(status_code=404, detail="数据文件不存在")

    # 更新状态为"分析中"
    task_status[task_id]["status"] = "analyzing"
    task_status[task_id]["analysis_progress"] = 0
    add_task_log(task_id, "🔬 开始深度分析爆款笔记...", "info")

    # 启动后台分析任务
    background_tasks.add_task(
        analyze_viral_notes_task,
        task_id,
        data_file,
        request.use_ai,
        request.analysis_type,
        request.video_source_mode
    )

    return {
        "status": "started",
        "task_id": task_id,
        "message": "分析任务已启动，请轮询状态接口获取进度"
    }


# ==================== 任务管理 API ====================

@app.get("/api/tasks")
async def list_tasks(
    status: Optional[str] = None,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    获取用户的任务列表

    Args:
        status: 筛选状态 (running/completed/all)

    Returns:
        任务列表
    """
    manager = get_task_manager()
    tasks = manager.list_tasks(username)

    # 状态筛选
    if status and status != "all":
        tasks = [t for t in tasks if t.status.value == status]

    # 按创建时间倒序
    tasks.sort(key=lambda t: t.created_at, reverse=True)

    # 统计活跃任务数
    active_states = {TaskState.RUNNING, TaskState.PAUSING, TaskState.ANALYZING}
    running_count = sum(1 for t in manager.list_tasks(username) if t.status in active_states)

    return {
        "tasks": [t.to_dict() for t in tasks[:50]],  # 最多返回50条
        "total": len(tasks),
        "running_count": running_count
    }


@app.get("/api/tasks/active")
async def list_active_tasks(
    username: str = Depends(verify_token_and_password_changed)
):
    """获取用户的活跃任务（运行中、暂停中、分析中）"""
    manager = get_task_manager()
    tasks = manager.list_active_tasks(username)
    return {
        "tasks": [t.to_dict() for t in tasks],
        "count": len(tasks)
    }


class BatchStatusRequest(BaseModel):
    """批量状态查询请求"""
    task_ids: List[str]
    log_cursors: Optional[Dict[str, int]] = None


@app.post("/api/tasks/batch-status")
async def batch_task_status(
    request: BatchStatusRequest,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    批量获取任务状态（轮询优化）

    一次请求获取多个任务的最新状态和增量日志
    返回格式: {"tasks": [...]} 数组格式，便于前端遍历
    """
    manager = get_task_manager()
    tasks = []
    log_cursors = request.log_cursors or {}

    for task_id in request.task_ids:
        task = manager.get_task(task_id)
        if not task or task.username != username:
            continue

        task_data = task.to_dict()

        # 处理增量日志
        cursor = log_cursors.get(task_id, 0)
        task_data["logs"] = [log for log in task.logs if log["id"] > cursor]

        tasks.append(task_data)

    return {"tasks": tasks}


@app.post("/api/tasks/{task_id}/pause")
async def pause_task(
    task_id: str,
    username: str = Depends(verify_token_and_password_changed)
):
    """暂停任务"""
    manager = get_task_manager()
    task = manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.username != username:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    if manager.pause_task(task_id):
        # 同步更新旧的 task_status 字典（兼容性）
        if task_id in task_status:
            task_status[task_id]["status"] = "pausing"
        return {"success": True, "message": "暂停信号已发送，任务将在下一个检查点暂停"}

    raise HTTPException(
        status_code=400,
        detail=f"当前状态 {task.status.value} 无法暂停"
    )


@app.post("/api/tasks/{task_id}/resume")
async def resume_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    username: str = Depends(verify_token_and_password_changed)
):
    """恢复暂停的任务"""
    manager = get_task_manager()
    task = manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.username != username:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    if task.status != TaskState.PAUSED:
        raise HTTPException(
            status_code=400,
            detail=f"当前状态 {task.status.value} 无法恢复"
        )

    # 恢复任务
    if manager.resume_task(task_id):
        # 兜底构建 task_status（服务重启后 task_status 为空）
        if task_id not in task_status:
            task_status[task_id] = {
                "status": "running",
                "progress": task.progress,
                "collected": task.collected,
                "target": task.target,
                "keywords": task.keywords,
                "keyword": ", ".join(task.keywords),
                "current_keyword": task.current_keyword or "",
                "min_sample_count": task.min_sample_count,
                "message": "任务恢复中...",
                "start_time": task.started_at or datetime.now().isoformat(),
                "logs": deque(maxlen=LOG_BUFFER_SIZE),
                "last_log_id": 0,
                "username": task.username
            }
        else:
            task_status[task_id]["status"] = "running"

        manager.add_log(task_id, "▶️ 任务已恢复，重新启动采集", "success")

        # 重新启动后台采集任务（从检查点恢复）
        background_tasks.add_task(
            collect_viral_notes_task,
            task_id,
            task.keywords,
            task.target,
            task.viral_ratio,
            task.note_type,
            task.time_range,
            task.min_sample_count
        )

        return {"success": True, "message": "任务已恢复"}

    raise HTTPException(status_code=400, detail="恢复任务失败")


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    username: str = Depends(verify_token_and_password_changed)
):
    """取消任务"""
    manager = get_task_manager()
    task = manager.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.username != username:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    if manager.cancel_task(task_id):
        # 同步更新旧的 task_status 字典
        if task_id in task_status:
            task_status[task_id]["status"] = "cancelling"
        return {"success": True, "message": "取消信号已发送"}

    raise HTTPException(
        status_code=400,
        detail=f"当前状态 {task.status.value} 无法取消"
    )


@app.get("/api/viral/export/latest")
async def export_latest(
    format: str = "excel",
    export_mode: str = "combined",
    username: str = Depends(verify_token_and_password_changed)
):
    """
    导出最新的分析结果（需要认证）

    Args:
        format: 导出格式 (excel/json)
        export_mode: 导出模式 (combined/separate/image_only/video_only)
            - combined: 单个Excel（默认）
            - separate: 图文+视频两个独立Excel
            - image_only: 仅图文报告
            - video_only: 仅视频报告

    Returns:
        文件下载或文件列表JSON
    """
    # 使用用户专属数据目录
    user_data = get_user_data_service(username)
    data_dir = user_data.get_analysis_dir()
    if not data_dir.exists():
        raise HTTPException(status_code=404, detail="数据目录不存在")

    # 查找最新的文件
    all_files = list(data_dir.glob("viral_*.json"))
    if not all_files:
        raise HTTPException(status_code=404, detail="没有找到任何数据文件")

    # 按修改时间排序，获取最新的
    latest_file = max(all_files, key=lambda p: p.stat().st_mtime)
    logger.info(f"导出最新文件: {latest_file}")

    if format == "json":
        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return JSONResponse(content=data)

    elif format == "excel":
        from viral_agent.services.export.export_service import export_to_excel

        try:
            result = export_to_excel(str(latest_file), export_mode=export_mode)

            # separate模式返回多个文件的下载链接
            if export_mode == "separate" and isinstance(result, dict):
                download_links = {}
                for file_type, file_path in result.items():
                    if file_path and os.path.exists(file_path):
                        filename = os.path.basename(file_path)
                        download_links[file_type] = f"/api/viral/download/{filename}"
                return JSONResponse(content={
                    "status": "success",
                    "export_mode": "separate",
                    "files": download_links
                })

            # 其他模式返回单个文件
            if not os.path.exists(result):
                raise HTTPException(status_code=500, detail="Excel文件生成失败")

            from fastapi.responses import FileResponse
            return FileResponse(
                result,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=f"viral_analysis_latest.xlsx"
            )
        except Exception as e:
            logger.error(f"Excel导出失败: {e}")
            raise HTTPException(status_code=500, detail=f"Excel导出失败: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="不支持的导出格式")


@app.get("/api/viral/history")
async def get_history(username: str = Depends(verify_token_and_password_changed)):
    """
    获取历史分析记录（需要认证）

    Returns:
        历史记录列表（用户专属）
    """
    # 使用用户专属数据目录
    user_data = get_user_data_service(username)
    data_dir = user_data.get_analysis_dir()
    if not data_dir.exists():
        return []

    history = []
    for file in data_dir.glob("viral_notes_*.json"):
        try:
            with open(file, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # 格式化时间：20251109_141459 -> 2025-11-09 14:14
                collection_time = data.get("collection_time", "")
                formatted_time = ""
                if collection_time:
                    try:
                        # 解析格式：20251109_141459
                        if "_" in collection_time:
                            date_part, time_part = collection_time.split("_")
                            year = date_part[:4]
                            month = date_part[4:6]
                            day = date_part[6:8]
                            hour = time_part[:2]
                            minute = time_part[2:4]
                            formatted_time = f"{year}-{month}-{day} {hour}:{minute}"
                        else:
                            formatted_time = collection_time
                    except:
                        formatted_time = collection_time

                # 获取关键词
                # 优先从search_keyword字段读取（新数据）
                keyword = data.get("search_keyword", "")

                # 如果没有search_keyword，尝试从笔记标题提取（旧数据兼容）
                if not keyword:
                    notes = data.get("notes", [])
                    if notes and len(notes) > 0:
                        # 尝试从第一篇笔记的标题中提取可能的关键词
                        first_title = notes[0].get("title", "")
                        if first_title:
                            # 提取前10个字符作为关键词预览
                            keyword = first_title[:10] + ("..." if len(first_title) > 10 else "")
                    else:
                        keyword = "未知"

                history.append({
                    "filename": file.name,
                    "time": formatted_time or collection_time,
                    "total_notes": data.get("total_notes", 0),
                    "keyword": keyword
                })
        except Exception as e:
            logger.warning(f"读取历史记录失败 {file.name}: {e}")
            continue

    # 按时间倒序
    history.sort(key=lambda x: x.get("time", ""), reverse=True)

    # 只返回最近20条记录
    return history[:20]


@app.get("/api/viral/download/{filename}")
async def download_file(
    filename: str,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    下载已生成的文件（用于分开导出模式）

    Args:
        filename: 文件名

    Returns:
        文件下载（用户专属）
    """
    from fastapi.responses import FileResponse

    # 安全检查：防止路径遍历攻击
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")

    # 使用用户专属数据目录
    user_data = get_user_data_service(username)
    data_dir = user_data.get_analysis_dir()
    file_path = data_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {filename}")

    return FileResponse(
        str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename
    )


@app.get("/api/viral/export/{task_id}")
async def export_results(
    task_id: str,
    format: str = "excel",
    export_mode: str = "combined",
    username: str = Depends(verify_token_and_password_changed)
):
    """
    导出分析结果（需要认证）

    Args:
        format: 导出格式 (excel/json)
        export_mode: 导出模式 (combined/separate/image_only/video_only)
            - combined: 单个Excel（默认）
            - separate: 图文+视频两个独立Excel
            - image_only: 仅图文报告
            - video_only: 仅视频报告

    Returns:
        文件下载或文件列表JSON
    """
    # 如果task_id在内存中，验证归属权限
    if task_id in task_status:
        verify_task_ownership(task_id, username)
    else:
        # task_id不在内存中，尝试从文件系统查找（仅搜索当前用户目录，天然隔离）
        logger.warning(f"任务 {task_id} 不在内存中，尝试从文件系统查找...")

        # 使用用户专属数据目录
        user_data = get_user_data_service(username)
        data_dir = user_data.get_analysis_dir()
        if not data_dir.exists():
            raise HTTPException(status_code=404, detail="数据目录不存在")

        # 查找匹配的文件（优先分析结果，其次原始数据）
        analysis_pattern = f"viral_analysis_{task_id.replace('viral_', '')}.json"
        notes_pattern = f"viral_notes_{task_id.replace('viral_', '')}.json"

        analysis_file = data_dir / analysis_pattern
        notes_file = data_dir / notes_pattern

        target_file = None
        if analysis_file.exists():
            target_file = str(analysis_file)
        elif notes_file.exists():
            target_file = str(notes_file)
        else:
            raise HTTPException(
                status_code=404,
                detail=f"找不到任务数据文件。请确保采集任务已完成。查找文件: {analysis_pattern} 或 {notes_pattern}"
            )

        # 创建临时任务状态（必须标记归属用户，防止跨用户访问）
        task_status[task_id] = {
            "data_file": target_file if "notes" in str(target_file) else None,
            "analysis_file": target_file if "analysis" in str(target_file) else None,
            "username": username  # 标记为当前用户的任务
        }

    if format == "json":
        # 返回JSON文件
        analysis_file = task_status[task_id].get("analysis_file")
        if not analysis_file or not os.path.exists(analysis_file):
            raise HTTPException(status_code=404, detail="分析结果不存在")

        with open(analysis_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return JSONResponse(content=data)

    elif format == "excel":
        # 生成Excel报告
        from viral_agent.services.export.export_service import export_to_excel

        # 优先使用分析文件，如果没有则使用原始数据文件
        analysis_file = task_status[task_id].get("analysis_file")
        data_file = task_status[task_id].get("data_file")

        if not analysis_file and not data_file:
            raise HTTPException(status_code=404, detail="数据文件不存在")

        # 如果没有分析文件，使用原始数据文件
        target_file = analysis_file if analysis_file else data_file

        if not os.path.exists(target_file):
            raise HTTPException(status_code=404, detail=f"文件不存在: {target_file}")

        try:
            result = export_to_excel(target_file, export_mode=export_mode)

            # separate模式返回多个文件的下载链接
            if export_mode == "separate" and isinstance(result, dict):
                download_links = {}
                for file_type, file_path in result.items():
                    if file_path and os.path.exists(file_path):
                        filename = os.path.basename(file_path)
                        download_links[file_type] = f"/api/viral/download/{filename}"
                return JSONResponse(content={
                    "status": "success",
                    "export_mode": "separate",
                    "files": download_links
                })

            # 其他模式返回单个文件
            if not os.path.exists(result):
                raise HTTPException(status_code=500, detail="Excel文件生成失败")

            from fastapi.responses import FileResponse
            return FileResponse(
                result,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=f"viral_analysis_{task_id}.xlsx"
            )
        except Exception as e:
            logger.error(f"Excel导出失败: {e}")
            raise HTTPException(status_code=500, detail=f"Excel导出失败: {str(e)}")

    else:
        raise HTTPException(status_code=400, detail="不支持的导出格式")


# ==================== 后台任务 ====================

async def collect_viral_notes_task(
    task_id: str,
    keywords: List[str],
    target_count: int,
    viral_ratio: float,
    note_type: int,
    time_range: int,
    min_sample_count: int = 50,
    # 新增搜索模式参数
    search_mode: str = "ratio",
    min_interaction: Optional[int] = None,
    max_collect_count: int = 500,
    enable_ai_expansion: bool = True
):
    """
    后台任务：采集爆款笔记（支持多关键词、双模式、暂停/恢复）

    Args:
        task_id: 任务ID
        keywords: 搜索关键词列表
        target_count: 目标爬取数量（比例模式）
        viral_ratio: 爆款比例（比例模式）
        note_type: 笔记类型
        time_range: 时间范围
        min_sample_count: 最低样本量要求
        search_mode: 搜索模式（ratio=比例筛选 threshold=阈值筛选）
        min_interaction: 最低互动阈值（阈值模式）
        max_collect_count: 最大采集数量（阈值模式）
        enable_ai_expansion: 是否启用AI关键词扩展
    """
    # 获取任务管理器和控制信号
    manager = get_task_manager()
    signal = manager.get_signal(task_id)

    try:
        # 更新状态（双轨同步：task_status + TaskManager）
        task_status[task_id]["message"] = "正在初始化采集器..."
        add_task_log(task_id, "⚙️ 正在初始化采集器...", "info")
        manager.add_log(task_id, "⚙️ 正在初始化采集器...", "info")

        # 获取任务所属用户
        task_username = task_status[task_id].get("username", "admin")

        # 获取用户专属 Cookie
        user_data = get_user_data_service(task_username)
        cookies_str = user_data.get_cookie()

        if not cookies_str and is_admin(task_username):
            # 仅 admin 用户可回退到环境变量中的全局 Cookie
            cookies_str = os.getenv("COOKIE") or os.getenv("COOKIES") or ""
            cookies_str = cookies_str.strip().replace('\n', '').replace('\r', '')
            if cookies_str:
                add_task_log(task_id, "ℹ️ 使用环境变量中的全局Cookie", "info")

        if not cookies_str:
            add_task_log(task_id, "❌ Cookie未配置，无法采集", "error")
            raise ValueError("未配置Cookie，请在「设置」中输入您的小红书Cookie")

        # 创建采集器
        collector = ViralNoteCollector(cookies_str)

        # 注入控制信号（支持暂停/恢复/取消）
        if signal:
            collector.set_control_signal(signal)
            add_task_log(task_id, "🔗 已启用任务控制（支持暂停/恢复）", "info")

        # 检查是否有检查点需要恢复
        checkpoint = manager.get_checkpoint(task_id)
        if checkpoint:
            collector.set_checkpoint(checkpoint)
            add_task_log(
                task_id,
                f"♻️ 从检查点恢复：关键词 {checkpoint.keyword_index + 1}/{len(keywords)}",
                "info"
            )
            manager.add_log(task_id, f"♻️ 从检查点恢复", "info")

        add_task_log(task_id, "✅ 采集器初始化完成", "success")
        manager.add_log(task_id, "✅ 采集器初始化完成", "success")

        # 用于控制日志频率的计数器
        last_logged_count = [0]  # 使用列表以便在闭包中修改

        # 更新状态回调（支持解析当前关键词，同步 TaskManager）
        def update_progress(progress_or_collected, message=""):
            current_kw = ""

            # 解析当前关键词
            if message and "关键词" in message:
                import re
                match = re.search(r'关键词.*?:\s*(.+?)(?:\s|$)', message)
                if match:
                    current_kw = match.group(1)
                    if task_status[task_id].get("current_keyword") != current_kw:
                        task_status[task_id]["current_keyword"] = current_kw
                        add_task_log(task_id, f"🔄 正在采集关键词：{current_kw}", "info")

            # 更新进度（双轨同步）
            progress_val = 0
            if isinstance(progress_or_collected, int) and progress_or_collected <= 100:
                progress_val = progress_or_collected
                task_status[task_id]["progress"] = progress_val
            if message:
                task_status[task_id]["message"] = message

            # 计算平均互动数并记录采集进度日志
            collected_count = 0
            if hasattr(collector, 'collected_notes') and len(collector.collected_notes) > 0:
                collected_count = len(collector.collected_notes)
                total_interaction = sum(note.interaction_score for note in collector.collected_notes)
                avg_interaction = int(total_interaction / collected_count)
                if "statistics" not in task_status[task_id]:
                    task_status[task_id]["statistics"] = {}
                task_status[task_id]["statistics"]["avg_interaction"] = avg_interaction

                # 每采集20篇记录一次日志（避免日志过多）
                if collected_count >= last_logged_count[0] + 20:
                    last_logged_count[0] = collected_count
                    add_task_log(
                        task_id,
                        f"📈 已采集 {collected_count} 篇，平均互动 {avg_interaction:,}",
                        "info"
                    )

            # 同步到 TaskManager
            manager.update_progress(
                task_id,
                progress=progress_val,
                collected=collected_count,
                message=message or task_status[task_id].get("message", ""),
                current_keyword=current_kw
            )

        keyword_display = "、".join(keywords)
        add_task_log(task_id, f"🔍 开始搜索「{keyword_display}」相关笔记", "info")

        # 根据搜索模式选择不同的采集策略
        if search_mode == "threshold":
            # ==================== 阈值模式 ====================
            add_task_log(task_id, f"🔥 【阈值筛选模式】只采集互动 >= {min_interaction:,} 的笔记", "success")
            add_task_log(task_id, f"   最大采集数: {max_collect_count}, AI扩展: {'开启' if enable_ai_expansion else '关闭'}", "info")
            task_status[task_id]["message"] = f"阈值模式采集「{keyword_display}」..."

            # 使用统一入口进行阈值模式采集
            notes = await collector.search_viral_notes_unified(
                keywords=keywords,
                search_mode="threshold",
                min_interaction=min_interaction,
                max_collect_count=max_collect_count,
                note_type=note_type,
                time_range=time_range,
                progress_callback=update_progress
            )
        else:
            # ==================== 比例模式（现有逻辑） ====================
            add_task_log(task_id, f"📊 比例筛选模式：取前 {int(viral_ratio * 100)}%", "info")

            # 智能调整参数：确保 min_sample_count 不超过合理范围
            expected_analysis = int(target_count * viral_ratio)
            original_min_sample = min_sample_count

            if min_sample_count > target_count:
                min_sample_count = target_count
                add_task_log(task_id, f"📊 智能调整: 最低样本量 {original_min_sample} → {min_sample_count} (不超过目标数量)", "info")
            elif min_sample_count > expected_analysis:
                min_sample_count = max(expected_analysis, 10)  # 至少保留10条
                add_task_log(task_id, f"📊 智能调整: 最低样本量 {original_min_sample} → {min_sample_count} (适配预估分析量)", "info")

            # 判断使用单关键词还是多关键词采集
            if len(keywords) == 1:
                # 单关键词模式（向后兼容）
                task_status[task_id]["message"] = f"开始采集「{keywords[0]}」相关笔记..."
                add_task_log(task_id, f"📝 单关键词模式：{keywords[0]}", "info")
                notes = await collector.search_viral_notes(
                    query=keywords[0],
                    target_count=target_count,
                    viral_ratio=viral_ratio,
                    note_type=note_type,
                    time_range=time_range,
                    progress_callback=update_progress
                )
            else:
                # 多关键词模式
                task_status[task_id]["message"] = f"开始多关键词采集「{keyword_display}」..."
                add_task_log(task_id, f"📝 多关键词模式：共 {len(keywords)} 个关键词", "info")
                notes = await collector.search_viral_notes_multi_keywords(
                    keywords=keywords,
                    target_count=target_count,
                    viral_ratio=viral_ratio,
                    note_type=note_type,
                    time_range=time_range,
                    min_sample_count=min_sample_count,
                    progress_callback=update_progress
                )

        # 检查是否被暂停或取消
        if signal:
            if signal.is_cancelled:
                # 任务被取消
                manager.confirm_cancelled(task_id)
                task_status[task_id].update({
                    "status": "cancelled",
                    "message": "任务已取消",
                    "end_time": datetime.now().isoformat()
                })
                logger.info(f"任务 {task_id} 已取消")
                return  # 提前返回

            if signal.is_paused:
                # 任务被暂停，保存检查点
                checkpoint = collector.get_checkpoint()
                if checkpoint:
                    manager.confirm_paused(task_id, checkpoint)
                    task_status[task_id].update({
                        "status": "paused",
                        "message": f"任务已暂停 (关键词 {checkpoint.keyword_index + 1}/{len(keywords)})",
                        "paused_at": datetime.now().isoformat()
                    })
                    logger.info(f"任务 {task_id} 已暂停，检查点已保存")
                return  # 提前返回，等待恢复

        # 保存数据（使用用户专属目录）
        task_status[task_id]["message"] = "正在保存数据..."
        add_task_log(task_id, f"📊 采集完成，共获取 {len(notes)} 篇笔记", "success")
        add_task_log(task_id, "💾 正在保存数据...", "info")
        # 获取用户专属数据目录
        task_username = task_status[task_id].get("username", "admin")
        user_data = get_user_data_service(task_username)
        user_analysis_dir = str(user_data.get_analysis_dir())
        data_file = collector.save_collected_notes(output_dir=user_analysis_dir)
        add_task_log(task_id, "✅ 数据保存完成", "success")

        # 获取统计信息
        statistics = collector.get_statistics()

        # 添加多关键词统计
        if hasattr(collector, 'keyword_stats'):
            statistics['keyword_distribution'] = collector.keyword_stats
            statistics['multi_match_count'] = getattr(collector, 'multi_match_count', 0)

        # 检查样本量是否充足，不足则自动补采
        resupply_result: Optional[ResupplyResult] = None
        sample_warning = None

        # 根据模式确定最低样本量
        effective_min_sample = min_sample_count
        if search_mode == "threshold":
            # 阈值模式：使用配置的 min_sample_count 或默认值
            effective_min_sample = min_sample_count

        if len(notes) < effective_min_sample:
            logger.info(f"📊 样本量不足 ({len(notes)} < {effective_min_sample})，启动自动补采...")
            task_status[task_id]["message"] = "样本量不足，正在自动补采..."
            task_status[task_id]["progress"] = 87
            add_task_log(task_id, f"⚠️ 样本量不足 ({len(notes)} < {effective_min_sample})", "warning")
            add_task_log(task_id, "🔄 启动自动补采...", "info")

            resupply_service = AutoResupplyService()

            # 获取筛选前的全部笔记（用于阶段1零成本补采）
            all_notes_before_filter = getattr(
                collector, '_all_notes_before_filter', notes
            )

            # 根据搜索模式选择补采策略
            if search_mode == "threshold":
                # 阈值模式：始终走带阈值过滤的路径（无论是否启用AI扩展）
                if enable_ai_expansion:
                    add_task_log(task_id, "🤖 启用 AI 关键词扩展补采", "info")
                else:
                    add_task_log(task_id, "🔥 阈值模式补采（未启用AI扩展）", "info")

                notes, resupply_result = await resupply_service.execute_resupply_with_expansion(
                    all_notes_before_filter=all_notes_before_filter,
                    current_viral_ratio=viral_ratio,
                    min_sample_count=effective_min_sample,
                    collector=collector,
                    keywords=keywords,
                    original_target_count=target_count,
                    note_type=note_type,
                    time_range=time_range,
                    search_mode=search_mode,
                    min_interaction=min_interaction,
                    enable_ai_expansion=enable_ai_expansion,  # 即使为 False 也走这个路径以保证阈值过滤
                    progress_callback=lambda p, m: task_status[task_id].update({
                        "progress": p, "message": m
                    })
                )
            else:
                # 比例模式：使用原有补采逻辑
                notes, resupply_result = await resupply_service.execute_resupply(
                    all_notes_before_filter=all_notes_before_filter,
                    current_viral_ratio=viral_ratio,
                    min_sample_count=effective_min_sample,
                    collector=collector,
                    keywords=keywords,
                    original_target_count=target_count,
                    note_type=note_type,
                    time_range=time_range,
                    progress_callback=lambda p, m: task_status[task_id].update({
                        "progress": p, "message": m
                    })
                )

            # 补采后需要重新保存数据（使用用户专属目录）
            collector.collected_notes = notes
            data_file = collector.save_collected_notes(output_dir=user_analysis_dir)
            statistics = collector.get_statistics()

            # 重新附加多关键词统计（补采可能改变了笔记列表）
            if hasattr(collector, 'keyword_stats'):
                statistics['keyword_distribution'] = collector.keyword_stats
                statistics['multi_match_count'] = getattr(collector, 'multi_match_count', 0)

            if resupply_result.success:
                logger.success(f"✅ 自动补采成功: {resupply_result.message}")
                sample_warning = f"✅ 通过自动补采达到 {len(notes)} 篇: {resupply_result.message}"
                add_task_log(task_id, f"✅ 补采成功，现有 {len(notes)} 篇", "success")
            else:
                logger.warning(f"⚠️ 自动补采完成但仍不足: {resupply_result.message}")
                sample_warning = f"⚠️ {resupply_result.message}"
                add_task_log(task_id, f"⚠️ 补采后仍不足：{len(notes)} 篇", "warning")

        # 更新最终状态（清空 current_keyword 表示全部完成）
        task_status[task_id].update({
            "status": "completed",
            "progress": 100,
            "collected": len(notes),
            "current_keyword": "",  # 清空，表示全部关键词采集完成
            "message": f"成功采集 {len(notes)} 篇爆款笔记",
            "data_file": data_file,
            "end_time": datetime.now().isoformat(),
            "statistics": statistics,
            "sample_warning": sample_warning,
            "resupply_info": {
                "executed": resupply_result is not None,
                "phases": resupply_result.phases_executed if resupply_result else [],
                "success": resupply_result.success if resupply_result else None,
                "extra_collected": resupply_result.extra_collected if resupply_result else 0
            } if resupply_result else None
        })

        logger.success(f"任务 {task_id} 完成，采集 {len(notes)} 篇爆款笔记")
        add_task_log(task_id, f"🎉 采集任务完成！共 {len(notes)} 篇笔记", "success")

        # 同步到 TaskManager（标记采集完成）
        manager.mark_completed(task_id, data_file, statistics)
        if sample_warning:
            manager.set_sample_warning(task_id, sample_warning)
            logger.info(sample_warning)

    except Exception as e:
        logger.error(f"任务 {task_id} 失败: {e}")
        add_task_log(task_id, f"❌ 任务失败: {str(e)}", "error")
        task_status[task_id].update({
            "status": "failed",
            "message": f"任务失败: {str(e)}",
            "error": str(e),
            "end_time": datetime.now().isoformat()
        })
        # 同步到 TaskManager
        manager.mark_failed(task_id, str(e))


async def analyze_viral_notes_task(
    task_id: str,
    data_file: str,
    use_ai: bool,
    analysis_type: str,
    video_source_mode: Optional[str]
):
    """
    后台任务：分析爆款笔记（支持实时日志）

    Args:
        task_id: 任务ID（复用采集时的ID）
        data_file: 采集数据文件路径
        use_ai: 是否启用AI分析
        analysis_type: 分析类型（image/video/all）
        video_source_mode: 视频源模式
    """
    # 获取任务管理器和控制信号
    manager = get_task_manager()
    signal = manager.get_signal(task_id)

    try:
        # 在分析开始前检查是否已被取消
        if signal and signal.is_cancelled:
            manager.confirm_cancelled(task_id)
            task_status[task_id].update({
                "status": "cancelled",
                "message": "任务已取消",
                "end_time": datetime.now().isoformat()
            })
            logger.info(f"分析任务 {task_id} 启动前已被取消")
            return

        # 标记开始分析（同步 TaskManager）
        manager.mark_analyzing(task_id)

        # 加载数据
        add_task_log(task_id, "📂 正在加载采集数据...", "info")
        with open(data_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 转换为ViralNote对象
        notes = [ViralNote.from_spider_data(note) for note in data['notes']]
        add_task_log(task_id, f"✅ 已加载 {len(notes)} 篇笔记", "success")

        # 统计笔记类型
        video_count = sum(1 for n in notes if n.note_type == '视频')
        image_count = len(notes) - video_count
        add_task_log(task_id, f"📊 笔记类型分布: 图文 {image_count} 篇, 视频 {video_count} 篇", "info")

        # 创建分析器
        add_task_log(task_id, "⚙️ 初始化AI分析器...", "info")
        task_status[task_id]["analysis_progress"] = 10
        if use_ai:
            analyzer = ViralAnalyzer(api_key="auto")
        else:
            analyzer = ViralAnalyzer(api_key=None)
        add_task_log(task_id, "✅ 分析器初始化完成", "success")

        # 开始分析（细分步骤由回调报告，避免重复日志）
        task_status[task_id]["analysis_progress"] = 15
        analysis_desc = []
        if analysis_type in ['image', 'all'] and image_count > 0:
            analysis_desc.append(f"图文 {image_count} 篇")
        if analysis_type in ['video', 'all'] and video_count > 0:
            analysis_desc.append(f"视频 {video_count} 篇")
        desc_text = ', '.join(analysis_desc) if analysis_desc else "全部笔记"
        add_task_log(task_id, f"🚀 开始AI深度分析（{desc_text}）...", "info")

        # 定义进度回调函数，将分析器内部进度同步到任务日志
        def analysis_progress_callback(message: str, progress: int = None):
            """分析进度回调：将内部日志同步到任务状态"""
            add_task_log(task_id, message, "info")
            if progress is not None:
                task_status[task_id]["analysis_progress"] = progress

        # 定义取消检查函数，让分析器能感知取消信号
        def check_cancelled() -> bool:
            return signal is not None and signal.is_cancelled

        # 执行完整分析（带进度回调和取消检查）
        # 使用 asyncio.to_thread 卸载到线程池，避免同步 AI 调用阻塞事件循环
        # 这样多任务可以并行分析，取消信号也能及时响应
        result = await asyncio.to_thread(
            analyzer.analyze_viral_notes,
            notes=notes,
            keyword=task_status[task_id]["keyword"],
            threshold=data.get('statistics', {}).get('viral_threshold', 5000),
            analysis_type=analysis_type,
            video_source_mode=video_source_mode,
            progress_callback=analysis_progress_callback,
            cancel_check=check_cancelled
        )

        task_status[task_id]["analysis_progress"] = 88
        add_task_log(task_id, "✅ AI分析完成", "success")

        # 检查视频分析结果（日志已由回调处理，这里只做额外检查）
        video_ai_insights = result.viral_model.get('video_ai_insights', {})
        if video_ai_insights.get('status') == 'success':
            pass  # 成功日志已由回调处理
        elif video_ai_insights.get('status') == 'skipped':
            add_task_log(task_id, "📷 图文模式跳过视频分析", "info")

        # 将原始笔记数据添加到分析结果中
        result.notes = [note.to_dict() for note in notes]

        # 保存分析结果
        task_status[task_id]["analysis_progress"] = 90
        add_task_log(task_id, "💾 正在保存分析结果...", "info")
        analysis_file = data_file.replace('viral_notes', 'viral_analysis')
        result.save_to_file(analysis_file)
        add_task_log(task_id, "✅ 分析结果已保存", "success")

        # 更新最终状态
        task_status[task_id].update({
            "status": "analyzed",
            "analysis_progress": 100,
            "analysis_completed": True,
            "analysis_file": analysis_file,
            "analysis_end_time": datetime.now().isoformat(),
            "analysis_summary": {
                "total_notes": result.total_notes,
                "keyword": result.keyword,
                "model_generated": bool(result.viral_model)
            }
        })

        add_task_log(task_id, f"🎉 分析完成！共分析 {result.total_notes} 篇笔记", "success")
        add_task_log(task_id, "💡 可以点击「导出报告」下载Excel分析报告", "info")
        logger.success(f"分析任务 {task_id} 完成")

        # 同步到 TaskManager
        manager.mark_analyzed(task_id, analysis_file)

    except AnalysisCancelled:
        # 分析过程中被用户取消
        logger.info(f"分析任务 {task_id} 已被用户取消")
        add_task_log(task_id, "🛑 分析已取消", "warning")
        manager.confirm_cancelled(task_id)
        task_status[task_id].update({
            "status": "cancelled",
            "message": "任务已取消",
            "end_time": datetime.now().isoformat()
        })

    except Exception as e:
        logger.error(f"分析任务 {task_id} 失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        add_task_log(task_id, f"❌ 分析失败: {str(e)}", "error")
        task_status[task_id].update({
            "status": "analysis_failed",
            "analysis_error": str(e),
            "analysis_end_time": datetime.now().isoformat()
        })
        # 同步到 TaskManager
        manager.mark_analysis_failed(task_id, str(e))


# ==================== 知识库管理API ====================

@app.get("/api/knowledge/domains")
async def get_domains(username: str = Depends(verify_token_and_password_changed)):
    """获取所有领域列表（需要认证）"""
    try:
        config = get_knowledge_config()
        domains = config.get_all_domains()
        return {
            "status": "success",
            "count": len(domains),
            "domains": domains
        }
    except Exception as e:
        logger.error(f"获取领域列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取失败: {str(e)}")


@app.get("/api/knowledge/domains/{domain_id}")
async def get_domain(domain_id: str, username: str = Depends(verify_token_and_password_changed)):
    """获取单个领域详情（需要认证）"""
    try:
        config = get_knowledge_config()
        domain = config.get_domain_by_id(domain_id)

        if not domain:
            raise HTTPException(status_code=404, detail=f"领域不存在: {domain_id}")

        return {
            "status": "success",
            "domain": domain
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取领域详情失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取失败: {str(e)}")


@app.post("/api/knowledge/domains")
async def create_domain(request: DomainCreateRequest, username: str = Depends(verify_token_and_password_changed)):
    """创建新领域（需要认证）"""
    try:
        config = get_knowledge_config()

        # 构建领域数据
        domain_data = {
            "id": request.id,
            "name": request.name,
            "enabled": request.enabled,
            "priority": request.priority,
            "keywords": request.keywords,
            "knowledge": {
                "problems": request.problems,
                "intro_ways": request.intro_ways,
                "embed_ways": request.embed_ways,
                "examples": request.examples
            }
        }

        # 添加领域
        config.add_domain(domain_data)

        return {
            "status": "success",
            "message": f"领域 {request.name} 创建成功",
            "domain_id": request.id
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"创建领域失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建失败: {str(e)}")


@app.put("/api/knowledge/domains/{domain_id}")
async def update_domain(domain_id: str, request: DomainUpdateRequest, username: str = Depends(verify_token_and_password_changed)):
    """更新领域（需要认证）"""
    try:
        config = get_knowledge_config()

        # 获取现有领域
        existing_domain = config.get_domain_by_id(domain_id)
        if not existing_domain:
            raise HTTPException(status_code=404, detail=f"领域不存在: {domain_id}")

        # 构建更新后的数据（只更新提供的字段）
        updated_domain = existing_domain.copy()

        if request.name is not None:
            updated_domain['name'] = request.name
        if request.enabled is not None:
            updated_domain['enabled'] = request.enabled
        if request.priority is not None:
            updated_domain['priority'] = request.priority
        if request.keywords is not None:
            updated_domain['keywords'] = request.keywords

        # 更新knowledge字段
        if any([request.problems, request.intro_ways, request.embed_ways, request.examples]):
            knowledge = updated_domain.get('knowledge', {})
            if request.problems is not None:
                knowledge['problems'] = request.problems
            if request.intro_ways is not None:
                knowledge['intro_ways'] = request.intro_ways
            if request.embed_ways is not None:
                knowledge['embed_ways'] = request.embed_ways
            if request.examples is not None:
                knowledge['examples'] = request.examples
            updated_domain['knowledge'] = knowledge

        # 执行更新
        config.update_domain(domain_id, updated_domain)

        return {
            "status": "success",
            "message": f"领域 {domain_id} 更新成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新领域失败: {e}")
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")


@app.delete("/api/knowledge/domains/{domain_id}")
async def delete_domain(domain_id: str, username: str = Depends(verify_token_and_password_changed)):
    """删除领域（需要认证）"""
    try:
        config = get_knowledge_config()

        if not config.delete_domain(domain_id):
            raise HTTPException(status_code=404, detail=f"领域不存在: {domain_id}")

        return {
            "status": "success",
            "message": f"领域 {domain_id} 删除成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除领域失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.post("/api/knowledge/domains/{domain_id}/keywords")
async def add_keyword(domain_id: str, request: KeywordRequest, username: str = Depends(verify_token_and_password_changed)):
    """为领域添加关键词（需要认证）"""
    try:
        config = get_knowledge_config()

        if not config.add_keyword(domain_id, request.keyword):
            raise HTTPException(status_code=400, detail="关键词已存在或领域不存在")

        return {
            "status": "success",
            "message": f"关键词 '{request.keyword}' 添加成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"添加关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"添加失败: {str(e)}")


@app.delete("/api/knowledge/domains/{domain_id}/keywords/{keyword}")
async def remove_keyword(domain_id: str, keyword: str, username: str = Depends(verify_token_and_password_changed)):
    """删除领域关键词（需要认证）"""
    try:
        config = get_knowledge_config()

        if not config.remove_keyword(domain_id, keyword):
            raise HTTPException(status_code=404, detail="关键词不存在或领域不存在")

        return {
            "status": "success",
            "message": f"关键词 '{keyword}' 删除成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除关键词失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.post("/api/knowledge/reload")
async def reload_config(username: str = Depends(verify_token_and_password_changed)):
    """重新加载知识库配置（需要认证）"""
    try:
        reload_knowledge_config()
        return {
            "status": "success",
            "message": "知识库配置已重新加载"
        }
    except Exception as e:
        logger.error(f"重新加载配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"重新加载失败: {str(e)}")


@app.post("/api/knowledge/test-detection")
async def test_detection(request: TestDetectionRequest, username: str = Depends(verify_token_and_password_changed)):
    """测试领域检测（需要认证）"""
    try:
        config = get_knowledge_config()
        domains = config.detect_domain(request.title, request.description)

        # 获取检测到的领域详情
        domain_details = []
        for domain_id in domains:
            domain = config.get_domain_by_id(domain_id)
            if domain:
                domain_details.append({
                    "id": domain_id,
                    "name": domain['name'],
                    "keywords": domain['keywords']
                })

        return {
            "status": "success",
            "input": {
                "title": request.title,
                "description": request.description
            },
            "detected_domains": domain_details,
            "count": len(domain_details)
        }
    except Exception as e:
        logger.error(f"测试检测失败: {e}")
        raise HTTPException(status_code=500, detail=f"测试失败: {str(e)}")


@app.get("/api/knowledge/export")
async def export_config(username: str = Depends(verify_token_and_password_changed)):
    """导出知识库配置（需要认证）"""
    try:
        config = get_knowledge_config()
        config_data = config.export_config()

        return JSONResponse(
            content=config_data,
            headers={
                "Content-Disposition": "attachment; filename=knowledge_base.json"
            }
        )
    except Exception as e:
        logger.error(f"导出配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"导出失败: {str(e)}")


@app.post("/api/knowledge/import")
async def import_config(config_data: dict, username: str = Depends(verify_token_and_password_changed)):
    """导入知识库配置（需要认证）"""
    try:
        config = get_knowledge_config()
        config.import_config(config_data, validate=True)

        return {
            "status": "success",
            "message": "配置导入成功"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"配置格式错误: {str(e)}")
    except Exception as e:
        logger.error(f"导入配置失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


# ==================== 文档管理API（RAG） ====================

# 文件上传限制常量
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.md', '.txt'}


def validate_doc_id(doc_id: str) -> bool:
    """验证文档 ID 格式：必须是 doc_ + 12位十六进制"""
    return bool(re.match(r"^doc_[a-f0-9]{12}$", doc_id))


def safe_file_path(storage_dir: Path, filename: str) -> Path:
    """安全地构建文件路径，防止目录遍历"""
    file_path = (storage_dir / filename).resolve()
    # 确保解析后的路径仍在 storage_dir 下
    if not str(file_path).startswith(str(storage_dir.resolve())):
        raise ValueError("非法文件路径")
    return file_path


@app.post("/api/documents/upload")
async def upload_document(request: Request, username: str = Depends(verify_token_and_password_changed)):
    """
    上传知识文档并建立RAG索引（需要认证）

    安全特性:
    - 文件类型白名单验证
    - 文件大小限制（50MB）
    - 分块写入避免内存溢出
    """
    import shutil

    try:
        # 获取表单数据
        form = await request.form()
        file = form.get("file")
        title = form.get("title", "未命名文档")
        domains_str = form.get("domains", "[]")
        description = form.get("description", "")

        # 解析领域列表
        try:
            domains = json.loads(domains_str)
        except:
            domains = []

        if not file:
            raise HTTPException(status_code=400, detail="未提供文件")

        # 1. 检查文件扩展名
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式，仅支持: {', '.join(ALLOWED_EXTENSIONS)}"
            )

        # 2. 检查文件大小（不读取全部内容到内存）
        file.file.seek(0, 2)  # 移动到文件末尾
        file_size = file.file.tell()  # 获取当前位置即文件大小
        file.file.seek(0)  # 重置到开头

        if file_size > MAX_UPLOAD_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大（{file_size // 1024 // 1024}MB），最大支持 {MAX_UPLOAD_SIZE // 1024 // 1024}MB"
            )

        # 3. 生成安全的文档 ID
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"

        # 4. 安全保存文件（边写边计数，二次验证大小）
        storage_dir = Path("viral_agent/storage/documents")
        storage_dir.mkdir(parents=True, exist_ok=True)
        file_path = storage_dir / f"{doc_id}{file_ext}"

        written_size = 0
        chunk_size = 1024 * 1024  # 1MB chunks

        with open(file_path, "wb") as buffer:
            while True:
                chunk = file.file.read(chunk_size)
                if not chunk:
                    break
                written_size += len(chunk)
                if written_size > MAX_UPLOAD_SIZE:
                    # 超出大小，删除已写入的文件
                    buffer.close()
                    file_path.unlink()
                    raise HTTPException(status_code=413, detail="文件过大")
                buffer.write(chunk)

        logger.info(f"文件已保存: {file_path} ({written_size} bytes)")

        # 解析文档
        parser = DocumentParser()
        parsed_data = parser.parse_file(str(file_path))

        # 提取关键词
        keywords = parser.extract_keywords(parsed_data['text'], top_k=10)

        # 创建文档对象
        doc = KnowledgeDocument(
            doc_id=doc_id,
            title=title,
            description=description,
            domains=domains,
            content=parsed_data['text'],
            metadata=DocumentMetadata(
                filename=file.filename,
                format=parsed_data['metadata']['format'],
                word_count=parsed_data['metadata']['word_count'],
                pages=parsed_data['metadata'].get('pages'),
                title=parsed_data['metadata'].get('title', title),
                file_size=file_path.stat().st_size
            ),
            keywords=keywords
        )

        # 存入RAG向量库
        rag_service = RAGService()
        success = rag_service.add_document(doc)

        if not success:
            raise HTTPException(status_code=500, detail="文档索引失败")

        logger.success(f"文档上传成功: {title} (ID: {doc_id})")

        return {
            "status": "success",
            "doc_id": doc_id,
            "message": f"文档《{title}》已上传并建立索引",
            "metadata": {
                "filename": file.filename,
                "format": doc.metadata.format,
                "word_count": doc.metadata.word_count,
                "chunks": len(doc.chunks)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"上传文档失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@app.get("/api/documents")
async def list_documents(domain: Optional[str] = None, username: str = Depends(verify_token_and_password_changed)):
    """列出所有知识文档（需要认证）"""
    try:
        rag_service = RAGService()
        documents = rag_service.list_all_documents(domain_filter=domain)

        return {
            "status": "success",
            "count": len(documents),
            "documents": documents
        }
    except Exception as e:
        logger.error(f"获取文档列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取失败: {str(e)}")


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str, username: str = Depends(verify_token_and_password_changed)):
    """
    删除知识文档（需要认证）

    安全特性:
    - doc_id 格式验证（防止通配符攻击）
    - Path.resolve() 路径校验（防止目录遍历）
    """
    try:
        # 1. 验证 doc_id 格式
        if not validate_doc_id(doc_id):
            raise HTTPException(status_code=400, detail="无效的文档 ID 格式")

        # 2. 使用精确匹配而非 glob（防止通配符攻击）
        storage_dir = Path("viral_agent/storage/documents").resolve()
        deleted = False

        for ext in ALLOWED_EXTENSIONS:
            try:
                file_path = safe_file_path(storage_dir, f"{doc_id}{ext}")
                if file_path.exists():
                    file_path.unlink()
                    logger.info(f"已删除文件: {file_path}")
                    deleted = True
                    break
            except ValueError:
                raise HTTPException(status_code=400, detail="非法文件路径")

        # 3. 删除向量库中的记录
        rag_service = RAGService()
        rag_service.delete_document(doc_id)

        if not deleted:
            # 即使文件不存在，也尝试删除向量库记录（可能只有索引没有文件）
            logger.warning(f"文档文件不存在，但已尝试清理向量库: {doc_id}")

        return {
            "status": "success",
            "message": f"文档 {doc_id} 已删除"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除文档失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.post("/api/documents/search")
async def search_documents(
    query: str,
    domains: Optional[List[str]] = None,
    top_k: int = 5,
    username: str = Depends(verify_token_and_password_changed)
):
    """搜索知识文档（需要认证）"""
    try:
        retriever = UnifiedKnowledgeRetriever()
        results = retriever.search_documents(query=query, domains=domains, top_k=top_k)

        return {
            "status": "success",
            "query": query,
            "count": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"搜索文档失败: {e}")
        raise HTTPException(status_code=500, detail=f"搜索失败: {str(e)}")


@app.get("/api/knowledge/summary")
async def get_knowledge_summary(username: str = Depends(verify_token_and_password_changed)):
    """获取知识库摘要信息（需要认证）"""
    try:
        retriever = UnifiedKnowledgeRetriever()
        summary = retriever.get_knowledge_summary()

        return {
            "status": "success",
            **summary
        }
    except Exception as e:
        logger.error(f"获取知识库摘要失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取失败: {str(e)}")


# ==================== 系统清理API ====================

@app.get("/api/cleanup/info")
async def get_cleanup_info(username: str = Depends(verify_token_and_password_changed)):
    """
    获取清理类别信息（需要认证）

    Returns:
        各类别的文件数量和大小
    """
    try:
        service = CleanupService(username=username)
        info = service.get_category_info()
        total = service.get_total_cache_size()

        return {
            "status": "success",
            "categories": info,
            "total_size_mb": total['total_mb']
        }
    except Exception as e:
        logger.error(f"获取清理信息失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取失败: {str(e)}")


@app.post("/api/cleanup")
async def cleanup_history(
    request: CleanupRequest,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    清理历史数据（需要认证）

    Args:
        request: 清理请求，包含要清理的类别和是否预览

    Returns:
        清理结果汇总
    """
    try:
        logger.info(f"清理请求: categories={request.categories}, dry_run={request.dry_run}")
        service = CleanupService(username=username)
        logger.info(f"CleanupService base_path: {service.base_path}")

        # 验证类别
        valid_categories = set(service.CATEGORIES.keys())
        invalid = set(request.categories) - valid_categories
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"无效的类别: {', '.join(invalid)}。有效类别: {', '.join(valid_categories)}"
            )

        # 执行清理
        summary = service.cleanup_all(
            categories=request.categories,
            dry_run=request.dry_run
        )

        # 转换结果
        results = []
        for r in summary.results:
            results.append({
                "category": r.category,
                "name": service.CATEGORIES.get(r.category, {}).get('name', r.category),
                "files_deleted": r.files_deleted,
                "size_freed_mb": r.size_freed_mb,
                "success": r.success,
                "error": r.error
            })

        action = "预览" if request.dry_run else "已清理"
        logger.info(f"{action}: {summary.total_files} 文件, {summary.total_size_mb:.2f} MB")

        return {
            "status": "success",
            "dry_run": request.dry_run,
            "message": f"{action} {summary.total_files} 个文件，共 {summary.total_size_mb:.2f} MB",
            "results": results,
            "total_files": summary.total_files,
            "total_size_mb": summary.total_size_mb,
            "timestamp": summary.timestamp
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"清理失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


@app.delete("/api/cleanup/all")
async def cleanup_all_data(
    dry_run: bool = True,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    清理全部历史数据（需要认证，危险操作）

    Args:
        dry_run: 是否仅预览，默认为True（安全模式）

    Returns:
        清理结果汇总
    """
    try:
        service = CleanupService(username=username)

        # 执行清理所有类别
        summary = service.cleanup_all(dry_run=dry_run)

        action = "预览" if dry_run else "已清理"
        logger.warning(f"全量清理 {action}: {summary.total_files} 文件, {summary.total_size_mb:.2f} MB")

        return {
            "status": "success",
            "dry_run": dry_run,
            "message": f"{action}全部历史数据: {summary.total_files} 个文件，共 {summary.total_size_mb:.2f} MB",
            "total_files": summary.total_files,
            "total_size_mb": summary.total_size_mb,
            "timestamp": summary.timestamp
        }
    except Exception as e:
        logger.error(f"全量清理失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


# ==================== 扫码登录API ====================

@app.get("/api/qrcode/status")
async def check_qrcode_feature(force: bool = False):
    """
    检查扫码登录功能是否可用

    Args:
        force: 是否强制重新检测

    Returns:
        available: 功能是否可用
        message: 状态说明
    """
    try:
        from viral_agent.services.auth import get_qrcode_login_service
        service = get_qrcode_login_service()
        available = await service.check_playwright_available(force_recheck=force)
        return {
            "available": available,
            "message": "扫码登录功能已启用" if available else "扫码功能未启用，请安装: pip install playwright && playwright install chromium"
        }
    except Exception as e:
        return {
            "available": False,
            "message": f"扫码功能不可用: {str(e)}"
        }


@app.post("/api/qrcode/session")
async def create_qrcode_session(
    username: str = Depends(verify_token_and_password_changed)
):
    """
    创建扫码登录会话

    使用 Playwright 浏览器获取二维码（优化版，约 10-15 秒）。
    创建成功后需轮询 /api/qrcode/{session_id}/status 获取状态。

    Returns:
        session_id: 会话ID，用于后续状态查询
        status: 当前状态
    """
    try:
        from viral_agent.services.auth import get_qrcode_login_service
        service = get_qrcode_login_service()
        session = await service.create_session(username)

        return {
            "session_id": session.session_id,
            "status": session.status.value,
            "message": "正在启动浏览器获取二维码..."
        }
    except RuntimeError as e:
        # 功能不可用或并发超限
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"创建扫码会话失败: {e}")
        raise HTTPException(status_code=500, detail=f"创建会话失败: {str(e)}")


@app.get("/api/qrcode/{session_id}/status")
async def get_qrcode_session_status(
    session_id: str,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    获取扫码登录状态

    前端应每1-2秒轮询此接口。

    Returns:
        status: 当前状态 (initializing/waiting_scan/scanned/confirmed/success/expired/error)
        qrcode_base64: 二维码图片base64（仅在waiting_scan状态返回）
        expires_at: 二维码过期时间
        message: 错误信息（如有）
    """
    try:
        from viral_agent.services.auth import get_qrcode_login_service
        service = get_qrcode_login_service()
        session = await service.get_session(session_id)

        if not session:
            raise HTTPException(status_code=404, detail="会话不存在或已过期")

        # 验证会话归属
        if session.username != username:
            raise HTTPException(status_code=403, detail="无权访问此会话")

        response = {
            "session_id": session.session_id,
            "status": session.status.value,
            "expires_at": session.expires_at.isoformat() if session.expires_at else None,
            "message": session.error_message,
            "is_completed": session.is_completed,
        }

        # 在等待扫码和需要短信验证码状态下返回截图
        from viral_agent.services.auth import QRLoginStatus
        if session.status in (QRLoginStatus.WAITING_SCAN, QRLoginStatus.NEED_SMS_CODE):
            response["qrcode_base64"] = session.qrcode_base64

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取扫码状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取状态失败: {str(e)}")


@app.delete("/api/qrcode/{session_id}")
async def cancel_qrcode_session(
    session_id: str,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    取消扫码登录会话

    释放会话资源。
    """
    try:
        from viral_agent.services.auth import get_qrcode_login_service
        service = get_qrcode_login_service()
        session = await service.get_session(session_id)

        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        if session.username != username:
            raise HTTPException(status_code=403, detail="无权操作此会话")

        await service.cancel_session(session_id)
        return {"status": "success", "message": "会话已取消"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消扫码会话失败: {e}")
        raise HTTPException(status_code=500, detail=f"取消失败: {str(e)}")


@app.post("/api/qrcode/{session_id}/sms")
async def submit_qrcode_sms_code(
    session_id: str,
    request: Request,
    username: str = Depends(verify_token_and_password_changed)
):
    """
    提交短信验证码

    当扫码后需要短信验证时，通过此接口提交验证码。
    """
    try:
        data = await request.json()
        sms_code = data.get("sms_code", "").strip()

        if not sms_code:
            raise HTTPException(status_code=400, detail="验证码不能为空")

        if not sms_code.isdigit() or len(sms_code) < 4:
            raise HTTPException(status_code=400, detail="验证码格式不正确")

        from viral_agent.services.auth import get_qrcode_login_service
        service = get_qrcode_login_service()
        session = await service.get_session(session_id)

        if not session:
            raise HTTPException(status_code=404, detail="会话不存在或已过期")

        if session.username != username:
            raise HTTPException(status_code=403, detail="无权操作此会话")

        success = await service.submit_sms_code(session_id, sms_code)

        if success:
            return {"status": "success", "message": "验证码已提交"}
        else:
            raise HTTPException(status_code=400, detail="提交验证码失败，请重试")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"提交短信验证码失败: {e}")
        raise HTTPException(status_code=500, detail=f"提交失败: {str(e)}")


# ==================== 健康检查 ====================

@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "active_tasks": len([t for t in task_status.values() if t.get("status") == "running"])
    }


# ==================== 启动应用 ====================

if __name__ == "__main__":
    import uvicorn

    # 确保必要的目录存在（使用 UserDataService 创建默认用户目录）
    default_user_data = get_user_data_service()  # 默认用户 admin
    os.makedirs("web/static", exist_ok=True)
    os.makedirs("web/templates", exist_ok=True)

    # 启动服务
    # 生产环境：设置 PRODUCTION=true 禁用热重载
    is_production = os.getenv("PRODUCTION", "false").lower() == "true"
    logger.info(f"启动小红书爆文Agent服务... (生产模式: {is_production})")

    # Windows 兼容性配置
    uvicorn_config = {
        "host": "0.0.0.0",
        "port": 8000,
        "log_level": "info",
        "workers": 1,  # 单worker模式：任务状态存储在内存中
    }

    if sys.platform == 'win32':
        # Windows: 禁用 reload 以避免子进程事件循环问题
        # Playwright 需要 ProactorEventLoop，但 reload 模式下子进程无法正确继承
        uvicorn_config["reload"] = False
        uvicorn_config["loop"] = "asyncio"
        if not is_production:
            logger.warning("Windows 下已禁用热重载（reload），修改代码后需手动重启服务")
    else:
        # Linux/Mac: 可以使用 reload
        uvicorn_config["reload"] = not is_production

    uvicorn.run("viral_app:app", **uvicorn_config)
