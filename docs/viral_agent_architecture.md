# 爆文分析系统架构解析

本文档整理了爆文分析系统（viral_agent）的核心架构，包括知识库设计、Prompt 构建流程和特征提取机制。

---

## 目录

1. [知识库架构](#1-知识库架构)
   - [knowledge_base.json 的作用](#11-knowledge_basejson-的作用)
   - [三层架构设计](#12-三层架构设计)
   - [数据与代码分离](#13-数据与代码分离)
2. [AI 分析流程](#2-ai-分析流程)
   - [Prompt 拼接流程](#21-prompt-拼接流程)
   - [特征提取机制](#22-特征提取机制)
3. [附录：Claude Code 数据存储](#3-附录claude-code-数据存储)

---

## 1. 知识库架构

### 1.1 knowledge_base.json 的作用

`viral_agent/config/knowledge_base.json` 是**爆文分析系统的核心知识库**，为 AI 分析小红书笔记提供专业指导。

#### 文件结构

```
knowledge_base.json
├── analysis_knowledge (分析知识)
│   ├── title_analysis      → 标题分析：公式、热词、技巧
│   ├── content_analysis    → 内容分析：开头钩子、结构模板
│   ├── product_analysis    → 产品植入：时机策略、植入方法
│   └── viral_model         → 爆文模型：成功因素、创作模板
│
├── base_knowledge (基础知识)
│   ├── content_types       → 7种内容类型（单品推荐、干货教程等）
│   └── embed_methods       → 植入方式分类
│
└── domains (领域知识) ← 核心部分
    ├── eye_care           → 眼部护理
    ├── face_care          → 面部护理
    ├── lip_care           → 唇部护理
    ├── makeup             → 彩妆
    ├── body_care          → 身体护理
    ├── chocolate          → 巧克力/糖果
    ├── food_snacks        → 食品/零食
    └── beverages          → 饮品
```

#### 核心作用

| 功能 | 说明 | 示例 |
|------|------|------|
| **领域识别** | 通过关键词匹配判断笔记所属领域 | `["眼", "眼霜", "黑眼圈"]` → 眼部护理 |
| **专业分析指导** | 告诉 AI 如何分析标题、内容、植入 | 标题公式、开头钩子、植入时机 |
| **领域专属知识** | 每个领域有针对性的分析维度 | 眼部问题点、引出方式、植入方式 |
| **爆文创作模板** | 提供可复制的内容结构 | 干货教程模板、种草推荐模板 |

---

### 1.2 三层架构设计

系统采用经典的三层架构，将数据、访问层、业务逻辑分离：

```
┌─────────────────────────────────────────────────────────────────────┐
│                         外部调用者                                   │
│         (viral_analyzer.py, title_prompts.py, 等等)                  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│          prompts/knowledge_base.py  【对外接口层】                    │
│  ─────────────────────────────────────────────────────────────────  │
│  职责：提供简单的函数给其他模块使用                                    │
│                                                                     │
│  • detect_domain(title, desc)     → 检测领域                        │
│  • get_domain_knowledge(domains)  → 获取领域知识文本                  │
│  • build_dynamic_prompt(...)      → 构建动态 Prompt                  │
│                                                                     │
│  特点：                                                              │
│  ✓ 对外暴露简单函数                                                  │
│  ✓ 包含硬编码知识作为降级兼容                                         │
│  ✓ 优先使用 JSON 配置，失败时用硬编码                                 │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│          config/knowledge_loader.py  【数据访问层】                   │
│  ─────────────────────────────────────────────────────────────────  │
│  职责：JSON 文件的增删改查操作                                        │
│                                                                     │
│  class KnowledgeBaseConfig:                                         │
│  • _load_config()               → 加载 JSON 文件                    │
│  • save_config()                → 保存 JSON 文件（自动备份）          │
│  • get_domain_by_id()           → 查询单个领域                       │
│  • add_domain() / update_domain() / delete_domain()  → 增删改       │
│  • detect_domain()              → 关键词匹配检测                     │
│  • get_domain_knowledge_text()  → 格式化知识为字符串                  │
│                                                                     │
│  特点：                                                              │
│  ✓ 单例模式 (get_knowledge_config)                                  │
│  ✓ 自动备份机制                                                      │
│  ✓ 配置验证                                                         │
│  ✓ 支持导入/导出                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│          config/knowledge_base.json  【数据存储层】                   │
│  ─────────────────────────────────────────────────────────────────  │
│  职责：持久化存储知识库数据                                           │
│                                                                     │
│  特点：                                                              │
│  ✓ 纯数据，无代码逻辑                                                │
│  ✓ 可通过 Web 界面编辑                                               │
│  ✓ 人类可读，方便手动修改                                            │
└─────────────────────────────────────────────────────────────────────┘
```

#### 职责分工

| 文件 | 角色 | 职责 | 类比 |
|------|------|------|------|
| `knowledge_base.json` | **数据层** | 存储原始数据 | 数据库表 |
| `knowledge_loader.py` | **数据访问层** | CRUD 操作、数据验证 | DAO/Repository |
| `knowledge_base.py` | **业务逻辑层** | 对外提供业务功能 | Service |

#### 为什么需要三层？

| 问题 | 解决方案 |
|------|---------|
| JSON 加载失败怎么办？ | `knowledge_base.py` 有硬编码降级 |
| 多处调用 JSON 会重复加载？ | `knowledge_loader.py` 用单例模式 |
| 修改 JSON 后如何生效？ | `reload_knowledge_config()` 刷新单例 |
| 如何保证数据安全？ | Loader 自动备份，保留最近 10 个 |
| 外部调用太复杂？ | `knowledge_base.py` 封装简单函数 |

---

### 1.3 数据与代码分离

`knowledge_base.json` 和 `prompts/*.py` 的区别：

| 对比维度 | `knowledge_base.json` | `prompts/*.py` |
|---------|----------------------|----------------|
| **文件类型** | 纯数据（JSON） | 代码（Python） |
| **内容** | 领域知识、规则、示例 | 提示词模板、构建逻辑 |
| **谁维护** | 运营/产品人员 | 开发人员 |
| **修改难度** | 直接编辑，无需懂代码 | 需要 Python 知识 |
| **作用** | **"知道什么"** | **"如何使用"** |

#### prompts 目录结构

```
prompts/
├── title_prompts.py        → 标题分析的 Prompt 模板
├── content_prompts.py      → 内容分析的 Prompt 模板
├── product_prompts.py      → 产品植入分析的 Prompt 模板
├── viral_model_prompts.py  → 爆文模型生成的 Prompt 模板
├── viral_analysis_prompts.py → 综合分析的 Prompt 模板
├── knowledge_base.py       → 知识库加载器（读取 JSON）
└── ...
```

#### 设计优势

1. **灵活性**：新增"母婴"领域？只需在 JSON 中添加一个 domain，无需改代码
2. **可维护性**：运营团队可以直接更新知识库
3. **降级兼容**：JSON 加载失败时，代码中有硬编码副本兜底
4. **关注点分离**：数据归数据，逻辑归逻辑

---

## 2. AI 分析流程

### 2.1 Prompt 拼接流程

最终发送给 AI 的 Prompt 由多个部分动态组装而成：

#### 完整流程图

```
用户触发分析（关键词"眼霜"）
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Step 1: 检索知识                                        │
│  _retrieve_knowledge(keyword="眼霜", notes)              │
│       │                                                 │
│       ├── 领域检测: detect_domain("眼霜") → ["eye_care"] │
│       ├── JSON知识: 从 knowledge_base.json 获取          │
│       └── RAG检索: 从 ChromaDB 向量库检索相关文档         │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Step 2: 构建知识部分                                    │
│  knowledge_section = ""                                 │
│  knowledge_section += "【结构化知识库】" + JSON知识       │
│  knowledge_section += "【历史成功案例】" + RAG检索结果    │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Step 3: 调用 Prompt 构建器                              │
│  build_viral_analysis_prompt(                           │
│      keyword="眼霜",                                    │
│      samples=[...],        # 3篇样本笔记                │
│      features={...},       # 统计数据                   │
│      knowledge_section     # 上一步的知识               │
│  )                                                      │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│  Step 4: 发送给 AI                                       │
│  messages = [                                           │
│    {"role": "system", "content": "你是分析专家..."},     │
│    {"role": "user", "content": 拼接后的完整Prompt}       │
│  ]                                                      │
└─────────────────────────────────────────────────────────┘
```

#### 最终 Prompt 结构

```
┌────────────────────────────────────────────────────────────┐
│  你是小红书爆款内容分析专家。                                │
│  请对以下关于"眼霜"的爆款图文笔记进行深度分析。              │  ← 任务描述
│                                                            │
│  ══════════════════════════════════════════════════════   │
│                                                            │
│  【结构化知识库（行业规则与模板）】                           │
│  眼部护理问题相关：                                         │
│  - 眼纹/细纹/鱼尾纹                                         │  ← 来自 JSON 配置
│  - 黑眼圈/熊猫眼                                            │
│  眼部产品引出方式：眼部问题引出、护理眼部...                  │
│                                                            │
│  【历史成功案例（RAG智能检索）】                             │
│  案例1: "这款眼霜用了3周，眼纹明显淡了..."                   │  ← 来自 RAG 检索
│                                                            │
│  ══════════════════════════════════════════════════════   │
│                                                            │
│  【样本笔记】                                               │
│  [                                                         │
│    {"title": "眼纹怎么救？", "content": "...", ...},        │  ← 采集的爆款数据
│    ...                                                     │
│  ]                                                         │
│                                                            │
│  【整体统计特征】                                           │
│  - 平均标题长度：18字                                       │
│  - 标题高频词：眼霜, 黑眼圈, 眼纹...                         │  ← 特征提取器计算
│  - 平均点赞数：5200                                         │
│                                                            │
│  【深度分析要求】                                           │
│  请从以下维度分析：                                         │
│  1. 标题策略                                               │  ← 固定模板
│  2. 内容结构                                               │
│  3. 情感共鸣                                               │
│  ...                                                       │
│                                                            │
│  请以JSON格式返回：{...}                                    │  ← 输出格式约束
└────────────────────────────────────────────────────────────┘
```

#### 各部分来源汇总

| Prompt 组成部分 | 来源文件 | 来源方式 |
|---------------|---------|---------|
| 系统角色 | `viral_analyzer.py:524` | 硬编码 |
| 任务描述 | `viral_analysis_prompts.py:14` | 模板 |
| 结构化知识 | `knowledge_base.json` → `knowledge_loader.py` | JSON 动态加载 |
| RAG 知识 | `ChromaDB` → `rag_service.py` | 向量检索 |
| 样本笔记 | 爬虫采集数据 | 运行时传入 |
| 统计特征 | `feature_extractor.py` 计算 | 运行时传入 |
| 分析任务 | `viral_analysis_prompts.py:29-65` | 模板固定 |
| 输出格式 | `viral_analysis_prompts.py:66-93` | JSON Schema |

---

### 2.2 特征提取机制

`features` 是**特征提取器**（`ViralFeatureExtractor`）从原始笔记数据中统计计算出来的结构化数据。

#### 作用

1. **给 AI 提供量化依据** — 告诉 AI "平均标题长度18字"，而非让 AI 自己数
2. **填充 Prompt 模板** — 插入到 `【整体统计特征】` 部分
3. **生成爆文模型** — 独立于 AI，直接用统计数据生成部分建议

#### features 完整结构

```python
features = {
    'title_features': {
        'avg_length': 18.5,              # 平均标题长度
        'length_distribution': {...},    # 长度分布
        'top_keywords': [...],           # 高频词（jieba分词）
        'common_patterns': [...],        # 标题模式（正则匹配）
        'emoji_usage': {...}             # Emoji 使用情况
    },
    'content_features': {
        'avg_length': 350.2,             # 平均正文长度
        'top_keywords': [...],           # 正文高频词
        'top_tags': [...],               # 标签统计
        'structure_patterns': {          # 内容结构分析
            'has_list': 65.0,            # 65% 包含列表
            'has_steps': 40.0,           # 40% 包含步骤
            'has_tips': 55.0,            # 55% 包含小贴士
            'has_cta': 80.0              # 80% 包含行动号召
        }
    },
    'interaction_features': {
        'avg_liked': 5200,               # 平均点赞
        'avg_collected': 3100,           # 平均收藏
        'avg_collection_rate': 0.596,    # 收藏率
        'max_interaction': 85000         # 最高互动
    },
    'user_features': {
        'unique_users': 18,              # 去重用户数
        'top_viral_creators': [...],     # 高产爆款用户
        'top_locations': [...]           # IP 归属地分布
    },
    'time_features': {
        'upload_distribution': {...}     # 发布时间分布
    },
    'cover_features': {...},             # 封面特征（OCR）
    'video_features': {...}              # 视频特征
}
```

#### 提取方法

| 特征 | 方法 |
|-----|------|
| 高频词 | `jieba` 中文分词 + `Counter` 统计 |
| 标题模式 | 正则表达式匹配（数字开头、问号结尾等） |
| Emoji | Unicode 范围正则匹配 |
| 内容结构 | 正则检测（列表、步骤、CTA等） |
| 互动数据 | 数学计算（平均值、比率、分布） |

#### 数据流

```
原始笔记数据 (25篇)
        │
        ▼
ViralFeatureExtractor.extract_all_features()
        │
        ├── extract_title_features()    → jieba分词、正则匹配
        ├── extract_content_features()  → 结构检测、标签统计
        ├── extract_interaction_features() → 求平均、计算比率
        └── ...
        │
        ▼
features 字典
        │
        ├──→ 填充 Prompt 模板（发送给 AI）
        └──→ 生成爆文模型（不依赖 AI）
```

#### 为什么需要特征提取？

| 问题 | 解决方案 |
|-----|---------|
| AI 处理原始数据成本高 | 先统计，只把摘要给 AI |
| AI 数不清楚数字 | 用代码精确计算 |
| 需要量化分析依据 | 提供具体数字佐证 |
| 部分功能不需要 AI | 统计数据直接生成建议 |

---

## 3. 附录：Claude Code 数据存储

Claude Code 在 `~/.claude/` 目录下存储数据，主要包括：

| 存储位置 | 内容 | 大小 | 可否清除 |
|---------|------|------|---------|
| `history.jsonl` | 命令输入历史（用于补全） | ~75KB | ✅ **最安全清除** |
| `file-history/` | 文件修改历史（支持撤销） | 多个会话 | ⚠️ 清除后失去撤销能力 |
| `projects/` | 项目会话记录（对话历史） | ~14MB | ⚠️ 清除后失去历史对话 |

#### 清理命令

```bash
# 只清除命令历史（最安全）
rm ~/.claude/history.jsonl

# 清除旧的文件历史（保留撤销能力的折中方案）
find ~/.claude/file-history -type d -mtime +7 -exec rm -rf {} +

# 完全清理（重置为全新状态）
rm -rf ~/.claude/file-history ~/.claude/projects ~/.claude/history.jsonl
```

---

## 总结

### 核心设计原则

1. **数据与代码分离** — JSON 存数据，Python 写逻辑
2. **三层架构** — 数据层、访问层、业务层职责清晰
3. **特征工程** — 用传统统计预处理，降低 AI 成本
4. **模块化 Prompt** — 角色 + 知识 + 数据 + 任务 + 格式

### 关键文件清单

| 文件 | 作用 |
|------|------|
| `config/knowledge_base.json` | 知识库数据存储 |
| `config/knowledge_loader.py` | 知识库 CRUD 操作 |
| `prompts/knowledge_base.py` | 知识库业务接口 |
| `prompts/viral_analysis_prompts.py` | Prompt 模板 |
| `services/feature_extractor.py` | 特征提取器 |
| `services/viral_analyzer.py` | 核心分析器 |
| `services/knowledge_retriever.py` | 统一知识检索（JSON+RAG） |

---

*文档生成时间：2025-12-07*
