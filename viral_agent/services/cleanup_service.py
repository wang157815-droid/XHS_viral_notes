"""
历史记录清理服务

提供清理各类缓存和历史数据的功能，支持选择性清理。
"""

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from loguru import logger


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
    """历史记录清理服务"""

    # 清理类别定义
    CATEGORIES = {
        'cover_cache': {
            'name': '封面缓存',
            'path': 'datas/cover_cache',
            'description': '笔记封面图片缓存',
            'safe_to_delete': True
        },
        'video_cache': {
            'name': '视频缓存',
            'path': 'datas/video_cache',
            'description': '视频文件缓存（包含下载的视频和封面）',
            'safe_to_delete': True
        },
        'viral_analysis': {
            'name': '分析结果',
            'path': 'datas/viral_analysis',
            'description': '爆文分析的 JSON 和 Excel 报告',
            'safe_to_delete': True
        },
        'excel_datas': {
            'name': '爬虫数据',
            'path': 'datas/excel_datas',
            'description': '爬虫采集的 Excel 数据',
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
            'description': 'RAG 知识库的向量索引',
            'safe_to_delete': True
        },
        'av_sync_cache': {
            'name': '音画同步缓存',
            'path': 'datas/av_sync_cache',
            'description': '音画同步分析的临时文件',
            'safe_to_delete': True
        }
    }

    # 不可删除的目录（保护）
    PROTECTED_PATHS = [
        'datas/auth',  # 用户认证数据
        'viral_agent/config',  # 配置文件
    ]

    def __init__(self, base_path: Optional[str] = None):
        """
        初始化清理服务

        Args:
            base_path: 项目根目录路径，默认为当前工作目录
        """
        self.base_path = Path(base_path) if base_path else Path.cwd()
        logger.info(f"清理服务初始化，根目录: {self.base_path}")

    def get_category_info(self) -> Dict[str, dict]:
        """
        获取所有清理类别的信息

        Returns:
            类别信息字典，包含名称、路径、大小等
        """
        info = {}
        for key, cat in self.CATEGORIES.items():
            full_path = self.base_path / cat['path']
            size_mb = 0
            file_count = 0

            if full_path.exists():
                size_mb, file_count = self._get_dir_size(full_path)

            info[key] = {
                'name': cat['name'],
                'description': cat['description'],
                'path': cat['path'],
                'size_mb': round(size_mb, 2),
                'file_count': file_count,
                'exists': full_path.exists(),
                'safe_to_delete': cat['safe_to_delete']
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
        full_path = self.base_path / cat['path']

        if not full_path.exists():
            return CleanupResult(
                category=category,
                files_deleted=0,
                size_freed_mb=0,
                success=True,
                error=None
            )

        # 检查是否受保护
        for protected in self.PROTECTED_PATHS:
            if cat['path'].startswith(protected):
                return CleanupResult(
                    category=category,
                    files_deleted=0,
                    size_freed_mb=0,
                    success=False,
                    error=f"受保护的目录，不可删除: {cat['path']}"
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
