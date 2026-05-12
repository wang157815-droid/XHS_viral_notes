"""
Legacy knowledge configuration compatibility entry point.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional


class KnowledgeBaseConfig:
    def __init__(self, config_path: str = None):
        self.config_path = Path(config_path) if config_path else Path(__file__).parent / "knowledge_base.json"
        self.config: Dict[str, Any] = {"version": "disabled", "base_knowledge": {}, "domains": []}

    def save_config(self, backup=True):
        return None

    def get_enabled_domains(self) -> List[dict]:
        return []

    def get_all_domains(self) -> List[dict]:
        return []

    def get_domain_by_id(self, domain_id: str) -> Optional[dict]:
        return None

    def add_domain(self, domain_data: dict) -> bool:
        return False

    def update_domain(self, domain_id: str, domain_data: dict) -> bool:
        return False

    def delete_domain(self, domain_id: str) -> bool:
        return False

    def add_keyword(self, domain_id: str, keyword: str) -> bool:
        return False

    def remove_keyword(self, domain_id: str, keyword: str) -> bool:
        return False

    def detect_domain(self, title: str = "", description: str = "") -> List[str]:
        return []

    def get_video_knowledge(self, domain_id: str) -> Optional[dict]:
        return None

    def get_optimal_timing(self, domain_id: str) -> Optional[dict]:
        return None

    def get_video_examples(self, domain_id: str) -> List[dict]:
        return []

    def get_video_knowledge_text(self, domain_ids: List[str]) -> str:
        return ""

    def get_domain_knowledge_text(self, domain_ids: List[str]) -> str:
        return ""

    def export_config(self) -> dict:
        return dict(self.config)

    def import_config(self, config_data: dict, validate=True):
        self.config = {"version": "disabled", "base_knowledge": {}, "domains": []}
        return None


_knowledge_config: Optional[KnowledgeBaseConfig] = None


def get_knowledge_config(config_path: str = None) -> KnowledgeBaseConfig:
    global _knowledge_config
    if _knowledge_config is None:
        _knowledge_config = KnowledgeBaseConfig(config_path)
    return _knowledge_config


def reload_knowledge_config(config_path: str = None) -> KnowledgeBaseConfig:
    global _knowledge_config
    _knowledge_config = None
    return get_knowledge_config(config_path)
