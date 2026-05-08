# Prompt 治理（阶段 4.3pre.3）

本目录集中管理所有 Agent 使用的提示词。设计目标：

- **纯自然语言**：业务人员可以直接阅读和修改
- **无模板引擎**：不使用 Jinja2、不使用变量占位符
- **动态数据由代码拼接**：Agent 在 Python 代码里 f-string 或字符串拼接
- **每个 prompt 独立自包含**：业务约束就地重复，不靠 include

## 目录规范

- 每个 `.md` 文件对应一个 prompt，文件名要能自解释
- 使用 `snake_case` 命名，和 Agent 里调用的 key 保持一致
- 文件内容用标准 Markdown，可以分节（业务背景 / 分析要求 / 输出格式）
- 文末给出**严格 JSON 输出格式示例**（不要加 markdown 代码块标记）
- 文件以下划线开头（例如 `_notes.md`）会被 `list_all()` 忽略（仅做工程笔记）

## 当前 prompt 清单（4.3pre.3 已精简）

| 文件 | 用途 | 使用方 |
| --- | --- | --- |
| `input_parser.md` | 用户自然语言 → 关键词 + 维度 + 调整需求 | InputParserAgent |
| `image_6elements.md` | 图文笔记 6 要素结构化标注（A/B/C/D/E/F + pain + direction） | ImageAnalysisAgent |
| `video_6elements.md` | 视频笔记 6 要素结构化标注（同图文 schema） | VideoAnalysisAgent |
| `viral_model_naming.md` | 给聚类后的爆文模型起名 + 写描述 | ViralModelAgent |
| `insight_summary.md` | 内容方向总结 + SEO 差异化建议（4.3pre.3 原生 schema） | InsightAgent |
| `rag_query_rewrite.md` | 改写查询 + 提取业务约束关键词 | RAGAgent |
| `canvas_render_disclaimer.md` | 画布合规免责声明 | CanvasRenderAgent |

## 4.3pre.3 清理记录

| 已删除 | 原使用方 | 替代品 |
| --- | --- | --- |
| `strategy_title.md` / `strategy_product.md` / `strategy_cover.md` / `strategy_structure.md` | StrategyAgent（已删除） | `viral_model_naming.md` + ViralModelAgent 矩阵聚类 |
| `insight_industry.md` / `insight_competitor.md` / `insight_brand.md` | InsightAgent 旧三段式 | `insight_summary.md`（一次调用原生 schema） |
| `image_analysis.md` / `video_analysis.md` | 旧汇总报告模式 | `image_6elements.md` / `video_6elements.md`（每条笔记结构化标注） |

## 写作约定

### 1. 系统指令 + 业务背景

每个 prompt 第一段说明 Agent 扮演的角色，第二段补充 B2B 业务定位
（RedMuse 只提供洞察和框架，不提供现成文案）。

### 2. 分析要求要明确、可检查

比如「每条结论不超过 40 字」「要保留行业通用术语（手持口播、单推手、开箱测评）」等，
不要写「详细」「全面」这种模糊副词。

### 3. 输出格式（最关键）

- 文末固定一段 `## 输出格式`
- 写明"严格返回 JSON，不要多余 markdown 代码块"
- 给出最小可行的 JSON 结构示例
- 字段命名统一使用 snake_case

### 4. 动态数据不要放 prompt

错误示例（有占位符）：

```markdown
关键词：{keywords}   ← 不要这样
```

正确做法：Agent 代码侧拼接：

```python
system_prompt = prompt_registry.load("insight_summary.md")
user_content = f"【关键词】{', '.join(keywords)}\n【样本】{len(samples)} 条\n" + ...
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_content},
]
```

## 修改 / 新增 prompt 的流程

1. 新增或修改文件 → 业务相关约束就地写清楚
2. 如果新增，更新本 README 的清单表格
3. Agent 调用处保持引用文件名即可
4. 运行 `pytest backend/tests/test_prompt_registry.py` 确认加载/缓存/计数
5. 手动 E2E 验证输出格式能被 JSON 解析
