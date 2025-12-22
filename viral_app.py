"""
小红书爆文笔记生成Agent - FastAPI应用
提供Web API接口和界面
"""
import os
import asyncio
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from loguru import logger
from dotenv import load_dotenv

# 导入爆文Agent模块
from viral_agent.services.viral_collector import ViralNoteCollector
from viral_agent.services.feature_extractor import ViralFeatureExtractor
from viral_agent.services.viral_analyzer import ViralAnalyzer
from viral_agent.models.viral_note import ViralNote, ViralAnalysisResult

# 导入视频分析模块
from viral_agent.services.video_enhanced_analyzer import VideoEnhancedAnalyzer
from viral_agent.models.video_analysis_model import VideoAnalysisResult, VideoAnalysisBatch

# 导入知识库管理模块
from viral_agent.config.knowledge_loader import (
    get_knowledge_config,
    reload_knowledge_config
)

# 导入RAG和文档管理模块
from viral_agent.services.rag_service import RAGService
from viral_agent.services.document_parser import DocumentParser
from viral_agent.services.knowledge_retriever import UnifiedKnowledgeRetriever
from viral_agent.models.document import KnowledgeDocument, DocumentMetadata

# 加载环境变量
load_dotenv()

# 创建FastAPI应用
app = FastAPI(
    title="小红书爆文笔记生成Agent",
    description="自动采集和分析小红书爆款笔记，生成爆文模型",
    version="1.0.0"
)

# 配置静态文件和模板
app.mount("/static", StaticFiles(directory="web/static", html=True), name="static")
templates = Jinja2Templates(directory="web/templates")

# 全局任务状态存储
task_status = {}


# ==================== 请求模型定义 ====================

class ViralSearchRequest(BaseModel):
    """爆款搜索请求模型（并行多维度爬取）"""
    keyword: str = Field(..., description="搜索关键词", json_schema_extra={"example": "防脱精华"})
    target_count: int = Field(default=100, description="目标爬取数量（三维度总和）", ge=30, le=500)
    viral_ratio: float = Field(
        default=0.5,
        description="爆款比例: 0.5(前1/2), 0.33(前1/3), 0.25(前1/4)",
        ge=0.1,
        le=1.0
    )
    note_type: int = Field(default=0, description="笔记类型：0不限 1视频 2图文")
    time_range: int = Field(default=0, description="时间范围：0不限 1一天内 2一周内 3半年内")


class AnalysisRequest(BaseModel):
    """分析请求模型"""
    task_id: str = Field(..., description="采集任务ID")
    use_ai: bool = Field(default=True, description="是否使用AI分析")
    analysis_type: str = Field(
        default="all",
        description="分析类型：image=仅图文 video=仅视频 all=全部",
        pattern="^(image|video|all)$"
    )


class CookieRequest(BaseModel):
    """Cookie请求模型"""
    cookie: str = Field(..., description="小红书Cookie字符串")


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


# ==================== Cookie管理 ====================
# 全局Cookie存储（生产环境应该使用数据库或缓存）
app_cookie = None

# ==================== API路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/viral/cookie")
async def save_cookie(request: CookieRequest):
    """
    保存Cookie

    Returns:
        保存状态
    """
    global app_cookie

    # 清理Cookie中的换行符和多余空白
    cookie = request.cookie.strip().replace('\n', '').replace('\r', '')

    # 基本验证
    if not cookie:
        return {"status": "error", "message": "Cookie不能为空"}

    # 检查是否包含关键字段
    if 'a1=' not in cookie:
        return {"status": "error", "message": "Cookie格式不正确，缺少a1字段"}

    # 保存Cookie
    app_cookie = cookie

    # 同时更新.env文件（可选）
    try:
        # 读取现有.env内容
        env_path = Path(".env")
        if env_path.exists():
            with open(env_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # 更新或添加COOKIES行
            cookie_updated = False
            for i, line in enumerate(lines):
                if line.startswith('COOKIES=') or line.startswith('COOKIE='):
                    lines[i] = f'COOKIES="{cookie}"\n'
                    cookie_updated = True
                    break

            if not cookie_updated:
                lines.append(f'\nCOOKIES="{cookie}"\n')

            # 写回文件
            with open(env_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)
    except Exception as e:
        logger.warning(f"更新.env文件失败: {e}")

    logger.info(f"Cookie已更新，长度: {len(cookie)}")
    return {
        "status": "success",
        "message": "Cookie保存成功",
        "cookie_length": len(cookie)
    }


@app.get("/api/viral/cookie/status")
async def get_cookie_status():
    """
    获取Cookie状态

    Returns:
        Cookie配置状态
    """
    global app_cookie

    # 优先使用内存中的Cookie
    if app_cookie:
        return {
            "has_cookie": True,
            "cookie_length": len(app_cookie),
            "cookie_preview": app_cookie[:50] if len(app_cookie) > 50 else app_cookie
        }

    # 尝试从环境变量获取
    env_cookie = os.getenv("COOKIE") or os.getenv("COOKIES") or ""
    env_cookie = env_cookie.strip().replace('\n', '').replace('\r', '')

    if env_cookie:
        app_cookie = env_cookie  # 缓存到内存
        return {
            "has_cookie": True,
            "cookie_length": len(env_cookie),
            "cookie_preview": env_cookie[:50] if len(env_cookie) > 50 else env_cookie
        }

    return {
        "has_cookie": False,
        "cookie_length": 0,
        "cookie_preview": ""
    }


@app.post("/api/viral/search")
async def start_viral_search(
    request: ViralSearchRequest,
    background_tasks: BackgroundTasks
):
    """
    启动爆款笔记搜索任务

    Returns:
        任务ID和初始状态
    """
    # 生成任务ID
    task_id = f"viral_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    # 初始化任务状态
    task_status[task_id] = {
        "status": "running",
        "progress": 0,
        "collected": 0,
        "target": request.target_count,
        "keyword": request.keyword,
        "message": "正在初始化...",
        "start_time": datetime.now().isoformat()
    }

    # 启动后台任务
    background_tasks.add_task(
        collect_viral_notes_task,
        task_id,
        request.keyword,
        request.target_count,
        request.viral_ratio,
        request.note_type,
        request.time_range
    )

    return {
        "task_id": task_id,
        "status": "started",
        "message": f"开始搜索'{request.keyword}'相关爆款笔记"
    }


@app.get("/api/viral/status/{task_id}")
async def get_task_status(task_id: str):
    """
    获取任务状态

    Returns:
        任务当前状态信息
    """
    if task_id not in task_status:
        raise HTTPException(status_code=404, detail="任务不存在")

    return task_status[task_id]


@app.post("/api/viral/analyze")
async def analyze_viral_notes(request: AnalysisRequest):
    """
    分析爆款笔记生成爆文模型

    Returns:
        分析结果
    """
    task_id = request.task_id

    if task_id not in task_status:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task_status[task_id]["status"] != "completed":
        raise HTTPException(status_code=400, detail="采集任务尚未完成")

    # 加载采集的数据
    data_file = task_status[task_id].get("data_file")
    if not data_file or not os.path.exists(data_file):
        raise HTTPException(status_code=404, detail="数据文件不存在")

    with open(data_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 转换为ViralNote对象
    notes = [ViralNote.from_spider_data(note) for note in data['notes']]

    # 创建分析器
    if request.use_ai:
        # 启用AI分析，使用环境变量配置
        analyzer = ViralAnalyzer(api_key="auto")  # "auto"会自动读取环境变量
    else:
        # 禁用AI分析
        analyzer = ViralAnalyzer(api_key=None)  # None表示明确禁用AI

    # 执行分析（根据 analysis_type 进行分流）
    result = analyzer.analyze_viral_notes(
        notes=notes,
        keyword=task_status[task_id]["keyword"],
        threshold=data.get('statistics', {}).get('viral_threshold', 5000),
        analysis_type=request.analysis_type  # 新增：分析类型参数
    )

    # 将原始笔记数据添加到分析结果中（用于导出Excel原始数据分表）
    result.notes = [note.to_dict() for note in notes]

    # 保存分析结果
    analysis_file = data_file.replace('viral_notes', 'viral_analysis')
    result.save_to_file(analysis_file)

    # 更新任务状态
    task_status[task_id]["analysis_completed"] = True
    task_status[task_id]["analysis_file"] = analysis_file

    return {
        "status": "success",
        "analysis_file": analysis_file,
        "summary": {
            "total_notes": result.total_notes,
            "keyword": result.keyword,
            "model_generated": bool(result.viral_model)
        }
    }


@app.get("/api/viral/export/latest")
async def export_latest(format: str = "excel"):
    """
    导出最新的分析结果（不需要task_id）

    Args:
        format: 导出格式 (excel/json)

    Returns:
        文件下载
    """
    data_dir = Path("datas/viral_analysis")
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
        from viral_agent.services.export_service import export_to_excel

        try:
            excel_file = export_to_excel(str(latest_file))

            if not os.path.exists(excel_file):
                raise HTTPException(status_code=500, detail="Excel文件生成失败")

            from fastapi.responses import FileResponse
            return FileResponse(
                excel_file,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=f"viral_analysis_latest.xlsx"
            )
        except Exception as e:
            logger.error(f"Excel导出失败: {e}")
            raise HTTPException(status_code=500, detail=f"Excel导出失败: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="不支持的导出格式")


@app.get("/api/viral/history")
async def get_history():
    """
    获取历史分析记录

    Returns:
        历史记录列表
    """
    # 扫描数据目录
    data_dir = Path("datas/viral_analysis")
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


@app.get("/api/viral/export/{task_id}")
async def export_results(task_id: str, format: str = "excel"):
    """
    导出分析结果

    Args:
        format: 导出格式 (excel/json)

    Returns:
        文件下载
    """
    # 如果task_id不在内存中，尝试从文件系统查找
    if task_id not in task_status:
        logger.warning(f"任务 {task_id} 不在内存中，尝试从文件系统查找...")

        # 尝试查找对应的数据文件
        data_dir = Path("datas/viral_analysis")
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

        # 创建临时任务状态
        task_status[task_id] = {
            "data_file": target_file if "notes" in str(target_file) else None,
            "analysis_file": target_file if "analysis" in str(target_file) else None
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
        from viral_agent.services.export_service import export_to_excel

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
            excel_file = export_to_excel(target_file)

            if not os.path.exists(excel_file):
                raise HTTPException(status_code=500, detail="Excel文件生成失败")

            from fastapi.responses import FileResponse
            return FileResponse(
                excel_file,
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
    keyword: str,
    target_count: int,
    viral_ratio: float,
    note_type: int,
    time_range: int
):
    """
    后台任务：采集爆款笔记（并行多维度爬取）

    Args:
        task_id: 任务ID
        keyword: 搜索关键词
        target_count: 目标爬取数量
        viral_ratio: 爆款比例
        note_type: 笔记类型
        time_range: 时间范围
    """
    try:
        # 更新状态
        task_status[task_id]["message"] = "正在初始化采集器..."

        # 获取Cookie - 优先使用前端提供的Cookie
        global app_cookie
        cookies_str = app_cookie or os.getenv("COOKIE") or os.getenv("COOKIES") or ""
        # 清理Cookie中的换行符和多余空白
        cookies_str = cookies_str.strip().replace('\n', '').replace('\r', '')
        if not cookies_str:
            raise ValueError("未配置Cookie，请在前端输入Cookie或在.env文件中设置COOKIES")

        # 创建采集器
        collector = ViralNoteCollector(cookies_str)

        # 更新状态回调
        def update_progress(collected, message=""):
            task_status[task_id]["collected"] = collected
            task_status[task_id]["progress"] = min(100, int(collected / target_count * 100))
            if message:
                task_status[task_id]["message"] = message

            # 计算平均互动数
            if hasattr(collector, 'collected_notes') and len(collector.collected_notes) > 0:
                total_interaction = sum(note.interaction_score for note in collector.collected_notes)
                avg_interaction = int(total_interaction / len(collector.collected_notes))
                if "statistics" not in task_status[task_id]:
                    task_status[task_id]["statistics"] = {}
                task_status[task_id]["statistics"]["avg_interaction"] = avg_interaction

        # 开始采集（并行多维度爬取）
        task_status[task_id]["message"] = f"开始并行爬取'{keyword}'相关笔记..."

        notes = await collector.search_viral_notes(
            query=keyword,
            target_count=target_count,
            viral_ratio=viral_ratio,
            note_type=note_type,
            time_range=time_range,
            progress_callback=update_progress  # 传入进度回调
        )

        # 保存数据
        task_status[task_id]["message"] = "正在保存数据..."
        data_file = collector.save_collected_notes()

        # 更新最终状态
        task_status[task_id].update({
            "status": "completed",
            "progress": 100,
            "collected": len(notes),
            "message": f"成功采集{len(notes)}篇爆款笔记",
            "data_file": data_file,
            "end_time": datetime.now().isoformat(),
            "statistics": collector.get_statistics()
        })

        logger.success(f"任务 {task_id} 完成，采集{len(notes)}篇爆款笔记")

    except Exception as e:
        logger.error(f"任务 {task_id} 失败: {e}")
        task_status[task_id].update({
            "status": "failed",
            "message": f"任务失败: {str(e)}",
            "error": str(e),
            "end_time": datetime.now().isoformat()
        })


# ==================== 知识库管理API ====================

@app.get("/api/knowledge/domains")
async def get_domains():
    """获取所有领域列表"""
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
async def get_domain(domain_id: str):
    """获取单个领域详情"""
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
async def create_domain(request: DomainCreateRequest):
    """创建新领域"""
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
async def update_domain(domain_id: str, request: DomainUpdateRequest):
    """更新领域"""
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
async def delete_domain(domain_id: str):
    """删除领域"""
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
async def add_keyword(domain_id: str, request: KeywordRequest):
    """为领域添加关键词"""
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
async def remove_keyword(domain_id: str, keyword: str):
    """删除领域关键词"""
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
async def reload_config():
    """重新加载知识库配置"""
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
async def test_detection(request: TestDetectionRequest):
    """测试领域检测"""
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
async def export_config():
    """导出知识库配置"""
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
async def import_config(config_data: dict):
    """导入知识库配置"""
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

@app.post("/api/documents/upload")
async def upload_document(request: Request):
    """
    上传知识文档并建立RAG索引

    流程:
    1. 接收文件和元数据
    2. 解析文档内容
    3. 向量化并存入ChromaDB
    4. 返回文档ID
    """
    from fastapi import UploadFile, File, Form
    import uuid
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

        # 生成文档ID
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"

        # 保存文件
        storage_dir = Path("viral_agent/storage/documents")
        storage_dir.mkdir(parents=True, exist_ok=True)

        file_ext = Path(file.filename).suffix
        file_path = storage_dir / f"{doc_id}{file_ext}"

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"文件已保存: {file_path}")

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
async def list_documents(domain: Optional[str] = None):
    """列出所有知识文档"""
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
async def delete_document(doc_id: str):
    """删除知识文档"""
    try:
        rag_service = RAGService()
        success = rag_service.delete_document(doc_id)

        if not success:
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

        # 同时删除原始文件
        storage_dir = Path("viral_agent/storage/documents")
        for file_path in storage_dir.glob(f"{doc_id}.*"):
            file_path.unlink()
            logger.info(f"已删除文件: {file_path}")

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
async def search_documents(query: str, domains: Optional[List[str]] = None, top_k: int = 5):
    """搜索知识文档"""
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
async def get_knowledge_summary():
    """获取知识库摘要信息"""
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


# ==================== 健康检查 ====================

@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {
        "status": "healthy",
        "version": "1.0.0",
        "active_tasks": len([t for t in task_status.values() if t["status"] == "running"])
    }


# ==================== 启动应用 ====================

if __name__ == "__main__":
    import uvicorn

    # 确保必要的目录存在
    os.makedirs("datas/viral_analysis", exist_ok=True)
    os.makedirs("web/static", exist_ok=True)
    os.makedirs("web/templates", exist_ok=True)

    # 启动服务
    logger.info("启动小红书爆文Agent服务...")
    uvicorn.run(
        "viral_app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )