# Excel 导出与产品分析机制

本文档整理了爆文分析系统的 Excel 导出逻辑和产品分析判断机制。

---

## 目录

1. [Excel 导出机制](#1-excel-导出机制)
   - [文件结构](#11-文件结构)
   - [工作表清单](#12-工作表清单)
   - [调用流程](#13-调用流程)
2. [产品分析机制](#2-产品分析机制)
   - [最佳策略生成逻辑](#21-最佳策略生成逻辑)
   - [产品提及位置判断](#22-产品提及位置判断)

---

## 1. Excel 导出机制

### 1.1 文件结构

所有 Excel 导出逻辑都在 **`viral_agent/services/export_service.py`** 中（约 2400 行）。

```
export_service.py (2400+ 行)
│
├── 导出入口函数
│   ├── export_to_excel()           :158  → 主入口，创建所有工作表
│   └── export_raw_data_to_excel()  :34   → 导出原始采集数据
│
└── 工作表创建函数（按 Excel 中的顺序）
    │
    ├── 1. create_overview_and_model_sheet()   :263  → 📊 总览与爆文模型
    ├── 2. create_multimodal_analysis_sheet()  :1818 → 🤖 AI深度分析
    ├── 3. create_product_analysis_sheet()     :1052 → 📦 产品分析
    ├── 4. create_title_analysis_sheet()       :709  → 📝 标题分析
    ├── 5. create_content_analysis_sheet()     :765  → 📄 内容分析
    ├── 6. create_interaction_sheet()          :839  → 💬 互动分析
    ├── 7. create_cover_analysis_sheet()       :930  → 🖼️ 封面分析
    ├── 8. create_all_images_ocr_sheet()       :1701 → 📷 图片OCR分析
    ├── 9. create_video_ai_analysis_sheet()    :2311 → 🎬 视频AI分析
    └── 10. create_raw_data_sheet()            :529  → 📋 原始数据
```

### 1.2 工作表清单

| 工作表 | 函数位置 | 主要内容 |
|-------|---------|---------|
| **总览与爆文模型** | `:263` | 分析概要、成功模式总结、标题/内容/封面策略、成功案例 |
| **AI深度分析** | `:1818` | 多模态分析结果、图文联合洞察 |
| **产品分析** | `:1052` | 产品引出时机、植入方式、产品策略建议 |
| **标题分析** | `:709` | 标题长度分布、高频词、标题模式统计 |
| **内容分析** | `:765` | 内容长度、结构模式、热门标签 |
| **互动分析** | `:839` | 点赞/收藏/评论分布、互动率图表 |
| **封面分析** | `:930` | 封面文字、颜色、布局分析 |
| **图片OCR** | `:1701` | 所有图片的文字提取结果 |
| **视频AI分析** | `:2311` | 视频封面/标题/时间轴分析、7个关键数据点 |
| **原始数据** | `:529` | 爆款笔记原始数据表格 |

### 1.3 调用流程

```python
# viral_app.py 中触发导出
@app.get("/api/export")
async def export_report(file_path: str):
    excel_path = export_to_excel(file_path)  # 调用导出服务
    return FileResponse(excel_path)

# export_service.py 内部流程
def export_to_excel(analysis_file_path):
    wb = openpyxl.Workbook()

    # 依次创建各工作表
    create_overview_and_model_sheet(wb, data)    # Sheet 1
    create_multimodal_analysis_sheet(wb, data)   # Sheet 2
    create_product_analysis_sheet(wb, data)      # Sheet 3
    create_title_analysis_sheet(wb, data)        # Sheet 4
    create_content_analysis_sheet(wb, data)      # Sheet 5
    create_interaction_sheet(wb, data)           # Sheet 6
    create_cover_analysis_sheet(wb, data)        # Sheet 7
    create_all_images_ocr_sheet(wb, data)        # Sheet 8
    create_video_ai_analysis_sheet(wb, data)     # Sheet 9
    create_raw_data_sheet(wb, data)              # Sheet 10

    wb.save(output_path)
    return output_path
```

#### 如何修改某个工作表

直接定位到对应的 `create_xxx_sheet()` 函数即可：

- **修改标题分析表格** → 找 `create_title_analysis_sheet()` (行 709)
- **修改产品分析表格** → 找 `create_product_analysis_sheet()` (行 1052)
- **修改视频分析表格** → 找 `create_video_ai_analysis_sheet()` (行 2311)

---

## 2. 产品分析机制

### 2.1 最佳策略生成逻辑

Excel 中显示的"最佳策略：【开门见山型】在内容前1/3直接引入产品，快速吸引目标用户"这类结论，来源于 `product_analyzer.py`。

#### 策略生成 = 模板 + 数据

| 部分 | 来源 | 说明 |
|-----|------|------|
| **策略名称** | 硬编码 | "开门见山型"、"痛点切入型"、"效果展示型" |
| **策略描述** | 硬编码 | 运营经验总结的专业文案 |
| **百分比数据** | 动态计算 | 从实际笔记数据统计 |
| **排序/推荐** | 数据驱动 | 哪个策略占比高就推荐哪个 |

#### 策略模板（硬编码）

位置：`product_analyzer.py:360-403`

```python
def _generate_top3_timing_strategies(self, distribution):
    strategies = []

    # 策略1: 开门见山型
    strategy1 = {
        'name': '开门见山型',
        'summary': '【开门见山型】在内容前1/3直接引入产品，快速吸引目标用户',
        'applicable_scene': '适合已有明确需求的用户群、产品知名度高的场景',
        'operation_tips': [
            '前30字内出现产品名或核心功效',
            '标题直接包含产品关键词',
            '开头用效果或数据吸引注意'
        ],
        'expected_effect': '精准触达目标用户，转化效率高...',
        'data_support': f"数据显示{distribution.get('early_mention_rate', 0)}%的爆款采用此策略"
    }

    # 策略2: 痛点切入型
    strategy2 = {
        'name': '痛点切入型',
        'summary': '【痛点切入型】先阐述问题和困扰，引发共鸣后自然引出产品作为解决方案',
        'applicable_scene': '适合解决特定问题的产品、用户有明确痛点的领域',
        # ...
    }

    # 策略3: 效果展示型
    strategy3 = {
        'name': '效果展示型',
        'summary': '【效果展示型】通过前后对比、使用效果先吸引注意，再揭晓产品',
        'applicable_scene': '适合效果可视化的产品、护肤/美妆/健身等领域',
        # ...
    }
```

#### 排序逻辑（数据驱动）

位置：`product_analyzer.py:405-430`

```python
# 根据数据排序，将最匹配当前数据的策略排在前面
early_rate = distribution.get('early_mention_rate', 0)    # 如 45%
middle_rate = distribution.get('middle_mention_rate', 0)  # 如 30%
late_rate = distribution.get('late_mention_rate', 0)      # 如 25%

# 计算每个策略的匹配分数
scores = [
    (0, early_rate),     # 开门见山型 → 45分
    (1, middle_rate),    # 痛点切入型 → 30分
    (2, late_rate),      # 效果展示型 → 25分
]
scores.sort(key=lambda x: x[1], reverse=True)  # 按分数排序

# 标记推荐度
for i, s in enumerate(sorted_strategies):
    if i == 0:
        s['recommendation'] = '⭐ 最佳推荐（基于数据分析）'
    elif i == 1:
        s['recommendation'] = '✓ 备选方案'
    else:
        s['recommendation'] = '○ 可选方案'
```

#### 完整数据流

```
25篇爆款笔记原始数据
        │
        ▼
┌─────────────────────────────────────┐
│  _analyze_product_timing()          │
│  遍历每篇笔记，分析产品首次提及位置    │
│                                     │
│  统计结果：                          │
│  • 前1/3提及：12篇 → 48%            │
│  • 中1/3提及：8篇 → 32%             │
│  • 后1/3提及：5篇 → 20%             │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│  _generate_top3_timing_strategies() │
│  3个预设策略模板 + 统计百分比        │
│                                     │
│  排序后：                            │
│  [开门见山型(48%), 痛点切入型(32%),  │
│   效果展示型(20%)]                   │
│   ⭐ 最佳    ✓ 备选    ○ 可选       │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│  Excel 输出                         │
│  最佳策略：【开门见山型】...         │
│  数据支撑：48% 的爆款采用此策略      │
└─────────────────────────────────────┘
```

---

### 2.2 产品提及位置判断

#### 判断逻辑：关键词定位 + 比例计算

位置：`product_analyzer.py:73-151` 和 `product_analyzer.py:330-341`

```
┌─────────────────────────────────────────────────────────────────────┐
│  笔记正文 (note.desc)                                               │
│  "最近皮肤状态不太好，黑眼圈越来越重。试了很多方法都没用，           │
│   直到闺蜜推荐了【这款眼霜】，用了一周真的有改善..."                 │
│   ↑                    ↑                                           │
│   位置0              位置45（"这款"出现的位置）                      │
│                                                                     │
│   全文长度 = 100字                                                  │
└─────────────────────────────────────────────────────────────────────┘
```

#### Step 1: 产品关键词库

```python
self.product_keywords = [
    '产品', '商品', '款', '品牌', '型号', '系列',
    '这个', '这款', '这瓶', '这支', '这盒',
    '链接', '购买', '下单', '入手', '价格', '优惠'
]
```

#### Step 2: 找关键词位置

```python
def _find_product_positions(self, text: str) -> List[int]:
    """找出产品关键词在文本中的位置"""
    positions = []
    for keyword in self.product_keywords:
        index = 0
        while True:
            index = text.find(keyword, index)  # 找关键词位置
            if index == -1:
                break
            positions.append(index)
            index += len(keyword)
    return sorted(positions)  # 按位置排序
```

#### Step 3: 计算比例并判断区间

```python
def _analyze_product_timing(self, notes):
    for note in notes:
        content_length = len(note.desc)                    # 全文长度
        product_positions = self._find_product_positions(note.desc)

        if product_positions:
            # 计算首次提及的相对位置
            first_position = product_positions[0] / content_length

            # 判断属于哪个区间
            if first_position <= 0.33:        # ≤33% → 前1/3
                timing_stats['first_third'] += 1
            elif first_position <= 0.66:      # 33%-66% → 中1/3
                timing_stats['middle_third'] += 1
            else:                             # >66% → 后1/3
                timing_stats['last_third'] += 1
```

#### 示例说明

| 正文 | 关键词位置 | 全文长度 | 比例 | 判定 |
|-----|----------|---------|------|-----|
| "**这款**眼霜真的超好用..." | 0 | 100 | 0% | 前1/3 |
| "最近眼纹困扰我很久，试了**这款**后..." | 45 | 150 | 30% | 前1/3 |
| "分享一下我的护肤心得，经过对比发现**这款**..." | 80 | 200 | 40% | 中1/3 |
| "总结一下，最推荐**这款**..." | 180 | 200 | 90% | 后1/3 |

#### 局限性

| 问题 | 说明 |
|-----|------|
| **关键词覆盖不全** | 如果用户用"它"、"这玩意"等代词，可能检测不到 |
| **不区分产品类型** | "这款"可能指任何产品，不一定是推广产品 |
| **只看首次提及** | 忽略了产品在文中的整体分布 |
| **纯文本分析** | 无法分析图片/视频中的产品露出 |

这是一个**启发式方法**，简单高效但不完美。更精准的分析需要结合 AI 语义理解。

---

## 总结

### 关键文件清单

| 文件 | 作用 | 行数 |
|------|------|------|
| `services/export_service.py` | Excel 导出，10个工作表 | ~2400 |
| `services/product_analyzer.py` | 产品分析，策略生成 | ~500 |

### 核心设计原则

1. **模块化工作表** — 每个 `create_xxx_sheet()` 函数独立负责一个工作表
2. **模板 + 数据** — 策略文案预定义，排序由数据驱动
3. **启发式分析** — 用关键词匹配快速定位，简单高效

---

*文档生成时间：2025-12-07*
