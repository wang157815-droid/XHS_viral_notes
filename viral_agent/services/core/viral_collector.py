"""
爆款笔记采集服务
负责搜索、筛选和收集爆款笔记

支持并行多维度爬取（点赞/评论/收藏），去重后按比例筛选爆款
"""
import asyncio
import math
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Dict, Any, Optional, Callable
from loguru import logger
import sys
import os


class SortType(IntEnum):
    """排序方式枚举"""
    GENERAL = 0      # 综合
    TIME = 1         # 最新
    LIKES = 2        # 最多点赞
    COMMENTS = 3     # 最多评论
    COLLECTS = 4     # 最多收藏


@dataclass
class DimensionResult:
    """单维度爬取结果"""
    sort_type: SortType
    dimension_name: str
    notes: List[Dict[str, Any]]
    total_fetched: int
    success: bool
    error_message: str = ""

# 添加项目根目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from apis.xhs_pc_apis import XHS_Apis
from viral_agent.models.viral_note import ViralNote
from viral_agent.utils import parse_chinese_number
from xhs_utils.cookie_util import trans_cookies
from xhs_utils.data_util import handle_note_info


class ViralNoteCollector:
    """爆款笔记采集器"""

    def __init__(self, cookies_str: str):
        """
        初始化采集器

        Args:
            cookies_str: Cookie字符串
        """
        self.cookies_str = cookies_str
        self.cookies = trans_cookies(cookies_str)
        self.client = XHS_Apis()  # XHS_Apis 不需要参数
        self.collected_notes: List[ViralNote] = []
        self.search_keyword: str = ""  # 存储搜索关键词

    def calculate_interaction_score(self, note_info: Dict[str, Any]) -> int:
        """
        计算笔记互动分数

        Args:
            note_info: 笔记信息字典

        Returns:
            互动总分数
        """
        # 处理笔记类型（兼容中文和英文）
        note_type = note_info.get('note_type', '') or note_info.get('type', '')
        # 将英文类型转换为中文（用于判断）
        if note_type == 'video':
            note_type = '视频'
        elif note_type == 'normal':
            note_type = '图集'

        # 优先从interact_info获取数据
        interact_info = note_info.get('interact_info', {})

        if interact_info:
            # interact_info中的数据可能是字符串或中文格式（如"2.6万"），需要转换
            liked = parse_chinese_number(interact_info.get('liked_count', '0'))
            collected = parse_chinese_number(interact_info.get('collected_count', '0'))
            comment = parse_chinese_number(interact_info.get('comment_count', '0'))
            # 注意是shared_count而不是share_count
            share = parse_chinese_number(interact_info.get('shared_count', '0'))
        else:
            # 如果没有interact_info，尝试从顶层获取
            liked = parse_chinese_number(note_info.get('liked_count', 0))
            collected = parse_chinese_number(note_info.get('collected_count', 0))
            comment = parse_chinese_number(note_info.get('comment_count', 0))
            share = parse_chinese_number(note_info.get('shared_count', 0))

        total_score = liked + collected + comment

        # 记录调试信息
        if total_score > 0:
            logger.debug(f"互动分数: 点赞{liked} 收藏{collected} 评论{comment} = {total_score}")

        if note_type == "视频" or note_type == "video":
            return total_score + share
        else:
            return total_score

    # [已废弃] 新版本使用并行多维度爬取 + 比例筛选，不再需要阈值判断
    # def is_viral_note(self, note_info: Dict[str, Any], threshold: int) -> bool:
    #     """判断是否为爆款笔记（已废弃，保留供参考）"""
    #     pass

    async def search_viral_notes(
        self,
        query: str,
        target_count: int = 100,
        viral_ratio: float = 0.5,
        note_type: int = 0,  # 0:不限 1:视频 2:图文
        time_range: int = 0,  # 0:不限 1:一天内 2:一周内 3:半年内
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> List[ViralNote]:
        """
        并行多维度搜索爆款笔记

        同时按"最多点赞"、"最多评论"、"最多收藏"三个维度爬取，
        去重后按互动分数排序，按比例截取爆款。

        Args:
            query: 搜索关键词
            target_count: 目标爬取总数量（三维度总和）
            viral_ratio: 爆款比例 (0.5=前1/2, 0.33=前1/3, 0.25=前1/4)
            note_type: 笔记类型
            time_range: 时间范围
            progress_callback: 进度回调函数

        Returns:
            按互动分数排序并截取的爆款笔记列表
        """
        self.search_keyword = query

        logger.info(f"开始并行多维度爬取: {query}")
        logger.info(f"目标数量: {target_count}, 爆款比例: {viral_ratio}")

        # 三个爬取维度
        dimensions = [
            (SortType.LIKES, "点赞"),
            (SortType.COMMENTS, "评论"),
            (SortType.COLLECTS, "收藏")
        ]

        # 每个维度的目标数量（向上取整确保总数足够）
        target_per_dimension = math.ceil(target_count / len(dimensions))

        if progress_callback:
            progress_callback(5, "开始依次爬取点赞/评论/收藏三个维度...")

        # 串行执行三个维度的爬取（避免触发反爬机制）
        results = []
        for i, (sort_type, name) in enumerate(dimensions):
            if progress_callback:
                progress_callback(
                    10 + i * 25,
                    f"正在爬取【{name}】维度 ({i+1}/3)..."
                )

            result = await self._fetch_dimension(
                query=query,
                sort_type=sort_type,
                dimension_name=name,
                target_per_dimension=target_per_dimension,
                note_type=note_type,
                time_range=time_range
            )
            results.append(result)

            # 维度之间等待一段时间，避免请求过快
            if i < len(dimensions) - 1:
                logger.info(f"等待 3 秒后爬取下一个维度...")
                await asyncio.sleep(3)

        # 合并结果并去重（使用 note_id 作为键）
        all_notes: Dict[str, Dict[str, Any]] = {}
        dimension_stats = {"点赞": 0, "评论": 0, "收藏": 0}

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"维度爬取异常: {result}")
                continue

            if isinstance(result, DimensionResult):
                dimension_stats[result.dimension_name] = result.total_fetched

                for note in result.notes:
                    note_id = note.get('note_id')
                    if note_id and note_id not in all_notes:
                        all_notes[note_id] = note

        logger.info(
            f"三维度爬取完成: 点赞{dimension_stats['点赞']} "
            f"评论{dimension_stats['评论']} 收藏{dimension_stats['收藏']}"
        )
        logger.info(f"去重后笔记数量: {len(all_notes)}")

        if progress_callback:
            progress_callback(80, f"爬取完成，去重后{len(all_notes)}篇，正在排序筛选...")

        # 转换为 ViralNote 对象
        viral_notes = [ViralNote.from_spider_data(note) for note in all_notes.values()]

        # 按互动分数降序排序
        viral_notes.sort(key=lambda x: x.interaction_score, reverse=True)

        # 按比例截取爆款
        viral_count = max(1, int(len(viral_notes) * viral_ratio))

        logger.info(f"按比例 {viral_ratio} 筛选出 {viral_count} 篇爆款笔记")

        if progress_callback:
            progress_callback(100, f"完成！共{len(viral_notes)}篇，筛选出{viral_count}篇爆款")

        self.collected_notes = viral_notes[:viral_count]
        return self.collected_notes

    async def _search_notes_async(self, **kwargs) -> List[Dict]:
        """异步搜索笔记（封装同步方法）"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.client.search_note(
                query=kwargs['query'],
                cookies_str=self.cookies_str,  # 添加 cookies_str 参数
                page=kwargs['page'],
                sort_type_choice=kwargs.get('sort_type', 2),
                note_type=kwargs.get('note_type', 0),
                note_time=kwargs.get('time_range', 0),
                note_range=0,
                pos_distance=0,
                geo=""
            )
        )

        # 解析返回值（返回的是元组：success, msg, res_json）
        if isinstance(result, tuple) and len(result) == 3:
            success, msg, res_json = result
            logger.info(f"搜索API返回: success={success}, msg={msg[:100] if msg else 'None'}")
            if success and res_json and 'data' in res_json:
                # 返回笔记列表
                items = res_json.get('data', {}).get('items', [])
                logger.info(f"搜索返回 {len(items)} 条结果")
                notes = []
                for item in items:
                    if 'note_card' in item:
                        note = item['note_card']
                        # ID和xsec_token都在item顶层
                        note_id = item.get('id')
                        xsec_token = item.get('xsec_token', '')

                        if note_id:
                            # 构造完整的URL，包含必要的参数
                            if xsec_token:
                                note['note_url'] = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_search"
                            else:
                                # 如果没有token，至少加上xsec_source
                                note['note_url'] = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_source=pc_search"

                            note['note_id'] = note_id  # 添加ID到note对象
                            notes.append(note)
                        else:
                            # 如果还是没有ID，打印调试信息
                            logger.debug(f"无法获取笔记ID，item字段: {list(item.keys())}")
                return notes
            else:
                logger.warning(f"搜索失败: {msg}")
                return []
        else:
            logger.error(f"意外的返回格式: {type(result)}")
            return []

    async def _get_note_detail_async(self, note_url: str) -> Optional[Dict]:
        """异步获取笔记详情（封装同步方法）"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.client.get_note_info(note_url, self.cookies_str)
        )

        # 解析返回值（返回的是元组：success, msg, res_json）
        if isinstance(result, tuple) and len(result) == 3:
            success, msg, res_json = result
            if success and res_json and 'data' in res_json:
                # 获取笔记详情
                items = res_json.get('data', {}).get('items', [])
                if items and len(items) > 0:
                    # 使用handle_note_info处理原始数据，正确提取视频URL等字段
                    try:
                        item = items[0]
                        # handle_note_info函数期望数据中有url字段，需要先添加
                        item['url'] = note_url
                        processed_data = handle_note_info(item)
                        return processed_data
                    except Exception as e:
                        logger.warning(f"处理笔记数据失败 {note_url}: {e}，返回原始note_card")
                        # 如果处理失败，返回原始note_card
                        return items[0].get('note_card', {})
                else:
                    # items为空，可能是笔记被删除或不存在
                    logger.debug(f"笔记详情items为空: {note_url}")
            else:
                logger.debug(f"获取笔记详情失败: {msg}")
        else:
            logger.error(f"意外的返回格式: {type(result)}")

        return None

    async def _fetch_dimension(
        self,
        query: str,
        sort_type: SortType,
        dimension_name: str,
        target_per_dimension: int,
        note_type: int,
        time_range: int
    ) -> DimensionResult:
        """
        单维度爬取实现

        Args:
            query: 搜索关键词
            sort_type: 排序方式
            dimension_name: 维度名称（用于日志）
            target_per_dimension: 该维度目标数量
            note_type: 笔记类型
            time_range: 时间范围

        Returns:
            DimensionResult: 该维度的爬取结果
        """
        notes = []
        page = 1
        max_pages = 30  # 每个维度最多30页

        logger.info(f"[{dimension_name}] 开始爬取，目标 {target_per_dimension} 条")

        try:
            while len(notes) < target_per_dimension and page <= max_pages:
                # 搜索当前页
                search_results = await self._search_notes_async(
                    query=query,
                    page=page,
                    note_type=note_type,
                    time_range=time_range,
                    sort_type=int(sort_type)
                )

                if not search_results:
                    logger.debug(f"[{dimension_name}] 第 {page} 页无结果，停止")
                    break

                # 获取笔记详情
                for note_brief in search_results:
                    note_url = note_brief.get('note_url', '')
                    if not note_url:
                        continue

                    try:
                        note_detail = await self._get_note_detail_async(note_url)
                        if note_detail:
                            notes.append(note_detail)
                            if len(notes) >= target_per_dimension:
                                break
                    except Exception as e:
                        logger.debug(f"[{dimension_name}] 获取详情失败: {e}")
                        continue

                    await asyncio.sleep(1)  # 防止请求过快

                page += 1
                await asyncio.sleep(2)  # 页面间隔

            logger.info(f"[{dimension_name}] 爬取完成: {len(notes)} 条")

            return DimensionResult(
                sort_type=sort_type,
                dimension_name=dimension_name,
                notes=notes,
                total_fetched=len(notes),
                success=True
            )

        except Exception as e:
            logger.error(f"[{dimension_name}] 爬取失败: {e}")
            return DimensionResult(
                sort_type=sort_type,
                dimension_name=dimension_name,
                notes=notes,
                total_fetched=len(notes),
                success=False,
                error_message=str(e)
            )

    def filter_by_interaction(
        self,
        notes: List[Dict[str, Any]],
        min_threshold: int = 1000,
        max_threshold: Optional[int] = None
    ) -> List[ViralNote]:
        """
        按互动量范围筛选笔记

        Args:
            notes: 原始笔记数据列表
            min_threshold: 最小互动阈值
            max_threshold: 最大互动阈值（可选）

        Returns:
            筛选后的爆款笔记列表
        """
        filtered_notes = []

        for note_data in notes:
            score = self.calculate_interaction_score(note_data)

            # 检查是否在范围内
            if score >= min_threshold:
                if max_threshold is None or score <= max_threshold:
                    viral_note = ViralNote.from_spider_data(note_data)
                    filtered_notes.append(viral_note)

        # 按互动分数降序排序
        filtered_notes.sort(key=lambda x: x.interaction_score, reverse=True)

        return filtered_notes

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取采集统计信息

        Returns:
            统计信息字典
        """
        if not self.collected_notes:
            return {
                'total_count': 0,
                'avg_interaction': 0,
                'video_count': 0,
                'image_count': 0,
                'max_interaction': 0,
                'min_interaction': 0
            }

        interactions = [note.interaction_score for note in self.collected_notes]
        video_count = sum(1 for note in self.collected_notes if note.note_type == "视频")

        return {
            'total_count': len(self.collected_notes),
            'avg_interaction': sum(interactions) // len(interactions),
            'video_count': video_count,
            'image_count': len(self.collected_notes) - video_count,
            'max_interaction': max(interactions),
            'min_interaction': min(interactions),
            'top_notes': [
                {
                    'title': note.title[:30],
                    'interaction': note.interaction_score,
                    'type': note.note_type
                }
                for note in self.collected_notes[:5]
            ]
        }

    def save_collected_notes(self, output_dir: str = "datas/viral_analysis"):
        """
        保存采集的笔记数据

        Args:
            output_dir: 输出目录
        """
        import json
        from datetime import datetime

        os.makedirs(output_dir, exist_ok=True)

        # 生成文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"viral_notes_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)

        # 保存数据
        data = {
            'collection_time': timestamp,
            'search_keyword': self.search_keyword,  # 保存搜索关键词
            'total_notes': len(self.collected_notes),
            'statistics': self.get_statistics(),
            'notes': [note.to_dict() for note in self.collected_notes]
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.success(f"爆款笔记已保存到: {filepath}")
        return filepath