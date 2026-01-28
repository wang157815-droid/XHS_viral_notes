"""
搜索模式相关数据模型

支持两种搜索模式：
1. ratio（比例筛选）：按互动排名取前 N%
2. threshold（阈值筛选）：只采集互动量 >= 阈值的笔记
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class SearchMode(str, Enum):
    """搜索模式枚举"""
    RATIO = "ratio"           # 比例筛选模式（现有）
    THRESHOLD = "threshold"   # 阈值筛选模式（新增）


class ThresholdConfig(BaseModel):
    """阈值模式配置"""
    min_interaction: int = Field(
        default=5000,
        ge=100,
        le=100000,
        description="互动量最低阈值"
    )
    max_collect_count: int = Field(
        default=500,
        ge=50,
        le=1000,
        description="最大采集数量（防止无限采集）"
    )
    enable_ai_expansion: bool = Field(
        default=True,
        description="样本不足时是否启用 AI 关键词扩展"
    )
    min_sample_for_expansion: int = Field(
        default=30,
        description="触发 AI 扩展的最低样本量阈值"
    )
