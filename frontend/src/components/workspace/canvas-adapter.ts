/**
 * CanvasSchema(后端契约) → PrototypeCanvasModel(前端渲染模型)
 *
 * 4.3pre.4 适配后端画布契约(品类 TOP 无独立样本卡):
 *   - mod-overview-stats        → 3-4 个 static paragraph(总览 KPI)
 *   - mod-viral-model-matrix    → 1 个 ViralMatrixSection(专用手风琴)
 *   - mod-competitor/top-interaction/serp-top-samples → data-table + 封面缩略图
 *   - mod-pain-points           → static(轴名) + data-table(关键词频次)
 *   - mod-seo-insights          → data-table × 2 + static(差异化建议)
 *   - mod-draft-workbench       → editable-group(6 字段骨架)
 *
 * 旧 11 模块 id 不再处理,fallback 走通用 key/value 打印(保留兜底不崩)。
 */

import type {
  CanvasDimension,
  CanvasModule,
  CanvasSchema,
  ModuleStatus,
} from "@/lib/contracts";

import type {
  DataTableCell,
  DataTableSection,
  DimensionState,
  ModuleSection,
  PrototypeAction,
  PrototypeBadge,
  PrototypeCanvasModel,
  PrototypeIcon,
  PrototypeModule,
  TagRowSection,
  ViralMatrixElementCategory,
  ViralMatrixExample,
  ViralMatrixModel,
  ViralMatrixSection,
  ViralMatrixUnusedDirection,
} from "./mock-canvas-data";

type IconSpec = PrototypeIcon;

// 新画布模块 + 静态草稿图标表(mod-source-samples 已下线,旧数据过滤不展示)
const ICON_BY_MODULE: Record<string, IconSpec> = {
  "mod-overview-stats":          { character: "O", bg: "#FFF8F0", color: "#8B6914" },
  "mod-viral-model-matrix":      { character: "M", bg: "rgba(185, 206, 209, 0.28)", color: "#4a5d4a" },
  "mod-competitor-samples":      { character: "X", bg: "rgba(185, 206, 209, 0.28)", color: "#4a5d4a" },
  "mod-competitor-samples-image": { character: "X", bg: "#FFF5F3", color: "#E85D4C" },
  "mod-competitor-samples-video": { character: "X", bg: "#FFECE8", color: "#C73E1D" },
  "mod-top-interaction-samples": { character: "T", bg: "#FFF8F0", color: "#8B6914" },
  "mod-top-interaction-samples-image": { character: "T", bg: "#FFFAF2", color: "#7A5C12" },
  "mod-top-interaction-samples-video": { character: "T", bg: "#FFF3E0", color: "#6B4E0F" },
  "mod-serp-top-samples":        { character: "S", bg: "#F0FAF0", color: "#3D8C40" },
  "mod-serp-top-samples-image":  { character: "S", bg: "#F2FAF2", color: "#2F7A38" },
  "mod-serp-top-samples-video":  { character: "S", bg: "#E8F5E9", color: "#256B2E" },
  "mod-pain-points":             { character: "P", bg: "#FFF0F5", color: "#B8336A" },
  "mod-seo-insights":            { character: "E", bg: "#F0F5FF", color: "#3D5BA8" },
  "mod-draft-workbench":         { character: "W", bg: "#FFF8E6", color: "#8B6914" },
};

const DEFAULT_ICON: IconSpec = { character: "·", bg: "#F5F3F0", color: "#8A8580" };

const STATUS_BADGE: Record<string, PrototypeBadge | null> = {
  ready: { text: "已就绪", color: "green" },
  generating: { text: "生成中", color: "amber" },
  stale: { text: "需更新", color: "amber" },
  failed: { text: "生成失败", color: "red" },
  pending: { text: "待生成", color: "gray" },
  deleted: { text: "已删除", color: "gray" },
};

const ACTION_LABEL: Record<string, string> = {
  regen_sheet2_narrative: "重新写分类叙事",
  rename_models: "重新起名",
  regenerate: "重新生成",
  regenerate_cascade: "级联重生",
  delete: "删除",
  restore: "恢复",
  export: "导出",
};

const DRAFT_FIELD_LABELS: Array<{ key: string; label: string }> = [
  { key: "title", label: "标题" },
  { key: "cover_concept", label: "封面构想" },
  { key: "hook", label: "开场钩子" },
  { key: "structure", label: "内容结构" },
  { key: "product_intro", label: "产品引出" },
  { key: "cta", label: "CTA 收尾" },
];

export function adaptCanvas(canvas: CanvasSchema): PrototypeCanvasModel {
  const themeBadge = canvas.themes?.[0]?.label
    ? `主题: ${canvas.themes[0].label}`
    : "主题: 默认";

  return {
    title: canvas.title || "爆文洞察与框架",
    meta: canvas.subtitle || `画布版本 v${canvas.canvas_version ?? 1}`,
    themeBadge,
    dimensions: (canvas.dimensions ?? []).map(adaptDimension),
    modules: (canvas.modules ?? [])
      .filter((m) => m.module_id !== "mod-source-samples")
      .map(adaptModule),
  };
}

function adaptDimension(dim: CanvasDimension): {
  id: string;
  label: string;
  state: DimensionState;
} {
  let state: DimensionState = "on";
  if (!dim.active) state = "off";
  else if (dim.highlighted) state = "focus";
  return {
    id: dim.id,
    label: dim.label,
    state,
  };
}

function adaptModule(module: CanvasModule): PrototypeModule {
  const icon = ICON_BY_MODULE[module.module_id] ?? DEFAULT_ICON;
  const badges: PrototypeBadge[] = [];

  if (module.highlighted) {
    badges.push({ text: "重点维度", color: "red" });
  }
  const statusBadge = STATUS_BADGE[module.status as string];
  if (statusBadge && module.status !== ("ready" as ModuleStatus)) {
    badges.push(statusBadge);
  }

  const actions: PrototypeAction[] = (module.actions ?? [])
    .filter((a) => a.command !== "export" && a.command !== "deep_dive")
    .map((a) => ({
      id: a.command as PrototypeAction["id"],
      label: ACTION_LABEL[a.command] ?? a.label,
    }));

  const sections = buildSections(module);

  const paragraphFeedback: Record<string, string> = {};
  const fm = (module.content?.feedback_map ?? {}) as Record<string, unknown>;
  for (const [k, v] of Object.entries(fm)) {
    if (v && typeof v === "object" && v !== null && "action" in v) {
      const a = String((v as { action?: string }).action ?? "");
      if (a && a !== "reset") paragraphFeedback[k] = a;
    }
  }

  return {
    moduleId: module.module_id,
    layer: (module.layer as 1 | 2 | 3) ?? 1,
    title: module.title,
    icon,
    badges,
    version: module.version ?? 1,
    highlighted: module.highlighted,
    defaultExpanded: module.default_expanded ?? module.highlighted ?? false,
    summary: module.summary ?? undefined,
    paragraphFeedback: Object.keys(paragraphFeedback).length ? paragraphFeedback : undefined,
    actions,
    sections,
  };
}

function buildSections(module: CanvasModule): ModuleSection[] {
  const content = (module.content ?? {}) as Record<string, unknown>;

  switch (module.module_id) {
    case "mod-overview-stats":
      return buildOverviewStats(module.module_id, content);
    case "mod-viral-model-matrix":
      return buildViralMatrix(module.module_id, content);
    case "mod-competitor-samples":
    case "mod-competitor-samples-image":
    case "mod-competitor-samples-video":
      return buildSampleTable(module.module_id, content, {
        emptyLabel: "暂无竞品样本",
        includeCompetitorSeo: true,
      });
    case "mod-top-interaction-samples":
    case "mod-top-interaction-samples-image":
    case "mod-top-interaction-samples-video":
      return buildSampleTable(module.module_id, content, {
        emptyLabel: "暂无互动 TOP 样本",
      });
    case "mod-serp-top-samples":
    case "mod-serp-top-samples-image":
    case "mod-serp-top-samples-video":
      return buildSampleTable(module.module_id, content, {
        emptyLabel: "暂无 SERP 头部样本",
      });
    case "mod-pain-points":
      return buildPainPoints(module.module_id, content);
    case "mod-seo-insights":
      return buildSeoInsights(module.module_id, content);
    case "mod-draft-workbench":
      return buildDraftWorkbench(module.module_id, content);
    default:
      return fallbackSections(module.module_id, content);
  }
}

// ---------------------------------------------------------------
// mod-overview-stats
// ---------------------------------------------------------------
function buildOverviewStats(
  moduleId: string,
  content: Record<string, unknown>,
): ModuleSection[] {
  const total = toNumber(content.total_notes);
  const img = toNumber(content.image_count);
  const video = toNumber(content.video_count);
  const keyword = typeof content.keyword === "string" ? content.keyword : "";

  const sections: ModuleSection[] = [];

  sections.push({
    id: `${moduleId}-total`,
    kind: "static",
    html:
      `<strong>总样本数:</strong>${total} 篇 · 图文 ${img} / 视频 ${video}` +
      (keyword ? ` · 关键词 <span style="color:#6F9095">${escapeHtml(keyword)}</span>` : ""),
  });

  const sourceBreakdown = Array.isArray(content.source_breakdown)
    ? (content.source_breakdown as Array<Record<string, unknown>>)
    : [];
  if (sourceBreakdown.length) {
    const parts = sourceBreakdown
      .map((b) => {
        const label = SOURCE_TYPE_LABEL[String(b.source_type ?? "")] ?? String(b.source_type ?? "");
        return `${label} ${toNumber(b.count)}`;
      })
      .join(" · ");
    sections.push({
      id: `${moduleId}-sources`,
      kind: "static",
      html: `<strong>三源分布:</strong>${escapeHtml(parts)}`,
    });
  }

  const directionBreakdown = Array.isArray(content.direction_breakdown)
    ? (content.direction_breakdown as Array<Record<string, unknown>>)
    : [];
  if (directionBreakdown.length) {
    const parts = directionBreakdown
      .slice(0, 6)
      .map((d) => `${escapeHtml(String(d.direction ?? ""))} ${toNumber(d.count)}`)
      .join(" · ");
    sections.push({
      id: `${moduleId}-directions`,
      kind: "static",
      html: `<strong>内容方向 TOP:</strong>${parts}`,
    });
  }

  const dateRange = typeof content.sample_date_range === "string" ? content.sample_date_range : "";
  if (dateRange) {
    sections.push({
      id: `${moduleId}-date`,
      kind: "static",
      html: `<strong>样本时间窗:</strong>${escapeHtml(dateRange)}`,
    });
  }

  return sections.length ? sections : fallbackSections(moduleId, content);
}

const SOURCE_TYPE_LABEL: Record<string, string> = {
  category_top: "品类 TOP",
  competitor: "竞品爆文",
  top_interaction: "互动 TOP",
  serp_top: "SERP 前 10 屏",
};

// ---------------------------------------------------------------
// mod-viral-model-matrix
// ---------------------------------------------------------------
function buildViralMatrix(
  moduleId: string,
  content: Record<string, unknown>,
): ModuleSection[] {
  const matrix = (content.matrix ?? {}) as Record<string, unknown>;
  const rawModels = Array.isArray(matrix.models) ? matrix.models : [];
  const rawUnused = Array.isArray(matrix.unused_directions) ? matrix.unused_directions : [];
  const emptyReason = String(matrix.empty_reason ?? "").trim();

  const models: ViralMatrixModel[] = rawModels
    .map((m) => shapeModel(m as Record<string, unknown>))
    .filter((m): m is ViralMatrixModel => m !== null);

  if (!models.length && !rawUnused.length) {
    const reasonHint: Record<string, string> = {
      no_annotations:
        "暂无矩阵：多模态 6 要素标注为空（请确认爬虫样本有封面、图片/视频分析是否成功）。",
      all_below_threshold: "暂无矩阵：内容方向占比均低于阈值，未形成可展示模型。",
    };
    const msg =
      emptyReason && reasonHint[emptyReason]
        ? reasonHint[emptyReason]
        : emptyReason
          ? `暂无矩阵：${emptyReason}`
          : "暂无爆文模型矩阵（上游尚未写入矩阵，或仍在聚类中）。";
    return [
      {
        id: `${moduleId}-empty`,
        kind: "static",
        html: `<em style="color:#A8A4A0">${msg}</em>`,
      },
    ];
  }

  const unusedDirections: ViralMatrixUnusedDirection[] = rawUnused.map((u) => {
    const obj = (u ?? {}) as Record<string, unknown>;
    return {
      direction: String(obj.direction ?? ""),
      ratio: toFloat(obj.ratio),
      avg_interaction: toNumber(obj.avg_interaction),
      reason: String(obj.reason ?? ""),
    };
  });

  const section: ViralMatrixSection = {
    id: `${moduleId}-matrix`,
    kind: "viral-matrix",
    statsAxisLabel: String(matrix.stats_axis_label ?? "高频痛点 / 议程"),
    totalSampleCount: toNumber(matrix.total_sample_count),
    taxonomyVersion: String(matrix.taxonomy_version ?? ""),
    models,
    unusedDirections,
  };
  return [section];
}

function shapeModel(raw: Record<string, unknown>): ViralMatrixModel | null {
  if (!raw || typeof raw !== "object") return null;
  const elementsRaw = (raw.elements ?? {}) as Record<string, unknown>;
  const elements: Record<string, ViralMatrixElementCategory[]> = {};
  for (const [code, cats] of Object.entries(elementsRaw)) {
    if (!Array.isArray(cats)) continue;
    elements[code] = cats
      .map((c) => shapeCategory(c as Record<string, unknown>))
      .filter((c): c is ViralMatrixElementCategory => c !== null);
  }
  return {
    model_id: String(raw.model_id ?? ""),
    name: String(raw.name ?? ""),
    description: String(raw.description ?? ""),
    coverage: toFloat(raw.coverage),
    avg_interaction: toNumber(raw.avg_interaction),
    elements,
    paragraph_id: typeof raw.paragraph_id === "string" ? raw.paragraph_id : undefined,
  };
}

function shapeCategory(raw: Record<string, unknown>): ViralMatrixElementCategory | null {
  if (!raw || typeof raw !== "object") return null;
  const examplesRaw = Array.isArray(raw.examples) ? raw.examples : [];
  const examples: ViralMatrixExample[] = examplesRaw.map((e) => {
    const obj = (e ?? {}) as Record<string, unknown>;
    return {
      note_id: String(obj.note_id ?? ""),
      title: String(obj.title ?? ""),
      cover_url: String(obj.cover_url ?? ""),
      likes: typeof obj.likes === "number" ? obj.likes : undefined,
    };
  });
  return {
    type: String(raw.type ?? ""),
    ratio: toFloat(raw.ratio),
    count: toNumber(raw.count),
    examples,
    paragraph_id: typeof raw.paragraph_id === "string" ? raw.paragraph_id : undefined,
  };
}

// ---------------------------------------------------------------
// 四样本模块统一构造器
// ---------------------------------------------------------------
interface SampleTableOpts {
  emptyLabel: string;
  includeCompetitorSeo?: boolean;
}

function buildSampleTable(
  moduleId: string,
  content: Record<string, unknown>,
  opts: SampleTableOpts,
): ModuleSection[] {
  const notes = Array.isArray(content.notes)
    ? (content.notes as Array<Record<string, unknown>>)
    : [];

  if (!notes.length) {
    return [
      {
        id: `${moduleId}-empty`,
        kind: "static",
        html: `<em style="color:#A8A4A0">${escapeHtml(opts.emptyLabel)}。</em>`,
      },
    ];
  }

  const rows: DataTableCell[][] = notes.map((n) => {
    const coverUrl = typeof n.cover_url === "string" ? (n.cover_url as string) : "";
    const noteUrl = typeof n.note_url === "string" ? (n.note_url as string) : "";
    const paragraphId = typeof n.paragraph_id === "string" ? n.paragraph_id : undefined;
    return [
      { kind: "cover", coverUrl, noteUrl, paragraphId },
      { text: String(n.title ?? "") },
      { text: String(n.author ?? "") },
      { text: formatNumber(n.likes) },
      { text: formatNumber(n.comments) },
      { text: formatNumber(n.collects) },
      { text: String(n.content_direction ?? "") },
    ];
  });

  const table: DataTableSection = {
    id: `${moduleId}-table`,
    kind: "data-table",
    headers: ["封面", "标题", "作者", "点赞", "评论", "收藏", "方向"],
    rows,
  };

  const sections: ModuleSection[] = [table];

  if (opts.includeCompetitorSeo) {
    // 聚合所有 notes 的 seo_top10 / comment_hotwords_top10,去重后展示为 tag-row
    const seoBag = new Set<string>();
    const hotBag = new Set<string>();
    for (const n of notes) {
      if (Array.isArray(n.seo_top10)) {
        for (const kw of n.seo_top10 as unknown[]) {
          if (typeof kw === "string" && kw.trim()) seoBag.add(kw.trim());
        }
      }
      if (Array.isArray(n.comment_hotwords_top10)) {
        for (const kw of n.comment_hotwords_top10 as unknown[]) {
          if (typeof kw === "string" && kw.trim()) hotBag.add(kw.trim());
        }
      }
    }
    if (seoBag.size) {
      const tagRow: TagRowSection = {
        id: `${moduleId}-seo`,
        kind: "tag-row",
        label: "笔记涵盖热搜词 Top:",
        tags: Array.from(seoBag).slice(0, 15),
      };
      sections.push(tagRow);
    }
    if (hotBag.size) {
      const tagRow: TagRowSection = {
        id: `${moduleId}-hot`,
        kind: "tag-row",
        label: "评论区热词:",
        tags: Array.from(hotBag).slice(0, 15),
      };
      sections.push(tagRow);
    }
  }

  return sections;
}

// ---------------------------------------------------------------
// mod-pain-points
// ---------------------------------------------------------------
function buildPainPoints(
  moduleId: string,
  content: Record<string, unknown>,
): ModuleSection[] {
  const axisLabel = typeof content.stats_axis_label === "string" && content.stats_axis_label
    ? (content.stats_axis_label as string)
    : "高频痛点 / 议程";
  const items = Array.isArray(content.items)
    ? (content.items as Array<Record<string, unknown>>)
    : [];

  const sections: ModuleSection[] = [
    {
      id: `${moduleId}-axis`,
      kind: "static",
      html: `<strong>统计轴:</strong>${escapeHtml(axisLabel)}`,
    },
  ];

  if (!items.length) {
    sections.push({
      id: `${moduleId}-empty`,
      kind: "static",
      html: '<em style="color:#A8A4A0">暂无高频痛点条目。</em>',
    });
    return sections;
  }

  const rows: DataTableCell[][] = items.map((item, idx) => [
    {
      text: String(idx + 1),
      paragraphId: typeof item.paragraph_id === "string" ? item.paragraph_id : undefined,
    },
    { text: String(item.keyword ?? "") },
    { text: formatNumber(item.count) },
  ]);

  sections.push({
    id: `${moduleId}-table`,
    kind: "data-table",
    headers: ["#", "关键词", "频次"],
    rows,
  });
  return sections;
}

// ---------------------------------------------------------------
// mod-seo-insights
// ---------------------------------------------------------------
function buildSeoInsights(
  moduleId: string,
  content: Record<string, unknown>,
): ModuleSection[] {
  const core = Array.isArray(content.core_keywords)
    ? (content.core_keywords as Array<Record<string, unknown>>)
    : [];
  const long = Array.isArray(content.long_tail)
    ? (content.long_tail as Array<Record<string, unknown>>)
    : [];
  const advice = typeof content.differentiation_advice === "string"
    ? (content.differentiation_advice as string)
    : "";

  const sections: ModuleSection[] = [];

  if (core.length) {
    sections.push({
      id: `${moduleId}-core`,
      kind: "data-table",
      headers: ["#", "核心词", "频次"],
      rows: core.map((item, idx) => [
        {
          text: String(idx + 1),
          paragraphId: typeof item.paragraph_id === "string" ? item.paragraph_id : undefined,
        },
        { text: String(item.keyword ?? "") },
        { text: formatNumber(item.count) },
      ]),
    });
  }

  if (long.length) {
    sections.push({
      id: `${moduleId}-long`,
      kind: "data-table",
      headers: ["#", "长尾词", "频次"],
      rows: long.map((item, idx) => [
        {
          text: String(idx + 1),
          paragraphId: typeof item.paragraph_id === "string" ? item.paragraph_id : undefined,
        },
        { text: String(item.keyword ?? "") },
        { text: formatNumber(item.count) },
      ]),
    });
  }

  if (advice) {
    sections.push({
      id: `${moduleId}-advice`,
      kind: "static",
      html: `<strong>差异化建议:</strong>${escapeHtml(advice)}`,
    });
  }

  if (!sections.length) {
    return [
      {
        id: `${moduleId}-empty`,
        kind: "static",
        html: '<em style="color:#A8A4A0">暂无 SEO 关键词洞察(等待竞品样本)。</em>',
      },
    ];
  }
  return sections;
}

// ---------------------------------------------------------------
// mod-draft-workbench
// ---------------------------------------------------------------
function buildDraftWorkbench(
  moduleId: string,
  content: Record<string, unknown>,
): ModuleSection[] {
  const fields = (content.fields ?? {}) as Record<string, unknown>;
  const items = DRAFT_FIELD_LABELS.map(({ key, label }) => {
    const raw = fields[key];
    const value = typeof raw === "string" ? raw.trim() : "";
    const innerHtml = value
      ? escapeHtml(value)
      : '<span style="color:#A8A4A0"> 请点击编辑...</span>';
    return {
      id: `${moduleId}-draft-${key}`,
      html: `<strong>${label}:</strong>${value ? " " : ""}${innerHtml}`,
    };
  });

  return [
    {
      id: `${moduleId}-draft`,
      kind: "editable-group",
      items,
    },
  ];
}

// ---------------------------------------------------------------
// fallback(未知 module_id 的兜底)
// ---------------------------------------------------------------
function fallbackSections(moduleId: string, content: Record<string, unknown>): ModuleSection[] {
  const sections: ModuleSection[] = [];
  let idx = 0;
  for (const [key, value] of Object.entries(content)) {
    const label = `<strong>${escapeHtml(humanizeKey(key))}:</strong>`;
    if (Array.isArray(value)) {
      const items = value.map((v) => (typeof v === "string" ? v : JSON.stringify(v)));
      sections.push({
        id: `${moduleId}-s-${idx++}`,
        kind: "static",
        html: label + items.map((t) => escapeHtml(t)).join("、"),
      });
    } else if (value && typeof value === "object") {
      sections.push({
        id: `${moduleId}-s-${idx++}`,
        kind: "static",
        html:
          label +
          Object.entries(value as Record<string, unknown>)
            .map(([k, v]) => `${escapeHtml(humanizeKey(k))}:${escapeHtml(stringifySafe(v))}`)
            .join(" · "),
      });
    } else if (value != null) {
      sections.push({
        id: `${moduleId}-s-${idx++}`,
        kind: "static",
        html: label + escapeHtml(stringifySafe(value)),
      });
    }
  }

  if (!sections.length) {
    sections.push({
      id: `${moduleId}-empty`,
      kind: "static",
      html: '<em style="color:#A8A4A0">该模块暂无详细内容。</em>',
    });
  }

  return sections;
}

// ---------------------------------------------------------------
// 工具函数
// ---------------------------------------------------------------
function humanizeKey(key: string): string {
  const map: Record<string, string> = {
    total_notes: "总样本数",
    image_count: "图文数",
    video_count: "视频数",
    source_breakdown: "三源分布",
    direction_breakdown: "内容方向分布",
    sample_date_range: "时间窗",
    keyword: "关键词",
    matrix: "矩阵",
    models: "爆文模型",
    unused_directions: "不做的方向",
    total_sample_count: "总样本数",
    stats_axis_label: "统计轴",
    items: "条目",
    core_keywords: "核心词",
    long_tail: "长尾词",
    differentiation_advice: "差异化建议",
    notes: "样本",
    source_type: "来源",
    sample_count: "样本数",
    fields: "字段",
  };
  return map[key] ?? key;
}

function stringifySafe(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function toNumber(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return Math.trunc(v);
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

function toFloat(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function formatNumber(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "-";
  return Math.trunc(n).toLocaleString("zh-CN");
}

function escapeHtml(text: string): string {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
