# 画布 + 导出重构基准文档（以"抗老精华爆文模型—兴长信达.xlsx"为准）

> **文档地位**：本文档是项目 4.3pre 阶段 Canvas 内容重构的**权威设计基准**。
>
> 用户 2026-04-20 明确要求"画布以此模板为准",此后所有 Agent 输出 schema、CanvasModule 字段、导出 Excel 模板、前端渲染,都基于本文档定义。
>
> 原模板信息:7 sheet / 257 张嵌入图片 / 98 MB。原始 spec 在 [`docs/templates/kanglao_spec.md`](docs/templates/kanglao_spec.md),图片在 [`docs/templates/images/`](docs/templates/images/)。

---

## 1. 模板核心洞察(必读)

### 1.1 这不是"分析报告",是"爆文模型矩阵"

传统理解的"小红书分析" = 列爆款笔记 + 写总结。这份模板颠覆了这个思路:

> **真正的价值在于把爆款笔记拆解成"爆文模型 × 6 要素"的二维矩阵,再统计每种模型里每个要素的分布占比,最后用代表图示例化**。

这个矩阵就是**可复用的创作公式**。

### 1.2 爆文模型维度(Y 轴,识别出 4 种)

模板里为"抗老精华"这个品类归纳出了 4 个核心爆文模型:

| 模型代号 | 名称 | 代表特征 |
| --- | --- | --- |
| **①** | 单品推荐 | 纯产品图封面 + 讲效果切入 + 直接带出产品 |
| **①** | 手持单推 | 达人手持产品 + 口播推荐 + 痛点切入 |
| **②** | 干货分享 | 前后对比 / 方法教程 + 干货切入 + 融合使用感受 |
| **③** | 知识科普 | 科普原理 + 痒点/痛点切入 + 干货带出产品 |

(有的行里 ① 和 ② 的编号复用,这是业务分析师的习惯;我们系统里统一重编 M1-M4。)

**关键**:不同品类的"爆文模型"不一样。抗老精华是 4 种,洗发水可能是 5 种,母婴可能是 3 种 — **这是 AI 分析要识别的**。

### 1.2.1 品类可变性(必读,避免把示例模板当通用表头)

本仓库基准 xlsx 文件名虽为「抗老精华…」,其 **Sheet 文案、列名、枚举举例** 均带该品类的叙事与字段选择。换关键词/换品类时:

- **内容风格、左侧「内容方向」枚举、右侧统计表的语义轴**都应随样本与任务语境变化,而不是照搬「抗老精华」字面。
- 模板 Sheet 1 右侧在护肤品语境下常体现为 **「皮肤问题」TopN**;在别的行业可能是 **「使用场景顾虑」「喂养问题」「功效诉求」** 等 — **系统里应抽象为「高频痛点/议程 Top 轴」**,轴的**展示标题**由任务元数据(关键词、行业、或由 Insight 归纳的一行 `stats_axis_label`)驱动,**禁止**在产品与导出层写死「皮肤问题」四字。

### 1.3 要素维度(X 轴,固定 6 个)

不论什么模型,都用同样的 6 要素切片:

| 要素代号 | 名称 | 含义 | 输出示例 |
| --- | --- | --- | --- |
| **A** | 封面 | 封面图的视觉结构 | 纯产品图 / 前后对比 / 好状态颜值照 / 皮肤问题展示 |
| **B** | 封面压字 | 封面上的文字策略 | 干货/经验分享 / 痛点 / 吸睛词 / 猎奇字 |
| **C** | 标题 | 标题文案策略 | 干货分享 / 笔记主题 / 痛点+解决方案 |
| **D** | 内容切入点 | 开篇怎么切入话题 | 干货切入 / 痛点切入 / 痒点切入 / 好状态切入 |
| **E** | 产品引出方式 | 怎么从内容过渡到产品 | 直接带出 / 干货分享引出 / 剧情中引出 / 自用推荐 |
| **F** | 产品植入方式 | 产品在笔记里怎么呈现 | 融合自己使用方法/感受 / 直接讲卖点 / 产品用法/使用年龄 |

### 1.4 统计维度(Z 轴,每个单元格有占比)

每个"模型 × 要素"单元格里给出:
- **枚举分类**(该要素下有哪些典型做法)
- **占比**(比如"纯产品图 28%"/"皮肤展示 14%")
- **代表图示例**(2-5 张真实笔记封面)

---

## 2. Sheet 级别分析

### 2.1 总览

| # | Sheet 名 | 行数 | 列数 | 图片 | 性质 | 每行表示 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **爆文总结** | 96 | 9 | 0 | 统计归纳 | 一个内容方向或**一条右侧统计轴上的议程/痛点**的统计条目(本模板示例轴为「皮肤问题」) |
| 2 | **爆文总结详情2** | 170 | 8 | 36 | **核心矩阵** | 爆文模型 × 6 要素的分类占比 + 示例图 |
| 3 | **数据源总** | 97 | 20 | 95 | 原始样本 | 一条抗老精华品类下的爆款笔记 |
| 4 | **竞品爆文** | 36 | 27 | 35 | 竞品样本 | 一条特定竞品的爆款笔记(含 SEO) |
| 5 | **【品类】抗老精华互动 top** | 50 | 27 | 48 | 互动 Top 榜 | 按互动量排序的 top 50 |
| 6 | **【精华】小红书前 10 屏爆文** | 11 | 26 | 10 | SERP 头部 | 小红书搜索"精华"前 10 屏爆文 |
| 7 | **草稿** | 88 | 9 | 33 | 工作区 | 和 Sheet 2 同结构,分析师手工整理用 |

**关键**:
- **Sheet 3/4/5/6 是"同字段不同来源"**(都带 6 要素标注和封面截图)
- **Sheet 1/2 是 Sheet 3/4/5/6 的"归纳结果"**(统计 + 矩阵)
- **Sheet 7 是模板**(让用户针对新品类套用)

### 2.2 Sheet 3 "数据源总" — 原始样本标准字段(20 列)

> 这是最重要的底层数据 schema。所有 Agent 采集结果最终要对齐成这套字段。

```
A  来源            "【品类】抗老精华 TOP"
B  达人昵称        "憨桃人儿"
C  类型            "个护分享;口播单品推荐"
D  品牌            "IPSA茵芙莎"
E  笔记链接        xhscdn url
F  互动量          公式 =G+H+I
G  点赞
H  收藏
I  评论
J  内容方向        "口播单推" / "剧情" / "知识科普" / "日常vlog" ...
K  封面截图        【图片】锚点在 K 列对应行
L  标题            "4⃣️招秒变上镜美女！✨网图本人独家㊙️籍！"
M  内容关键词/具体痛点   "松垮暗沉"
—— 以下是 6 要素标注 ——
N  封面            "学习前后对比图"
O  封面压字        "干货/经验分享"
P  标题            "干货/经验分享"       ← 注意这是"标题的分类",不是标题文本本身
Q  内容切入点      "好皮肤的重要性（痒点）"
R  产品引出方式    "直接带出"
S  产品植入方式(单品)   "融合自己使用方法/感受讲卖点"
T  评论区关键词    "泪沟法令纹"          ← 新品类分析额外补充的字段
```

**值得注意的细节**:
- **P 列(标题)和 L 列(标题)是两个字段**:L 是笔记真实标题文案,P 是这个标题归到哪一类标签("干货分享"/"痒点切入"等)
- **F 列是公式**`=G+H+I`,表示"互动量"是点赞+收藏+评论三者之和
- **T 列"评论区关键词"**来自评论区挖掘,不是笔记本身的关键词

### 2.3 Sheet 4 "竞品爆文" — 比数据源总多 2 个字段

同 20 列 + 2 个 SEO 字段:

```
T  笔记涵盖热搜词 Top10    "法令纹,胶原,抗老,精华测评,鱼尾纹,松露,垮脸,细纹,提亮,暗沉"
U  笔记相关评论热词 Top10  "购物车,OLAY,PMPM,下单,不好,不拉几,东西,买不起,产品,作用"
```

**这是竞品分析的高价值数据** — 告诉我们:
- 用户在搜什么词进入竞品笔记(需求侧)
- 用户在评论区最常说什么(传播侧)

### 2.4 Sheet 5 "【品类】互动 top" — 多了一列发布时间

```
B  笔记发布时间     2023-07-31T00:00:00     ← 日期类型(有排序/时效性用)
```

**少了** 列 M "内容关键词/具体痛点" 和 列 T "评论区关键词"。

### 2.5 Sheet 6 "【精华】小红书前 10 屏爆文"

字段和 Sheet 5 类似,但更精简(没有品牌、发布时间等)。**含义**是"小红书搜索'精华'两个字时,结果页前 10 屏出现的爆款笔记"—— 这本质上是 SERP 头部洞察。

### 2.6 Sheet 2 "爆文总结详情2" — 矩阵核心

这个 sheet 的结构最特殊。它不是"一行一条数据",而是**树形矩阵**:

```
爆文模型①单品推荐(锚点 B12:C12 合并)
  ├─ A 封面         (B13:B17 合并) → 5 种封面类型 × 占比 × 示例图
  ├─ B 封面压字     (B18:B22 合并) → 5 种压字类型 × 占比 × 示例图
  ├─ C 标题         (B23:B27 合并) → 5 种标题类型
  ├─ D 内容切入点   (B28:B30 合并) → 3 种切入点
  ├─ E 产品引出方式 (B31:B34 合并) → 4 种引出方式
  └─ F 产品植入方式 (B35:B37 合并) → 3 种植入方式

爆文模型①手持单推(锚点 B39:C39 合并)
  ├─ A 封面         (B40:B42 合并)
  ├─ B 封面压字     (B43:B47 合并)
  ... 同样 6 要素

爆文模型②干货分享(B64:C64)
爆文模型③知识科普(B80:C80)
```

每个单元格里的图片(36 张)就是该要素类型下的**真实笔记封面示例**(从 Sheet 3 数据源总里挑选最典型的几张)。

### 2.7 Sheet 1 "爆文总结" — 双维统计

```
左侧表(B-E 列):                     右侧表(H-I 列):
内容方向   数量  占比       均互动     皮肤问题       出现次数
干货分享    25   25/95=26%  19399      法令纹          18
手持单推    24   24/95=25%  15511      垮脸            14
口播单推    15   15/95=16%  39168  ← 最高互动!        暗沉            12
知识科普     9   9/95=9%    15111      敏感泛红        12
日常vlog     8   ...        19623 *不做  毛孔粗大        6
剧情         6   ...        29714 *不做  干纹细纹         5
...
```

- F 列有**业务批注**(带 `*不做` 的行说明"闭环数据验证,此方向后链路效果差")
- 右侧表在本模板中是 **「皮肤问题」× 出现次数** 的 TopN;在通用产品里应理解为 **「高频痛点/议程」TopN**,**表头文案随品类与任务参数化**(见 §1.2.1),数据仍来自样本侧可统计字段(如 M 列 `内容关键词/具体痛点`、评论区关键词等聚合)。

---

## 3. 新 Canvas 模块设计(基于模板反推)

### 3.1 模块对应关系

| 新模块 ID | 模块名 | 对应 Sheet | 展示形式 | 用户可反馈? |
| --- | --- | --- | --- | --- |
| `mod-overview-stats` | 统计总览 | Sheet 1 | 双表 + 饼图/柱状图 | ❌ 只读 |
| `mod-viral-model-matrix` | **爆文模型矩阵** | Sheet 2 | **树形矩阵**(手风琴式),每要素含占比 + 示例图 | ✅ 每个枚举项可踩/编 |
| `mod-source-samples` | 样本明细:品类 TOP | Sheet 3 | 表格 + 封面缩略图 | ❌ 只读 |
| `mod-competitor-samples` | 样本明细:竞品爆文 | Sheet 4 | 表格 + 封面缩略图 + SEO 标签云 | ❌ 只读 |
| `mod-top-interaction-samples` | 样本明细:互动 TOP | Sheet 5 | 表格 + 发布时间时序 | ❌ 只读 |
| `mod-serp-top-samples` | 样本明细:SERP 前 10 屏 | Sheet 6 | 卡片流 | ❌ 只读 |
| `mod-seo-insights` | SEO 关键词洞察 | Sheet 4 的 T/U 字段聚合 | 词云 + 频次柱状图 | ❌ 只读 |
| `mod-pain-points` | **高频痛点/议程 Top**(展示标题参数化;抗老模板示例为「皮肤问题」) | Sheet 1 右侧表(轴名随任务) | 竖向排行榜 | ❌ 只读 |
| `mod-draft-workbench` | 创作草稿区 | Sheet 7 | 可编辑矩阵 | ✅ 全部可编 |

### 3.2 删除/合并的原模块

| 原模块 | 动作 | 去向 |
| --- | --- | --- |
| `mod-title-strategy` | ❌ 删 | 融合到 `mod-viral-model-matrix` 的 **C 要素** |
| `mod-product-strategy` | ❌ 删 | 融合到 `mod-viral-model-matrix` 的 **R/S 要素** |
| `mod-cover-strategy` | ❌ 删 | 融合到 `mod-viral-model-matrix` 的 **A/B 要素** |
| `mod-structure-strategy` | ❌ 删 | 融合到 `mod-viral-model-matrix` 的 **D 要素** |
| `mod-insight-industry` | ❌ 删 | 模板中无此粒度,被 `mod-overview-stats` 取代 |
| `mod-insight-competitor` | ✏️ 改名 | → `mod-competitor-samples` + `mod-seo-insights` |
| `mod-insight-brand` | ❌ 删 | 模板中不分"本品" |
| `mod-insight-knowledge` | ❌ 删 | 模板中无 RAG 业务规则展示 |
| `mod-image-analysis` | ❌ 删 | 融合到 `mod-viral-model-matrix` 的 **A/B 要素** |
| `mod-video-analysis` | ❌ 删 | 视频只是 J 列"内容方向"的一个值,不独立展示 |
| `mod-crawler-sample` | ✏️ 拆分 | → `mod-source-samples` + `mod-top-interaction-samples` + `mod-serp-top-samples` |

### 3.3 新 Canvas 模块数量变化

```
原:11 个模块
新:9 个模块 — 且结构性增强(矩阵式 + 多源样本分类)
```

---

## 4. Agent 输出 Schema 重设计

### 4.1 CrawlerAgent 的产出结构

**目前**:`crawler_output = { samples_by_dimension, notes_image, notes_video, ... }`

**4.3pre.2 起实际落地为**(三维采集 + 四源视图映射,**扁平 `List[note]`**):

```json
{
  "source": "live",
  "cache_source": "L3",
  "sample_count": 95,
  "keywords": ["抗老精华"],
  "sources": {
    "category_top":    [ {note 对象, sources_hit: ["category_top", ...]}, ... ],
    "competitor":      [ ... ],
    "top_interaction": [ ... ],
    "serp_top":        [ ... ]
  },
  "all_notes": [ ... 合并去重后的所有 note, 每条带 sources_hit 列表 ... ]
}
```

> **spec 校正(v1.2.2)**:`sources` 的每个源值为**扁平 `List[dict]`**,不是 spec v1.0 里写的 `{"query": ..., "notes": [...]}` 嵌套对象。下游 Agent(`InsightAgent`、`CanvasRenderAgent`)的消费代码同时兼容两种形态,但以扁平 list 为当前实际落地。
>
> SERP 前 10 屏的 `serp_top` 不单独调 API,而是从 `all_notes` 按互动降序取前 `CRAWLER_SERP_TOP_N`(默认 10)条;`top_interaction` 同理按前 `CRAWLER_TOP_INTERACTION_N`(默认 50)条。

### 4.2 新增 `ViralModelAgent`(替换原 StrategyAgent)

这是**最核心的改造**。原 StrategyAgent 产出的 4 个子策略(title/product/cover/structure)要重构为**一个矩阵输出**:

```json
{
  "viral_models": [
    {
      "model_id": "M1",
      "name": "单品推荐",
      "coverage": 0.25,                   ← 25% 的爆款走这个模型
      "avg_interaction": 15511,
      "elements": {
        "A_cover": [
          {"type": "纯产品图", "ratio": 0.28, "examples": [{note_id, title, cover_url}, ...]},
          {"type": "皮肤展示", "ratio": 0.14, "examples": [...]},
          ...
        ],
        "B_cover_text": [...],
        "C_title": [...],
        "D_opening": [...],
        "E_product_intro": [...],
        "F_product_placement": [...]
      }
    },
    {"model_id": "M2", "name": "手持单推", "coverage": 0.25, ...},
    {"model_id": "M3", "name": "干货分享", "coverage": 0.26, ...},
    {"model_id": "M4", "name": "知识科普", "coverage": 0.09, ...}
  ],
  "unused_directions": [                  ← 模板的 F 列"不做"标注
    {"direction": "日常vlog", "ratio": 0.08, "avg_interaction": 19623, "reason": "闭环验证后链路数据差"},
    {"direction": "剧情",   "ratio": 0.06, "avg_interaction": 29714, "reason": "同上"}
  ]
}
```

### 4.3 `InsightAgent` 简化

原三维并行(行业/竞品/本品)改为:
- **内容方向统计**(对应 Sheet 1 左侧)
- **痛点 Top**(对应 Sheet 1 右侧)
- **SEO 关键词洞察**(聚合 Sheet 4 的 T/U 字段)

### 4.4 **VideoAnalysisAgent 是主力产出方**(2026-04-20 用户强调的关键决策)

> **反转**:我最初判断 VideoAnalysisAgent 可下线,**错了**。
>
> 用户明确:"表格里的绝大部分内容都是通过分析视频内容得来的,视频分析可以主力军"。
>
> 也就是说,**模板中 N/O/P/Q/R/S 六要素的大部分字段,要靠视觉大模型分析视频内容直接产出**,不是人工打标或文本 Agent 推导。

#### 新 VideoAnalysisAgent 输出 schema(对齐模板字段)

每条视频笔记分析后,产出**直接对齐模板 Sheet 3 N-S 列的标注**:

```json
{
  "note_id": "xxx",
  "source_type": "video",
  "content_direction": "口播单推",               // 对应 J 列
  "cover_analysis": {
    "type": "学习前后对比图",                     // 对应 N 列
    "text_strategy": "干货/经验分享",            // 对应 O 列("封面压字"分类)
    "text_on_cover": "4招秒变上镜美女"          // 实际封面字(原始文本)
  },
  "title_analysis": {
    "type": "干货/经验分享",                      // 对应 P 列("标题"分类)
    "text": "4招秒变上镜美女！网图本人独家秘籍",  // 实际标题
    "hook_elements": ["数字", "效果承诺"]        // 标题里的钩子元素
  },
  "content_analysis": {
    "opening_type": "好皮肤的重要性（痒点）",     // 对应 Q 列
    "opening_text_sample": "前3秒的具体台词/画面描述",
    "product_intro_timing_sec": 12.5,            // 产品首次出现秒数
    "product_intro_type": "直接带出",            // 对应 R 列
    "product_placement_type": "融合自己使用方法/感受讲卖点"  // 对应 S 列
  },
  "business_analysis": {
    "pain_points": ["松垮", "暗沉", "法令纹"],    // M 列细粒度分解
    "effect_claims": ["14天见效", "嘭弹脸"],     // 效果承诺
    "credibility_tokens": ["亲测", "低成本"],    // 可信度标签
    "numbers_mentioned": ["14天", "3步", "32岁"], // 数字钩子
    "brand_mentioned": "IPSA茵芙莎"              // D 列
  }
}
```

#### 为什么视频分析必须主力化

1. **模板里 ~70% 的数据字段需要动态内容理解**(情绪切入点 / 产品引出时机 / 植入方式),静态封面图看不出来
2. **通义千问 qwen3-vl 支持 video_url 直接分析**(4.2 已经跑通 URL 直传,无需下载)
3. **一次视频调用可输出所有 6 要素**,比"封面 Agent + 文案 Agent + ASR + 归纳 Agent"链路简单得多

#### 4.2 阶段做的异步支路基础设施仍然复用

- VideoAnalysisAgent 的两段式 run() 继续用
- 修改的是**后台协程里构造的 prompt + 输出解析**
- URL 直传 + asyncio 并发的基础设施不变

### 4.5 `ImageAnalysisAgent` 改为图文笔记的 6 要素标注器

视频 Agent 负责视频笔记(J 列含"口播/剧情/知识科普/vlog"等),Image Agent 负责图文笔记(J 列"干货经验分享"的大部分):

- **输出 schema 与 VideoAnalysisAgent 对齐**(N/O/P/Q 要素都要)
- E 列产品引出方式、F 列产品植入方式视频特有字段(timing)可为 null
- `pain_points` / `effect_claims` 等业务分析从**文案 + 封面 OCR**推断

**两个 Agent 共享同一套 taxonomy + 同一套输出字段**,ViralModelAgent 消费时不用区分来源。

---

## 5. 数据模型字段标准(权威)

### 5.1 Note 标准字段(20 基础 + 2 扩展)

```python
@dataclass
class ViralNote:
    # 基础元数据(10 字段)
    source: str              # "【品类】抗老精华 TOP" | "【竞品】PMPM" | "【品类】精华前十屏爆文"
    creator_nickname: str    # 达人昵称
    creator_type: str        # "个护分享;口播单品推荐"
    brand: str               # "IPSA茵芙莎"
    note_url: str            # 小红书链接
    interaction_total: int   # 互动总量 = 点赞+收藏+评论
    likes: int
    collects: int
    comments: int
    published_at: Optional[str]  # ISO 日期,可选(Sheet 5 才有)

    # 内容分类(3 字段)
    content_direction: str   # "口播单推" | "剧情" | "知识科普" | "日常vlog" | ...
    cover_url: str           # 封面截图 url
    title: str               # 笔记真实标题文案

    # 内容标注(7 字段,AI 产出)
    pain_keywords: str       # "松垮暗沉" / "法令纹（痛点）/仅用14天（见效快数字）"
    cover_type: str          # N: "学习前后对比图" / "纯产品图"
    cover_text_type: str     # O: "干货/经验分享" / "痛点"
    title_type: str          # P: "干货/经验分享" (分类标签)
    opening_type: str        # Q: "好皮肤的重要性（痒点）"
    product_intro_type: str  # R: "直接带出"
    product_placement_type: str  # S: "融合自己使用方法/感受讲卖点"

    # 扩展(竞品 sheet 才有)
    comment_keywords: Optional[str] = None        # T: 评论区关键词
    seo_top10: Optional[List[str]] = None         # 笔记涵盖热搜词 Top10(竞品)
    comment_hotwords_top10: Optional[List[str]] = None  # 评论热词 Top10(竞品)
```

### 5.3 paragraph_id 规则(4.3pre.3 确立)

画布里凡"用户可反馈 / 可重生 / 可编辑"的粒度,都要配稳定的 `paragraph_id`,作为
4.3 段落反馈闭环与 4.4 对话中枢中 `refine_canvas` 子能力的定位锚点。规则表:

| 模块 | paragraph_id 格式 | 示例 | 粒度 |
| --- | --- | --- | --- |
| `mod-viral-model-matrix` | `M{i}` | `M1` `M2` | 模型级(整模型踩/重生) |
| `mod-viral-model-matrix` | `M{i}-{ELEMENT_CODE}-C{j}` | `M1-A_cover-C1` | 分类级(单要素单分类编辑) |
| `mod-pain-points` | `P{i}` | `P1` | 痛点条目级 |
| `mod-seo-insights` | `S-core-{i}` / `S-long-{i}` | `S-core-3` | SEO 条目级 |
| 样本类模块(4 个) | `{module_id}-note-{note_id}` | `mod-source-samples-note-abc123` | 单条笔记级 |
| `mod-overview-stats` | 无 | — | 整块,不支持细粒度反馈 |
| `mod-draft-workbench` | 无 | — | 用户私有工作台 |

**实施位置**:
- `mod-viral-model-matrix` 两级 paragraph_id 在 [`ViralModelAgent._compute_element_stats`](../backend/app/application/agents/viral_model_agent.py) + `ViralModel` 构造时写入
- `mod-pain-points` / `mod-seo-insights` / 样本类模块 paragraph_id 在 [`CanvasRenderAgent`](../backend/app/application/agents/canvas_render_agent.py) 的对应 `_build_*` 构造函数里生成

**唯一性保障**:全画布 paragraph_id 必须**全局唯一**,测试 `test_paragraph_ids_are_globally_unique` 深度遍历 `canvas.to_dict()` 收集所有 `paragraph_id`,断言无重复。

### 5.2 6 要素枚举值(从模板归纳,初版,AI 需能扩展)

```python
COVER_TYPES = [                    # N 列取值候选
    "纯产品图", "前后对比", "好状态颜值照", "皮肤问题展示",
    "达人手持产品", "场景摆拍", "使用过程拼图", "其他(制造真实性)",
]

COVER_TEXT_TYPES = [               # O 列
    "干货/经验分享", "痛点", "效果", "吸睛词", "猎奇字",
    "数字", "人群身份", "其他",
]

OPENING_TYPES = [                  # Q 列
    "干货切入", "痛点切入", "痒点切入", "好状态(痒点切入)",
    "自身切入(年龄/经历)", "效果切入", "场景切入",
]

PRODUCT_INTRO_TYPES = [            # R 列
    "直接带出", "融入到干货/经验分享中", "剧情中自然直入",
    "自用推荐", "亲测好用狂推", "对比引出", "痛点过渡",
]

PRODUCT_PLACEMENT_TYPES = [        # S 列
    "融合自己使用方法/感受讲卖点", "直接讲卖点", "产品用法/使用年龄",
    "效果+质地", "剧情中展示",
]
```

(这些是**初始枚举**,AI 分析时可识别出新类型并扩展。保存在一个 `viral_taxonomy.json` 里,可被领域专家编辑。)

---

## 6. 导出 Excel 映射(新 Agent 输出 → 模板)

| Excel Sheet | 来源 Agent 输出 | 映射规则 |
| --- | --- | --- |
| **爆文总结** | `viral_models[*].coverage` + 内容方向统计 + 痛点 Top | Sheet 1 双表 |
| **爆文总结详情2** | `viral_models` | Sheet 2 树形矩阵,逐要素输出 |
| **数据源总** | `crawler_output.sources.category_top.notes` | 每条 note 一行;K 列嵌入 `cover_url` 下载的图 |
| **竞品爆文** | `crawler_output.sources.competitor.notes` | 同上 + T/U 列写 SEO 字段 |
| **【品类】抗老精华互动 top** | `crawler_output.sources.top_interaction.notes` | 同上,按 interaction 降序 |
| **【精华】小红书前 10 屏爆文** | `crawler_output.sources.serp_top.notes` | 每条一行 |
| **草稿** | 空白模板复制 | 初始化为 Sheet 2 的空壳 |

**导出脚本位置**: `backend/app/services/canvas_export/excel_exporter.py`(新建)

关键难点:
- **封面图下载嵌入**:导出时要把 `cover_url` 下载成二进制,用 openpyxl 的 `XLImage` 嵌入 K 列
- **合并单元格 + 树形矩阵**:Sheet 2 的爆文模型矩阵要程序化生成 `merge_cells` 调用
- **分类占比计算**:前端展示时可以现算,导出时要从 `viral_models.elements[*].ratio` 直接取

---

## 7. 实施路径(子阶段)

### 4.3pre.1 数据模型重构(2-3 天)
- [ ] 定义 `ViralNote` dataclass(`backend/app/domain/viral_note.py`)
- [ ] 定义 `ViralModel` / `ViralElement` dataclass
- [ ] 定义 `viral_taxonomy.json`(6 要素初始枚举)
- [ ] 更新 `CanvasModule.content` 的 JSON schema 文档

### 4.3pre.2 Agent 改造(3-5 天)
- [ ] CrawlerAgent 改为四路采集(category_top/competitor/top_interaction/serp_top)
- [ ] ImageAnalysisAgent 改为 6 要素标注器
- [ ] 新建 ViralModelAgent(替换 StrategyAgent)
- [ ] InsightAgent 简化
- [ ] VideoAnalysisAgent 下线(归入内容方向)

### 4.3pre.3 CanvasRenderAgent 重构(1-2 天)
- [ ] `_build_modules` 按新 9 模块构建
- [ ] 每个模块 content 字段按本文档 schema
- [ ] **给每个可反馈条目生成稳定 paragraph_id**(为 4.3 反馈闭环打基础)

### 4.3pre.4 前端 canvas-adapter.ts 重写(2-3 天)
- [ ] 9 个模块的专用 sections 构建
- [ ] 爆文模型矩阵:手风琴式树形展示
- [ ] 样本明细:表格 + 封面缩略图
- [ ] SEO 洞察:词云(或标签云)

### 4.3pre.5 Excel 导出实现(2-3 天)
- [ ] `canvas_export/excel_exporter.py` 新建
- [ ] 7 个 sheet 的模板化生成(openpyxl)
- [ ] 封面图下载 + 嵌入(异步并发)
- [ ] 合并单元格 + 树形矩阵程序化生成

### 4.3pre.6 联调 + 回归(1-2 天)
- [ ] 单测覆盖各 Agent 新 schema
- [ ] 端到端:提交"抗老精华"任务 → 导出 Excel → 和原模板肉眼对比
- [ ] 回归现有 158 条测试

**预估总工作量**: 11-18 天(约 2.5 周),比原 4.3 规划大,但这是**一次性对齐到产品终态**,后续 4.3/4.4 基础稳固。

---

## 8. 关键决策清单(2026-04-20 用户已回答)

### ✅ Q1: 爆文模型数量 — **AI 动态识别 2-6 个**

- LLM 对爆款样本做聚类,品类自适应
- 抗老 = 4 个,洗发水可能是 5 个,母婴可能是 3 个
- 硬上限 6 个避免过度拆分

### ✅ Q2: 6 要素枚举 — **软编码 taxonomy.json + AI 可扩展**

- `backend/app/config/viral_taxonomy.json` 初始枚举(从抗老模板归纳)
- 管理员可在设置页编辑
- AI 分析时识别到新类型 → 写入一个 pending 队列,管理员审核后入 taxonomy

### ✅ Q3: 用户编辑占比的语义 — **偏好提示(bias_hint)**

- 用户改"M1/A 纯产品图 28% → 40%" → 下次重生时 AI 按用户期望倾向
- 实现:feedback_map 里记录 `{element_id, user_adjusted_ratio, timestamp}`
- 只在"重新生成整个模块"时生效,不立即触发任何后台动作

### ✅ Q4: SEO 关键词洞察 — **只对竞品 sheet 做**

- 和模板对齐:只有"竞品爆文"sheet 带 T/U 列(笔记热搜词 + 评论热词)
- 后续若需要扩展到其他 sheet,作为 4.3pre 完成后的独立增强

### ✅ Q5: **视频分析是主力产出方**(重大反转!详见第 4.4 章)

- VideoAnalysisAgent **不下线**,而是改造成**主力**
- 输出 schema 重写,直接对齐模板的 N/O/P/Q/R/S 六要素 + J 列内容方向 + M 列痛点
- 4.2 已建好的 asyncio 异步支路 + URL 直传基础设施**完整复用**
- ImageAnalysisAgent 负责图文笔记,**输出同 schema**,ViralModelAgent 消费时不用区分

---

## 9. 参考文件

- **原模板**: [`docs/templates/抗老精华爆文模型—兴长信达.xlsx`](docs/templates/) (98 MB, 7 sheet, 257 图片)
- **原始结构 spec**: [`docs/templates/kanglao_spec.md`](docs/templates/kanglao_spec.md) (1783 行)
- **抽出的图片**: [`docs/templates/images/`](docs/templates/images/) (257 张)
- **抽取工具**: [`scripts/inspect_excel_template.py`](scripts/inspect_excel_template.py)

---

*Generated: 2026-04-20*
*Authority: 用户确认"项目以此模板为准"*
*Version: **1.2.2** (4.3pre.3 CanvasRenderAgent 重写落地,记录最终契约)*

## 10. 版本变更记录

### v1.2.2 (2026-04-21 4.3pre.3 落地)

**CanvasRenderAgent 按本 spec 整体重写 + 画布契约硬切换**,旧 11 模块全部下线,
新 8 模块(+ 1 个静态草稿 = 9 个)一次对齐。主要落地与 spec 的差异说明:

- **§3.1 模块数量微调**:spec 给出 9 模块(含 `mod-insight-knowledge` 业务约束),
  实施时按用户决策 #1A **删除 `mod-insight-knowledge`**(RAG 输出是 LLM 硬约束,
  不作为面向用户的画布内容)。最终新模块数 = **8 AI + 1 静态草稿 = 9 个**。
  `RAGAgent.provides = []`,但 `rag_output` 分区保留给 4.4 对话中枢中的 Knowledge QA / refine_canvas 路由消费。
- **§4.1 CrawlerAgent 产出结构**:校正为扁平 `List[dict]`(实际落地),
  同时记录 spec 原写法与代码实际形态的兼容策略。
- **§4.3 InsightAgent 新 schema**:彻底清理旧 industry/competitor/brand 三段式适配,
  `semantic_output` 直接存储 `content_direction / pain_points_top / seo_aggregation / stats_axis_label`,
  其中 `pain_points_top` 和 `seo_aggregation.core_keywords/long_tail` 均为 `[{keyword, count}]` 结构。
- **§5.3 paragraph_id 规则表**新增:全画布 paragraph_id 格式与粒度统一规范。
- **StrategyAgent 彻底删除**:包括 `backend/app/application/agents/strategy_agent.py`
  以及 4 个 `strategy_*.md` prompt。同时清理 5 个 4.3pre.2 已弃用的旧 prompt
  (`insight_industry.md` / `insight_competitor.md` / `insight_brand.md`
  / `image_analysis.md` / `video_analysis.md`),prompts 目录从 13 → 7 个现役。
- **测试基线**:179 → 188 passed(改 7 个旧测试 + 新增 1 个 10 用例的契约测试文件)。

**决策 #1A/#2A/#3A/#4A/#5B 全部落地**:
- #1A: 删 `mod-insight-knowledge`
- #2A: `stats_axis_label` 默认 "高频痛点 / 议程",可由 LLM 或任务元数据覆盖
- #3A: `mod-draft-workbench` 初始为空白骨架(6 字段)
- #4A: 清理 InsightAgent 旧 3 段 schema 适配
- #5B: `mod-viral-model-matrix` 双级 paragraph_id(`M{i}` + `M{i}-{CODE}-C{j}`)

**未完成/下一步**:
- 4.3pre.4 前端 canvas-adapter.ts 暂未适配 8 新模块(走 fallback 展示 JSON,不 crash)
- 4.3pre.5 Excel 导出 `canvas_export/excel_exporter.py` 仍待实现(契约已稳定)

### v1.2.1 (2026-04-21 文档勘误)
- **品类可变性**:明确 Sheet 1 右侧「皮肤问题」为抗老模板示例轴,非全品类固定表头;`mod-pain-points` 语义为「高频痛点/议程 Top」,展示标题应随任务/行业参数化(§1.2.1、§2.7、§3.1)。

### v1.2 (2026-04-16 4.3pre.2 实施记录)
**实际落地 vs spec v1.1 差异**:

- **CrawlerAgent**: spec 提"四路并行采集",实施时采用**三维采集 + 四源视图映射**(零 API 调用增加,最小侵入)
  - industry → category_top, competitor → competitor, brand → top_interaction
  - serp_top 不单独采集,从 all_notes 按互动降序前 10 取
  - 每条 note 携 sources_hit: List[str] 标识所属源
- **VideoAnalysisAgent**: 完全按 spec v1.1 落地(同步并发 + 6 要素标注 + 共享 multimodal_output.annotations)
- **ImageAnalysisAgent**: 完全按 spec 落地,深 merge 与 VideoAgent 共享 dict
- **ViralModelAgent**: 完全按 spec 落地,混合聚类(direction 粗分 + LLM 起名 + coverage<5% 阈值)
- **InsightAgent**: spec 提"删除三维洞察",实际**保留 3 module ID 接口兼容 canvas_render**(内部一次 LLM 调用,适配回旧 schema)。这是**spec 简化为实际 0 破坏**的妥协决策。
- **Orchestrator/LangGraphEngine**: 拓扑完全按 spec v1.1 (Image‖Video → ViralModel → Insight‖RAG)
- **测试**: 158 → 179 passed,删 10 改 6 加 16

**未来清理项**(4.3pre.3 及之后处理):
- canvas_render_agent 当前还在引用 strategy_output(空 dict),4 个旧策略 module 内容空白
- 旧 prompts (strategy_*.md / insight_industry/competitor/brand.md / video_analysis.md / image_analysis.md) 文件保留,需要 4.3pre.3 完成后批量清理

### v1.1 (2026-04-20 当日修订)
- **Q5 反转**: VideoAnalysisAgent 从"可下线候选"反转为**主力 Agent**
- **4.4 章节重写**: 详细定义 VideoAnalysisAgent 的新输出 schema(对齐模板 N-S 列 + J/M 列)
- **4.5 章节新增**: ImageAnalysisAgent 作为图文笔记版的"同 schema 6 要素标注器"
- Q1-Q5 确定: AI 动态 2-6 模型 / 软编码 taxonomy / bias_hint 编辑语义 / 竞品限定 SEO

### v1.0 (2026-04-20 初版)
- 首次完整分析"抗老精华爆文模型—兴长信达.xlsx"模板
- 定义 9 新 Canvas 模块 / 7 Sheet 分层 / 爆文模型 × 6 要素矩阵
- 6 子阶段工作量估算
