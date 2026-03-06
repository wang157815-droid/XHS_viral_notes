"""
用户数据隔离服务

提供用户专属数据目录管理，确保不同用户的数据完全隔离：
- 分析结果（viral_analysis/）
- 导出 Excel（excel_datas/）
- 封面缓存（cover_cache/）
- Cookie 配置（cookies.json）
- 上传的知识库文档（documents/）

目录结构：
datas/
├── users/                      # 用户数据根目录
│   ├── admin/                  # 默认管理员用户
│   │   ├── viral_analysis/     # 分析结果
│   │   ├── excel_datas/        # 导出的 Excel
│   │   ├── cover_cache/        # 封面缓存
│   │   ├── cookies.json        # 用户 Cookie
│   │   └── documents/          # 知识库文档
│   ├── user_a/
│   │   └── ...
│   └── user_b/
│       └── ...
├── shared/                     # 共享资源
│   ├── knowledge_base/         # 公共知识库配置
│   └── chromadb/               # 向量数据库（全局共享）
└── auth/                       # 认证数据
    └── users.json              # 用户账户信息
"""
import os
import shutil
from pathlib import Path
from typing import Optional
from loguru import logger


class UserDataService:
    """用户数据隔离服务"""

    # 数据根目录
    DATA_ROOT = Path("datas")
    # 用户数据目录
    USERS_ROOT = DATA_ROOT / "users"
    # 共享资源目录
    SHARED_ROOT = DATA_ROOT / "shared"
    @staticmethod
    def get_default_user() -> str:
        """获取默认用户名（初始管理员的当前用户名）"""
        try:
            from viral_agent.auth.auth_service import get_initial_admin_username
            return get_initial_admin_username()
        except ImportError:
            return "admin"

    def __init__(self, username: Optional[str] = None):
        """
        初始化用户数据服务

        Args:
            username: 用户名，为空时动态获取初始管理员用户名
        """
        self.username = username or self.get_default_user()
        self._ensure_user_dirs()

    def _ensure_user_dirs(self) -> None:
        """确保用户目录结构存在"""
        dirs_to_create = [
            self.get_user_data_dir(),
            self.get_analysis_dir(),
            self.get_excel_dir(),
            self.get_cover_cache_dir(),
            self.get_documents_dir(),
        ]
        for dir_path in dirs_to_create:
            dir_path.mkdir(parents=True, exist_ok=True)

    # ========================
    # 用户专属目录
    # ========================

    def get_user_data_dir(self) -> Path:
        """获取用户数据根目录"""
        return self.USERS_ROOT / self.username

    def get_analysis_dir(self) -> Path:
        """获取用户分析结果目录"""
        return self.get_user_data_dir() / "viral_analysis"

    def get_excel_dir(self) -> Path:
        """获取用户 Excel 导出目录"""
        return self.get_user_data_dir() / "excel_datas"

    def get_cover_cache_dir(self) -> Path:
        """获取用户封面缓存目录"""
        return self.get_user_data_dir() / "cover_cache"

    def get_documents_dir(self) -> Path:
        """获取用户知识库文档目录"""
        return self.get_user_data_dir() / "documents"

    def get_cookies_file(self) -> Path:
        """获取用户 Cookie 配置文件路径"""
        return self.get_user_data_dir() / "cookies.json"

    # ========================
    # Cookie 管理
    # ========================

    def save_cookie(self, cookie: str) -> bool:
        """
        保存用户 Cookie

        Args:
            cookie: Cookie 字符串

        Returns:
            是否保存成功
        """
        import json
        from datetime import datetime

        cookie_file = self.get_cookies_file()
        # 保留已有字段（例如 backup_cookie）
        cookie_data = {}
        if cookie_file.exists():
            try:
                cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cookie_data = {}

        cookie_data.update({
            "cookie": cookie,
            "updated_at": datetime.now().isoformat(),
            "cookie_length": len(cookie)
        })

        try:
            cookie_file.write_text(
                json.dumps(cookie_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            # 设置文件权限为仅所有者可读写（安全考虑）
            try:
                cookie_file.chmod(0o600)
            except OSError:
                pass  # Windows 不支持 chmod

            logger.info(f"Cookie 已保存到用户 {self.username} 的配置")
            return True
        except OSError as e:
            logger.error(f"保存 Cookie 失败: {e}")
            return False

    def get_cookie(self) -> Optional[str]:
        """
        获取用户 Cookie

        Returns:
            Cookie 字符串，不存在返回 None
        """
        import json

        cookie_file = self.get_cookies_file()
        if not cookie_file.exists():
            return None

        try:
            cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
            return cookie_data.get("cookie")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"读取 Cookie 失败: {e}")
            return None

    def get_cookie_info(self) -> Optional[dict]:
        """
        获取用户 Cookie 信息（不含完整 Cookie 内容）

        Returns:
            Cookie 信息字典，不存在返回 None
        """
        import json

        cookie_file = self.get_cookies_file()
        if not cookie_file.exists():
            return None

        try:
            cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
            return {
                "has_cookie": bool(cookie_data.get("cookie")),
                "cookie_length": cookie_data.get("cookie_length", 0),
                "updated_at": cookie_data.get("updated_at"),
                "has_backup": bool(cookie_data.get("backup_cookie")),
                "backup_updated_at": cookie_data.get("backup_updated_at"),
                "source": "user_config"
            }
        except (json.JSONDecodeError, OSError):
            return None

    def save_backup_cookie(self, cookie: str) -> bool:
        """
        保存备用 Cookie（校验 a1 + web_session）

        Args:
            cookie: 备用 Cookie 字符串

        Returns:
            是否保存成功
        """
        import json
        from datetime import datetime

        cookie = cookie.strip().replace('\n', '').replace('\r', '')
        if not cookie:
            return False

        try:
            from viral_agent.services.auth.cookie_validator import check_cookie_fields
            result = check_cookie_fields(cookie)
            if not result.is_valid:
                logger.warning(f"备用 Cookie 校验失败: {result.reason}")
                return False
        except Exception as e:
            logger.warning(f"备用 Cookie 校验异常: {e}")
            return False

        cookie_file = self.get_cookies_file()
        cookie_data = {}
        if cookie_file.exists():
            try:
                cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cookie_data = {}

        cookie_data.update({
            "backup_cookie": cookie,
            "backup_updated_at": datetime.now().isoformat(),
            "backup_cookie_length": len(cookie),
        })

        try:
            cookie_file.write_text(
                json.dumps(cookie_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            try:
                cookie_file.chmod(0o600)
            except OSError:
                pass
            logger.info(f"备用 Cookie 已保存到用户 {self.username} 的配置")
            return True
        except OSError as e:
            logger.error(f"保存备用 Cookie 失败: {e}")
            return False

    def get_backup_cookie(self) -> Optional[str]:
        """
        获取备用 Cookie

        Returns:
            备用 Cookie 字符串，不存在返回 None
        """
        import json

        cookie_file = self.get_cookies_file()
        if not cookie_file.exists():
            return None

        try:
            cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
            return cookie_data.get("backup_cookie")
        except (json.JSONDecodeError, OSError):
            return None

    def delete_backup_cookie(self) -> bool:
        """
        删除备用 Cookie（保留主 Cookie）

        Returns:
            是否删除成功
        """
        import json

        cookie_file = self.get_cookies_file()
        if not cookie_file.exists():
            return False

        try:
            cookie_data = json.loads(cookie_file.read_text(encoding="utf-8"))
            cookie_data.pop("backup_cookie", None)
            cookie_data.pop("backup_updated_at", None)
            cookie_data.pop("backup_cookie_length", None)
            cookie_file.write_text(
                json.dumps(cookie_data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            try:
                cookie_file.chmod(0o600)
            except OSError:
                pass
            logger.info(f"已删除用户 {self.username} 的备用 Cookie")
            return True
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"删除备用 Cookie 失败: {e}")
            return False

    def delete_cookie(self) -> bool:
        """
        删除用户 Cookie

        Returns:
            是否删除成功
        """
        cookie_file = self.get_cookies_file()
        if not cookie_file.exists():
            return True

        try:
            cookie_file.unlink()
            logger.info(f"已删除用户 {self.username} 的 Cookie")
            return True
        except OSError as e:
            logger.error(f"删除 Cookie 失败: {e}")
            return False

    # ========================
    # 共享资源目录
    # ========================

    @classmethod
    def get_shared_knowledge_dir(cls) -> Path:
        """获取公共知识库配置目录"""
        path = cls.SHARED_ROOT / "knowledge_base"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def get_shared_chromadb_dir(cls) -> Path:
        """获取共享向量数据库目录"""
        path = cls.SHARED_ROOT / "chromadb"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def get_auth_dir(cls) -> Path:
        """获取认证数据目录"""
        path = cls.DATA_ROOT / "auth"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ========================
    # 工具方法
    # ========================

    def get_analysis_file(self, filename: str) -> Path:
        """获取分析结果文件的完整路径"""
        return self.get_analysis_dir() / filename

    def list_analysis_files(self) -> list[Path]:
        """列出用户的所有分析结果文件"""
        analysis_dir = self.get_analysis_dir()
        if not analysis_dir.exists():
            return []
        return sorted(
            analysis_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True  # 最新的在前
        )

    def get_storage_usage_mb(self) -> float:
        """计算用户已用存储空间（MB）"""
        total_size = 0
        user_dir = self.get_user_data_dir()
        if not user_dir.exists():
            return 0.0

        for file_path in user_dir.rglob("*"):
            if file_path.is_file():
                total_size += file_path.stat().st_size

        return total_size / (1024 * 1024)

    def cleanup_old_analysis(self, keep_count: int = 50) -> int:
        """
        清理旧的分析结果，保留最新的 N 个

        Args:
            keep_count: 保留的文件数量

        Returns:
            删除的文件数量
        """
        files = self.list_analysis_files()
        if len(files) <= keep_count:
            return 0

        deleted_count = 0
        for file_path in files[keep_count:]:
            try:
                file_path.unlink()
                deleted_count += 1
                logger.debug(f"已删除旧分析文件: {file_path.name}")
            except OSError as e:
                logger.warning(f"删除文件失败: {file_path}, 错误: {e}")

        if deleted_count > 0:
            logger.info(f"已清理 {deleted_count} 个旧分析文件")

        return deleted_count


# ========================
# 数据迁移工具
# ========================

def migrate_legacy_data(target_user: Optional[str] = None) -> bool:
    """
    将旧版数据迁移到用户目录

    旧版数据位置：
    - datas/viral_analysis/ → datas/users/{user}/viral_analysis/
    - datas/excel_datas/ → datas/users/{user}/excel_datas/
    - datas/cover_cache/ → datas/users/{user}/cover_cache/

    Args:
        target_user: 目标用户名，默认为初始管理员

    Returns:
        迁移是否成功
    """
    target_user = target_user or UserDataService.get_default_user()
    data_root = Path("datas")
    user_service = UserDataService(target_user)

    # 旧目录 → 新目录映射
    migrations = [
        (data_root / "viral_analysis", user_service.get_analysis_dir()),
        (data_root / "excel_datas", user_service.get_excel_dir()),
        (data_root / "cover_cache", user_service.get_cover_cache_dir()),
    ]

    migrated = False
    for old_dir, new_dir in migrations:
        if not old_dir.exists():
            continue

        # 检查旧目录是否有文件
        old_files = list(old_dir.glob("*"))
        if not old_files:
            continue

        logger.info(f"迁移数据: {old_dir} → {new_dir}")

        # 确保目标目录存在
        new_dir.mkdir(parents=True, exist_ok=True)

        # 移动文件
        for old_file in old_files:
            new_file = new_dir / old_file.name
            if new_file.exists():
                logger.warning(f"目标文件已存在，跳过: {new_file}")
                continue
            try:
                shutil.move(str(old_file), str(new_file))
                logger.debug(f"已迁移: {old_file.name}")
                migrated = True
            except OSError as e:
                logger.error(f"迁移失败: {old_file}, 错误: {e}")

        # 如果旧目录已空，删除它
        if not list(old_dir.glob("*")):
            try:
                old_dir.rmdir()
                logger.info(f"已删除空目录: {old_dir}")
            except OSError:
                pass

    if migrated:
        logger.info(f"数据迁移完成，目标用户: {target_user}")
    else:
        logger.info("无需迁移，旧目录为空或不存在")

    return migrated


# ========================
# 便捷函数
# ========================

# 单例缓存
_user_services: dict[str, UserDataService] = {}


def get_user_data_service(username: Optional[str] = None) -> UserDataService:
    """
    获取用户数据服务实例（带缓存）

    Args:
        username: 用户名，为空时使用默认用户

    Returns:
        UserDataService 实例
    """
    username = username or UserDataService.get_default_user()
    if username not in _user_services:
        _user_services[username] = UserDataService(username)
    return _user_services[username]
