"""
知识库配置加载器
支持从JSON文件动态加载知识库配置
"""
import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

# 可选导入logger
try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class KnowledgeBaseConfig:
    """知识库配置类"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            # 默认路径
            config_path = Path(__file__).parent / "knowledge_base.json"
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self._validate_config()

    def _load_config(self) -> dict:
        """加载配置文件"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"知识库配置文件不存在: {self.config_path}")

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"配置文件JSON格式错误: {e}")

    def _validate_config(self):
        """验证配置格式"""
        required_keys = ['version', 'base_knowledge', 'domains']
        for key in required_keys:
            if key not in self.config:
                raise ValueError(f"配置文件缺少必需字段: {key}")

        # 验证domains结构
        if not isinstance(self.config['domains'], list):
            raise ValueError("domains必须是数组类型")

        for domain in self.config['domains']:
            required_domain_keys = ['id', 'name', 'keywords', 'knowledge']
            for key in required_domain_keys:
                if key not in domain:
                    raise ValueError(f"领域 {domain.get('id', 'unknown')} 缺少必需字段: {key}")

    def save_config(self, backup=True):
        """
        保存配置到文件

        Args:
            backup: 是否备份当前配置
        """
        if backup:
            self._create_backup()

        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

        logger.info(f"知识库配置已保存: {self.config_path}")

    def _create_backup(self):
        """创建配置备份"""
        if not self.config_path.exists():
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.config_path.parent / f"knowledge_base.json.backup.{timestamp}"
        shutil.copy2(self.config_path, backup_path)

        # 保留最近10个备份
        self._cleanup_old_backups()

        logger.info(f"已创建配置备份: {backup_path}")

    def _cleanup_old_backups(self, keep_count=10):
        """清理旧备份文件"""
        backup_files = sorted(
            self.config_path.parent.glob("knowledge_base.json.backup.*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        # 删除超过keep_count的备份
        for backup in backup_files[keep_count:]:
            backup.unlink()
            logger.debug(f"已删除旧备份: {backup}")

    def get_enabled_domains(self) -> List[dict]:
        """获取已启用的领域"""
        return [d for d in self.config['domains'] if d.get('enabled', True)]

    def get_all_domains(self) -> List[dict]:
        """获取所有领域（包括禁用的）"""
        return self.config['domains']

    def get_domain_by_id(self, domain_id: str) -> Optional[dict]:
        """根据ID获取领域配置"""
        for domain in self.config['domains']:
            if domain['id'] == domain_id:
                return domain
        return None

    def add_domain(self, domain_data: dict) -> bool:
        """
        添加新领域

        Args:
            domain_data: 领域配置字典

        Returns:
            是否添加成功
        """
        # 验证必需字段
        required_keys = ['id', 'name', 'keywords', 'knowledge']
        for key in required_keys:
            if key not in domain_data:
                raise ValueError(f"缺少必需字段: {key}")

        # 检查ID是否已存在
        if self.get_domain_by_id(domain_data['id']):
            raise ValueError(f"领域ID已存在: {domain_data['id']}")

        # 添加默认值
        if 'enabled' not in domain_data:
            domain_data['enabled'] = True
        if 'priority' not in domain_data:
            domain_data['priority'] = 0
        if 'metadata' not in domain_data:
            domain_data['metadata'] = {
                'created_at': datetime.now().isoformat(),
                'author': '用户添加'
            }

        self.config['domains'].append(domain_data)
        self.save_config()
        logger.info(f"已添加新领域: {domain_data['name']} ({domain_data['id']})")
        return True

    def update_domain(self, domain_id: str, domain_data: dict) -> bool:
        """
        更新领域配置

        Args:
            domain_id: 领域ID
            domain_data: 新的领域配置

        Returns:
            是否更新成功
        """
        for i, domain in enumerate(self.config['domains']):
            if domain['id'] == domain_id:
                # 保留ID不变
                domain_data['id'] = domain_id

                # 更新元数据
                if 'metadata' not in domain_data:
                    domain_data['metadata'] = domain.get('metadata', {})
                domain_data['metadata']['updated_at'] = datetime.now().isoformat()

                self.config['domains'][i] = domain_data
                self.save_config()
                logger.info(f"已更新领域: {domain_data['name']} ({domain_id})")
                return True

        return False

    def delete_domain(self, domain_id: str) -> bool:
        """
        删除领域
        Args:
            domain_id: 领域ID
        Returns:
            是否删除成功
        """
        original_count = len(self.config['domains'])
        self.config['domains'] = [d for d in self.config['domains'] if d['id'] != domain_id]

        if len(self.config['domains']) < original_count:
            self.save_config()
            logger.info(f"已删除领域: {domain_id}")
            return True

        return False

    def add_keyword(self, domain_id: str, keyword: str) -> bool:
        """
        为领域添加关键词
        Args:
            domain_id: 领域ID
            keyword: 关键词
        Returns:
            是否添加成功
        """
        domain = self.get_domain_by_id(domain_id)
        if not domain:
            return False

        if keyword not in domain['keywords']:
            domain['keywords'].append(keyword)
            self.save_config()
            logger.info(f"已为领域 {domain_id} 添加关键词: {keyword}")
            return True

        return False

    def remove_keyword(self, domain_id: str, keyword: str) -> bool:
        """
        删除领域关键词

        Args:
            domain_id: 领域ID
            keyword: 关键词

        Returns:
            是否删除成功
        """
        domain = self.get_domain_by_id(domain_id)
        if not domain:
            return False

        if keyword in domain['keywords']:
            domain['keywords'].remove(keyword)
            self.save_config()
            logger.info(f"已从领域 {domain_id} 删除关键词: {keyword}")
            return True

        return False

    def detect_domain(self, title: str = "", description: str = "") -> List[str]:
        """
        检测文本所属领域
        返回领域ID列表（按优先级和匹配度排序）

        Args:
            title: 标题
            description: 描述

        Returns:
            领域ID列表
        """
        text = (title + " " + description).lower()
        domain_scores = {}

        for domain in self.get_enabled_domains():
            # 计算匹配度
            score = sum(1 for kw in domain['keywords'] if kw in text)
            if score > 0:
                # 考虑优先级
                priority = domain.get('priority', 0)
                domain_scores[domain['id']] = (score, priority)

        # 按匹配度和优先级排序
        sorted_domains = sorted(
            domain_scores.items(),
            key=lambda x: (x[1][0], x[1][1]),  # (匹配度, 优先级)
            reverse=True
        )

        return [domain_id for domain_id, _ in sorted_domains]

    def get_video_knowledge(self, domain_id: str) -> Optional[dict]:
        """
        获取领域的视频专属知识

        Args:
            domain_id: 领域ID

        Returns:
            视频知识配置字典，如果不存在返回None
        """
        domain = self.get_domain_by_id(domain_id)
        if not domain:
            return None
        return domain.get('video_knowledge')

    def get_optimal_timing(self, domain_id: str) -> Optional[dict]:
        """
        获取领域的最优时间配置

        Args:
            domain_id: 领域ID

        Returns:
            最优时间配置字典
        """
        video_knowledge = self.get_video_knowledge(domain_id)
        if not video_knowledge:
            return None
        return video_knowledge.get('optimal_timing')

    def get_video_examples(self, domain_id: str) -> List[dict]:
        """
        获取领域的视频成功案例

        Args:
            domain_id: 领域ID

        Returns:
            视频案例列表
        """
        video_knowledge = self.get_video_knowledge(domain_id)
        if not video_knowledge:
            return []
        return video_knowledge.get('video_examples', [])

    def get_video_knowledge_text(self, domain_ids: List[str]) -> str:
        """
        获取视频知识的文本格式（用于视频分析提示词）

        Args:
            domain_ids: 领域ID列表

        Returns:
            格式化的视频知识文本
        """
        if not domain_ids:
            return ""

        knowledge_parts = []
        for domain_id in domain_ids:
            domain = self.get_domain_by_id(domain_id)
            if not domain:
                continue

            video_knowledge = domain.get('video_knowledge')
            if not video_knowledge:
                continue

            # 构建视频知识文本
            knowledge_text = f"\n【{domain['name']}视频创作知识】\n"

            # 开场钩子类型
            hook_types = video_knowledge.get('hook_types', [])
            if hook_types:
                knowledge_text += "\n推荐开场钩子类型：\n"
                knowledge_text += "\n".join(f"- {h}" for h in hook_types)

            # 内容结构
            structures = video_knowledge.get('content_structures', [])
            if structures:
                knowledge_text += "\n\n推荐内容结构：\n"
                knowledge_text += "\n".join(f"- {s}" for s in structures)

            # 最优时间配置
            timing = video_knowledge.get('optimal_timing', {})
            if timing:
                knowledge_text += "\n\n最优时间配置：\n"
                for key, value in timing.items():
                    knowledge_text += f"- {key}: {value}\n"

            # 封面风格
            cover_styles = video_knowledge.get('cover_styles', [])
            if cover_styles:
                knowledge_text += "\n推荐封面风格：\n"
                knowledge_text += "\n".join(f"- {c}" for c in cover_styles)

            # 视频案例
            examples = video_knowledge.get('video_examples', [])
            if examples:
                knowledge_text += "\n\n成功案例参考：\n"
                for ex in examples:
                    knowledge_text += f"标题：{ex.get('title', '')}\n"
                    knowledge_text += f"钩子类型：{ex.get('hook', '')}\n"
                    knowledge_text += f"结构：{ex.get('structure', '')}\n"
                    timing_data = ex.get('timing', {})
                    if timing_data:
                        knowledge_text += f"时间点：A={timing_data.get('A', '/')}s, B={timing_data.get('B', '/')}s, C={timing_data.get('C', '/')}s\n"

            knowledge_parts.append(knowledge_text)

        return "\n".join(knowledge_parts)

    def get_domain_knowledge_text(self, domain_ids: List[str]) -> str:
        """
        获取领域知识的文本格式（用于提示词）
        兼容现有代码的字符串格式

        Args:
            domain_ids: 领域ID列表

        Returns:
            格式化的知识文本
        """
        if not domain_ids:
            return ""

        knowledge_parts = []
        for domain_id in domain_ids:
            domain = self.get_domain_by_id(domain_id)
            if not domain:
                continue

            knowledge = domain['knowledge']

            # 构建知识文本（与knowledge_base.py格式兼容）
            knowledge_text = f"\n【{domain['name']}问题相关】\n"
            knowledge_text += "\n".join(f"- {p}" for p in knowledge.get('problems', []))

            knowledge_text += f"\n\n【{domain['name']}产品引出方式】\n"
            knowledge_text += "\n".join(f"- {w}" for w in knowledge.get('intro_ways', []))

            knowledge_text += f"\n\n【{domain['name']}产品植入方式】\n"
            knowledge_text += "\n".join(f"- {w}" for w in knowledge.get('embed_ways', []))

            # 添加示例（如果有）
            examples = knowledge.get('examples', [])
            if examples:
                knowledge_text += f"\n\n【{domain['name']}示例】\n"
                for ex in examples:
                    timeline = ex.get('timeline', '')
                    content_type = ex.get('content_type', '')
                    problem = ex.get('problem', '')
                    intro_way = ex.get('intro_way', '')
                    embed_way = ex.get('embed_way', '')
                    knowledge_text += f"Result_{timeline},{content_type},{problem},{intro_way},{embed_way}\n"

            knowledge_parts.append(knowledge_text)

        return "\n".join(knowledge_parts)

    def export_config(self) -> dict:
        """导出配置（用于下载）"""
        return self.config.copy()

    def import_config(self, config_data: dict, validate=True):
        """
        导入配置

        Args:
            config_data: 配置数据
            validate: 是否验证格式
        """
        if validate:
            # 简单验证
            required_keys = ['version', 'base_knowledge', 'domains']
            for key in required_keys:
                if key not in config_data:
                    raise ValueError(f"导入的配置缺少必需字段: {key}")

        # 备份当前配置
        self._create_backup()

        # 替换配置
        self.config = config_data
        self.save_config(backup=False)

        logger.info("配置导入成功")


# ==================== 全局单例 ====================

_knowledge_config: Optional[KnowledgeBaseConfig] = None


def get_knowledge_config(config_path: str = None) -> KnowledgeBaseConfig:
    """
    获取知识库配置单例

    Args:
        config_path: 配置文件路径（可选）

    Returns:
        KnowledgeBaseConfig实例
    """
    global _knowledge_config
    if _knowledge_config is None:
        _knowledge_config = KnowledgeBaseConfig(config_path)
    return _knowledge_config


def reload_knowledge_config(config_path: str = None) -> KnowledgeBaseConfig:
    """
    重新加载配置（用于Web界面更新后刷新）

    Args:
        config_path: 配置文件路径（可选）

    Returns:
        新的KnowledgeBaseConfig实例
    """
    global _knowledge_config
    _knowledge_config = None
    return get_knowledge_config(config_path)
