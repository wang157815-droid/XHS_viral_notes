# 红书爆文模型 AI Agent 开发方案 v2.0

> 本版本基于初版方案，整合以下优化：
> - 补充 CrawlerAgent 作为流程第一节点
> - 明确爬虫与 Agent 系统的分层边界
> - 加入三层数据策略（基础库 / 按需爬取 / 缓存复用）
> - 修正并行架构与耗时估算
> - 补充完整的技术栈说明（ChromaDB / 爬虫 / 知识库）

---

## 1. 业务理解摘要

本系统是面向小红书平台的爆文分析与生成 Multi-Agent 系统。用户输入关键词和配置参数后，系统首先由 **CrawlerAgent** 判断并按需采集该关键词的真实平台数据，再经过语义理解 → 多模态图文对齐 → 视频分析（异步）→ 爆文策略生成的流水线，结合 RAG 知识库（爆文规则、行业SOP）增强生成质量，最终输出 Excel 报告和 AI 深度洞察。

**核心设计原则**：爬虫是流程第一节点而非离线定时任务，但通过缓存和基础知识库将爬取频率降到最低，保证用户体验。

---

## 2. 架构总览

### 2.1 分层架构

```
┌──────────────────────────────────────────────────────┐
│                   交互层（用户侧）                      │
│         关键词输入 / 参数配置 / 进度推送(SSE)            │
└──────────────────────────┬───────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────┐
│                  在线 Agent 流水线                      │
│                                                      │
│  CrawlerAgent → [并行] SemanticAnalysis + RAG         │
│                          ↓                           │
│                  MultimodalAlignAgent                 │
│                          ↓  (VideoAnalysis 异步)      │
│                  ContentStrategyAgent                 │
│                          ↓                           │
│                     ReportAgent                      │
└──────────────────────────┬───────────────────────────┘
                           │ 读写
┌──────────────────────────▼───────────────────────────┐
│                    数据基础设施层                        │
│                                                      │
│  ChromaDB（向量库）  Redis（缓存）  JSON知识库文件        │
│                                                      │
│  后台补充：定时爬虫（热榜词预热，不依赖用户触发）            │
└──────────────────────────────────────────────────────┘
```

### 2.2 三层数据策略

```
用户提交关键词
      ↓
CrawlerAgent 判断逻辑：
      ├── 第一层：Redis 缓存命中（7天内爬过）→ 直接跳过，< 1s
      ├── 第二层：ChromaDB 已有 ≥ 30 条相关数据 → 跳过，< 1s
      └── 第三层：无数据 → 实时爬取 20~50 条，60~90s
                         → 通知前端显示等待进度
```

---

## 3. Agent 拆分方案

### Agent 清单

| 顺序 | Agent 名称 | 职责描述 | 触发方式 | 关键输入 | 关键输出 |
|---|---|---|---|---|---|
| 1 | **CrawlerAgent** | 判断数据充足性，按需爬取关键词数据 | 流程入口，用户提交后第一个执行 | 关键词、场景配置 | 数据就绪信号、数据来源标记 |
| 2 | **SemanticAnalysisAgent** | 深度文本语义理解，提取标题/场景/情绪特征 | CrawlerAgent 完成后并行启动 | 关键词、知识库文本 | 语义特征向量、标题模式、场景标签 |
| 2 | **RAGRetrievalAgent** | 检索爆文规则和行业SOP，与语义分析并行 | CrawlerAgent 完成后并行启动 | 场景标签、特征 | 爆文规则列表、SOP文档片段 |
| 3 | **MultimodalAlignAgent** | 封面图分析、图文对齐、产品植入识别 | SemanticAnalysis 完成后 | 语义特征、封面图URL | 视觉特征、图文对齐分数 |
| 4 | **VideoAnalysisAgent** | 视频关键帧提取与内容理解 | 异步触发，不阻塞主流程 | 视频URL | 关键帧摘要、视频内容特征（异步追加） |
| 5 | **ContentStrategyAgent** | 综合所有特征生成爆文策略和候选标题 | Multimodal + RAG 汇聚后 | 全量特征 + RAG结果 | 爆文策略、标题候选、内容结构 |
| 6 | **ReportAgent** | 生成 Excel 报告和 AI 深度洞察 | ContentStrategy 完成后 | 策略结果 | Excel文件、洞察文本 |

### Agent 独立性说明

- **CrawlerAgent**：流程第一节点，职责是数据准备而非内容分析。**LLM 非必须**，主要是规则判断（缓存检查 → 数据量检查 → 爬取决策）+ I/O 操作。独立的原因是它的执行时间高度不确定（0s 或 90s），必须单独管理状态和用户通知。
- **SemanticAnalysisAgent**：需要 LLM 做深度语义推理，职责单一，可复用于其他内容分析场景。
- **RAGRetrievalAgent**：纯检索逻辑，**LLM 非必须**（向量检索 + rerank），与语义分析无依赖关系，天然适合并行。
- **MultimodalAlignAgent**：涉及视觉模型（Qwen3 多模态），资源消耗与纯文本处理差异大，独立保障 GPU 资源调度。
- **VideoAnalysisAgent**：单次耗时 30~60s，**必须异步化**，结果完成后追加到报告，不能阻塞主流程。
- **ContentStrategyAgent**：核心生成节点，使用 DeepSeek 最强推理能力，独立保障 token 优先级和 prompt 策略。
- **ReportAgent**：主要是代码逻辑（openpyxl），LLM 仅用于洞察摘要，独立便于格式模板迭代。

---

## 4. 工具（Tools）清单

### CrawlerAgent Tools

```python
tools = [
    Tool(
        name="check_cache",
        description="检查 Redis 中是否存在该关键词的近期爬取记录（7天内），有则跳过爬取",
        func=check_cache_func,
        args_schema=CacheCheckInput  # keyword: str
        # 输出: {hit: bool, crawled_at: str | None}
    ),
    Tool(
        name="check_data_sufficiency",
        description="查询 ChromaDB 中该关键词相关数据的数量，≥30条视为充足",
        func=check_data_func,
        args_schema=DataCheckInput  # keyword: str, scene: str
        # 输出: {count: int, is_sufficient: bool}
    ),
    Tool(
        name="crawl_keyword",
        description="针对指定关键词实时爬取小红书笔记，清洗后写入 ChromaDB，仅在数据不足时调用",
        func=crawl_keyword_func,
        args_schema=CrawlInput  # keyword: str, limit: int = 50
        # 输出: {crawled: int, stored: int, skipped: int}
        # 失败处理: 爬取失败降级使用通用知识库，不中断流程
    ),
    Tool(
        name="notify_user_waiting",
        description="当需要实时爬取时通过 SSE 通知前端显示等待提示和预计时间",
        func=notify_func,
        args_schema=NotifyInput  # task_id: str, message: str, eta_seconds: int
    ),
]
```

### SemanticAnalysisAgent Tools

```python
tools = [
    Tool(
        name="embed_text",
        description="使用 Qwen text-embedding-v4 对文本列表进行向量化，用于语义相似度计算",
        func=embed_text_func,
        args_schema=EmbedInput  # texts: List[str]
        # 输出: List[List[float]]
        # 失败处理: 超长文本自动截断至4096 token，API限流时指数退避重试3次
    ),
    Tool(
        name="extract_title_features",
        description="从爆文标题列表中提取高频模式：情绪词、数字用法、疑问句式、痛点表达",
        func=extract_title_func,
        args_schema=TitleInput  # titles: List[str]
        # 输出: {emotion_words: List, number_patterns: List, question_ratio: float, pain_points: List}
    ),
    Tool(
        name="analyze_scene_features",
        description="识别内容所属垂类场景（美妆/穿搭/美食/数码等）及场景下的内容特色标签",
        func=analyze_scene_func,
        args_schema=SceneInput  # content: str
        # 输出: {scene: str, sub_tags: List[str], confidence: float}
    ),
]
```

### RAGRetrievalAgent Tools

```python
tools = [
    Tool(
        name="search_viral_rules",
        description="在爆文规则 JSON 知识库对应的 ChromaDB Collection 中检索匹配规则，按场景过滤",
        func=search_rules_func,
        args_schema=SearchInput  # query: str, scene: str, top_k: int = 5
        # 输出: List[{rule_id, rule_text, category, weight, relevance_score}]
        # 失败处理: 检索结果为空时降级返回 general.json 通用规则
    ),
    Tool(
        name="search_industry_sop",
        description="检索行业SOP文档，获取特定垂类内容的标准创作流程和最佳实践",
        func=search_sop_func,
        args_schema=SOPInput  # industry: str, content_type: str
        # 输出: {sop_text: str, source_file: str}
    ),
    Tool(
        name="vector_search_similar_posts",
        description="在 ChromaDB viral_posts Collection 中检索语义相似的历史爆文案例",
        func=vector_search_func,
        args_schema=VectorSearchInput  # embedding: List[float], top_k: int = 10, scene: str
        # 输出: List[{post_id, title, content_snippet, metrics, similarity_score}]
    ),
]
```

### MultimodalAlignAgent Tools

```python
tools = [
    Tool(
        name="analyze_cover_image",
        description="使用 Qwen3 多模态模型分析封面图片，提取色彩、构图、人物、文字占比等视觉特征",
        func=analyze_cover_func,
        args_schema=CoverInput  # image_url: str
        # 输出: {color_scheme, composition, has_face, text_ratio, attractiveness_score}
        # 失败处理: 图片加载失败时跳过，以空结果继续，报告中标注"封面数据不可用"
    ),
    Tool(
        name="align_text_image",
        description="计算文本内容与封面图片的语义对齐分数，识别图文不一致的具体问题",
        func=align_func,
        args_schema=AlignInput  # text: str, image_url: str
        # 输出: {alignment_score: float, misalign_points: List[str]}
    ),
    Tool(
        name="detect_product_placement",
        description="识别图片中的产品植入位置、品牌可见度和植入自然度评分",
        func=detect_product_func,
        args_schema=ProductInput  # image_url: str
        # 输出: {has_product: bool, position: str, visibility_score: float, naturalness_score: float}
    ),
]
```

### VideoAnalysisAgent Tools

```python
tools = [
    Tool(
        name="extract_keyframes",
        description="从视频URL按时间轴提取关键帧，每5秒一帧，识别场景切换点",
        func=extract_keyframes_func,
        args_schema=VideoInput  # video_url: str, interval_sec: int = 5
        # 输出: List[{timestamp, frame_base64, scene_desc}]
        # 失败处理: 不支持的视频格式返回空列表，整个 VideoAnalysisAgent 静默跳过
    ),
    Tool(
        name="analyze_video_content",
        description="使用 Qwen3 多模态理解视频关键帧序列，提取核心卖点、情绪弧线和高光时刻",
        func=analyze_video_func,
        args_schema=VideoAnalysisInput  # keyframes: List[dict]
        # 输出: {summary, highlights: List[{timestamp, description}], emotion_curve, key_selling_points}
    ),
    Tool(
        name="fetch_interaction_data",
        description="获取该笔记的点赞、评论、收藏数据，用于互动特征分析（来自爬虫已采集数据）",
        func=fetch_interaction_func,
        args_schema=InteractionInput  # post_id: str
        # 输出: {likes, comments, saves, shares, engagement_rate}
    ),
]
```

### ContentStrategyAgent Tools

```python
tools = [
    Tool(
        name="generate_title_candidates",
        description="基于语义特征和爆文规则，使用 DeepSeek 生成10个差异化候选标题，覆盖不同钩子策略",
        func=gen_titles_func,
        args_schema=TitleGenInput  # semantic_features: dict, rules: List[str], scene: str
        # 输出: List[{title, hook_type, predicted_ctr, reason}]
    ),
    Tool(
        name="generate_content_structure",
        description="生成爆文内容骨架：开头钩子（前3行）、正文段落结构、结尾互动引导",
        func=gen_structure_func,
        args_schema=StructureInput  # strategy: dict, sop: str, video_insights: dict | None
        # 输出: {hook, body_sections: List, cta, estimated_length}
    ),
    Tool(
        name="score_viral_potential",
        description="对生成的标题和内容结构进行爆文潜力综合评分，输出改进建议",
        func=score_func,
        args_schema=ScoreInput  # titles: List, structure: dict, features: dict
        # 输出: {overall_score: float, breakdown: dict, top_title: str, improvement_suggestions: List}
        # 冷启动说明: 数据不足时使用规则评分而非模型预测
    ),
]
```

### ReportAgent Tools

```python
tools = [
    Tool(
        name="generate_excel_report",
        description="将爆文分析全量结果导出为多 Sheet 的 Excel 报告（标题候选/特征分析/相似案例/评分明细）",
        func=gen_excel_func,
        args_schema=ExcelInput  # strategy_result: dict, task_id: str
        # 输出: {file_path: str, sheet_count: int}
        # 失败处理: 字段缺失时用空值填充，确保文件始终生成
    ),
    Tool(
        name="generate_ai_insights",
        description="基于全量分析数据生成结构化 AI 洞察文本，提炼3个核心发现和可执行建议",
        func=gen_insights_func,
        args_schema=InsightInput  # analysis_results: dict
        # 输出: {insights: List[str], recommendations: List[str], summary: str}
    ),
    Tool(
        name="append_video_insights",
        description="视频分析异步完成后，将视频洞察追加写入已生成的 Excel 报告",
        func=append_video_func,
        args_schema=AppendInput  # task_id: str, video_result: dict
    ),
]
```

---

## 5. 数据基础设施

### 5.1 ChromaDB 向量库

三个 Collection 分工明确：

| Collection | 存储内容 | 写入方 | 读取方 |
|---|---|---|---|
| `viral_posts` | 爬取的历史爆文全文 + 互动指标 | CrawlerAgent / 定时爬虫 | RAGRetrievalAgent |
| `viral_rules` | JSON知识库中的爆文规则条目 | 知识库同步脚本 | RAGRetrievalAgent |
| `industry_sop` | 行业SOP文档分块 | 知识库同步脚本 | RAGRetrievalAgent |

```python
import chromadb
from chromadb.utils import embedding_functions

client = chromadb.PersistentClient(path="./chroma_db")

qwen_ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key="DASHSCOPE_KEY",
    api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    model_name="text-embedding-v4"
)

viral_posts = client.get_or_create_collection(
    name="viral_posts",
    embedding_function=qwen_ef,
    metadata={"hnsw:space": "cosine"}
)
```

### 5.2 爬虫实现方案

**推荐优先级**：MediaCrawler 开源工具 > App 接口逆向 > 浏览器自动化

**CrawlerAgent 调用的核心爬取逻辑**：

```python
import asyncio, httpx, redis, json
from pathlib import Path

redis_client = redis.Redis(host="localhost", port=6379, db=1)

class XHSCrawlerService:
    """
    被 CrawlerAgent 调用的爬取服务
    内置频率控制、Cookie 轮换、代理池
    """
    
    def __init__(self, cookie_pool: CookiePool, proxy_pool: list):
        self.cookie_pool = cookie_pool
        self.proxy_pool = proxy_pool
        self.rate_limiter = RateLimiter(max_calls=30, period=60)
    
    async def crawl_keyword(self, keyword: str, limit: int = 50) -> list:
        cookie = self.cookie_pool.get_available()
        if not cookie:
            raise Exception("所有 Cookie 均已达到今日上限")
        
        results = []
        page = 1
        
        while len(results) < limit:
            await self.rate_limiter.acquire()
            await asyncio.sleep(2 + random.random() * 3)  # 2~5秒随机间隔
            
            raw = await self._fetch_page(keyword, page, cookie)
            if not raw:
                break
            results.extend(raw)
            page += 1
        
        return results[:limit]
    
    async def _fetch_page(self, keyword: str, page: int, cookie: dict) -> list:
        import random
        proxy = random.choice(self.proxy_pool)
        
        async with httpx.AsyncClient(proxy=proxy, timeout=15) as client:
            headers = self.signer.build_headers(
                "/api/sns/web/v1/search/notes",
                {"keyword": keyword, "page": page}
            )
            resp = await client.post(
                "https://edith.xiaohongshu.com/api/sns/web/v1/search/notes",
                json={"keyword": keyword, "page": page, "page_size": 20},
                headers=headers
            )
            data = resp.json()
            if data.get("code") == -9999:
                self.cookie_pool.mark_blocked(cookie["account_id"])
                return []
            return data.get("data", {}).get("items", [])
```

**数据清洗与入库 Pipeline**：

```python
class DataPipeline:
    
    def clean(self, raw: dict) -> dict | None:
        content = raw.get("desc", "").strip()
        if len(content) < 50:
            return None
        likes = int(str(raw.get("liked_count", "0")).replace("万", "0000"))
        if likes < 500:
            return None  # 过滤低互动内容
        
        return {
            "post_id": raw["note_id"],
            "title": raw.get("title", ""),
            "content": re.sub(r"#\S+", "", content).strip(),
            "tags": [t["name"] for t in raw.get("tag_list", [])],
            "metrics": {
                "likes": likes,
                "comments": raw.get("comment_count", 0),
                "saves": raw.get("collect_count", 0),
            },
            "covers": raw.get("image_list", []),
            "video_url": raw.get("video_url"),
            "crawled_at": datetime.now().isoformat(),
        }
    
    def upsert_to_chromadb(self, data: dict):
        doc = f"{data['title']}\n{data['content']}"
        viral_posts.upsert(
            documents=[doc],
            metadatas=[{
                "post_id": data["post_id"],
                "tags": ",".join(data["tags"]),
                "likes": str(data["metrics"]["likes"]),
                "has_video": str(bool(data["video_url"])),
            }],
            ids=[data["post_id"]]
        )
```

### 5.3 JSON 知识库结构

```
knowledge_base/
├── viral_rules/
│   ├── beauty.json        # 美妆垂类规则
│   ├── fashion.json       # 穿搭垂类规则
│   ├── food.json          # 美食垂类规则
│   └── general.json       # 通用兜底规则
├── industry_sop/
│   ├── product_review.json
│   ├── lifestyle.json
│   └── tutorial.json
└── config/
    └── scene_mapping.json # 场景标签映射
```

规则条目格式：

```json
{
  "scene": "beauty",
  "version": "1.2.0",
  "rules": [
    {
      "id": "beauty_001",
      "category": "title",
      "rule": "标题包含具体数字效果（如'用了7天皮肤变白'）比模糊表达点击率高约40%",
      "examples": ["28天祛痘日记", "用了3瓶终于搞懂了精华"],
      "weight": 0.9
    }
  ]
}
```

---

## 6. Agent 间通信与协作方案

### 协作模式：Hierarchical + Sequential + Parallel 混合

```
OrchestratorAgent
│
├─→ CrawlerAgent（串行，必须先完成）
│         ↓
├─→ [并行]
│   ├─→ SemanticAnalysisAgent
│   │         ↓
│   │   MultimodalAlignAgent
│   └─→ RAGRetrievalAgent
│         ↓（两路汇聚）
├─→ ContentStrategyAgent
│         ↓
├─→ ReportAgent → 主流程结束
│
└─→ VideoAnalysisAgent（异步，完成后追加）
```

### 全局状态定义

```python
from langgraph.graph import StateGraph, END
from langgraph.constants import Send
from typing import TypedDict, Annotated, List, Optional
import operator

class ViralAgentState(TypedDict):
    # 用户输入
    task_id: str
    keywords: List[str]
    config: dict

    # 任务状态
    task_status: str          # pending / crawling / analyzing / generating / done / failed
    data_source: str          # cache / knowledge_base / realtime_crawl

    # 各 Agent 输出
    crawler_result: Optional[dict]
    semantic_features: Optional[dict]
    multimodal_results: Optional[dict]
    video_analysis: Optional[dict]       # 异步填充，可为 None
    rag_context: Optional[dict]
    content_strategy: Optional[dict]

    # 最终输出
    excel_report_path: Optional[str]
    ai_insights: Optional[str]

    # 错误收集（append 模式）
    errors: Annotated[List[str], operator.add]
```

### LangGraph 状态图骨架

```python
from langgraph.graph import StateGraph, END
from langgraph.constants import Send
from langgraph.checkpoint.memory import MemorySaver

workflow = StateGraph(ViralAgentState)

# 注册节点
workflow.add_node("orchestrator",       orchestrator_node)
workflow.add_node("crawler",            crawler_node)          # 新增
workflow.add_node("semantic_analysis",  semantic_analysis_node)
workflow.add_node("rag_retrieval",      rag_retrieval_node)
workflow.add_node("multimodal_align",   multimodal_align_node)
workflow.add_node("join",               join_node)             # 等待并行汇聚
workflow.add_node("content_strategy",   content_strategy_node)
workflow.add_node("report",             report_node)

# 入口
workflow.set_entry_point("orchestrator")
workflow.add_edge("orchestrator", "crawler")

# Crawler 完成后并行分发
def post_crawler_fanout(state: ViralAgentState):
    return [
        Send("semantic_analysis", state),
        Send("rag_retrieval", state),
    ]

workflow.add_conditional_edges("crawler", post_crawler_fanout)

# 主流水线
workflow.add_edge("semantic_analysis", "multimodal_align")

# 汇聚：multimodal + rag 都完成才进入策略生成
workflow.add_edge("multimodal_align", "join")
workflow.add_edge("rag_retrieval",    "join")
workflow.add_edge("join",             "content_strategy")
workflow.add_edge("content_strategy", "report")
workflow.add_edge("report",           END)

# 编译，strategy 前支持人工审核介入
app = workflow.compile(
    checkpointer=MemorySaver(),
    interrupt_before=["content_strategy"]
)

# 视频分析独立异步任务（Celery）
@celery_app.task
def video_analysis_task(task_id: str, video_url: str):
    result = run_video_analysis(video_url)
    append_video_insights_to_report(task_id, result)
    notify_frontend(task_id, {"type": "video_done", "data": result})
```

### 消息协议

```python
class AgentResult(BaseModel):
    agent_name: str
    task_id: str
    status: Literal["success", "partial", "failed"]
    data: dict
    metadata: dict    # duration_ms, tokens_used, model_version
    errors: List[str]
```

### 错误处理策略

| 失败场景 | 处理策略 |
|---|---|
| CrawlerAgent 爬取失败 | 降级使用现有知识库数据，报告中标注"基于通用数据" |
| Cookie 全部封禁 | 暂停爬取，通知用户，仍用已有数据继续分析 |
| VideoAnalysisAgent 超时 | 静默跳过，报告中标注"视频分析不可用" |
| RAG 检索结果为空 | 降级返回 general.json 通用规则 |
| LLM API 限流 | 指数退避重试3次，仍失败则规则引擎兜底 |
| 封面图加载失败 | 跳过多模态分析，仅用文本特征 |

---

## 7. 性能与用户体验

### 耗时分析（优化后）

```
用户提交
  ↓ 0s      立即返回 task_id，SSE 连接建立

【有缓存 / 数据充足】
  ↓ <1s     CrawlerAgent 跳过爬取
  ↓ 1~5s    "✅ 语义分析中..."（并行）
  ↓ 5~13s   "✅ 语义完成 | ⏳ 图文分析中..."
  ↓ 13s     ContentStrategyAgent 开始，LLM 流式输出
  ↓ 13~23s  标题和策略内容逐字出现
  ↓ 25s     "✅ 报告已生成，可下载"

【首次关键词，需要爬取】
  ↓ 0s      "「职场穿搭」是新关键词，正在采集数据（预计60秒）..."
  ↓ 60~90s  爬取完成，进入正常分析流程
  ↓ 90~115s 报告完成

【视频内容（异步补充）】
  主流程 25s 完成，报告可下载
  后台继续："🔄 视频深度分析中，完成后自动更新报告"
  +30~60s   "✅ 视频分析完成，报告已更新"
```

| 场景 | 耗时 | 用户感知 |
|---|---|---|
| 有缓存（7天内） | **< 25s** | 流式输出，13s 开始看到内容 |
| 知识库已有数据 | **< 25s** | 同上 |
| 首次关键词 | **90~115s** | 明确提示等待，进度条可见 |
| 含视频内容 | **25s + 异步** | 先拿报告，视频结果后补 |

### SSE 前端对接

```javascript
const evtSource = new EventSource(`/api/task/${taskId}/stream`);

evtSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    
    switch(data.type) {
        case "progress":
            updateStatusBar(data.step, data.message);
            break;
        case "stream_token":
            appendToken(data.content);      // LLM 逐字追加
            break;
        case "done":
            showDownloadButton(data.report_url);
            evtSource.close();
            break;
        case "video_done":
            showVideoUpdateBadge();         // 报告已更新提示
            break;
    }
};
```

---

## 8. 实施建议

### 开发优先级（推荐顺序）

**第一阶段（核心链路，2周）**
1. `CrawlerAgent` + MediaCrawler 接入 + Redis 缓存层
2. `RAGRetrievalAgent` + ChromaDB 搭建 + JSON 知识库初始化（≥ 500 条规则）
3. `SemanticAnalysisAgent` + Qwen text-embedding-v4 接入
4. `ContentStrategyAgent` + DeepSeek 流式输出
5. 端到端跑通主流程，验证生成质量

**第二阶段（多模态增强，2周）**
6. `MultimodalAlignAgent` + Qwen3 图片分析
7. `VideoAnalysisAgent` + Celery 异步任务队列
8. `ReportAgent` + Excel 模板设计

**第三阶段（工程化，1周）**
9. SSE 进度推送 + 前端对接
10. Cookie 池管理 + 代理池配置
11. LangSmith 全链路追踪接入
12. Human-in-the-loop 审核节点

### 风险点

- **Cookie 稳定性**：单账号日请求上限约 300~500 次，需准备至少 5 个账号组成 Cookie 池，超限时能自动轮换。
- **RAG 冷启动**：初期知识库不足时检索质量差，建议人工整理至少 500 条垂类规则后再开放用户使用。
- **视频 API 成本**：Qwen3 视频理解每次调用 token 消耗较高，建议设置每日调用上限，超出后降级为仅截帧分析。
- **爆文评分可信度**：`score_viral_potential` 冷启动阶段用规则评分，积累 1000+ 条带互动数据的历史记录后再训练预测模型。
- **平台反爬升级**：小红书签名算法可能更新，需安排人力持续维护，或优先使用 MediaCrawler 等社区维护的工具。

### 可观测性

```python
from langchain.callbacks import LangChainTracer

tracer = LangChainTracer(project_name="viral-agent-prod")

# 每个 Agent 节点结构化日志
logger.info({
    "event": "agent_completed",
    "agent": "semantic_analysis",
    "task_id": state["task_id"],
    "data_source": state["data_source"],
    "duration_ms": elapsed,
    "tokens_used": usage.total_tokens,
})
```

### Human-in-the-Loop 节点

两个关键审核点：

1. **`content_strategy` 前（推荐）**：展示 CrawlerAgent 采集数量、RAG 检索到的规则列表、SemanticAgent 提取的特征，让运营人员确认分析方向再生成策略。适合对质量要求高的场景。

2. **`report` 前（可选）**：对生成的爆文策略人工评分，低于阈值退回重新生成（`interrupt_after=["content_strategy"]`）。

---

## 9. 技术栈汇总

| 模块 | 技术选型 | 说明 |
|---|---|---|
| Agent 框架 | LangGraph | 有状态流程管理 |
| 语言模型 | DeepSeek（综合推理/内容生成） | ContentStrategyAgent 核心 |
| 多模态模型 | Qwen3（图片/视频理解） | MultimodalAlign / VideoAnalysis |
| 向量化模型 | Qwen text-embedding-v4 | 所有文本向量化 |
| 向量数据库 | ChromaDB（本地/Server模式） | 三个 Collection |
| 缓存 | Redis | 爬取记录缓存、RAG结果缓存 |
| 异步任务 | Celery + Redis | 视频分析异步队列 |
| 爬虫工具 | MediaCrawler / DrissionPage | 小红书数据采集 |
| 进度推送 | SSE（Server-Sent Events） | 实时进度通知前端 |
| 报告生成 | openpyxl | Excel 多 Sheet 报告 |
| 可观测性 | LangSmith | 全链路追踪 |
| 定时任务 | APScheduler | 热榜词预热爬取 |
