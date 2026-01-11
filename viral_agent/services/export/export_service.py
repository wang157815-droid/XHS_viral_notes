"""
导出服务
负责生成Excel和其他格式的分析报告
"""
import json
import os
from datetime import datetime
from typing import Dict, Any, List
import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.chart import BarChart, Reference, PieChart
from openpyxl.utils import get_column_letter
from openpyxl.cell.cell import MergedCell
from loguru import logger


def safe_set_cell_style(cell, **kwargs):
    """
    安全地设置单元格样式，跳过MergedCell

    Args:
        cell: 单元格对象
        **kwargs: 要设置的属性（font, fill, border, alignment等）
    """
    if isinstance(cell, MergedCell):
        return

    for attr, value in kwargs.items():
        if hasattr(cell, attr):
            setattr(cell, attr, value)


def safe_set_cell_value(ws, cell_ref: str, value, **style_kwargs):
    """
    安全地设置单元格值和样式，跳过MergedCell

    Args:
        ws: 工作表对象
        cell_ref: 单元格引用（如 'A1', 'B2'）
        value: 要设置的值
        **style_kwargs: 可选的样式属性（font, fill, border, alignment等）

    Returns:
        bool: 是否成功设置
    """
    try:
        cell = ws[cell_ref]
        if isinstance(cell, MergedCell):
            logger.debug(f"跳过合并单元格 {cell_ref} 的写入")
            return False
        cell.value = value
        for attr, style_value in style_kwargs.items():
            if hasattr(cell, attr):
                setattr(cell, attr, style_value)
        return True
    except AttributeError as e:
        logger.debug(f"单元格 {cell_ref} 写入失败: {e}")
        return False


def safe_write_merged_cell(ws, row: int, col: int, value, merge_cols: int = 1, **style_kwargs):
    """
    P2-2: 安全写入可能需要合并的单元格

    原则：先写入左上角单元格的值，再进行合并（避免MergedCell只读错误）

    Args:
        ws: 工作表对象
        row: 行号
        col: 列号（起始列）
        value: 要写入的值
        merge_cols: 要合并的列数（默认1表示不合并）
        **style_kwargs: 可选的样式属性

    Returns:
        int: 实际写入的行号（方便链式调用）
    """
    try:
        # 1. 先写入左上角单元格的值
        cell = ws.cell(row=row, column=col)
        if not isinstance(cell, MergedCell):
            cell.value = value
            # 应用样式
            for attr, style_value in style_kwargs.items():
                if hasattr(cell, attr):
                    setattr(cell, attr, style_value)

        # 2. 如果需要合并多列，再进行合并
        if merge_cols > 1:
            end_col = col + merge_cols - 1
            ws.merge_cells(start_row=row, start_column=col, end_row=row, end_column=end_col)

        return row
    except Exception as e:
        logger.debug(f"安全写入合并单元格失败 ({row}, {col}): {e}")
        return row


def export_raw_data_to_excel(data: Dict[str, Any], original_file_path: str) -> str:
    """
    将原始采集数据导出为Excel

    Args:
        data: 原始数据
        original_file_path: 原始文件路径

    Returns:
        生成的Excel文件路径
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "爆款笔记数据"

    # 设置标题样式
    title_font = Font(size=14, bold=True)
    header_font = Font(size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="667EEA", end_color="667EEA", fill_type="solid")

    # 添加标题
    ws['A1'] = f"小红书爆款笔记数据 - {data.get('statistics', {}).get('keyword', '未知关键词')}"
    ws['A1'].font = title_font
    ws.merge_cells('A1:L1')

    # 添加统计信息
    ws['A2'] = f"采集时间: {data.get('collection_time', '未知')}"
    ws['A3'] = f"总计笔记: {data.get('total_notes', 0)}"
    ws['A4'] = f"爆款阈值: {data.get('statistics', {}).get('viral_threshold', 5000)}"

    # 添加列标题
    headers = [
        "序号", "标题", "内容预览", "点赞数", "收藏数", "评论数", "分享数",
        "总互动", "用户昵称", "笔记类型", "笔记链接", "采集时间"
    ]

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=6, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')

    # 添加数据行
    notes = data.get('notes', [])
    for idx, note in enumerate(notes, 1):
        row = idx + 6

        # 基础信息
        ws.cell(row=row, column=1, value=idx)
        ws.cell(row=row, column=2, value=note.get('title', ''))

        # 内容预览（前100字符）
        content = note.get('desc', '')
        ws.cell(row=row, column=3, value=content[:100] + '...' if len(content) > 100 else content)

        # 互动数据
        interact_info = note.get('interact_info', {})
        try:
            liked = int(interact_info.get('liked_count', '0'))
        except (ValueError, TypeError):
            liked = 0
        try:
            collected = int(interact_info.get('collected_count', '0'))
        except (ValueError, TypeError):
            collected = 0
        try:
            comment = int(interact_info.get('comment_count', '0'))
        except (ValueError, TypeError):
            comment = 0
        try:
            shared = int(interact_info.get('shared_count', '0'))
        except (ValueError, TypeError):
            shared = 0

        ws.cell(row=row, column=4, value=liked)
        ws.cell(row=row, column=5, value=collected)
        ws.cell(row=row, column=6, value=comment)
        ws.cell(row=row, column=7, value=shared)
        ws.cell(row=row, column=8, value=liked + collected + comment + shared)

        # 用户信息
        user = note.get('user_info', {})
        ws.cell(row=row, column=9, value=user.get('nickname', ''))

        # 笔记类型
        note_type = '视频' if note.get('type') == 'video' else '图文'
        ws.cell(row=row, column=10, value=note_type)

        # 链接
        ws.cell(row=row, column=11, value=note.get('note_url', ''))

        # 时间
        ws.cell(row=row, column=12, value=data.get('collection_time', ''))

    # 调整列宽
    column_widths = [8, 30, 40, 10, 10, 10, 10, 10, 20, 10, 50, 20]
    for col, width in enumerate(column_widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    # 添加边框（需要跳过合并单元格）
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    for row in ws.iter_rows(min_row=6, max_row=len(notes) + 6, min_col=1, max_col=12):
        for cell in row:
            # 跳过合并单元格
            if not isinstance(cell, MergedCell):
                cell.border = thin_border

    # 保存文件
    output_dir = os.path.dirname(original_file_path)
    filename = f"viral_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    output_path = os.path.join(output_dir, filename)

    wb.save(output_path)
    logger.success(f"原始数据Excel报告已生成: {output_path}")

    return output_path


def export_to_excel(analysis_file_path: str) -> str:
    """
    将分析结果导出为Excel报告

    Args:
        analysis_file_path: 分析结果JSON文件路径（或原始数据文件路径）

    Returns:
        生成的Excel文件路径
    """
    try:
        # 读取数据文件
        with open(analysis_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 检查是否是原始数据文件（包含notes字段）
        if 'notes' in data and 'title_patterns' not in data:
            # 这是原始数据文件，创建简化的Excel报告
            return export_raw_data_to_excel(data, analysis_file_path)

        # 获取分析类型（从viral_model中读取，默认为all）
        analysis_type = data.get('viral_model', {}).get('analysis_type', 'all')
        logger.info(f"Excel导出：分析类型 = {analysis_type}")

        # 创建Excel工作簿
        wb = openpyxl.Workbook()

        # 创建各个工作表（根据分析类型动态生成）
        # 1. 总览+爆文创作模型（通用，所有类型都需要）
        try:
            create_overview_and_model_sheet(wb, data)
        except Exception as e:
            logger.warning(f"创建总览与爆文模型工作表失败: {e}")

        # 2. AI深度分析（通用）
        try:
            create_multimodal_analysis_sheet(wb, data)
        except Exception as e:
            logger.warning(f"创建多模态AI分析工作表失败: {e}")

        # 3. 产品分析（通用）
        try:
            create_product_analysis_sheet(wb, data)
        except Exception as e:
            logger.warning(f"创建产品分析工作表失败: {e}")

        # 4. 场景方向分析（新增：在标题分析之前）
        try:
            create_scene_analysis_sheet(wb, data)
        except Exception as e:
            logger.warning(f"创建场景方向分析工作表失败: {e}")

        # 5. 标题分析（通用）
        try:
            create_title_analysis_sheet(wb, data)
        except Exception as e:
            logger.warning(f"创建标题分析工作表失败: {e}")

        # 5. 内容分析（通用）
        try:
            create_content_analysis_sheet(wb, data)
        except Exception as e:
            logger.warning(f"创建内容分析工作表失败: {e}")

        # 6. 封面与配图分析（图文/全部模式创建，视频模式跳过）
        if analysis_type in ['image', 'all']:
            try:
                create_cover_and_images_sheet(wb, data)
            except Exception as e:
                logger.warning(f"创建封面与配图分析工作表失败: {e}")
        else:
            logger.info("视频分析模式：跳过封面与配图分析工作表")

        # 7. 视频AI深度分析（视频/全部模式创建，图文模式跳过）
        if analysis_type in ['video', 'all']:
            try:
                create_video_ai_analysis_sheet(wb, data)
            except Exception as e:
                logger.warning(f"创建视频AI深度分析工作表失败: {e}")
        else:
            logger.info("图文分析模式：跳过视频AI深度分析工作表")

        # 10. 原始数据（通用）
        try:
            create_raw_data_sheet(wb, data)
        except Exception as e:
            logger.warning(f"创建原始数据工作表失败: {e}")

        # 删除默认的空Sheet
        if 'Sheet' in wb.sheetnames:
            del wb['Sheet']

        # 保存文件
        output_dir = os.path.dirname(analysis_file_path)
        filename = f"viral_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        output_path = os.path.join(output_dir, filename)

        wb.save(output_path)
        logger.success(f"Excel报告已生成: {output_path}")

        return output_path

    except Exception as e:
        logger.error(f"Excel导出失败: {e}")
        import traceback
        traceback.print_exc()
        raise


def create_overview_and_model_sheet(wb, data):
    """
    创建总览+爆文创作模型合并工作表

    结构：
    A. 分析概要（原总览内容）
    B. 综合成功模式总结（移到最上面）
    C. 场景策略（新增）
    D. 标题策略
    E. 内容策略
    F. 封面策略
    G. 产品植入策略
    H. 成功案例参考（含note_url链接）
    I. 发布前检查清单
    """
    ws = wb.create_sheet("📊 总览与爆文模型")
    ws.freeze_panes = 'A2'  # 冻结第一行，滚动时标题始终可见

    # 获取AI综合推理结果
    viral_model = data.get('viral_model', {})
    final_delivery = viral_model.get('final_delivery', {})
    ai_success = final_delivery.get('status') == 'success'

    # ==================== 标题 ====================
    if ai_success:
        ws['A1'] = "🔥 小红书爆文分析报告（AI深度推理版）"
        ws['A2'] = f"基于{data.get('total_notes', 0)}篇爆款数据+AI综合推理生成"
    else:
        ws['A1'] = "🔥 小红书爆文分析报告（数据驱动版）"
        ws['A2'] = "基于数据分析生成"

    ws['A1'].font = Font(size=16, bold=True, color="FF0000")
    ws.merge_cells('A1:H1')
    ws['A2'].font = Font(size=10, italic=True, color="666666")
    ws.merge_cells('A2:H2')

    row = 4

    # ==================== A. 分析概要 ====================
    ws[f'A{row}'] = "【A. 分析概要】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    # 基础信息
    info_items = [
        ("关键词", data.get('keyword', '')),
        ("分析时间", data.get('analysis_time', '')),
        ("爆款笔记数", data.get('total_notes', 0)),
        ("互动阈值", data.get('viral_threshold', 5000))
    ]
    for key, value in info_items:
        ws[f'A{row}'] = key
        ws[f'B{row}'] = value
        row += 1
    row += 1

    # 核心指标
    if 'interaction_features' in data:
        metrics = data['interaction_features']
        ws[f'D{row - 5}'] = "核心互动指标"
        ws[f'D{row - 5}'].font = Font(bold=True)
        metrics_items = [
            ("平均点赞", metrics.get('avg_liked', 0)),
            ("平均收藏", metrics.get('avg_collected', 0)),
            ("平均评论", metrics.get('avg_comment', 0)),
            ("平均收藏率", f"{metrics.get('avg_collection_rate', 0) * 100:.1f}%")
        ]
        for i, (key, value) in enumerate(metrics_items):
            ws[f'D{row - 4 + i}'] = key
            ws[f'E{row - 4 + i}'] = value

    row += 1

    # ==================== 互动数据详情（整合原互动分析sheet） ====================
    ws[f'A{row}'] = "互动数据详情"
    ws[f'A{row}'].font = Font(bold=True, size=11, color="0066CC")
    row += 1

    if 'interaction_features' in data:
        interaction = data['interaction_features']

        # 极值数据
        ws[f'A{row}'] = "最高互动"
        ws[f'B{row}'] = interaction.get('max_interaction', 0)
        ws[f'D{row}'] = "最低互动"
        ws[f'E{row}'] = interaction.get('min_interaction', 0)
        row += 1

        # 互动分布
        dist = interaction.get('interaction_distribution', {})
        if dist:
            ws[f'A{row}'] = "互动量分布"
            ws[f'A{row}'].font = Font(bold=True)
            dist_parts = [f"{k}:{v}篇" for k, v in list(dist.items())[:4]]
            ws[f'B{row}'] = " | ".join(dist_parts)
            ws.merge_cells(f'B{row}:E{row}')
            row += 1

    # 用户特征
    if 'user_patterns' in data:
        user_data = data['user_patterns']
        ws[f'A{row}'] = "用户特征"
        ws[f'A{row}'].font = Font(bold=True)
        row += 1

        ws[f'A{row}'] = "独立用户数"
        ws[f'B{row}'] = user_data.get('unique_users', 0)
        ws[f'D{row}'] = "人均爆款数"
        ws[f'E{row}'] = user_data.get('avg_notes_per_user', 0)
        row += 1

        # 高产作者（简化显示）
        top_creators = user_data.get('top_viral_creators', [])
        if top_creators:
            ws[f'A{row}'] = "高产作者"
            ws[f'A{row}'].font = Font(bold=True)
            top_names = []
            for creator in top_creators[:3]:
                if isinstance(creator, dict):
                    name = creator.get('nickname', '未知')
                    count = creator.get('viral_count', 0)
                    top_names.append(f"{name}({count}篇)")
            ws[f'B{row}'] = " | ".join(top_names) if top_names else "暂无数据"
            ws.merge_cells(f'B{row}:E{row}')
            row += 1

    row += 1

    # ==================== B. 综合成功模式总结（最重要，放在最上面） ====================
    ws[f'A{row}'] = "【B. 综合成功模式总结】⭐ 核心结论"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="FF0000")
    ws[f'A{row}'].fill = PatternFill(start_color="FFF3CD", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    if ai_success and 'success_pattern_summary' in final_delivery:
        ws[f'A{row}'] = "🎯 AI核心总结"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        row += 1
        summary_text = final_delivery['success_pattern_summary']
        for line in _split_text(summary_text, 80):
            ws[f'B{row}'] = line
            ws.merge_cells(f'B{row}:H{row}')
            ws[f'B{row}'].font = Font(size=10)
            row += 1
        row += 1
    else:
        ws[f'A{row}'] = "基于数据统计的成功模式"
        ws[f'A{row}'].font = Font(bold=True)
        row += 1
        title_data = data.get('title_patterns', {})
        content_data = data.get('content_patterns', {})
        ws[f'B{row}'] = f"• 最佳标题长度：{title_data.get('avg_length', 17)}字"
        row += 1
        ws[f'B{row}'] = f"• 最佳内容长度：{content_data.get('avg_length', 300)}字"
        row += 1
        top_kw = title_data.get('top_keywords', [])[:3]
        if top_kw:
            ws[f'B{row}'] = f"• 高频关键词：{'、'.join([k['word'] for k in top_kw])}"
            row += 1
        row += 1

    # ==================== C. 场景策略（新增） ====================
    ws[f'A{row}'] = "【C. 场景策略】🎬 场景与内容方向"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="9B59B6")
    ws[f'A{row}'].fill = PatternFill(start_color="F5EEF8", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    scene_features = data.get('scene_features', {})
    if scene_features and scene_features.get('status') == 'success':
        strategy = scene_features.get('scene_strategy', {})

        # 推荐场景
        ws[f'A{row}'] = "⭐ 推荐场景"
        ws[f'A{row}'].font = Font(bold=True)
        recommended_scenes = strategy.get('recommended_scenes', [])
        ws[f'B{row}'] = "、".join(recommended_scenes) if recommended_scenes else "日常、居家"
        ws.merge_cells(f'B{row}:D{row}')
        row += 1

        # 推荐内容方向
        ws[f'A{row}'] = "⭐ 推荐方向"
        ws[f'A{row}'].font = Font(bold=True)
        recommended_dirs = strategy.get('recommended_directions', [])
        ws[f'B{row}'] = "、".join(recommended_dirs) if recommended_dirs else "单品推荐、好物合集"
        ws.merge_cells(f'B{row}:D{row}')
        row += 1

        # 高互动场景
        ws[f'A{row}'] = "🔥 高互动场景"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        high_scenes = strategy.get('high_interaction_scenes', [])
        ws[f'B{row}'] = "、".join(high_scenes) if high_scenes else "暂无明显高互动场景"
        ws.merge_cells(f'B{row}:D{row}')
        row += 1

        # 场景创作模板
        scene_templates = strategy.get('scene_templates', [])
        if scene_templates:
            ws[f'A{row}'] = "场景模板"
            ws[f'A{row}'].font = Font(bold=True)
            ws[f'B{row}'] = " | ".join(scene_templates[:3])
            ws.merge_cells(f'B{row}:H{row}')
            ws[f'B{row}'].font = Font(size=9, color="666666")
            row += 1

        # 策略总结
        summary = strategy.get('strategy_summary', '')
        if summary:
            ws[f'A{row}'] = "策略总结"
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            # 只取前2行
            lines = [line.strip() for line in summary.split('\n') if line.strip()][:2]
            for line in lines:
                ws[f'B{row}'] = line[:80]
                ws.merge_cells(f'B{row}:H{row}')
                ws[f'B{row}'].font = Font(size=9)
                row += 1
    else:
        ws[f'A{row}'] = "场景分析数据暂无（详见 🎬场景方向分析 工作表）"
        ws[f'A{row}'].font = Font(size=10, italic=True, color="666666")
        row += 1

    row += 1

    # ==================== D. 标题策略 ====================
    row = _write_strategy_section(ws, row, "D", "标题策略",
                                   final_delivery.get('title_strategy', {}), ai_success, data)
    row += 1

    # ==================== E. 内容策略 ====================
    row = _write_strategy_section(ws, row, "E", "内容策略",
                                   final_delivery.get('content_strategy', {}), ai_success, data)
    row += 1

    # ==================== F. 封面策略 ====================
    row = _write_strategy_section(ws, row, "F", "封面策略",
                                   final_delivery.get('cover_strategy', {}), ai_success, data)
    row += 1

    # ==================== G. 产品植入策略 ====================
    row = _write_strategy_section(ws, row, "G", "产品植入策略",
                                   final_delivery.get('product_strategy', {}), ai_success, data)
    row += 1

    # ==================== H. 成功案例参考（含note_url链接） ====================
    ws[f'A{row}'] = "【H. 成功案例参考】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    success_examples = viral_model.get('success_examples', [])[:5]
    for i, example in enumerate(success_examples, 1):
        ws[f'A{row}'] = f"案例{i}"
        ws[f'A{row}'].font = Font(bold=True, color="0066CC")
        ws[f'B{row}'] = example.get('title', '')
        ws.merge_cells(f'B{row}:E{row}')
        ws[f'F{row}'] = f"互动: {example.get('interaction_score', 0)}"
        ws[f'F{row}'].font = Font(bold=True, color="FF0000")
        row += 1

        # 添加笔记链接（关键优化）
        note_url = example.get('note_url', '')
        if note_url:
            ws[f'B{row}'] = f"📎 链接: {note_url}"
            ws[f'B{row}'].font = Font(size=9, color="0066CC", underline="single")
            ws.merge_cells(f'B{row}:H{row}')
            row += 1

        for highlight in example.get('highlights', [])[:3]:
            ws[f'B{row}'] = f"• {highlight}"
            ws.merge_cells(f'B{row}:H{row}')
            ws[f'B{row}'].font = Font(size=9, color="666666")
            row += 1
        row += 1

    # ==================== I. 发布前检查清单 ====================
    ws[f'A{row}'] = "【I. 发布前检查清单】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    if ai_success and 'checklist' in final_delivery:
        ai_checklist = final_delivery['checklist']
        for item in ai_checklist[:8]:
            if isinstance(item, dict):
                ws[f'A{row}'] = f"☑ {item.get('item', '')}"
                ws[f'A{row}'].font = Font(size=11)
                ws.merge_cells(f'A{row}:D{row}')
                ws[f'E{row}'] = item.get('reason', '')
                ws[f'E{row}'].font = Font(size=9, color="666666")
                ws.merge_cells(f'E{row}:H{row}')
                row += 1
    else:
        checklist = [
            ("☑ 标题是否包含关键词和情绪点？", "关键词提升搜索匹配，情绪词提升点击率"),
            ("☑ 封面图是否有大字压图？", "信息流中0.3秒定生死，大字才能抓眼球"),
            ("☑ 前30字是否设置了钩子？", "开头决定用户是否继续阅读"),
            ("☑ 是否有3个以上的价值点？", "多个价值点提升内容厚度和收藏率"),
            ("☑ 图片是否清晰且有对比？", "高质量图片是基本门槛"),
            ("☑ 是否添加了3-5个精准标签？", "标签决定内容的曝光池"),
            ("☑ 发布时间是否在高峰期（19-22点）？", "高峰期发布初始流量更好")
        ]
        for item, reason in checklist:
            ws[f'A{row}'] = item
            ws[f'A{row}'].font = Font(size=11)
            ws.merge_cells(f'A{row}:D{row}')
            ws[f'E{row}'] = reason
            ws[f'E{row}'].font = Font(size=9, color="666666")
            ws.merge_cells(f'E{row}:H{row}')
            row += 1

    # 设置列宽
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 30
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 15
    ws.column_dimensions['E'].width = 20
    ws.column_dimensions['F'].width = 15
    ws.column_dimensions['G'].width = 15
    ws.column_dimensions['H'].width = 20

    # 添加边框
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    for r in ws.iter_rows(min_row=4, max_row=row, min_col=1, max_col=8):
        for cell in r:
            if not isinstance(cell, MergedCell) and cell.value:
                cell.border = thin_border


def _write_strategy_section(ws, row: int, section_id: str, section_name: str,
                            strategy: Dict, ai_success: bool, data: Dict) -> int:
    """写入策略区块的辅助函数"""
    ws[f'A{row}'] = f"【{section_id}. {section_name}】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    if ai_success and strategy:
        conclusions = strategy.get('conclusions', [])
        for conclusion in conclusions[:3]:
            if isinstance(conclusion, dict):
                ws[f'A{row}'] = "📌 结论"
                ws[f'A{row}'].font = Font(bold=True, color="FF0000")
                ws[f'B{row}'] = conclusion.get('point', '')
                ws[f'B{row}'].font = Font(bold=True, size=11)
                ws.merge_cells(f'B{row}:H{row}')
                row += 1

                ws[f'A{row}'] = "💭 思考"
                ws[f'A{row}'].font = Font(bold=True, color="0066CC")
                reasoning = conclusion.get('reasoning', '')
                ws[f'B{row}'] = reasoning[:150] if len(reasoning) > 150 else reasoning
                ws[f'B{row}'].font = Font(size=9, color="666666")
                ws.merge_cells(f'B{row}:H{row}')
                row += 2

        # 附加信息（模板、关键词等）
        if 'templates' in strategy and strategy['templates']:
            ws[f'A{row}'] = "📝 推荐模板"
            ws[f'A{row}'].font = Font(bold=True)
            for tpl in strategy['templates'][:3]:
                row += 1
                ws[f'B{row}'] = f"• {tpl}"
                ws.merge_cells(f'B{row}:H{row}')
            row += 1
    else:
        # 数据驱动的回退方案
        ws[f'A{row}'] = "（基于数据统计）"
        ws[f'A{row}'].font = Font(size=9, italic=True, color="999999")
        row += 1

    return row


def create_raw_data_sheet(wb, data):
    """创建原始数据工作表"""
    ws = wb.create_sheet("原始数据")

    ws['A1'] = "爆款笔记原始数据"
    ws['A1'].font = Font(size=14, bold=True)
    ws.merge_cells('A1:L1')

    # 列标题
    headers = ["序号", "标题", "内容预览", "点赞", "收藏", "评论",
               "分享", "总互动", "用户昵称", "类型", "链接", "采集时间"]

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="667EEA", fill_type="solid")

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center')

    # 从notes中提取数据
    notes = data.get('notes', [])
    for idx, note in enumerate(notes, 1):
        row = idx + 3
        ws.cell(row=row, column=1, value=idx)
        ws.cell(row=row, column=2, value=note.get('title', '')[:50])

        content = note.get('desc', '')
        ws.cell(row=row, column=3, value=content[:80] + '...' if len(content) > 80 else content)

        # 兼容两种数据格式：
        # 1. ViralNote.to_dict() 格式：互动数据直接在顶层
        # 2. API原始格式：互动数据在 interact_info 嵌套字典中
        interact = note.get('interact_info', {})
        liked = _safe_int(interact.get('liked_count', 0)) or _safe_int(note.get('liked_count', 0))
        collected = _safe_int(interact.get('collected_count', 0)) or _safe_int(note.get('collected_count', 0))
        commented = _safe_int(interact.get('comment_count', 0)) or _safe_int(note.get('comment_count', 0))
        # 注意：API格式用 shared_count，ViralNote用 share_count
        shared = _safe_int(interact.get('shared_count', 0)) or _safe_int(note.get('share_count', 0))

        ws.cell(row=row, column=4, value=liked)
        ws.cell(row=row, column=5, value=collected)
        ws.cell(row=row, column=6, value=commented)
        ws.cell(row=row, column=7, value=shared)

        total = liked + collected + commented + shared
        ws.cell(row=row, column=8, value=total)

        # 兼容用户信息格式
        user = note.get('user_info', {})
        nickname = user.get('nickname', '') or note.get('nickname', '')
        ws.cell(row=row, column=9, value=nickname)

        # 兼容类型字段格式
        note_type = note.get('note_type', '') or note.get('type', '')
        if note_type in ('视频', 'video'):
            type_display = '视频'
        else:
            type_display = '图文'
        ws.cell(row=row, column=10, value=type_display)

        ws.cell(row=row, column=11, value=note.get('note_url', ''))
        ws.cell(row=row, column=12, value=data.get('collection_time', '') or data.get('analysis_time', ''))

    # 调整列宽
    widths = [6, 25, 35, 8, 8, 8, 8, 10, 15, 8, 40, 18]
    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width


def _safe_int(value) -> int:
    """安全转换为整数"""
    try:
        return int(value) if value else 0
    except (ValueError, TypeError):
        return 0


def _calculate_prd_compliance(video_ai_data: Dict[str, Any]) -> float:
    """
    计算视频内容的PRD合规率

    PRD要求：
    - 产品出现时间：不晚于30s
    - 日常用法：第30s左右
    - 产品讲解：40-60s期间
    - 干货内容：20-40s

    Args:
        video_ai_data: 视频AI分析数据

    Returns:
        PRD合规率百分比
    """
    insights = video_ai_data.get('individual_insights', [])
    if not insights:
        return 0.0

    compliant_count = 0
    total_count = len(insights)

    for insight in insights:
        # 基于AI分析结果评估合规性
        # 这里使用简化的启发式评估
        ai_analysis = insight.get('ai_analysis', '')
        is_compliant = True

        # 检查是否有产品相关内容
        if ai_analysis:
            # 如果分析中提到"产品"、"介绍"等词，认为有产品植入
            has_product = any(kw in ai_analysis for kw in ['产品', '介绍', '推荐', '展示'])
            if has_product:
                compliant_count += 1
            else:
                # 即使没有明确产品词，如果有结构化内容也算部分合规
                if len(ai_analysis) > 100:
                    compliant_count += 0.5

    return (compliant_count / total_count * 100) if total_count > 0 else 0.0


def create_summary_sheet(wb, data):
    """创建总览工作表（保留兼容，但不再使用）"""
    ws = wb.create_sheet("总览")

    # 设置标题样式
    title_font = Font(size=16, bold=True)
    header_font = Font(size=12, bold=True)
    header_fill = PatternFill(start_color="667EEA", end_color="667EEA", fill_type="solid")
    header_text = Font(color="FFFFFF", bold=True)

    # 标题
    ws['A1'] = "小红书爆文分析报告"
    ws['A1'].font = title_font
    ws.merge_cells('A1:E1')

    # 基础信息
    ws['A3'] = "分析信息"
    ws['A3'].font = header_font

    info_data = [
        ["关键词", data.get('keyword', '')],
        ["分析时间", data.get('analysis_time', '')],
        ["爆款笔记数", data.get('total_notes', 0)],
        ["互动阈值", data.get('viral_threshold', 5000)]
    ]

    for i, (key, value) in enumerate(info_data, start=4):
        ws[f'A{i}'] = key
        ws[f'B{i}'] = value

    # 核心指标
    ws['D3'] = "核心指标"
    ws['D3'].font = header_font

    if 'interaction_features' in data:
        metrics = data['interaction_features']
        metrics_data = [
            ["平均点赞", metrics.get('avg_liked', 0)],
            ["平均收藏", metrics.get('avg_collected', 0)],
            ["平均评论", metrics.get('avg_comment', 0)],
            ["平均收藏率", f"{metrics.get('avg_collection_rate', 0) * 100:.1f}%"]
        ]

        # 如果有点赞数据，计算点赞率（点赞数/总互动数）
        if metrics.get('avg_liked', 0) > 0:
            total_interaction = metrics.get('avg_liked', 0) + metrics.get('avg_collected', 0) + metrics.get('avg_comment', 0)
            like_rate = (metrics.get('avg_liked', 0) / total_interaction * 100) if total_interaction > 0 else 0
            metrics_data.append(["平均点赞率", f"{like_rate:.1f}%"])

        for i, (key, value) in enumerate(metrics_data, start=4):
            ws[f'D{i}'] = key
            ws[f'E{i}'] = value

    # 调整列宽
    for col in range(1, 6):
        ws.column_dimensions[get_column_letter(col)].width = 15


def create_title_analysis_sheet(wb, data):
    """创建标题分析工作表"""
    ws = wb.create_sheet("标题分析")

    # 标题
    ws['A1'] = "标题特征分析"
    ws['A1'].font = Font(size=14, bold=True)
    ws.merge_cells('A1:D1')

    if 'title_patterns' in data:
        title_data = data['title_patterns']

        # 基础统计
        ws['A3'] = "基础统计"
        ws['A3'].font = Font(bold=True)
        ws['A4'] = "平均长度"
        ws['B4'] = f"{title_data.get('avg_length', 0)} 字"

        # 长度分布
        ws['A6'] = "长度分布"
        ws['A6'].font = Font(bold=True)
        row = 7
        for range_key, count in title_data.get('length_distribution', {}).items():
            ws[f'A{row}'] = range_key
            ws[f'B{row}'] = count
            row += 1

        # 高频关键词
        ws['D3'] = "高频关键词TOP20"
        ws['D3'].font = Font(bold=True)
        ws['D4'] = "关键词"
        ws['E4'] = "出现次数"

        row = 5
        for keyword in title_data.get('top_keywords', [])[:20]:
            ws[f'D{row}'] = keyword['word']
            ws[f'E{row}'] = keyword['count']
            row += 1

        # 常见模式
        ws['G3'] = "常见模式"
        ws['G3'].font = Font(bold=True)
        ws['G4'] = "模式"
        ws['H4'] = "出现率"

        row = 5
        for pattern in title_data.get('common_patterns', [])[:10]:
            ws[f'G{row}'] = pattern['pattern']
            ws[f'H{row}'] = f"{pattern['percentage']}%"
            row += 1

    # 调整列宽
    for col in range(1, 9):
        ws.column_dimensions[get_column_letter(col)].width = 15


def create_content_analysis_sheet(wb, data):
    """创建内容分析工作表"""
    ws = wb.create_sheet("内容分析")

    ws['A1'] = "内容特征分析"
    ws['A1'].font = Font(size=14, bold=True)
    ws.merge_cells('A1:D1')

    if 'content_patterns' in data:
        content_data = data['content_patterns']

        # 基础统计
        ws['A3'] = "基础统计"
        ws['A3'].font = Font(bold=True)
        ws['A4'] = "平均长度"
        ws['B4'] = f"{content_data.get('avg_length', 0)} 字"

        # 内容结构
        ws['A6'] = "内容结构特征"
        ws['A6'].font = Font(bold=True)
        ws['A7'] = "（显示包含该特征的笔记占比）"
        ws['A7'].font = Font(size=9, italic=True, color="666666")

        # 中文映射
        structure_name_map = {
            'has_list': '包含列表/编号',
            'has_steps': '包含步骤说明',
            'has_tips': '包含小贴士/技巧',
            'has_warning': '包含注意事项/警告',
            'has_summary': '包含总结',
            'has_cta': '包含行动号召（关注/点赞/收藏）'
        }

        row = 8
        for structure, percentage in content_data.get('structure_patterns', {}).items():
            structure_name = structure_name_map.get(structure, structure.replace('has_', '包含'))
            ws[f'A{row}'] = structure_name
            ws[f'B{row}'] = f"{percentage}%"
            row += 1

        # 高频词汇
        ws['D3'] = "高频词汇TOP20"
        ws['D3'].font = Font(bold=True)
        ws['D4'] = "词汇"
        ws['E4'] = "频次"

        row = 5
        for keyword in content_data.get('top_keywords', [])[:20]:
            ws[f'D{row}'] = keyword['word']
            ws[f'E{row}'] = keyword['count']
            row += 1

        # 热门标签
        ws['G3'] = "热门标签TOP15"
        ws['G3'].font = Font(bold=True)
        ws['G4'] = "标签"
        ws['H4'] = "使用次数"

        row = 5
        top_tags = content_data.get('top_tags', [])
        if top_tags:
            for tag in top_tags[:15]:
                ws[f'G{row}'] = tag.get('tag', '')
                ws[f'H{row}'] = tag.get('count', 0)
                row += 1
        else:
            ws[f'G{row}'] = "（暂无标签数据）"
            ws[f'G{row}'].font = Font(size=9, italic=True, color="999999")

    # 调整列宽
    for col in range(1, 9):
        ws.column_dimensions[get_column_letter(col)].width = 15


def create_cover_and_images_sheet(wb, data):
    """
    创建封面与配图分析工作表（合并原封面分析和图片OCR分析）

    结构：
    一、封面分析
    二、配图OCR分析
    """
    ws = wb.create_sheet("封面与配图分析")

    # ==================== 标题 ====================
    ws['A1'] = "🎨 封面与配图分析"
    ws['A1'].font = Font(size=16, bold=True, color="0066CC")
    ws.merge_cells('A1:G1')

    ws['A2'] = "封面视觉策略 + 配图文字识别综合分析"
    ws['A2'].font = Font(size=10, italic=True, color="666666")
    ws.merge_cells('A2:G2')

    row = 4

    # ==================== 一、封面分析 ====================
    ws[f'A{row}'] = "【一、封面分析】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:G{row}')
    row += 2

    if 'cover_features' in data and data['cover_features'].get('enabled', True):
        cover_data = data['cover_features']

        # 1.1 文字分析
        if 'text_analysis' in cover_data:
            text_data = cover_data['text_analysis']
            ws[f'A{row}'] = "1.1 封面文字分析"
            ws[f'A{row}'].font = Font(bold=True, size=11)
            row += 1

            ws[f'A{row}'] = "包含文字的封面比例"
            ws[f'B{row}'] = f"{text_data.get('text_coverage_rate', 0)}%"
            row += 1

            ws[f'A{row}'] = "平均文字长度"
            ws[f'B{row}'] = f"{text_data.get('avg_text_length', 0)} 字"
            row += 1

            # 高频关键词（放在右侧）
            kw_start_row = row - 2
            ws[f'D{kw_start_row}'] = "封面文字高频词TOP10"
            ws[f'D{kw_start_row}'].font = Font(bold=True)
            ws[f'D{kw_start_row + 1}'] = "关键词"
            ws[f'E{kw_start_row + 1}'] = "出现次数"

            kw_row = kw_start_row + 2
            for keyword in text_data.get('top_keywords', [])[:10]:
                ws[f'D{kw_row}'] = keyword['word']
                ws[f'E{kw_row}'] = keyword['count']
                kw_row += 1

            row = max(row, kw_row) + 1

        # 1.2 颜色分析
        if 'color_analysis' in cover_data:
            color_data = cover_data['color_analysis']
            ws[f'A{row}'] = "1.2 色彩分析"
            ws[f'A{row}'].font = Font(bold=True, size=11)
            row += 1

            ws[f'A{row}'] = "主流色调"
            style_dist = color_data.get('color_style_distribution', {})
            if style_dist:
                dominant_style = max(style_dist.items(), key=lambda x: x[1])[0]
                ws[f'B{row}'] = dominant_style
            row += 1

            ws[f'A{row}'] = "平均亮度"
            ws[f'B{row}'] = color_data.get('avg_brightness', 127)
            row += 1

            # 流行色
            ws[f'A{row}'] = "流行配色TOP5"
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            for color in color_data.get('popular_colors', [])[:5]:
                ws[f'A{row}'] = color['color']
                ws[f'B{row}'] = color['count']
                try:
                    hex_color = color['color'].replace('#', '')
                    cell = ws[f'A{row}']
                    if not isinstance(cell, MergedCell):
                        cell.fill = PatternFill(
                            start_color=hex_color,
                            end_color=hex_color,
                            fill_type="solid"
                        )
                except:
                    pass
                row += 1
            row += 1

        # 1.3 布局分析
        if 'layout_analysis' in cover_data:
            layout_data = cover_data['layout_analysis']
            ws[f'A{row}'] = "1.3 布局分析"
            ws[f'A{row}'].font = Font(bold=True, size=11)
            row += 1

            ws[f'A{row}'] = "拼图使用率"
            ws[f'B{row}'] = f"{layout_data.get('collage_rate', 0)}%"
            ws[f'D{row}'] = "边框使用率"
            ws[f'E{row}'] = f"{layout_data.get('border_rate', 0)}%"
            row += 1

            # 图片方向分布
            ws[f'A{row}'] = "图片方向分布"
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            for orientation, count in layout_data.get('orientation_distribution', {}).items():
                ws[f'A{row}'] = orientation
                ws[f'B{row}'] = count
                row += 1
            row += 1

        # 1.4 视觉元素分析
        if 'visual_analysis' in cover_data:
            visual_data = cover_data['visual_analysis']
            ws[f'A{row}'] = "1.4 视觉元素"
            ws[f'A{row}'].font = Font(bold=True, size=11)
            row += 1

            ws[f'A{row}'] = "产品图比例"
            ws[f'B{row}'] = f"{visual_data.get('product_image_rate', 0)}%"
            ws[f'D{row}'] = "人物图比例"
            ws[f'E{row}'] = f"{visual_data.get('people_image_rate', 0)}%"
            row += 1

            # 复杂度分布
            ws[f'A{row}'] = "视觉复杂度分布"
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            for complexity, count in visual_data.get('complexity_distribution', {}).items():
                ws[f'A{row}'] = complexity
                ws[f'B{row}'] = count
                row += 1
    else:
        ws[f'A{row}'] = "封面分析未启用或数据不可用"
        ws[f'A{row}'].font = Font(italic=True, color="999999")
        row += 1
        ws[f'A{row}'] = "提示：安装PaddleOCR可启用封面文字识别功能"
        row += 1

    row += 2

    # ==================== 二、配图OCR分析 ====================
    ws[f'A{row}'] = "【二、配图OCR分析】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:G{row}')
    row += 2

    if 'all_images_ocr' in data and data['all_images_ocr'].get('enabled', True):
        ocr_data = data['all_images_ocr']

        # 2.1 图片统计信息
        ws[f'A{row}'] = "2.1 图片统计"
        ws[f'A{row}'].font = Font(bold=True, size=11)
        row += 1

        ws[f'A{row}'] = "总笔记数"
        ws[f'B{row}'] = ocr_data.get('total_notes', 0)
        ws[f'D{row}'] = "总图片数"
        ws[f'E{row}'] = ocr_data.get('total_images', 0)
        row += 1

        ws[f'A{row}'] = "平均图片数/篇"
        ws[f'B{row}'] = ocr_data.get('avg_images_per_note', 0)
        row += 2

        # 图片数量分布
        if 'image_count_distribution' in ocr_data:
            ws[f'A{row}'] = "图片数量分布"
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            for range_key, count in ocr_data['image_count_distribution'].items():
                ws[f'A{row}'] = range_key
                ws[f'B{row}'] = count
                row += 1
            row += 1

        # 2.2 OCR文字分析
        if 'ocr_text_analysis' in ocr_data:
            text_data = ocr_data['ocr_text_analysis']
            ws[f'A{row}'] = "2.2 OCR文字分析"
            ws[f'A{row}'].font = Font(bold=True, size=11)
            row += 1

            ws[f'A{row}'] = "包含文字的图片数"
            ws[f'B{row}'] = text_data.get('total_images_with_text', 0)
            ws[f'D{row}'] = "文字覆盖率"
            ws[f'E{row}'] = f"{text_data.get('text_coverage_rate', 0)}%"
            row += 1

            ws[f'A{row}'] = "识别的文本项数"
            ws[f'B{row}'] = text_data.get('total_text_items', 0)
            row += 2

            # 高频关键词（放在右侧）
            if 'top_keywords' in text_data and text_data['top_keywords']:
                kw_start_row = row - 3
                ws[f'D{row}'] = "配图文字高频词TOP20"
                ws[f'D{row}'].font = Font(bold=True, size=11)
                row += 1
                ws[f'D{row}'] = "关键词"
                ws[f'E{row}'] = "出现次数"
                row += 1

                for keyword in text_data['top_keywords'][:20]:
                    ws[f'D{row}'] = keyword['word']
                    ws[f'E{row}'] = keyword['count']
                    row += 1

        # 2.3 封面 vs 非封面对比
        if 'cover_vs_other' in ocr_data:
            compare_data = ocr_data['cover_vs_other']
            ws[f'A{row}'] = "2.3 封面vs配图对比"
            ws[f'A{row}'].font = Font(bold=True, size=11)
            row += 1

            ws[f'A{row}'] = "封面图片数"
            ws[f'B{row}'] = compare_data.get('cover_count', 0)
            ws[f'D{row}'] = "非封面图片数"
            ws[f'E{row}'] = compare_data.get('other_count', 0)
            row += 1

            ws[f'A{row}'] = "封面文字率"
            ws[f'B{row}'] = f"{compare_data.get('cover_text_rate', 0)}%"
            ws[f'D{row}'] = "非封面文字率"
            ws[f'E{row}'] = f"{compare_data.get('other_text_rate', 0)}%"
            row += 1

            # 分析结论
            cover_rate = compare_data.get('cover_text_rate', 0)
            other_rate = compare_data.get('other_text_rate', 0)
            ws[f'A{row}'] = "📊 分析结论"
            ws[f'A{row}'].font = Font(bold=True)
            if cover_rate > other_rate:
                ws[f'B{row}'] = "封面更注重文字压图，吸引点击"
            elif other_rate > cover_rate:
                ws[f'B{row}'] = "内页图片文字更多，注重内容传达"
            else:
                ws[f'B{row}'] = "封面与配图文字率相近，风格一致"
            ws.merge_cells(f'B{row}:E{row}')
    else:
        ws[f'A{row}'] = "配图OCR分析未启用或数据不可用"
        ws[f'A{row}'].font = Font(italic=True, color="999999")
        row += 1
        ws[f'A{row}'] = "提示：需要安装OCR引擎（PaddleOCR或EasyOCR）"

    # 调整列宽
    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 15
    ws.column_dimensions['C'].width = 5
    ws.column_dimensions['D'].width = 20
    ws.column_dimensions['E'].width = 15
    ws.column_dimensions['F'].width = 15
    ws.column_dimensions['G'].width = 15


def create_product_analysis_sheet(wb, data):
    """创建产品分析工作表"""
    ws = wb.create_sheet("产品分析")

    ws['A1'] = "产品引出与营销场景分析"
    ws['A1'].font = Font(size=14, bold=True, color="0000FF")
    ws.merge_cells('A1:F1')

    ws['A2'] = "（基于AI智能分析，识别笔记中的产品植入时机、营销场景和切入方式）"
    ws['A2'].font = Font(size=9, italic=True, color="666666")
    ws.merge_cells('A2:F2')

    if 'product_features' in data:
        product_data = data['product_features']
        row = 4

        # 产品引出时机分析
        if 'product_timing' in product_data:
            timing = product_data['product_timing']
            ws[f'A{row}'] = "【产品引出时机】"
            ws[f'A{row}'].font = Font(bold=True, size=12)
            row += 1

            if 'distribution' in timing:
                dist = timing['distribution']
                ws[f'A{row}'] = "标题提及率"
                ws[f'B{row}'] = f"{dist.get('title_mention_rate', 0)}%"
                row += 1
                ws[f'A{row}'] = "前1/3提及"
                ws[f'B{row}'] = f"{dist.get('early_mention_rate', 0)}%"
                row += 1
                ws[f'A{row}'] = "中1/3提及"
                ws[f'B{row}'] = f"{dist.get('middle_mention_rate', 0)}%"
                row += 1
                ws[f'A{row}'] = "后1/3提及"
                ws[f'B{row}'] = f"{dist.get('late_mention_rate', 0)}%"
                row += 1
                ws[f'A{row}'] = "多次提及率"
                ws[f'B{row}'] = f"{dist.get('multiple_mention_rate', 0)}%"
                row += 2

            # 新增：3个最佳策略展示区块
            if 'top3_strategies' in timing and timing['top3_strategies']:
                ws[f'A{row}'] = "⭐ 三大最佳策略（供业务选择）"
                ws[f'A{row}'].font = Font(bold=True, size=12, color="FF0000")
                ws[f'A{row}'].fill = PatternFill(start_color="FFF3CD", fill_type="solid")
                ws.merge_cells(f'A{row}:F{row}')
                row += 2

                for i, strategy in enumerate(timing['top3_strategies'][:3], 1):
                    # 策略标题
                    rec = strategy.get('recommendation', '')
                    ws[f'A{row}'] = f"策略{i}: {strategy.get('name', '')}"
                    ws[f'A{row}'].font = Font(bold=True, color="0066CC")
                    ws[f'B{row}'] = rec
                    ws[f'B{row}'].font = Font(size=10, color="FF6600" if i == 1 else "666666")
                    ws.merge_cells(f'B{row}:F{row}')
                    row += 1

                    # 策略摘要
                    ws[f'A{row}'] = "策略说明"
                    ws[f'B{row}'] = strategy.get('summary', '')
                    ws.merge_cells(f'B{row}:F{row}')
                    row += 1

                    # 适用场景
                    ws[f'A{row}'] = "适用场景"
                    ws[f'B{row}'] = strategy.get('applicable_scene', '')
                    ws.merge_cells(f'B{row}:F{row}')
                    row += 1

                    # 操作要点
                    tips = strategy.get('operation_tips', [])
                    if tips:
                        ws[f'A{row}'] = "操作要点"
                        ws[f'B{row}'] = ' | '.join(tips[:3])
                        ws[f'B{row}'].font = Font(size=9)
                        ws.merge_cells(f'B{row}:F{row}')
                        row += 1

                    # 预期效果
                    ws[f'A{row}'] = "预期效果"
                    ws[f'B{row}'] = strategy.get('expected_effect', '')
                    ws[f'B{row}'].font = Font(size=9, color="666666")
                    ws.merge_cells(f'B{row}:F{row}')
                    row += 1

                    # 数据支撑
                    ws[f'A{row}'] = "数据支撑"
                    ws[f'B{row}'] = strategy.get('data_support', '')
                    ws[f'B{row}'].font = Font(size=9, italic=True, color="0066CC")
                    ws.merge_cells(f'B{row}:F{row}')
                    row += 2

            elif 'optimal_strategy' in timing:
                # 向后兼容：如果没有top3_strategies，显示单个最佳策略
                ws[f'A{row}'] = "最佳策略"
                # P1-fix-2: 先写值再 merge，避免 MergedCell 只读错误
                ws[f'B{row}'] = timing['optimal_strategy']
                ws.merge_cells(f'B{row}:F{row}')
                row += 2

        # 营销场景分析
        if 'marketing_scenes' in product_data:
            scenes = product_data['marketing_scenes']
            ws[f'A{row}'] = "【营销场景分布】"
            ws[f'A{row}'].font = Font(bold=True, size=12)
            row += 1

            if 'distribution' in scenes:
                ws[f'A{row}'] = "场景类型"
                ws[f'B{row}'] = "占比"
                ws[f'C{row}'] = "数量"
                row += 1

                for scene, info in list(scenes['distribution'].items())[:7]:
                    ws[f'A{row}'] = scene
                    ws[f'B{row}'] = f"{info.get('percentage', 0)}%"
                    ws[f'C{row}'] = info.get('count', 0)
                    row += 1
                row += 1

            if 'top_scenes' in scenes:
                ws[f'A{row}'] = "TOP3场景"
                ws.merge_cells(f'B{row}:D{row}')
                ws[f'B{row}'] = ', '.join(scenes['top_scenes'])
                row += 2

        # 切入方式分析
        if 'approach_methods' in product_data:
            approaches = product_data['approach_methods']
            ws[f'D3'] = "【切入方式分析】"
            ws['D3'].font = Font(bold=True, size=12)

            d_row = 4
            if 'distribution' in approaches:
                ws[f'D{d_row}'] = "切入方式"
                ws[f'E{d_row}'] = "使用率"
                d_row += 1

                for approach, info in list(approaches['distribution'].items())[:6]:
                    ws[f'D{d_row}'] = approach
                    ws[f'E{d_row}'] = f"{info.get('percentage', 0)}%"
                    d_row += 1

            if 'tips' in approaches:
                d_row += 1
                ws[f'D{d_row}'] = "建议"
                ws[f'D{d_row}'].font = Font(bold=True)
                d_row += 1
                for tip in approaches['tips']:
                    # 先设置值，再合并单元格（避免MergedCell只读错误）
                    ws[f'D{d_row}'] = f"• {tip}"
                    ws.merge_cells(f'D{d_row}:F{d_row}')
                    d_row += 1

        # 提及策略分析
        if 'mention_strategies' in product_data:
            strategies = product_data['mention_strategies']
            ws[f'A{row}'] = "【提及策略分析】"
            ws[f'A{row}'].font = Font(bold=True, size=12)
            row += 1

            if 'distribution' in strategies:
                dist = strategies['distribution']
                ws[f'A{row}'] = "软植入"
                ws[f'B{row}'] = f"{dist.get('soft_mention', 0)}%"
                ws[f'C{row}'] = "硬广告"
                ws[f'D{row}'] = f"{dist.get('hard_mention', 0)}%"
                row += 1
                ws[f'A{row}'] = "对比提及"
                ws[f'B{row}'] = f"{dist.get('comparison', 0)}%"
                ws[f'C{row}'] = "故事化"
                ws[f'D{row}'] = f"{dist.get('story_telling', 0)}%"
                row += 2

            if 'balance_suggestion' in strategies:
                ws[f'A{row}'] = "平衡建议"
                # 先设置值，再合并单元格
                ws[f'B{row}'] = strategies['balance_suggestion']
                ws.merge_cells(f'B{row}:F{row}')

    else:
        ws['A3'] = "产品分析数据暂无"

    # 调整列宽
    for col in range(1, 7):
        ws.column_dimensions[get_column_letter(col)].width = 18


def create_final_delivery_sheet(wb, data):
    """
    创建最终交付工作表（合并爆文模型+创作指南，带AI思考推理）

    基于 viral_model['final_delivery'] 中的AI综合推理结果生成
    如果AI推理成功，使用AI生成的结论和思考
    如果AI推理失败，回退到数据驱动的模板方式
    """
    ws = wb.create_sheet("📋 爆文创作模型")

    # 获取AI综合推理结果
    viral_model = data.get('viral_model', {})
    final_delivery = viral_model.get('final_delivery', {})
    ai_success = final_delivery.get('status') == 'success'

    # 标题
    if ai_success:
        ws['A1'] = "🔥 小红书爆文创作模型（AI深度推理版）"
        ws['A2'] = f"基于{data.get('total_notes', 0)}篇爆款数据+AI综合推理生成，每条结论附带AI思考依据"
    else:
        ws['A1'] = "🔥 小红书爆文创作模型（数据驱动版）"
        ws['A2'] = "基于数据分析生成，AI推理服务暂不可用"

    ws['A1'].font = Font(size=16, bold=True, color="FF0000")
    ws.merge_cells('A1:H1')
    ws['A2'].font = Font(size=10, italic=True, color="666666")
    ws.merge_cells('A2:H2')

    row = 4

    # ==================== 使用AI推理结果 ====================
    if ai_success:
        row = _write_ai_driven_content(ws, row, data, final_delivery)
    else:
        # 回退到数据驱动模式
        row = _write_data_driven_content(ws, row, data)

    # ==================== 第五部分：创作检查清单 ====================
    ws[f'A{row}'] = "【五、发布前检查清单】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    # 优先使用AI生成的检查清单
    if ai_success and 'checklist' in final_delivery:
        ai_checklist = final_delivery['checklist']
        for item in ai_checklist[:8]:
            if isinstance(item, dict):
                ws[f'A{row}'] = f"☑ {item.get('item', '')}"
                ws[f'A{row}'].font = Font(size=11)
                ws.merge_cells(f'A{row}:D{row}')
                ws[f'E{row}'] = item.get('reason', '')
                ws[f'E{row}'].font = Font(size=9, color="666666")
                ws.merge_cells(f'E{row}:H{row}')
                row += 1
    else:
        # 默认检查清单
        checklist = [
            ("☑ 标题是否包含关键词和情绪点？", "关键词提升搜索匹配，情绪词提升点击率"),
            ("☑ 封面图是否有大字压图？", "信息流中0.3秒定生死，大字才能抓眼球"),
            ("☑ 前30字是否设置了钩子？", "开头决定用户是否继续阅读"),
            ("☑ 是否有3个以上的价值点？", "多个价值点提升内容厚度和收藏率"),
            ("☑ 图片是否清晰且有对比？", "高质量图片是基本门槛"),
            ("☑ 是否添加了3-5个精准标签？", "标签决定内容的曝光池"),
            ("☑ 发布时间是否在高峰期（19-22点）？", "高峰期发布初始流量更好")
        ]
        for item, reason in checklist:
            ws[f'A{row}'] = item
            ws[f'A{row}'].font = Font(size=11)
            ws.merge_cells(f'A{row}:D{row}')
            ws[f'E{row}'] = reason
            ws[f'E{row}'].font = Font(size=9, color="666666")
            ws.merge_cells(f'E{row}:H{row}')
            row += 1

    row += 2

    # ==================== 第六部分：综合成功模式 ====================
    ws[f'A{row}'] = "【六、综合成功模式总结】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    if ai_success and 'success_pattern_summary' in final_delivery:
        ws[f'A{row}'] = "🎯 AI总结"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        row += 1
        summary_text = final_delivery['success_pattern_summary']
        # 分行显示长文本
        for line in _split_text(summary_text, 80):
            ws[f'B{row}'] = line
            ws.merge_cells(f'B{row}:H{row}')
            ws[f'B{row}'].font = Font(size=10)
            row += 1
        row += 1

    # 成功案例
    ws[f'A{row}'] = "📚 成功案例参考"
    ws[f'A{row}'].font = Font(bold=True, size=12, color="0066CC")
    row += 1

    success_examples = viral_model.get('success_examples', [])[:3]
    for i, example in enumerate(success_examples, 1):
        ws[f'A{row}'] = f"案例{i}"
        ws[f'A{row}'].font = Font(bold=True, color="0066CC")
        ws[f'B{row}'] = example.get('title', '')
        ws.merge_cells(f'B{row}:F{row}')
        ws[f'G{row}'] = f"互动: {example.get('interaction_score', 0)}"
        ws[f'G{row}'].font = Font(bold=True, color="FF0000")
        row += 1

        for highlight in example.get('highlights', [])[:3]:
            ws[f'B{row}'] = f"• {highlight}"
            ws.merge_cells(f'B{row}:H{row}')
            ws[f'B{row}'].font = Font(size=9, color="666666")
            row += 1
        row += 1

    # 设置列宽
    ws.column_dimensions['A'].width = 12
    ws.column_dimensions['B'].width = 30
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['D'].width = 15
    ws.column_dimensions['E'].width = 20
    ws.column_dimensions['F'].width = 15
    ws.column_dimensions['G'].width = 15
    ws.column_dimensions['H'].width = 20

    # 添加边框
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    for r in ws.iter_rows(min_row=4, max_row=row, min_col=1, max_col=8):
        for cell in r:
            if not isinstance(cell, MergedCell) and cell.value:
                cell.border = thin_border


def _split_text(text: str, max_len: int = 80) -> List[str]:
    """将长文本按指定长度分割"""
    if not text:
        return []
    lines = []
    while len(text) > max_len:
        # 找到最近的标点或空格分割
        split_pos = max_len
        for char in ['。', '，', '；', '、', ' ', '！', '？']:
            pos = text[:max_len].rfind(char)
            if pos > max_len // 2:
                split_pos = pos + 1
                break
        lines.append(text[:split_pos])
        text = text[split_pos:]
    if text:
        lines.append(text)
    return lines


def _write_ai_driven_content(ws, row: int, data: Dict, final_delivery: Dict) -> int:
    """写入AI驱动的内容（基于AI综合推理结果）"""

    # ==================== 第一部分：标题策略 ====================
    ws[f'A{row}'] = "【一、标题策略】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    title_strategy = final_delivery.get('title_strategy', {})
    conclusions = title_strategy.get('conclusions', [])

    for conclusion in conclusions:
        if isinstance(conclusion, dict):
            # 📌 结论
            ws[f'A{row}'] = "📌 结论"
            ws[f'A{row}'].font = Font(bold=True, color="FF0000")
            ws[f'B{row}'] = conclusion.get('point', '')
            ws[f'B{row}'].font = Font(bold=True, size=12)
            ws.merge_cells(f'B{row}:H{row}')
            row += 1

            # 💭 思考（AI推理）
            ws[f'A{row}'] = "💭 思考"
            ws[f'A{row}'].font = Font(bold=True, color="0066CC")
            reasoning = conclusion.get('reasoning', '')
            ws[f'B{row}'] = reasoning[:200] if len(reasoning) > 200 else reasoning
            ws[f'B{row}'].font = Font(size=9, color="666666")
            ws.merge_cells(f'B{row}:H{row}')
            row += 2

    # 标题模板
    templates = title_strategy.get('templates', [])
    if templates:
        ws[f'A{row}'] = "📌 推荐标题模板"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        row += 1
        for tpl in templates[:5]:
            ws[f'B{row}'] = f"• {tpl}"
            ws.merge_cells(f'B{row}:H{row}')
            row += 1
        row += 1

    # 必含关键词
    keywords = title_strategy.get('keywords_must_have', [])
    if keywords:
        ws[f'A{row}'] = "📌 必含关键词"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        ws[f'B{row}'] = '、'.join(keywords[:10])
        ws[f'B{row}'].font = Font(bold=True, size=11)
        ws.merge_cells(f'B{row}:H{row}')
        row += 2

    row += 1

    # ==================== 第二部分：内容策略 ====================
    ws[f'A{row}'] = "【二、内容策略】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    content_strategy = final_delivery.get('content_strategy', {})
    conclusions = content_strategy.get('conclusions', [])

    for conclusion in conclusions:
        if isinstance(conclusion, dict):
            ws[f'A{row}'] = "📌 结论"
            ws[f'A{row}'].font = Font(bold=True, color="FF0000")
            ws[f'B{row}'] = conclusion.get('point', '')
            ws[f'B{row}'].font = Font(bold=True, size=12)
            ws.merge_cells(f'B{row}:H{row}')
            row += 1

            ws[f'A{row}'] = "💭 思考"
            ws[f'A{row}'].font = Font(bold=True, color="0066CC")
            reasoning = conclusion.get('reasoning', '')
            ws[f'B{row}'] = reasoning[:200] if len(reasoning) > 200 else reasoning
            ws[f'B{row}'].font = Font(size=9, color="666666")
            ws.merge_cells(f'B{row}:H{row}')
            row += 2

    # 结构指南
    structure_guide = content_strategy.get('structure_guide', '')
    if structure_guide:
        ws[f'A{row}'] = "📌 内容结构建议"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        ws[f'B{row}'] = structure_guide
        ws.merge_cells(f'B{row}:H{row}')
        row += 2

    # 开头钩子
    hooks = content_strategy.get('hooks', [])
    if hooks:
        ws[f'A{row}'] = "📌 开头钩子示例"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        row += 1
        for hook in hooks[:3]:
            ws[f'B{row}'] = f"• {hook}"
            ws.merge_cells(f'B{row}:H{row}')
            row += 1
        row += 1

    row += 1

    # ==================== 第三部分：封面策略 ====================
    ws[f'A{row}'] = "【三、封面策略】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    cover_strategy = final_delivery.get('cover_strategy', {})
    conclusions = cover_strategy.get('conclusions', [])

    for conclusion in conclusions:
        if isinstance(conclusion, dict):
            ws[f'A{row}'] = "📌 结论"
            ws[f'A{row}'].font = Font(bold=True, color="FF0000")
            ws[f'B{row}'] = conclusion.get('point', '')
            ws[f'B{row}'].font = Font(bold=True, size=12)
            ws.merge_cells(f'B{row}:H{row}')
            row += 1

            ws[f'A{row}'] = "💭 思考"
            ws[f'A{row}'].font = Font(bold=True, color="0066CC")
            reasoning = conclusion.get('reasoning', '')
            ws[f'B{row}'] = reasoning[:200] if len(reasoning) > 200 else reasoning
            ws[f'B{row}'].font = Font(size=9, color="666666")
            ws.merge_cells(f'B{row}:H{row}')
            row += 2

    # 封面文字建议
    text_guide = cover_strategy.get('text_guide', '')
    if text_guide:
        ws[f'A{row}'] = "📌 封面文字建议"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        ws[f'B{row}'] = text_guide
        ws.merge_cells(f'B{row}:H{row}')
        row += 2

    # 视觉设计建议
    visual_guide = cover_strategy.get('visual_guide', '')
    if visual_guide:
        ws[f'A{row}'] = "📌 视觉设计建议"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        ws[f'B{row}'] = visual_guide
        ws.merge_cells(f'B{row}:H{row}')
        row += 2

    row += 1

    # ==================== 第四部分：产品植入策略 ====================
    ws[f'A{row}'] = "【四、产品植入策略】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    product_strategy = final_delivery.get('product_strategy', {})
    conclusions = product_strategy.get('conclusions', [])

    for conclusion in conclusions:
        if isinstance(conclusion, dict):
            ws[f'A{row}'] = "📌 结论"
            ws[f'A{row}'].font = Font(bold=True, color="FF0000")
            ws[f'B{row}'] = conclusion.get('point', '')
            ws[f'B{row}'].font = Font(bold=True, size=12)
            ws.merge_cells(f'B{row}:H{row}')
            row += 1

            ws[f'A{row}'] = "💭 思考"
            ws[f'A{row}'].font = Font(bold=True, color="0066CC")
            reasoning = conclusion.get('reasoning', '')
            ws[f'B{row}'] = reasoning[:200] if len(reasoning) > 200 else reasoning
            ws[f'B{row}'].font = Font(size=9, color="666666")
            ws.merge_cells(f'B{row}:H{row}')
            row += 2

    # 时机建议
    timing_guide = product_strategy.get('timing_guide', '')
    if timing_guide:
        ws[f'A{row}'] = "📌 产品引出时机"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        ws[f'B{row}'] = timing_guide
        ws.merge_cells(f'B{row}:H{row}')
        row += 2

    # 场景建议
    scene_guide = product_strategy.get('scene_guide', '')
    if scene_guide:
        ws[f'A{row}'] = "📌 营销场景建议"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        ws[f'B{row}'] = scene_guide
        ws.merge_cells(f'B{row}:H{row}')
        row += 2

    row += 1

    return row


def _write_data_driven_content(ws, row: int, data: Dict) -> int:
    """写入数据驱动的内容（当AI推理不可用时的回退方案）"""

    # ==================== 第一部分：标题策略 ====================
    ws[f'A{row}'] = "【一、标题策略】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    title_data = data.get('title_patterns', {})
    avg_title_len = title_data.get('avg_length', 17)
    top_keywords = title_data.get('top_keywords', [])[:5]

    # 结论1：标题长度
    ws[f'A{row}'] = "📌 结论"
    ws[f'A{row}'].font = Font(bold=True, color="FF0000")
    ws[f'B{row}'] = f"最佳标题长度：{avg_title_len} 字左右"
    ws[f'B{row}'].font = Font(bold=True, size=12)
    ws.merge_cells(f'B{row}:D{row}')
    row += 1

    ws[f'A{row}'] = "💭 数据依据"
    ws[f'A{row}'].font = Font(bold=True, color="0066CC")
    ws[f'B{row}'] = f"分析{data.get('total_notes', 0)}篇爆款笔记，标题平均长度为{avg_title_len}字。（注：AI推理服务暂不可用，此为数据统计结果）"
    ws[f'B{row}'].font = Font(size=9, color="666666")
    ws.merge_cells(f'B{row}:H{row}')
    row += 2

    # 结论2：高频关键词
    ws[f'A{row}'] = "📌 结论"
    ws[f'A{row}'].font = Font(bold=True, color="FF0000")
    kw_text = '、'.join([kw['word'] for kw in top_keywords]) if top_keywords else "暂无数据"
    ws[f'B{row}'] = f"标题高频词：{kw_text}"
    ws[f'B{row}'].font = Font(bold=True, size=12)
    ws.merge_cells(f'B{row}:H{row}')
    row += 3

    # ==================== 第二部分：内容策略 ====================
    ws[f'A{row}'] = "【二、内容策略】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    content_data = data.get('content_patterns', {})
    avg_content_len = content_data.get('avg_length', 300)

    ws[f'A{row}'] = "📌 结论"
    ws[f'A{row}'].font = Font(bold=True, color="FF0000")
    ws[f'B{row}'] = f"最佳内容长度：{avg_content_len} 字左右"
    ws[f'B{row}'].font = Font(bold=True, size=12)
    ws.merge_cells(f'B{row}:D{row}')
    row += 3

    # ==================== 第三部分：封面策略 ====================
    ws[f'A{row}'] = "【三、封面策略】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    cover_data = data.get('cover_features', {})
    text_coverage = cover_data.get('text_analysis', {}).get('text_coverage_rate', 0)

    ws[f'A{row}'] = "📌 结论"
    ws[f'A{row}'].font = Font(bold=True, color="FF0000")
    ws[f'B{row}'] = f"封面文字覆盖率：{text_coverage}%"
    ws[f'B{row}'].font = Font(bold=True, size=12)
    ws.merge_cells(f'B{row}:H{row}')
    row += 3

    # ==================== 第四部分：产品植入策略 ====================
    ws[f'A{row}'] = "【四、产品植入策略】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:H{row}')
    row += 2

    product_data = data.get('product_features', {})
    top_scenes = product_data.get('marketing_scenes', {}).get('top_scenes', [])

    if top_scenes:
        ws[f'A{row}'] = "📌 结论"
        ws[f'A{row}'].font = Font(bold=True, color="FF0000")
        ws[f'B{row}'] = f"热门营销场景：{', '.join(top_scenes[:3])}"
        ws[f'B{row}'].font = Font(bold=True, size=12)
        ws.merge_cells(f'B{row}:H{row}')
        row += 2

    row += 1

    return row


def create_multimodal_analysis_sheet(wb, data):
    """
    创建多模态AI分析工作表（总-分结构重构版）

    结构：
    一、分析摘要（核心结论前置）
    二、分析概况
    三、分析详情
    四、创作建议
    """
    ws = wb.create_sheet("AI深度分析")

    ws['A1'] = "🤖 AI多模态深度分析（图文联合理解）"
    ws['A1'].font = Font(size=14, bold=True, color="FF6B35")
    ws.merge_cells('A1:G1')

    ws['A2'] = "基于视觉AI理解的图文爆款要素提取 | 采用「总-分」结构呈现"
    ws['A2'].font = Font(size=10, italic=True)
    ws.merge_cells('A2:G2')

    if 'viral_model' not in data or 'multimodal_insights' not in data['viral_model']:
        ws['A3'] = "多模态分析数据不可用"
        ws['A4'] = "提示：需要配置支持视觉的多模态模型（如qwen3-vl-plus、glm-4v-plus）"
        ws['A5'] = "在.env文件中设置 MULTIMODAL_MODEL_NAME"
        return

    multimodal = data['viral_model']['multimodal_insights']
    row = 4

    # 检查状态
    if multimodal.get('status') == 'disabled':
        ws[f'A{row}'] = "多模态分析未启用"
        ws[f'B{row}'] = multimodal.get('message', '')
        ws.merge_cells(f'B{row}:F{row}')
        return

    if multimodal.get('status') != 'success':
        ws[f'A{row}'] = "多模态分析状态"
        ws[f'B{row}'] = multimodal.get('status', 'unknown')
        row += 1
        if 'message' in multimodal:
            ws[f'A{row}'] = "说明"
            ws[f'B{row}'] = multimodal['message']
            ws.merge_cells(f'B{row}:F{row}')
        return

    # ==================== 一、分析摘要（核心结论前置） ====================
    ws[f'A{row}'] = "【一、分析摘要】⭐ 核心结论"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="FF0000")
    ws[f'A{row}'].fill = PatternFill(start_color="FFF3CD", fill_type="solid")
    ws.merge_cells(f'A{row}:G{row}')
    row += 2

    # 统计信息
    total_notes = data.get('total_notes', 0)
    # 兼容两种数据格式：API格式(type='video')和ViralNote格式(note_type='视频')
    notes = data.get('notes', [])
    video_count = len([n for n in notes if n.get('note_type') == '视频' or n.get('type') == 'video'])
    image_count = total_notes - video_count
    analyzed_count = multimodal.get('analyzed_count', 0)

    # 笔记数量统计
    ws[f'A{row}'] = "📊 笔记数量"
    ws[f'A{row}'].font = Font(bold=True, color="0066CC")
    ws[f'B{row}'] = f"共{total_notes}篇 | 图文{image_count}篇 | 视频{video_count}条 | AI分析{analyzed_count}篇"
    ws.merge_cells(f'B{row}:G{row}')
    row += 1

    # 场景分布总结（从产品分析中提取）
    product_features = data.get('product_features', {})
    marketing_scenes = product_features.get('marketing_scenes', {})
    top_scenes = marketing_scenes.get('top_scenes', [])
    if top_scenes:
        ws[f'A{row}'] = "🎯 场景分布"
        ws[f'A{row}'].font = Font(bold=True, color="0066CC")
        scene_dist = marketing_scenes.get('distribution', {})
        scene_text = ' | '.join([f"{s}({scene_dist.get(s, {}).get('percentage', 0)}%)" for s in top_scenes[:3]])
        ws[f'B{row}'] = scene_text
        ws.merge_cells(f'B{row}:G{row}')
        row += 1

    # 内容方向归纳
    approach_methods = product_features.get('approach_methods', {})
    top_approaches = approach_methods.get('top_approaches', [])
    if top_approaches:
        ws[f'A{row}'] = "📝 内容方向"
        ws[f'A{row}'].font = Font(bold=True, color="0066CC")
        ws[f'B{row}'] = f"主流切入方式：{'、'.join(top_approaches[:3])}"
        ws.merge_cells(f'B{row}:G{row}')
        row += 1

    # 创作特点概括
    title_patterns = data.get('title_patterns', {})
    content_patterns = data.get('content_patterns', {})
    ws[f'A{row}'] = "✨ 创作特点"
    ws[f'A{row}'].font = Font(bold=True, color="0066CC")
    features = []
    if title_patterns.get('avg_length'):
        features.append(f"标题{title_patterns['avg_length']}字")
    if content_patterns.get('avg_length'):
        features.append(f"正文{content_patterns['avg_length']}字")
    cover_features = data.get('cover_features', {})
    text_rate = cover_features.get('text_analysis', {}).get('text_coverage_rate', 0)
    if text_rate:
        features.append(f"封面文字率{text_rate}%")
    ws[f'B{row}'] = ' | '.join(features) if features else "数据统计中..."
    ws.merge_cells(f'B{row}:G{row}')
    row += 2

    # ==================== 二、分析概况 ====================
    ws[f'A{row}'] = "【二、分析概况】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:G{row}')
    row += 2

    ws[f'A{row}'] = "分析笔记数"
    ws[f'B{row}'] = analyzed_count
    row += 1
    import os as os_module
    ws[f'A{row}'] = "分析模型"
    ws[f'B{row}'] = os_module.getenv("MULTIMODAL_MODEL_NAME", "未知")
    row += 1

    # AI识别的爆款要素
    ws[f'A{row}'] = "AI识别要素"
    ws[f'A{row}'].font = Font(bold=True)
    row += 1
    key_elements = [
        "✅ 封面大字压图，对比强烈",
        "✅ 效果前后对比图明显",
        "✅ 产品细节特写清晰",
        "✅ 真人出镜增加可信度",
        "✅ 场景化展示贴近生活"
    ]
    for element in key_elements:
        ws[f'B{row}'] = element
        ws.merge_cells(f'B{row}:F{row}')
        row += 1

    row += 1

    # ==================== 三、分析详情 ====================
    ws[f'A{row}'] = "【三、分析详情】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="0066CC")
    ws[f'A{row}'].fill = PatternFill(start_color="E8F4FF", fill_type="solid")
    ws.merge_cells(f'A{row}:G{row}')
    row += 2

    # 个别笔记洞察
    if 'individual_insights' in multimodal and multimodal['individual_insights']:
        ws[f'A{row}'] = "各笔记AI分析"
        ws[f'A{row}'].font = Font(bold=True, size=12)
        row += 1

        for i, insight in enumerate(multimodal['individual_insights'], 1):
            ws[f'A{row}'] = f"笔记{i}"
            ws[f'A{row}'].font = Font(bold=True, color="0066CC")
            row += 1

            ws[f'A{row}'] = "标题"
            ws[f'B{row}'] = insight.get('note_title', '(无标题)')
            ws.merge_cells(f'B{row}:F{row}')
            row += 1

            ws[f'A{row}'] = "分析图片数"
            ws[f'B{row}'] = insight.get('images_count', 0)
            row += 1

            # AI分析洞察
            insights_data = insight.get('insights')
            if insights_data:
                # 如果是字典格式
                if isinstance(insights_data, dict):
                    # 检查是否有 raw_insights（纯文本）
                    if 'raw_insights' in insights_data or 'parsed' in insights_data:
                        ws[f'A{row}'] = "AI分析要点"
                        ws[f'A{row}'].font = Font(bold=True)
                        row += 1

                        raw_text = insights_data.get('raw_insights', str(insights_data))
                        lines = raw_text.split('\n')
                        for line in lines[:20]:  # 最多显示20行
                            if line.strip():
                                ws[f'B{row}'] = line.strip()
                                ws.merge_cells(f'B{row}:F{row}')
                                row += 1
                    else:
                        # 结构化JSON数据（支持多层嵌套）
                        ws[f'A{row}'] = "AI分析要点"
                        ws[f'A{row}'].font = Font(bold=True)
                        row += 1

                        # 遍历所有分析维度
                        for section_key, section_value in insights_data.items():
                            # 显示大分类标题（如"图片内容分析"）
                            ws[f'B{row}'] = f"【{section_key}】"
                            ws[f'B{row}'].font = Font(bold=True, color="0066CC")
                            ws.merge_cells(f'B{row}:F{row}')
                            row += 1

                            # 处理该分类下的详细内容
                            if isinstance(section_value, dict):
                                # 嵌套字典：显示所有子项
                                if not section_value:
                                    # 空字典
                                    ws[f'C{row}'] = "(无内容)"
                                    ws[f'C{row}'].font = Font(italic=True, color="999999")
                                    row += 1
                                else:
                                    for sub_key, sub_value in section_value.items():
                                        if isinstance(sub_value, str) and sub_value:
                                            # 字符串值：直接显示
                                            ws[f'C{row}'] = f"{sub_key}: {sub_value[:200]}"
                                            ws.merge_cells(f'C{row}:F{row}')
                                            row += 1
                                        elif isinstance(sub_value, list) and sub_value:
                                            # 列表值：显示列表项
                                            ws[f'C{row}'] = f"{sub_key}:"
                                            row += 1
                                            for item in sub_value[:5]:
                                                ws[f'D{row}'] = f"• {str(item)[:200]}"
                                                ws.merge_cells(f'D{row}:F{row}')
                                                row += 1
                                        elif isinstance(sub_value, dict) and sub_value:
                                            # 再嵌套一层字典
                                            ws[f'C{row}'] = f"{sub_key}:"
                                            row += 1
                                            for k, v in list(sub_value.items())[:10]:
                                                if v:
                                                    ws[f'D{row}'] = f"• {k}: {str(v)[:150]}"
                                                    ws.merge_cells(f'D{row}:F{row}')
                                                    row += 1
                                        elif sub_value is not None and str(sub_value).strip():
                                            # 其他非空值
                                            ws[f'C{row}'] = f"{sub_key}: {str(sub_value)[:200]}"
                                            ws.merge_cells(f'C{row}:F{row}')
                                            row += 1
                            elif isinstance(section_value, str) and section_value:
                                # 直接是字符串
                                ws[f'C{row}'] = section_value[:200]
                                ws.merge_cells(f'C{row}:F{row}')
                                row += 1
                            elif isinstance(section_value, list) and section_value:
                                # 直接是列表
                                for item in section_value[:5]:
                                    ws[f'C{row}'] = f"• {str(item)[:200]}"
                                    ws.merge_cells(f'C{row}:F{row}')
                                    row += 1
                            elif section_value is None or (isinstance(section_value, (str, list, dict)) and not section_value):
                                # 空值或None
                                ws[f'C{row}'] = "(无内容)"
                                ws[f'C{row}'].font = Font(italic=True, color="999999")
                                row += 1

                            row += 1  # 每个大分类之间空一行
                # 如果是字符串格式
                elif isinstance(insights_data, str) and insights_data.strip():
                    ws[f'A{row}'] = "AI分析要点"
                    ws[f'A{row}'].font = Font(bold=True)
                    row += 1

                    lines = insights_data.split('\n')
                    for line in lines[:20]:
                        if line.strip():
                            ws[f'B{row}'] = line.strip()
                            ws.merge_cells(f'B{row}:F{row}')
                            row += 1
                else:
                    # 未知格式，显示调试信息
                    ws[f'A{row}'] = "AI分析"
                    ws[f'B{row}'] = f"(数据格式: {type(insights_data).__name__})"
                    ws[f'B{row}'].font = Font(size=9, italic=True, color="999999")
                    row += 1
            else:
                # insights为空
                ws[f'A{row}'] = "AI分析要点"
                ws[f'B{row}'] = "(该笔记的AI分析未成功，可能是图片加载失败或API调用失败)"
                ws[f'B{row}'].font = Font(size=9, italic=True, color="FF0000")
                ws.merge_cells(f'B{row}:F{row}')
                row += 1

            row += 1
    else:
        # 没有individual_insights数据
        ws[f'A{row}'] = "【分析状态】"
        ws[f'A{row}'].font = Font(bold=True, size=12)
        row += 1

        ws[f'A{row}'] = "说明"
        ws[f'B{row}'] = "未检测到各笔记的AI分析数据，可能原因："
        ws.merge_cells(f'B{row}:F{row}')
        row += 1

        reasons = [
            "1. 未配置多模态AI模型（需要在.env中设置MULTIMODAL_MODEL_NAME）",
            "2. 图片下载失败或笔记不包含图片",
            "3. AI API调用失败（检查API密钥和网络连接）",
            "4. 数据采集时未保存图片信息"
        ]

        for reason in reasons:
            ws[f'B{row}'] = reason
            ws[f'B{row}'].font = Font(size=9, color="666666")
            ws.merge_cells(f'B{row}:F{row}')
            row += 1

    # ==================== 四、创作建议 ====================
    ws[f'A{row}'] = "【四、创作建议】"
    ws[f'A{row}'].font = Font(bold=True, size=14, color="00AA00")
    ws[f'A{row}'].fill = PatternFill(start_color="D4EDDA", fill_type="solid")
    ws.merge_cells(f'A{row}:G{row}')
    row += 2

    ws[f'A{row}'] = "AI创作建议汇总"
    ws[f'A{row}'].font = Font(bold=True, size=12)
    row += 1

    creation_tips = [
        ("封面设计", "必须有大字压图，主标题36-48px，使用高对比度配色"),
        ("图片排序", "第1张吸睛-第2-3张对比-第4-6张细节-第7-9张场景"),
        ("文字植入", "每3张图片至少1张有文字说明，关键信息重复3次"),
        ("视觉节奏", "静态产品图与动态使用图交替，避免单调"),
        ("情绪引导", "用视觉冲击制造情绪波动，最后给出解决方案")
    ]

    for tip_name, tip_desc in creation_tips:
        ws[f'A{row}'] = tip_name
        ws[f'B{row}'] = tip_desc
        ws.merge_cells(f'B{row}:F{row}')
        row += 1

    row += 1

    # 补充信息（如果有summary数据）
    if 'summary' in multimodal:
        summary = multimodal['summary']
        ws[f'A{row}'] = "【五、补充信息】"
        ws[f'A{row}'].font = Font(bold=True, size=12, color="666666")
        row += 1

        if isinstance(summary, dict):
            for key, value in summary.items():
                ws[f'A{row}'] = key
                if isinstance(value, (str, int, float)):
                    ws[f'B{row}'] = str(value)
                    ws.merge_cells(f'B{row}:F{row}')
                row += 1
        else:
            ws[f'A{row}'] = "摘要"
            ws[f'B{row}'] = str(summary)
            ws.merge_cells(f'B{row}:F{row}')

    # 调整列宽
    ws.column_dimensions['A'].width = 18
    ws.column_dimensions['B'].width = 50
    ws.column_dimensions['C'].width = 40
    for col in range(4, 7):
        ws.column_dimensions[get_column_letter(col)].width = 15


def create_viral_creation_guide_sheet(wb, data):
    """创建爆文创作指南工作表"""
    ws = wb.create_sheet("创作指南")

    ws['A1'] = "📝 图文爆文创作实操指南"
    ws['A1'].font = Font(size=14, bold=True, color="0066CC")
    ws.merge_cells('A1:G1')

    row = 3

    # 创作公式
    ws[f'A{row}'] = "【一、爆文创作公式】"
    ws[f'A{row}'].font = Font(bold=True, size=12, color="FF0000")
    row += 1

    formulas = [
        ("标题公式", "痛点/好奇 + 数字/对比 + 结果/利益 + 情绪词"),
        ("封面公式", "大字压图 + 对比视觉 + 品牌露出 + 情绪色彩"),
        ("内容公式", "钩子开头 + 干货主体 + 情感共鸣 + 行动引导"),
        ("图片公式", "产品展示 + 效果对比 + 细节特写 + 场景应用")
    ]

    for name, formula in formulas:
        ws[f'A{row}'] = name
        ws[f'B{row}'] = formula
        ws.merge_cells(f'B{row}:F{row}')
        ws[f'B{row}'].font = Font(bold=True, color="0000FF")
        row += 1

    row += 1

    # 图片策略
    ws[f'A{row}'] = "【二、图片拍摄与排版策略】"
    ws[f'A{row}'].font = Font(bold=True, size=12, color="FF0000")
    row += 1

    # 基于分析结果生成图片建议
    if 'all_images_ocr' in data:
        ocr_data = data['all_images_ocr']
        if ocr_data.get('ocr_text_analysis'):
            ws[f'A{row}'] = "封面图要求"
            ws[f'B{row}'] = f"文字覆盖率建议: {ocr_data['ocr_text_analysis'].get('text_coverage_rate', 60)}%"
            row += 1

    image_tips = [
        ("第1张", "封面图：大字标题+产品主图，3秒吸引注意力"),
        ("第2-3张", "效果对比：使用前后/竞品对比，突出差异"),
        ("第4-6张", "细节展示：质地/成分/使用方法，建立信任"),
        ("第7-9张", "场景应用：真实使用场景，增强代入感"),
        ("压字技巧", "字号36-48px，高对比度，关键词加粗加色")
    ]

    for position, tip in image_tips:
        ws[f'A{row}'] = position
        ws[f'B{row}'] = tip
        ws.merge_cells(f'B{row}:F{row}')
        row += 1

    row += 1

    # 文案模板
    ws[f'A{row}'] = "【三、爆款文案模板库】"
    ws[f'A{row}'].font = Font(bold=True, size=12, color="FF0000")
    row += 1

    # 基于AI分析结果生成模板
    if 'viral_model' in data and 'ai_insights' in data['viral_model']:
        ai_insights = data['viral_model'].get('ai_insights', {})
        if isinstance(ai_insights, dict) and 'title_strategy' in ai_insights:
            title_formulas = ai_insights['title_strategy'].get('title_formulas', [])
            for i, formula in enumerate(title_formulas[:3], 1):
                ws[f'A{row}'] = f"标题模板{i}"
                ws[f'B{row}'] = formula
                ws.merge_cells(f'B{row}:F{row}')
                row += 1

    # 通用模板
    templates = [
        ("测评型", "🔥实测N款{产品}，这{数字}款闭眼入！{情绪词}"),
        ("教程型", "💡{技能}保姆级教程！{时间}就能学会，{结果保证}"),
        ("避坑型", "⚠️{产品/行为}的{数字}个大坑，{损失描述}"),
        ("种草型", "✨{发现词}{产品}！{效果描述}，{推荐理由}"),
        ("对比型", "📊{A}VS{B}深度测评，结果{情绪词}了！")
    ]

    for tpl_type, template in templates:
        ws[f'A{row}'] = tpl_type
        ws[f'B{row}'] = template
        ws.merge_cells(f'B{row}:F{row}')
        row += 1

    row += 1

    # 创作检查清单
    ws[f'A{row}'] = "【四、发布前检查清单】"
    ws[f'A{row}'].font = Font(bold=True, size=12, color="FF0000")
    row += 1

    checklist = [
        "☑ 标题是否包含关键词和情绪点？",
        "☑ 封面图是否有大字压图？",
        "☑ 前30字是否设置了钩子？",
        "☑ 是否有3个以上的价值点？",
        "☑ 是否设置了互动话题？",
        "☑ 图片是否清晰且有对比？",
        "☑ 是否添加了3-5个精准标签？",
        "☑ 发布时间是否在高峰期（19-22点）？"
    ]

    for item in checklist:
        ws[f'B{row}'] = item
        ws.merge_cells(f'B{row}:F{row}')
        row += 1

    row += 1

    # A/B测试建议
    ws[f'A{row}'] = "【五、A/B测试建议】"
    ws[f'A{row}'].font = Font(bold=True, size=12, color="FF0000")
    row += 1

    ab_tests = [
        ("标题测试", "同内容配3个不同标题，测试点击率"),
        ("封面测试", "同内容配不同封面风格，测试吸引力"),
        ("发布时间", "测试早中晚不同时段的曝光效果"),
        ("标签组合", "测试不同标签组合的流量差异")
    ]

    for test_name, test_desc in ab_tests:
        ws[f'A{row}'] = test_name
        ws[f'B{row}'] = test_desc
        ws.merge_cells(f'B{row}:E{row}')
        row += 1

    # 设置列宽
    ws.column_dimensions['A'].width = 15
    ws.column_dimensions['B'].width = 60
    for col in range(3, 8):
        ws.column_dimensions[get_column_letter(col)].width = 15

    # 添加边框和背景色
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    for row_cells in ws.iter_rows(min_row=3, max_row=row, min_col=1, max_col=6):
        for cell in row_cells:
            if not isinstance(cell, MergedCell) and cell.value:
                cell.border = thin_border
                # 标题行添加背景色
                if '【' in str(cell.value) and '】' in str(cell.value):
                    cell.fill = PatternFill(start_color="E8F4FF", end_color="E8F4FF", fill_type="solid")


def create_video_ai_analysis_sheet(wb: Workbook, data: Dict[str, Any]) -> None:
    """
    创建视频AI深度分析工作表（PRD对齐完整版）

    结构：
    一、视频分析摘要（含PRD综合合规率）
    二、时间节点分析（PRD合规率明细）
    三、封面分析（图片类型、内容形式分布）
    四、标题分析（元素组合统计）
    五、切入方式分析
    六、产品植入分析（3个最佳策略）
    七、单个视频详情

    Args:
        wb: Excel工作簿对象
        data: 分析数据
    """
    viral_model = data.get('viral_model', {})
    video_ai_data = viral_model.get('video_ai_insights', {})

    # 如果没有视频AI分析数据，或状态不是success，跳过
    if not video_ai_data or video_ai_data.get('status') != 'success':
        logger.info("没有视频AI深度分析数据，跳过该工作表")
        return

    try:
        ws = wb.create_sheet("视频AI深度分析")

        # 设置列宽（扩展到M列，支持13列详细表格）
        column_widths = {
            'A': 30,  # 标题/视频标题
            'B': 12,  # 互动分
            'C': 12,  # 产品出现
            'D': 12,  # 干货开始
            'E': 18,  # 大类-类型
            'F': 14,  # 切入方式
            'G': 14,  # 产品引出
            'H': 14,  # 植入方式
            'I': 16,  # 封面类型
            'J': 16,  # 标题类型
            'K': 35,  # 视频链接
            'L': 8,   # 状态
            'M': 20,  # 备注
        }
        for col, width in column_widths.items():
            ws.column_dimensions[col].width = width

        # 标题
        ws['A1'] = '视频AI深度分析报告（PRD对齐完整版）'
        ws['A1'].font = Font(bold=True, size=14, color="0066CC")
        ws.merge_cells('A1:F1')

        ws['A2'] = '基于PRD文档的视频创作维度，提供完整的统计分析和最佳策略'
        ws['A2'].font = Font(size=10, italic=True, color="666666")
        ws.merge_cells('A2:F2')

        row = 4

        # 获取各维度统计数据
        summary = video_ai_data.get('summary', {})
        timeline_stats = summary.get('timeline_stats', {}) if isinstance(summary, dict) else {}
        cover_stats = summary.get('cover_stats', {}) if isinstance(summary, dict) else {}
        title_stats = summary.get('title_stats', {}) if isinstance(summary, dict) else {}

        # ==================== 一、视频分析摘要 ====================
        ws[f'A{row}'] = '【一、视频分析摘要】'
        ws[f'A{row}'].font = Font(bold=True, size=13, color="FFFFFF")
        ws[f'A{row}'].fill = PatternFill(start_color="FF6B35", fill_type="solid")
        ws.merge_cells(f'A{row}:F{row}')
        row += 2

        analyzed_count = video_ai_data.get('analyzed_count', 0)
        ws[f'A{row}'] = '分析视频数'
        ws[f'B{row}'] = analyzed_count
        ws[f'B{row}'].font = Font(bold=True, size=12)
        ws[f'C{row}'] = '分析模型'
        ws[f'D{row}'] = video_ai_data.get('model', '未知')
        row += 1

        # PRD综合合规率
        prd_compliance = timeline_stats.get('prd_compliance', {})
        overall_score = prd_compliance.get('overall_compliance_score', 0)
        ws[f'A{row}'] = 'PRD综合合规率'
        ws[f'A{row}'].font = Font(bold=True, color="0066CC")
        ws[f'B{row}'] = f"{overall_score:.1f}%"
        ws[f'B{row}'].font = Font(bold=True, size=14, color="FF0000" if overall_score < 60 else "00AA00")
        ws[f'C{row}'] = '基于产品出现、干货开始、产品讲解时间计算'
        ws[f'C{row}'].font = Font(size=9, italic=True, color="666666")
        ws.merge_cells(f'C{row}:F{row}')
        row += 2

        # 核心发现
        insights_list = summary.get('insights', []) if isinstance(summary, dict) else []
        if insights_list:
            ws[f'A{row}'] = '核心发现'
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            for i, insight in enumerate(insights_list[:3], 1):
                ws[f'B{row}'] = f"{i}. {insight}"
                ws.merge_cells(f'B{row}:F{row}')
                row += 1
        row += 1

        # ==================== 二、时间节点分析 ====================
        ws[f'A{row}'] = '【二、时间节点分析】'
        ws[f'A{row}'].font = Font(bold=True, size=13, color="FFFFFF")
        ws[f'A{row}'].fill = PatternFill(start_color="4472C4", fill_type="solid")
        ws.merge_cells(f'A{row}:F{row}')
        row += 2

        # PRD合规率明细
        ws[f'A{row}'] = 'PRD合规率明细'
        ws[f'A{row}'].font = Font(bold=True)
        row += 1

        prd_items = [
            ('产品≤30s出现率', 'product_under_30s_rate', 'PRD要求产品在30秒内出现'),
            ('干货20-40s开始率', 'content_20_40s_rate', 'PRD要求干货在20-40秒开始'),
            ('产品讲解40-60s率', 'explain_40_60s_rate', 'PRD要求产品讲解在40-60秒'),
        ]
        for item_name, key, desc in prd_items:
            rate = prd_compliance.get(key, 0)
            ws[f'B{row}'] = item_name
            ws[f'C{row}'] = f"{rate:.1f}%"
            ws[f'C{row}'].font = Font(bold=True, color="00AA00" if rate >= 50 else "FF0000")
            ws[f'D{row}'] = desc
            ws[f'D{row}'].font = Font(size=9, color="666666")
            ws.merge_cells(f'D{row}:F{row}')
            row += 1
        row += 1

        # 时间分布统计
        time_dist = timeline_stats.get('time_distributions', {})
        if time_dist:
            ws[f'A{row}'] = '时间分布统计'
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            appear_stats = time_dist.get('product_appear', {})
            ws[f'B{row}'] = '产品出现时间'
            ws[f'C{row}'] = f"平均{appear_stats.get('avg', 0):.1f}秒"
            ws[f'D{row}'] = f"范围{appear_stats.get('min', 0)}-{appear_stats.get('max', 0)}秒"
            row += 1
            content_stats = time_dist.get('content_start', {})
            ws[f'B{row}'] = '干货开始时间'
            ws[f'C{row}'] = f"平均{content_stats.get('avg', 0):.1f}秒"
            ws[f'D{row}'] = f"范围{content_stats.get('min', 0)}-{content_stats.get('max', 0)}秒"
            row += 1
        row += 1

        # ==================== 三、封面分析 ====================
        ws[f'A{row}'] = '【三、封面分析】'
        ws[f'A{row}'].font = Font(bold=True, size=13, color="FFFFFF")
        ws[f'A{row}'].fill = PatternFill(start_color="70AD47", fill_type="solid")
        ws.merge_cells(f'A{row}:F{row}')
        row += 2

        # 图片类型分布
        img_type_dist = cover_stats.get('image_type_distribution', {})
        if img_type_dist:
            ws[f'A{row}'] = '图片类型分布'
            ws[f'A{row}'].font = Font(bold=True)
            ws[f'B{row}'] = f"单图: {img_type_dist.get('单图', 0)}"
            ws[f'C{row}'] = f"拼图: {img_type_dist.get('拼图', 0)}"
            ws[f'D{row}'] = f"截图: {img_type_dist.get('截图', 0)}"
            row += 1

        # 内容形式分布
        content_type_dist = cover_stats.get('content_type_distribution', {})
        if content_type_dist:
            ws[f'A{row}'] = '内容形式分布'
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            for content_type, count in list(content_type_dist.items())[:5]:
                ws[f'B{row}'] = content_type
                ws[f'C{row}'] = f"{count}个"
                row += 1

        # 特殊指标
        ws[f'A{row}'] = '文字压图率'
        ws[f'B{row}'] = f"{cover_stats.get('text_overlay_rate', 0):.1f}%"
        ws[f'C{row}'] = '人物出镜率'
        ws[f'D{row}'] = f"{cover_stats.get('person_appear_rate', 0):.1f}%"
        row += 2

        # ==================== 四、标题分析 ====================
        ws[f'A{row}'] = '【四、标题分析】'
        ws[f'A{row}'].font = Font(bold=True, size=13, color="FFFFFF")
        ws[f'A{row}'].fill = PatternFill(start_color="ED7D31", fill_type="solid")
        ws.merge_cells(f'A{row}:F{row}')
        row += 2

        # 标题元素占比
        title_elements = title_stats.get('title_elements', {})
        if title_elements:
            ws[f'A{row}'] = '标题元素占比'
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            element_items = [
                ('年龄/状态', 'has_age_rate'),
                ('问题描述', 'has_problem_rate'),
                ('数字', 'has_number_rate'),
                ('产品词', 'has_product_rate'),
                ('效果描述', 'has_effect_rate'),
            ]
            for i, (name, key) in enumerate(element_items):
                col = 'B' if i % 2 == 0 else 'D'
                ws[f'{col}{row}'] = name
                next_col = 'C' if col == 'B' else 'E'
                ws[f'{next_col}{row}'] = f"{title_elements.get(key, 0):.1f}%"
                if i % 2 == 1:
                    row += 1
            if len(element_items) % 2 == 1:
                row += 1

        # 元素组合统计
        element_combos = title_stats.get('element_combinations', {})
        if element_combos:
            ws[f'A{row}'] = '热门元素组合'
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            for combo, count in list(element_combos.items())[:5]:
                ws[f'B{row}'] = combo
                ws[f'C{row}'] = f"{count}次"
                row += 1
        row += 1

        # ==================== 五、切入方式分析 ====================
        ws[f'A{row}'] = '【五、切入方式分析】'
        ws[f'A{row}'].font = Font(bold=True, size=13, color="FFFFFF")
        ws[f'A{row}'].fill = PatternFill(start_color="7030A0", fill_type="solid")
        ws.merge_cells(f'A{row}:F{row}')
        row += 2

        entry_stats = timeline_stats.get('entry_point_stats', {})
        if entry_stats:
            # 表头
            ws[f'A{row}'] = '切入方式'
            ws[f'B{row}'] = '使用次数'
            ws[f'C{row}'] = '平均互动'
            for col in ['A', 'B', 'C']:
                ws[f'{col}{row}'].font = Font(bold=True)
                ws[f'{col}{row}'].fill = PatternFill(start_color="E2D5F0", fill_type="solid")
            row += 1

            for entry, stats in list(entry_stats.items())[:6]:
                ws[f'A{row}'] = entry
                ws[f'B{row}'] = stats.get('count', 0)
                ws[f'C{row}'] = f"{stats.get('avg_interaction', 0):.0f}"
                row += 1
        row += 1

        # ==================== 六、产品植入分析 ====================
        ws[f'A{row}'] = '【六、产品植入分析】'
        ws[f'A{row}'].font = Font(bold=True, size=13, color="FFFFFF")
        ws[f'A{row}'].fill = PatternFill(start_color="C00000", fill_type="solid")
        ws.merge_cells(f'A{row}:F{row}')
        row += 2

        # 植入方式分布（过滤掉包含错误文本的异常数据）
        embed_stats = timeline_stats.get('embed_way_stats', {})
        error_keywords_stats = ["无法读取", "无法观看", "无法访问", "API调用", "所有分析基于"]
        if embed_stats:
            ws[f'A{row}'] = '植入方式分布'
            ws[f'A{row}'].font = Font(bold=True)
            row += 1
            for embed, stats in list(embed_stats.items())[:5]:
                # 过滤掉包含错误关键词的条目，以及异常长的文本
                if any(kw in str(embed) for kw in error_keywords_stats) or len(str(embed)) > 30:
                    continue
                ws[f'B{row}'] = embed
                ws[f'C{row}'] = f"{stats.get('count', 0)}次"
                ws[f'D{row}'] = f"平均互动{stats.get('avg_interaction', 0):.0f}"
                row += 1
        row += 1

        # 3个最佳植入策略（过滤包含错误文本的策略）
        top3_strategies = timeline_stats.get('top3_embed_strategies', [])
        if top3_strategies:
            # 过滤有效策略
            valid_strategies = [
                s for s in top3_strategies
                if not any(kw in str(s.get('name', '')) for kw in error_keywords_stats)
                and len(str(s.get('name', ''))) < 50
            ]

            if valid_strategies:
                ws[f'A{row}'] = '3个最佳植入策略'
                ws[f'A{row}'].font = Font(bold=True, size=12, color="FF0000")
                ws[f'A{row}'].fill = PatternFill(start_color="FFF3CD", fill_type="solid")
                ws.merge_cells(f'A{row}:F{row}')
                row += 2

                for strategy in valid_strategies[:3]:
                    # 策略标题
                    ws[f'A{row}'] = f"策略{strategy.get('rank', '')}: {strategy.get('name', '')}"
                    ws[f'A{row}'].font = Font(bold=True, size=11, color="0066CC")
                    ws[f'E{row}'] = strategy.get('recommendation', '')
                    ws.merge_cells(f'A{row}:D{row}')
                    row += 1

                    # 策略摘要
                    ws[f'B{row}'] = strategy.get('summary', '')
                    ws[f'B{row}'].font = Font(italic=True)
                    ws.merge_cells(f'B{row}:F{row}')
                    row += 1

                # 适用场景
                ws[f'B{row}'] = '适用场景'
                ws[f'B{row}'].font = Font(bold=True, size=9)
                ws[f'C{row}'] = strategy.get('applicable_scene', '')
                ws.merge_cells(f'C{row}:F{row}')
                row += 1

                # 操作建议
                tips = strategy.get('operation_tips', [])
                if tips:
                    ws[f'B{row}'] = '操作建议'
                    ws[f'B{row}'].font = Font(bold=True, size=9)
                    ws[f'C{row}'] = ' | '.join(tips[:3])
                    ws[f'C{row}'].font = Font(size=9)
                    ws.merge_cells(f'C{row}:F{row}')
                    row += 1

                # 数据支撑
                ws[f'B{row}'] = '数据支撑'
                ws[f'B{row}'].font = Font(bold=True, size=9)
                ws[f'C{row}'] = strategy.get('data_support', '')
                ws[f'C{row}'].font = Font(size=9, color="666666")
                ws.merge_cells(f'C{row}:F{row}')
                row += 2
        row += 1

        # ==================== 七、单个视频详情（13列拆分表格）====================
        ws[f'A{row}'] = '【七、单个视频详情】'
        ws[f'A{row}'].font = Font(bold=True, size=13, color="FFFFFF")
        ws[f'A{row}'].fill = PatternFill(start_color="4472C4", fill_type="solid")
        ws.merge_cells(f'A{row}:M{row}')
        row += 2

        # 表头（13列，拆分7个数据点+核心字段）
        headers = [
            '视频标题',    # A: note_title
            '互动分',      # B: interaction_score
            '产品出现',    # C: timeline_analysis.product_appear_time
            '干货开始',    # D: timeline_analysis.content_start_time
            '大类-类型',   # E: timeline_analysis.content_type
            '切入方式',    # F: timeline_analysis.entry_point
            '产品引出',    # G: timeline_analysis.product_intro_way
            '植入方式',    # H: timeline_analysis.product_embed_way
            '封面类型',    # I: cover_analysis
            '标题类型',    # J: title_analysis
            '视频链接',    # K: video_url
            '状态',        # L: analysis_status
            '备注'         # M: error or keywords
        ]
        for col, header in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col)
            cell.value = header
            cell.font = Font(bold=True, color="FFFFFF", size=9)
            cell.fill = PatternFill(start_color='4472C4', fill_type='solid')
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        ws.row_dimensions[row].height = 25
        row += 1

        # 视频数据（13列拆分填充）
        insights = video_ai_data.get('individual_insights', [])
        thin_border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

        # 错误关键词列表（用于识别AI返回的错误文本）
        error_keywords = [
            "无法读取视频", "无法观看视频", "无法访问视频",
            "视频无法加载", "视频加载失败", "无法获取视频",
            "API调用失败", "API调用异常", "所有分析基于标题和描述推断"
        ]

        def clean_timeline_field(value: str) -> str:
            """清理时间轴字段值，如果包含错误文本则返回占位符"""
            if not value or value == '/':
                return '/'
            # 检查是否包含错误关键词
            if any(kw in str(value) for kw in error_keywords):
                return '/'
            # 检查是否是异常长的文本（正常值应该很短，如"30秒"、"vlog"等）
            if len(str(value)) > 50:
                return '/'
            return value

        for insight in insights:
            timeline = insight.get('timeline_analysis', {})
            cover = insight.get('cover_analysis', {})
            title_info = insight.get('title_analysis', {})
            status = insight.get('analysis_status', 'pending')
            # P1-fix-1: 支持 partial 状态
            is_success = status == 'success' and timeline
            is_partial = status == 'partial'  # 部分成功

            # A: 视频标题
            ws[f'A{row}'] = insight.get('note_title', '')
            ws[f'A{row}'].alignment = Alignment(wrap_text=True, vertical='center')
            ws[f'A{row}'].font = Font(size=9)

            # B: 互动分数
            ws[f'B{row}'] = insight.get('interaction_score', 0)
            ws[f'B{row}'].alignment = Alignment(horizontal='center', vertical='center')
            ws[f'B{row}'].number_format = '#,##0'
            ws[f'B{row}'].font = Font(bold=True, size=9)

            # C-H: 时间轴分析数据（7个数据点中的6个关键字段）
            if is_success:
                # C: 产品出现时间（使用清理函数过滤错误文本）
                ws[f'C{row}'] = clean_timeline_field(timeline.get('product_appear_time', '/'))
                ws[f'C{row}'].alignment = Alignment(horizontal='center', vertical='center')
                ws[f'C{row}'].font = Font(size=9)

                # D: 干货开始时间
                ws[f'D{row}'] = clean_timeline_field(timeline.get('content_start_time', '/'))
                ws[f'D{row}'].alignment = Alignment(horizontal='center', vertical='center')
                ws[f'D{row}'].font = Font(size=9)

                # E: 大类-类型
                ws[f'E{row}'] = clean_timeline_field(timeline.get('content_type', '/'))
                ws[f'E{row}'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                ws[f'E{row}'].font = Font(size=9)

                # F: 切入方式
                ws[f'F{row}'] = clean_timeline_field(timeline.get('entry_point', '/'))
                ws[f'F{row}'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                ws[f'F{row}'].font = Font(size=9)

                # G: 产品引出方式
                ws[f'G{row}'] = clean_timeline_field(timeline.get('product_intro_way', '/'))
                ws[f'G{row}'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                ws[f'G{row}'].font = Font(size=9)

                # H: 产品植入方式
                ws[f'H{row}'] = clean_timeline_field(timeline.get('product_embed_way', '/'))
                ws[f'H{row}'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                ws[f'H{row}'].font = Font(size=9)
            else:
                # 分析失败：显示占位符
                for col_letter in ['C', 'D', 'E', 'F', 'G', 'H']:
                    ws[f'{col_letter}{row}'] = '无法读取'
                    ws[f'{col_letter}{row}'].font = Font(color='999999', italic=True, size=9)
                    ws[f'{col_letter}{row}'].alignment = Alignment(horizontal='center', vertical='center')

            # I: 封面类型
            if cover:
                cover_text = f"{cover.get('main_category', '-')}"
                if cover.get('sub_category'):
                    cover_text += f"-{cover.get('sub_category')}"
                ws[f'I{row}'] = cover_text
            else:
                ws[f'I{row}'] = '-'
            ws[f'I{row}'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            ws[f'I{row}'].font = Font(size=9)

            # J: 标题类型
            if title_info:
                title_text = f"{title_info.get('main_category', '-')}"
                if title_info.get('sub_category'):
                    title_text += f"-{title_info.get('sub_category')}"
                ws[f'J{row}'] = title_text
            else:
                ws[f'J{row}'] = '-'
            ws[f'J{row}'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            ws[f'J{row}'].font = Font(size=9)

            # K: 视频链接（超链接格式）
            video_url = insight.get('video_url', '')
            if video_url:
                ws[f'K{row}'] = '点击观看'
                ws[f'K{row}'].hyperlink = video_url
                ws[f'K{row}'].font = Font(color='0066CC', underline='single', size=9)
            else:
                ws[f'K{row}'] = '-'
                ws[f'K{row}'].font = Font(size=9)
            ws[f'K{row}'].alignment = Alignment(horizontal='center', vertical='center')

            # L: 状态（条件格式）- P1-fix-1: 支持 partial 状态
            if is_success:
                ws[f'L{row}'] = '✅'
                ws[f'L{row}'].font = Font(color='00AA00', bold=True)
            elif is_partial:
                ws[f'L{row}'] = '⚠️'
                ws[f'L{row}'].font = Font(color='FF8800', bold=True)  # 橙色
            else:
                ws[f'L{row}'] = '❌'
                ws[f'L{row}'].font = Font(color='FF0000', bold=True)
            ws[f'L{row}'].alignment = Alignment(horizontal='center', vertical='center')

            # M: 备注（P3：显示失败原因）- P1-fix-1: 支持 partial 状态
            if not is_success:
                # P3: 优先使用 error_message（模型字段），兼容旧数据的 error
                error_msg = insight.get('error_message') or insight.get('error', '视频分析失败')
                ws[f'M{row}'] = error_msg
                ws[f'M{row}'].font = Font(color='FF8800' if is_partial else 'FF0000', size=8)
            else:
                # 显示关键词
                keywords = title_info.get('keywords', []) if title_info else []
                ws[f'M{row}'] = ', '.join(keywords[:3]) if keywords else '-'
                ws[f'M{row}'].font = Font(size=8, color='666666')
            ws[f'M{row}'].alignment = Alignment(wrap_text=True, vertical='center')

            # 设置行高和边框
            ws.row_dimensions[row].height = 35
            for col in range(1, 14):  # A-M共13列
                ws.cell(row=row, column=col).border = thin_border

            row += 1

        # ==================== 八、视频详细分析（对齐图文笔记颗粒度）====================
        row += 2
        ws[f'A{row}'] = '【八、视频详细分析】'
        ws[f'A{row}'].font = Font(bold=True, size=13, color="FFFFFF")
        ws[f'A{row}'].fill = PatternFill(start_color="5B9BD5", fill_type="solid")
        ws.merge_cells(f'A{row}:M{row}')
        row += 2

        for i, insight in enumerate(insights, 1):
            status = insight.get('analysis_status', 'pending')
            timeline = insight.get('timeline_analysis', {})
            cover = insight.get('cover_analysis', {})
            title_info = insight.get('title_analysis', {})
            # P1-fix-1: 支持 partial 状态
            is_success = status == 'success' and timeline
            is_partial = status == 'partial'

            # 视频序号和标题
            ws[f'A{row}'] = f"视频{i}"
            ws[f'A{row}'].font = Font(bold=True, color="0066CC", size=11)
            row += 1

            ws[f'A{row}'] = "标题"
            ws[f'A{row}'].font = Font(bold=True, size=9)
            ws[f'B{row}'] = insight.get('note_title', '(无标题)')
            ws[f'B{row}'].font = Font(size=9)
            ws.merge_cells(f'B{row}:M{row}')
            row += 1

            # 分析状态检查 - P1-fix-1: 支持 partial 状态
            if not is_success and not is_partial:
                ws[f'A{row}'] = "分析状态"
                ws[f'A{row}'].font = Font(bold=True, size=9)
                # P3: 优先使用 error_message（模型字段），兼容旧数据的 error
                error_msg = insight.get('error_message') or insight.get('error', '无法读取视频')
                ws[f'B{row}'] = f"❌ 失败: {error_msg}"
                ws[f'B{row}'].font = Font(color='FF0000', italic=True, size=9)
                ws.merge_cells(f'B{row}:M{row}')
                row += 2
                continue

            # 如果是 partial 状态，显示警告但继续展示已有数据
            if is_partial:
                ws[f'A{row}'] = "分析状态"
                ws[f'A{row}'].font = Font(bold=True, size=9)
                error_msg = insight.get('error_message') or '部分分析失败'
                ws[f'B{row}'] = f"⚠️ 部分成功: {error_msg}"
                ws[f'B{row}'].font = Font(color='FF8800', italic=True, size=9)
                ws.merge_cells(f'B{row}:M{row}')
                row += 1

            # 【基础信息】
            ws[f'B{row}'] = "【基础信息】"
            ws[f'B{row}'].font = Font(bold=True, color="4472C4", size=10)
            row += 1

            base_items = [
                ('互动分数', f"{insight.get('interaction_score', 0):,}"),
                ('分析时间', insight.get('analysis_time', '-')),
                ('视频链接', insight.get('video_url', '-')[:60] + '...' if len(insight.get('video_url', '')) > 60 else insight.get('video_url', '-')),
            ]
            for key, value in base_items:
                ws[f'C{row}'] = f"• {key}:"
                ws[f'C{row}'].font = Font(size=9, color="666666")
                ws[f'D{row}'] = str(value)
                ws[f'D{row}'].font = Font(size=9)
                ws.merge_cells(f'D{row}:M{row}')
                row += 1

            # 【封面分析】
            if cover:
                ws[f'B{row}'] = "【封面分析】"
                ws[f'B{row}'].font = Font(bold=True, color="4472C4", size=10)
                row += 1

                cover_items = [
                    ('主分类', cover.get('main_category', '-')),
                    ('子分类', cover.get('sub_category', '-')),
                    ('图片类型', cover.get('image_type', '-')),
                ]
                for key, value in cover_items:
                    ws[f'C{row}'] = f"• {key}:"
                    ws[f'C{row}'].font = Font(size=9, color="666666")
                    ws[f'D{row}'] = str(value) if value else '-'
                    ws[f'D{row}'].font = Font(size=9)
                    ws.merge_cells(f'D{row}:M{row}')
                    row += 1

            # 【标题分析】
            if title_info:
                ws[f'B{row}'] = "【标题分析】"
                ws[f'B{row}'].font = Font(bold=True, color="4472C4", size=10)
                row += 1

                title_items = [
                    ('标题类型', title_info.get('main_category', '-')),
                    ('子分类', title_info.get('sub_category', '-')),
                    ('关键词', ', '.join(title_info.get('keywords', [])[:5]) if title_info.get('keywords') else '-'),
                ]
                for key, value in title_items:
                    ws[f'C{row}'] = f"• {key}:"
                    ws[f'C{row}'].font = Font(size=9, color="666666")
                    ws[f'D{row}'] = str(value) if value else '-'
                    ws[f'D{row}'].font = Font(size=9)
                    ws.merge_cells(f'D{row}:M{row}')
                    row += 1

            # 【时间轴分析】（7个数据点，应用清理函数过滤错误文本）
            if timeline:
                ws[f'B{row}'] = "【时间轴分析】⭐ 7个关键数据点"
                ws[f'B{row}'].font = Font(bold=True, color="FF6B35", size=10)
                row += 1

                timeline_items = [
                    ('A-产品出现时间', clean_timeline_field(timeline.get('product_appear_time', '/'))),
                    ('B-产品使用时间', clean_timeline_field(timeline.get('product_use_time', '/'))),
                    ('C-干货开始时间', clean_timeline_field(timeline.get('content_start_time', '/'))),
                    ('D-大类-类型', clean_timeline_field(timeline.get('content_type', '/'))),
                    ('E-内容切入点', clean_timeline_field(timeline.get('entry_point', '/'))),
                    ('F-产品引出方式', clean_timeline_field(timeline.get('product_intro_way', '/'))),
                    ('G-产品植入方式', clean_timeline_field(timeline.get('product_embed_way', '/'))),
                ]
                for key, value in timeline_items:
                    ws[f'C{row}'] = f"• {key}:"
                    ws[f'C{row}'].font = Font(size=9, color="666666")
                    ws[f'D{row}'] = str(value) if value else '/'
                    ws[f'D{row}'].font = Font(size=9, bold=True)
                    ws.merge_cells(f'D{row}:M{row}')
                    row += 1

            # 【成功要素总结】（AI分析结果）
            ai_analysis = insight.get('ai_analysis', '')
            if ai_analysis and ai_analysis.strip():
                # 过滤掉调试格式和错误信息
                ai_text = ai_analysis.strip()
                skip_patterns = [
                    'Result_',  # 调试格式输出
                    '无法读取视频',  # 错误信息
                    '无法观看视频',
                    '无法访问视频',
                ]
                should_display = not any(ai_text.startswith(p) or p in ai_text for p in skip_patterns)

                if should_display and len(ai_text) > 20:  # 确保有实际内容
                    ws[f'B{row}'] = "【成功要素总结】"
                    ws[f'B{row}'].font = Font(bold=True, color="00AA00", size=10)
                    row += 1

                    # 将AI分析结果按行展示
                    lines = ai_text.split('\n')
                    display_lines = [line.strip() for line in lines if line.strip()][:12]  # 最多显示12行
                    for line in display_lines:
                        ws[f'C{row}'] = line[:150]  # 每行最多150字符
                        ws[f'C{row}'].font = Font(size=9)
                        ws.merge_cells(f'C{row}:M{row}')
                        row += 1

            row += 1  # 每个视频之间空一行

        logger.info("视频AI分析工作表创建成功（PRD对齐完整版，含13列详情表格+详情展开）")

    except Exception as e:
        logger.error(f"创建视频AI分析工作表失败: {e}")


def create_scene_analysis_sheet(wb: Workbook, data: Dict[str, Any]) -> None:
    """
    创建场景方向分析工作表

    展示内容：
    1. 场景分布统计
    2. 内容方向分布统计
    3. 场景策略建议
    4. 场景/方向创作模板
    5. 笔记场景详情（抽样）
    """
    ws = wb.create_sheet("🎬 场景方向分析")

    # 样式定义
    title_font = Font(size=16, bold=True, color="FFFFFF")
    title_fill = PatternFill(start_color="9B59B6", fill_type="solid")  # 紫色
    section_font = Font(size=12, bold=True, color="8E44AD")
    header_font = Font(size=10, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="9B59B6", fill_type="solid")
    highlight_fill = PatternFill(start_color="F5EEF8", fill_type="solid")
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # 标题
    ws['A1'] = "🎬 场景方向分析"
    ws['A1'].font = title_font
    ws['A1'].fill = title_fill
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.merge_cells('A1:H1')
    ws.row_dimensions[1].height = 30

    ws['A2'] = "（分析笔记的使用场景和内容方向，为创作提供场景切入建议）"
    ws['A2'].font = Font(size=9, italic=True, color="666666")
    ws.merge_cells('A2:H2')

    # 检查数据
    scene_features = data.get('scene_features', {})
    if not scene_features or scene_features.get('status') == 'error':
        ws['A4'] = "场景分析数据暂无"
        ws['A5'] = scene_features.get('message', '未进行场景分析')
        logger.warning("场景分析数据为空，跳过详细内容创建")
        return

    row = 4

    # ==================== A. 分析概要 ====================
    ws[f'A{row}'] = "A. 分析概要"
    ws[f'A{row}'].font = section_font
    ws.merge_cells(f'A{row}:H{row}')
    row += 1

    ws[f'A{row}'] = "分析笔记数"
    ws[f'B{row}'] = scene_features.get('total_notes', 0)
    ws[f'C{row}'] = "图文笔记"
    ws[f'D{row}'] = scene_features.get('image_notes_count', 0)
    ws[f'E{row}'] = "视频笔记"
    ws[f'F{row}'] = scene_features.get('video_notes_count', 0)
    row += 2

    # ==================== B. 场景分布统计 ====================
    ws[f'A{row}'] = "B. 场景分布统计"
    ws[f'A{row}'].font = section_font
    ws.merge_cells(f'A{row}:D{row}')
    row += 1

    combined = scene_features.get('combined_results', {})
    scene_dist = combined.get('scene_distribution', {})

    ws[f'A{row}'] = "场景类型"
    ws[f'A{row}'].font = header_font
    ws[f'A{row}'].fill = header_fill
    ws[f'B{row}'] = "笔记数量"
    ws[f'B{row}'].font = header_font
    ws[f'B{row}'].fill = header_fill
    ws[f'C{row}'] = "占比"
    ws[f'C{row}'].font = header_font
    ws[f'C{row}'].fill = header_fill
    row += 1

    total_scene = sum(scene_dist.values()) if scene_dist else 1
    scene_start_row = row
    for scene_name, count in list(scene_dist.items())[:10]:
        ws[f'A{row}'] = scene_name
        ws[f'B{row}'] = count
        ws[f'C{row}'] = f"{count/total_scene*100:.1f}%"
        for col in ['A', 'B', 'C']:
            ws[f'{col}{row}'].border = border
        row += 1

    if not scene_dist:
        ws[f'A{row}'] = "暂无场景数据"
        row += 1

    row += 1

    # ==================== C. 内容方向分布统计 ====================
    direction_start_col = 'E'
    dir_row = scene_start_row - 2

    ws[f'{direction_start_col}{dir_row}'] = "C. 内容方向分布统计"
    ws[f'{direction_start_col}{dir_row}'].font = section_font
    ws.merge_cells(f'{direction_start_col}{dir_row}:H{dir_row}')
    dir_row += 1

    direction_dist = combined.get('direction_distribution', {})

    ws[f'{direction_start_col}{dir_row}'] = "内容方向"
    ws[f'{direction_start_col}{dir_row}'].font = header_font
    ws[f'{direction_start_col}{dir_row}'].fill = header_fill
    ws[f'F{dir_row}'] = "笔记数量"
    ws[f'F{dir_row}'].font = header_font
    ws[f'F{dir_row}'].fill = header_fill
    ws[f'G{dir_row}'] = "占比"
    ws[f'G{dir_row}'].font = header_font
    ws[f'G{dir_row}'].fill = header_fill
    dir_row += 1

    total_dir = sum(direction_dist.values()) if direction_dist else 1
    for dir_name, count in list(direction_dist.items())[:10]:
        ws[f'{direction_start_col}{dir_row}'] = dir_name
        ws[f'F{dir_row}'] = count
        ws[f'G{dir_row}'] = f"{count/total_dir*100:.1f}%"
        for col in ['E', 'F', 'G']:
            ws[f'{col}{dir_row}'].border = border
        dir_row += 1

    row = max(row, dir_row) + 1

    # ==================== D. 场景策略建议 ====================
    ws[f'A{row}'] = "D. 场景策略建议"
    ws[f'A{row}'].font = section_font
    ws[f'A{row}'].fill = highlight_fill
    ws.merge_cells(f'A{row}:H{row}')
    row += 1

    strategy = scene_features.get('scene_strategy', {})

    # 推荐场景
    ws[f'A{row}'] = "⭐ 推荐场景"
    ws[f'A{row}'].font = Font(bold=True)
    recommended_scenes = strategy.get('recommended_scenes', [])
    ws[f'B{row}'] = "、".join(recommended_scenes) if recommended_scenes else "无"
    ws.merge_cells(f'B{row}:D{row}')
    row += 1

    # 推荐内容方向
    ws[f'A{row}'] = "⭐ 推荐方向"
    ws[f'A{row}'].font = Font(bold=True)
    recommended_dirs = strategy.get('recommended_directions', [])
    ws[f'B{row}'] = "、".join(recommended_dirs) if recommended_dirs else "无"
    ws.merge_cells(f'B{row}:D{row}')
    row += 1

    # 高互动场景
    ws[f'A{row}'] = "🔥 高互动场景"
    ws[f'A{row}'].font = Font(bold=True, color="FF0000")
    high_scenes = strategy.get('high_interaction_scenes', [])
    ws[f'B{row}'] = "、".join(high_scenes) if high_scenes else "暂无明显高互动场景"
    ws.merge_cells(f'B{row}:D{row}')
    row += 2

    # 策略总结
    ws[f'A{row}'] = "【策略总结】"
    ws[f'A{row}'].font = Font(bold=True, color="8E44AD")
    row += 1

    summary = strategy.get('strategy_summary', '')
    if summary:
        lines = summary.split('\n')
        for line in lines:
            if line.strip():
                ws[f'A{row}'] = line.strip()
                ws.merge_cells(f'A{row}:H{row}')
                row += 1
    row += 1

    # ==================== E. 场景创作模板 ====================
    ws[f'A{row}'] = "E. 场景创作模板"
    ws[f'A{row}'].font = section_font
    ws.merge_cells(f'A{row}:D{row}')
    row += 1

    scene_templates = strategy.get('scene_templates', [])
    for i, template in enumerate(scene_templates[:6], 1):
        ws[f'A{row}'] = f"模板{i}"
        ws[f'B{row}'] = template
        ws.merge_cells(f'B{row}:D{row}')
        row += 1

    if not scene_templates:
        ws[f'A{row}'] = "暂无模板"
        row += 1

    row += 1

    # ==================== F. 内容方向创作模板 ====================
    ws[f'A{row}'] = "F. 内容方向创作模板"
    ws[f'A{row}'].font = section_font
    ws.merge_cells(f'A{row}:D{row}')
    row += 1

    dir_templates = strategy.get('direction_templates', [])
    for i, template in enumerate(dir_templates[:6], 1):
        ws[f'A{row}'] = f"模板{i}"
        ws[f'B{row}'] = template
        ws.merge_cells(f'B{row}:D{row}')
        row += 1

    if not dir_templates:
        ws[f'A{row}'] = "暂无模板"
        row += 1

    row += 1

    # ==================== G. 笔记场景详情（抽样） ====================
    text_analysis = scene_features.get('text_analysis', {})
    note_details = text_analysis.get('note_details', [])

    if note_details:
        ws[f'A{row}'] = "G. 笔记场景详情（抽样展示）"
        ws[f'A{row}'].font = section_font
        ws.merge_cells(f'A{row}:H{row}')
        row += 1

        # 表头
        headers = ["序号", "标题", "识别场景", "内容方向", "互动分"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
        row += 1

        # 数据行
        for i, detail in enumerate(note_details[:15], 1):
            ws.cell(row=row, column=1, value=i).border = border
            ws.cell(row=row, column=2, value=detail.get('title', '')[:25]).border = border
            ws.cell(row=row, column=3, value="、".join(detail.get('scenes', []))).border = border
            ws.cell(row=row, column=4, value="、".join(detail.get('directions', []))).border = border
            ws.cell(row=row, column=5, value=detail.get('interaction', 0)).border = border
            row += 1

    # 调整列宽
    column_widths = [15, 25, 15, 15, 12, 15, 12, 15]
    for col, width in enumerate(column_widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width

    logger.info("场景方向分析工作表创建成功")