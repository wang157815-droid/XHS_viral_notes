/**
 * 画布渲染模型(4.3pre.4 新契约)
 *
 * 对齐后端 8 新模块:
 *   mod-overview-stats / mod-viral-model-matrix /
 *   mod-competitor-samples / mod-top-interaction-samples / mod-serp-top-samples /
 *   mod-seo-insights / mod-pain-points / mod-draft-workbench
 *
 * ModuleSection union 在 4.3pre.4 新增:
 *   - ViralMatrixSection: 爆文模型矩阵专用手风琴渲染
 *   - DataTableCell 扩展 kind:"cover" 支持封面缩略图列
 */

export type BadgeColor = "red" | "green" | "amber" | "gray";

export interface PrototypeBadge {
  text: string;
  color: BadgeColor;
}

export interface PrototypeIcon {
  character: string;
  bg: string;
  color: string;
}

export type DimensionState = "focus" | "on" | "off";

export interface PrototypeDimension {
  id: string;
  label: string;
  state: DimensionState;
}

export type ModuleActionId =
  | "regenerate"
  | "regenerate_cascade"
  | "delete"
  | "restore"
  | "expand_all";

export interface PrototypeAction {
  id: ModuleActionId;
  label: string;
}

export type ParagraphKind = "default" | "strong-lead";

export interface EditableParagraph {
  id: string;
  html: string;
}

export interface EditableGroup {
  id: string;
  kind: "editable-group";
  items: EditableParagraph[];
}

export interface StaticParagraph {
  id: string;
  kind: "static";
  html: string;
  extraClass?: string;
}

export interface TagRowSection {
  id: string;
  kind: "tag-row";
  label: string;
  tags: string[];
}

export interface CaseCardSection {
  id: string;
  kind: "case";
  title: string;
  meta: string[];
  description: string;
  editable?: boolean;
}

// 4.3pre.4 扩展:DataTableCell 支持封面缩略图 cell
export interface DataTableCell {
  text?: string;
  tagColor?: "red" | "green" | "amber";
  kind?: "text" | "cover";
  coverUrl?: string;
  noteUrl?: string;
  /** 行级段落锚点（痛点表 / SEO 表 / 样本表等） */
  paragraphId?: string;
}

export interface DataTableSection {
  id: string;
  kind: "data-table";
  headers: string[];
  rows: DataTableCell[][];
  disclaimer?: string;
}

export interface ChecklistSection {
  id: string;
  kind: "checklist";
  items: string[];
}

// 4.3pre.4 新增:爆文模型矩阵专用 section(mod-viral-model-matrix 专用)
export interface ViralMatrixExample {
  note_id: string;
  title: string;
  cover_url: string;
  likes?: number;
}

export interface ViralMatrixElementCategory {
  type: string;
  ratio: number; // 0-1
  count: number;
  examples: ViralMatrixExample[];
  paragraph_id?: string;
}

export interface ViralMatrixModel {
  model_id: string;
  name: string;
  description: string;
  coverage: number; // 0-1
  avg_interaction: number;
  elements: Record<string, ViralMatrixElementCategory[]>;
  paragraph_id?: string;
}

export interface ViralMatrixUnusedDirection {
  direction: string;
  ratio: number;
  avg_interaction: number;
  reason: string;
}

export interface ViralMatrixSection {
  id: string;
  kind: "viral-matrix";
  statsAxisLabel: string;
  totalSampleCount: number;
  taxonomyVersion: string;
  models: ViralMatrixModel[];
  unusedDirections: ViralMatrixUnusedDirection[];
}

export type ModuleSection =
  | EditableGroup
  | StaticParagraph
  | TagRowSection
  | CaseCardSection
  | DataTableSection
  | ChecklistSection
  | ViralMatrixSection;

export interface PrototypeModule {
  moduleId: string;
  layer: 1 | 2 | 3;
  title: string;
  icon: PrototypeIcon;
  badges: PrototypeBadge[];
  version: number;
  highlighted?: boolean;
  defaultExpanded?: boolean;
  summary?: string;
  /** paragraph_id → 持久化反馈动作（来自 content.feedback_map） */
  paragraphFeedback?: Record<string, string>;
  actions: PrototypeAction[];
  sections: ModuleSection[];
}

export interface PrototypeCanvasModel {
  title: string;
  meta: string;
  themeBadge: string;
  dimensions: PrototypeDimension[];
  modules: PrototypeModule[];
}

export interface AgentStep {
  id: string;
  label: string;
  status: "done" | "active" | "pending";
}

/**
 * 与后端 AgentOrchestrator 4.3pre.3 新 8 Agent 对齐。
 * id = backend agent_id,用于按 `agent_progress` 事件点亮进度条。
 *
 * 新拓扑: InputParser → Crawler → Image ‖ Video → ViralModel → Insight ‖ RAG → CanvasRender
 */
export const ORCHESTRATOR_STEPS: Array<Omit<AgentStep, "status">> = [
  { id: "InputParserAgent",   label: "输入解析 · 关键词 / 维度 / 调整需求" },
  { id: "CrawlerAgent",       label: "数据采集 · 行业池 + 竞品 / 互动 TOP / SERP" },
  { id: "ImageAnalysisAgent", label: "图文 6 要素标注" },
  { id: "VideoAnalysisAgent", label: "视频 6 要素标注(同步并发)" },
  { id: "ViralModelAgent",    label: "爆文模型矩阵 · 混合聚类" },
  { id: "InsightAgent",       label: "洞察 · 方向分布 + 痛点 + SEO" },
  { id: "RAGAgent",           label: "业务约束检索" },
  { id: "CanvasRenderAgent",  label: "画布渲染 · 8 模块" },
];

/** 原型演示态(未发起任务时)的步骤列表。 */
export const DEFAULT_AGENT_STEPS: AgentStep[] = ORCHESTRATOR_STEPS.map((s) => ({
  ...s,
  status: "pending",
}));

export const SUGGESTION_TAGS: string[] = [
  "分析「抗老精华」的爆款模型矩阵,重点看封面与切入点要素",
  "研究「防脱精华」的内容方向分布和高频痛点",
  "对比「PMPM」和「林清轩」的爆文模型差异",
];

// ---------------------------------------------------------------
// DEMO_CANVAS: 未发起任务时 workspace 的静态预览(抗老精华)
// 4.3pre.4 换血到新 8 模块契约
// ---------------------------------------------------------------
export const DEMO_CANVAS: PrototypeCanvasModel = {
  title: "爆文洞察与框架 · 抗老精华",
  meta: "20 篇样本 · 来源演示 · 关键词 抗老精华",
  themeBadge: "主题: 默认",
  dimensions: [
    { id: "industry",   label: "行业洞察", state: "on" },
    { id: "competitor", label: "竞品洞察", state: "on" },
    { id: "brand",      label: "本品洞察", state: "on" },
  ],
  modules: [
    // LAYER 1: 核心框架(overview + matrix)
    {
      moduleId: "mod-overview-stats",
      layer: 1,
      title: "统计总览",
      icon: { character: "O", bg: "#FFF8F0", color: "#8B6914" },
      badges: [{ text: "20 篇 · 图文 12 / 视频 8", color: "amber" }],
      version: 1,
      highlighted: true,
      defaultExpanded: true,
      actions: [{ id: "expand_all", label: "展开" }],
      summary: "20 条样本 · 图文 12 / 视频 8",
      sections: [
        {
          id: "demo-overview-total",
          kind: "static",
          html: '<strong>总样本数:</strong>20 篇 · 图文 12 / 视频 8 · 关键词「抗老精华」',
        },
        {
          id: "demo-overview-sources",
          kind: "static",
          html:
            '<strong>三源分布:</strong>' +
            '竞品爆文 5 · 互动 TOP 12 · SERP 前 10 屏 4',
        },
        {
          id: "demo-overview-directions",
          kind: "static",
          html:
            '<strong>内容方向 TOP:</strong>' +
            '口播单推 40% · 干货分享 30% · 知识科普 20% · 其他 10%',
        },
      ],
    },
    {
      moduleId: "mod-viral-model-matrix",
      layer: 1,
      title: "爆文模型矩阵",
      icon: { character: "M", bg: "rgba(185, 206, 209, 0.28)", color: "#4a5d4a" },
      badges: [
        { text: "重点维度", color: "red" },
        { text: "2 个模型 · 示例演示", color: "amber" },
      ],
      version: 1,
      highlighted: true,
      defaultExpanded: true,
      actions: [{ id: "regenerate", label: "重新生成" }],
      summary: "2 个爆文模型 · 0 个「不做」方向",
      sections: [
        {
          id: "demo-matrix",
          kind: "viral-matrix",
          statsAxisLabel: "高频痛点 / 议程",
          totalSampleCount: 20,
          taxonomyVersion: "demo",
          models: [
            {
              model_id: "M1",
              name: "口播单推型",
              description: "达人手持产品直接口播推荐",
              coverage: 0.4,
              avg_interaction: 15000,
              paragraph_id: "M1",
              elements: {
                A_cover: [
                  {
                    type: "达人手持产品",
                    ratio: 0.6,
                    count: 5,
                    paragraph_id: "M1-A_cover-C1",
                    examples: [
                      { note_id: "demo-1", title: "手持 X 品牌精华一支,黑眼圈淡了", cover_url: "" },
                    ],
                  },
                  {
                    type: "纯产品图",
                    ratio: 0.4,
                    count: 3,
                    paragraph_id: "M1-A_cover-C2",
                    examples: [
                      { note_id: "demo-2", title: "抗老精华 · 15 天见效", cover_url: "" },
                    ],
                  },
                ],
                C_title: [
                  {
                    type: "痛点 + 解决方案",
                    ratio: 0.8,
                    count: 6,
                    paragraph_id: "M1-C_title-C1",
                    examples: [],
                  },
                ],
              },
            },
            {
              model_id: "M2",
              name: "干货分享型",
              description: "教程演示 + 效果前后对比",
              coverage: 0.3,
              avg_interaction: 12000,
              paragraph_id: "M2",
              elements: {
                A_cover: [
                  {
                    type: "前后对比",
                    ratio: 0.7,
                    count: 4,
                    paragraph_id: "M2-A_cover-C1",
                    examples: [],
                  },
                ],
                D_opening: [
                  {
                    type: "干货切入",
                    ratio: 1.0,
                    count: 6,
                    paragraph_id: "M2-D_opening-C1",
                    examples: [],
                  },
                ],
              },
            },
          ],
          unusedDirections: [],
        },
      ],
    },

    // LAYER 2: 洞察与工作台(pain / seo / draft)
    {
      moduleId: "mod-pain-points",
      layer: 2,
      title: "高频痛点 / 议程 Top",
      icon: { character: "P", bg: "#FFF0F5", color: "#B8336A" },
      badges: [{ text: "3 条痛点", color: "red" }],
      version: 1,
      actions: [{ id: "regenerate", label: "重新生成" }],
      summary: "共 3 条高频痛点 / 议程条目",
      sections: [
        {
          id: "demo-pain-axis",
          kind: "static",
          html: '<strong>统计轴:</strong>高频痛点 / 议程',
        },
        {
          id: "demo-pain-table",
          kind: "data-table",
          headers: ["#", "关键词", "频次"],
          rows: [
            [{ text: "1" }, { text: "暗沉" }, { text: "12" }],
            [{ text: "2" }, { text: "细纹" }, { text: "8" }],
            [{ text: "3" }, { text: "法令纹" }, { text: "5" }],
          ],
        },
      ],
    },
    {
      moduleId: "mod-seo-insights",
      layer: 2,
      title: "SEO 关键词洞察",
      icon: { character: "E", bg: "#F0F5FF", color: "#3D5BA8" },
      badges: [{ text: "核心词 2 / 长尾 1", color: "gray" }],
      version: 1,
      actions: [{ id: "regenerate", label: "重新生成" }],
      summary: "核心词 2 / 长尾 1",
      sections: [
        {
          id: "demo-seo-core",
          kind: "data-table",
          headers: ["#", "核心词", "频次"],
          rows: [
            [{ text: "1" }, { text: "抗老" }, { text: "15" }],
            [{ text: "2" }, { text: "精华" }, { text: "10" }],
          ],
        },
        {
          id: "demo-seo-long",
          kind: "data-table",
          headers: ["#", "长尾词", "频次"],
          rows: [[{ text: "1" }, { text: "30 天见效" }, { text: "3" }]],
        },
        {
          id: "demo-seo-advice",
          kind: "static",
          html: '<strong>差异化建议:</strong>聚焦细纹场景,避开 30 天承诺话术',
        },
      ],
    },
    {
      moduleId: "mod-draft-workbench",
      layer: 2,
      title: "创作草稿区",
      icon: { character: "W", bg: "#FFF8E6", color: "#8B6914" },
      badges: [{ text: "6 字段骨架", color: "gray" }],
      version: 1,
      actions: [{ id: "restore", label: "恢复" }],
      summary: "可编辑的创作草稿骨架(6 字段)",
      sections: [
        {
          id: "demo-draft",
          kind: "editable-group",
          items: [
            { id: "demo-draft-title",          html: '<strong>标题:</strong><span style="color:#A8A4A0"> 请点击编辑...</span>' },
            { id: "demo-draft-cover",          html: '<strong>封面构想:</strong><span style="color:#A8A4A0"> 请点击编辑...</span>' },
            { id: "demo-draft-hook",           html: '<strong>开场钩子:</strong><span style="color:#A8A4A0"> 请点击编辑...</span>' },
            { id: "demo-draft-structure",      html: '<strong>内容结构:</strong><span style="color:#A8A4A0"> 请点击编辑...</span>' },
            { id: "demo-draft-product-intro",  html: '<strong>产品引出:</strong><span style="color:#A8A4A0"> 请点击编辑...</span>' },
            { id: "demo-draft-cta",            html: '<strong>CTA 收尾:</strong><span style="color:#A8A4A0"> 请点击编辑...</span>' },
          ],
        },
      ],
    },

    // LAYER 3: 原始样本(三源,与 Excel 分表一致;品类 TOP 仅总览统计)
    {
      moduleId: "mod-competitor-samples",
      layer: 3,
      title: "样本 · 竞品爆文",
      icon: { character: "X", bg: "rgba(185, 206, 209, 0.28)", color: "#4a5d4a" },
      badges: [{ text: "1 条竞品样本", color: "red" }],
      version: 1,
      actions: [],
      summary: "1 条竞品样本",
      sections: [
        {
          id: "demo-competitor-table",
          kind: "data-table",
          headers: ["封面", "标题", "作者", "点赞", "评论", "收藏", "方向"],
          rows: [
            [
              { kind: "cover", coverUrl: "" },
              { text: "PMPM 抗老精华使用 30 天" },
              { text: "甜心" },
              { text: "22,000" },
              { text: "520" },
              { text: "12,100" },
              { text: "干货分享" },
            ],
          ],
        },
        {
          id: "demo-competitor-seo",
          kind: "tag-row",
          label: "笔记涵盖热搜词 Top:",
          tags: ["法令纹", "胶原", "抗老", "精华测评", "细纹"],
        },
        {
          id: "demo-competitor-hot",
          kind: "tag-row",
          label: "评论区热词:",
          tags: ["购物车", "下单", "不好用", "不拉几", "买不起"],
        },
      ],
    },
    {
      moduleId: "mod-top-interaction-samples",
      layer: 3,
      title: "样本 · 互动 TOP",
      icon: { character: "T", bg: "#FFF8F0", color: "#8B6914" },
      badges: [{ text: "1 条高互动样本", color: "amber" }],
      version: 1,
      actions: [],
      summary: "1 条互动 TOP 样本",
      sections: [
        {
          id: "demo-top-table",
          kind: "data-table",
          headers: ["封面", "标题", "作者", "点赞", "评论", "收藏", "方向"],
          rows: [
            [
              { kind: "cover", coverUrl: "" },
              { text: "抗老精华合集 · 6 款实测" },
              { text: "爱美的花" },
              { text: "38,000" },
              { text: "890" },
              { text: "22,340" },
              { text: "知识科普" },
            ],
          ],
        },
      ],
    },
    {
      moduleId: "mod-serp-top-samples",
      layer: 3,
      title: "样本 · SERP 前 10 屏",
      icon: { character: "S", bg: "#F0FAF0", color: "#3D8C40" },
      badges: [{ text: "1 条 SERP 头部", color: "green" }],
      version: 1,
      actions: [],
      summary: "1 条 SERP 头部样本",
      sections: [
        {
          id: "demo-serp-table",
          kind: "data-table",
          headers: ["封面", "标题", "作者", "点赞", "评论", "收藏", "方向"],
          rows: [
            [
              { kind: "cover", coverUrl: "" },
              { text: "抗老精华哪家强?" },
              { text: "编辑部" },
              { text: "5,200" },
              { text: "88" },
              { text: "2,980" },
              { text: "干货分享" },
            ],
          ],
        },
      ],
    },
  ],
};
