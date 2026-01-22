"""
用户数据迁移脚本

将旧版全局数据目录迁移到新的用户专属目录结构。

旧版结构:
  datas/viral_analysis/
  datas/excel_datas/
  datas/cover_cache/

新版结构:
  datas/users/admin/viral_analysis/
  datas/users/admin/excel_datas/
  datas/users/admin/cover_cache/

用法:
  python scripts/migrate_user_data.py           # 迁移到默认用户 admin
  python scripts/migrate_user_data.py user_a    # 迁移到指定用户
"""
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from viral_agent.services.user_data_service import migrate_legacy_data


def main():
    """执行迁移"""
    # 获取目标用户名
    target_user = sys.argv[1] if len(sys.argv) > 1 else "admin"

    logger.info("=" * 60)
    logger.info("用户数据迁移工具")
    logger.info("=" * 60)
    logger.info(f"目标用户: {target_user}")
    logger.info("")

    # 执行迁移
    migrated = migrate_legacy_data(target_user)

    if migrated:
        logger.success("迁移完成！")
        logger.info("")
        logger.info("新数据目录结构:")
        logger.info(f"  datas/users/{target_user}/viral_analysis/  - 分析结果")
        logger.info(f"  datas/users/{target_user}/excel_datas/     - 导出数据")
        logger.info(f"  datas/users/{target_user}/cover_cache/     - 封面缓存")
    else:
        logger.info("无需迁移（旧数据目录为空或不存在）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
