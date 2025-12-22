# -*- coding: utf-8 -*-
import openpyxl
import sys

# 设置输出编码
sys.stdout.reconfigure(encoding='utf-8')

# 读取Excel文件
file_path = r'C:/Users/ASUS/Desktop/viral_analysis_latest.xlsx'
wb = openpyxl.load_workbook(file_path)

# 重点检查的sheet
focus_sheets = ['爆文模型', '创作指南', '视频AI深度分析']

for sheet_name in focus_sheets:
    if sheet_name not in wb.sheetnames:
        print(f'\n⚠️ Sheet "{sheet_name}" 不存在')
        continue

    print(f'\n{"="*70}')
    print(f'Sheet: {sheet_name}')
    print(f'{"="*70}')
    ws = wb[sheet_name]
    print(f'行数: {ws.max_row}, 列数: {ws.max_column}')

    # 打印全部内容
    for i, row in enumerate(ws.iter_rows(max_row=min(50, ws.max_row), values_only=True), 1):
        row_str = []
        for cell in row:
            if cell is None:
                row_str.append('')
            else:
                s = str(cell)[:80]
                if len(str(cell)) > 80:
                    s += '...'
                row_str.append(s)
        # 只打印非空行
        if any(c for c in row_str):
            print(f'  行{i}: {row_str}')
