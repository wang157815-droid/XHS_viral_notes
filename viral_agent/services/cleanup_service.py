"""
历史记录清理服务

提供清理各类缓存和历史数据的功能，支持选择性清理。
支持用户数据隔离：部分缓存为用户专属，部分为全局共享。
"""

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from loguru import logger

from viral_agent.services.user_data_service import get_user_data_service, UserDataService


@dataclass
class CleanupResult:
    """清理结果"""
    category: str
    files_deleted: int
    size_freed_mb: float
    success: bool
    error: Optional[str] = None


@dataclass
class CleanupSummary:
    """清理汇总"""
    results: List[CleanupResult]
    total_files: int
    total_size_mb: float
    timestamp: str


class CleanupService:
    """历史记录清理服务（支持用户数据隔离）"""

    # 全局清理类别定义（与用户无关的共享资源）
    GLOBAL_CATEGORIES = {
        'video_cache': {
            'name': '视频缓存',
            'path': 'datas/video_cache',
            'description': '视频文件缓存（全局共享）',
            'safe_to_delete': True
        },
        'media_datas': {
            'name': '媒体文件',
            'path': 'datas/media_datas',
            'description': '下载的图片和视频媒体文件',
            'safe_to_delete': True
        },
        'logs': {
            'name': '日志文件',
            'path': 'logs',
            'description': '系统运行日志',
            'safe_to_delete': True
        },
        'chromadb': {
            'name': '向量数据库',
            'path': 'viral_agent/storage/chromadb',
            'description': 'RAG 知识库的向量索引（全局共享）',
            'safe_to_delete': True
        },
        'av_sync_cache': {
            'name': '音画同步缓存',
            'path': 'datas/av_sync_cache',
            'description': '音画同步分析的临时文件',
            'safe_to_delete': True
        }
    }

    # 用户专属清理类别定义（路径由 username 动态生成）
    USER_CATEGORIES = {
        'cover_cache': {
            'name': '封面缓存',
            'subdir': 'cover_cache',
            'description': '笔记封面图片缓存',
            'safe_to_delete': True
        },
        'viral_analysis': {
            'name': '分析结果',
            'subdir': 'viral_analysis',
            'description': '爆文分析的 JSON 和 Excel 报告',
            'safe_to_delete': True
        },
        'excel_datas': {
            'name': '导出数据',
            'subdir': 'excel_datas',
            'description': '导出的 Excel 数据',
            'safe_to_delete': True
        },
    }

    # 合并所有类别（用于兼容性）
    CATEGORIES = {**GLOBAL_CATEGORIES, **USER_CATEGORIES}

    # 不可删除的目录（保护）
    PROTECTED_PATHS = [
        'datas/auth',  # 用户认证数据
        'viral_agent/config',  # 配置文件
    ]

    def __init__(self, base_path: Optional[str] = None, username: Optional[str] = None):
        """
        初始化清理服务

        Args:
            base_path: 项目根目录路径，默认为当前工作目录
            username: 用户名，用于清理用户专属数据。为空时使用默认用户
        """
        self.base_path = Path(base_path) if base_path else Path.cwd()
        self.username = username or UserDataService.get_default_user()
        self.user_data = get_user_data_service(self.username)
        logger.info(f"清理服务初始化，根目录: {self.base_path}, 用户: {self.username}")

    def _get_category_path(self, category: str) -> Path:
        """获取类别的实际路径（区分全局和用户专属）"""
        if category in self.GLOBAL_CATEGORIES:
            return self.base_path / self.GLOBAL_CATEGORIES[category]['path']
        elif category in self.USER_CATEGORIES:
            subdir = self.USER_CATEGORIES[category]['subdir']
            return self.user_data.get_user_data_dir() / subdir
        else:
            return self.base_path / category  # 未知类别，返回相对路径

    def get_category_info(self) -> Dict[str, dict]:
        """
        获取所有清理类别的信息

        Returns:
            类别信息字典，包含名称、路径、大小等
        """
        info = {}
        for key, cat in self.CATEGORIES.items():
            full_path = self._get_category_path(key)
            size_mb = 0
            file_count = 0

            if full_path.exists():
                size_mb, file_count = self._get_dir_size(full_path)

            # 获取显示路径
            display_path = str(full_path.relative_to(self.base_path)) if full_path.is_relative_to(self.base_path) else str(full_path)

            info[key] = {
                'name': cat['name'],
                'description': cat['description'],
                'path': display_path,
                'size_mb': round(size_mb, 2),
                'file_count': file_count,
                'exists': full_path.exists(),
                'safe_to_delete': cat['safe_to_delete'],
                'is_user_specific': key in self.USER_CATEGORIES
            }

        return info

    def _get_dir_size(self, path: Path) -> tuple:
        """
        计算目录大小和文件数量

        Args:
            path: 目录路径

        Returns:
            (大小MB, 文件数量)
        """
        total_size = 0
        file_count = 0

        try:
            for item in path.rglob('*'):
                if item.is_file():
                    total_size += item.stat().st_size
                    file_count += 1
        except Exception as e:
            logger.warning(f"计算目录大小失败 {path}: {e}")

        return total_size / (1024 * 1024), file_count

    def cleanup_category(self, category: str, dry_run: bool = False) -> CleanupResult:
        """
        清理指定类别的数据

        Args:
            category: 类别键名
            dry_run: 是否仅预览（不实际删除）

        Returns:
            清理结果
        """
        if category not in self.CATEGORIES:
            return CleanupResult(
                category=category,
                files_deleted=0,
                size_freed_mb=0,
                success=False,
                error=f"未知类别: {category}"
            )

        cat = self.CATEGORIES[category]
        full_path = self._get_category_path(category)

        if not full_path.exists():
            return CleanupResult(
                category=category,
                files_deleted=0,
                size_freed_mb=0,
                success=True,
                error=None
            )

        # 检查是否受保护
        path_str = str(full_path)
        for protected in self.PROTECTED_PATHS:
            if protected in path_str:
                return CleanupResult(
                    category=category,
                    files_deleted=0,
                    size_freed_mb=0,
                    success=False,
                    error=f"受保护的目录，不可删除: {path_str}"
                )

        try:
            size_mb, file_count = self._get_dir_size(full_path)

            if not dry_run:
                # 删除目录下所有文件，但保留目录本身
                for item in full_path.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)

                logger.success(f"已清理 {cat['name']}: {file_count} 个文件, {size_mb:.2f} MB")
            else:
                logger.info(f"[预览] 将清理 {cat['name']}: {file_count} 个文件, {size_mb:.2f} MB")

            return CleanupResult(
                category=category,
                files_deleted=file_count,
                size_freed_mb=round(size_mb, 2),
                success=True
            )

        except Exception as e:
            logger.error(f"清理 {category} 失败: {e}")
            return CleanupResult(
                category=category,
                files_deleted=0,
                size_freed_mb=0,
                success=False,
                error=str(e)
            )

    def cleanup_all(self,
                    categories: Optional[List[str]] = None,
                    dry_run: bool = False,
                    exclude: Optional[List[str]] = None) -> CleanupSummary:
        """
        批量清理多个类别

        Args:
            categories: 要清理的类别列表，None 表示全部
            dry_run: 是否仅预览
            exclude: 要排除的类别列表

        Returns:
            清理汇总
        """
        if categories is None:
            categories = list(self.CATEGORIES.keys())

        if exclude:
            categories = [c for c in categories if c not in exclude]

        results = []
        total_files = 0
        total_size = 0

        for category in categories:
            result = self.cleanup_category(category, dry_run=dry_run)
            results.append(result)
            if result.success:
                total_files += result.files_deleted
                total_size += result.size_freed_mb

        action = "预览清理" if dry_run else "已清理"
        logger.info(f"{action}完成: 共 {total_files} 个文件, {total_size:.2f} MB")

        return CleanupSummary(
            results=results,
            total_files=total_files,
            total_size_mb=round(total_size, 2),
            timestamp=datetime.now().isoformat()
        )

    def get_total_cache_size(self) -> Dict[str, float]:
        """
        获取缓存总大小统计

        Returns:
            {'total_mb': 总大小, 'by_category': {类别: 大小}}
        """
        info = self.get_category_info()
        by_category = {k: v['size_mb'] for k, v in info.items()}
        total = sum(by_category.values())

        return {
            'total_mb': round(total, 2),
            'by_category': by_category
        }


# 便捷函数
def quick_cleanup(categories: List[str] = None, dry_run: bool = True) -> CleanupSummary:
    """
    快速清理函数

    Args:
        categories: 要清理的类别，默认为常用缓存
        dry_run: 是否仅预览，默认为 True（安全模式）

    Returns:
        清理汇总
    """
    if categories is None:
        # 默认只清理缓存，不清理分析结果
        categories = ['cover_cache', 'video_cache', 'logs', 'av_sync_cache']

    service = CleanupService()
    return service.cleanup_all(categories=categories, dry_run=dry_run)
