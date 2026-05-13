"use client";

import { ModuleCard, type CanvasParagraphEnv } from "./module-card";
import type {
  DimensionState,
  PrototypeAction,
  PrototypeCanvasModel,
  PrototypeDimension,
  PrototypeModule,
} from "./mock-canvas-data";

interface CanvasViewProps {
  canvas: PrototypeCanvasModel;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  onExportExcel?: () => void;
  onExportJson?: () => void;
  onRefresh?: () => void;
  refreshBusy?: boolean;
  refreshDisabled?: boolean;
  onModuleAction?: (module: PrototypeModule, action: PrototypeAction) => void;
  busyModuleIds?: Set<string>;
  paragraphEnv?: CanvasParagraphEnv;
}

const LAYER_LABELS: Record<1 | 2 | 3, { eyebrow: string; title: string; description: string }> = {
  1: { eyebrow: "Layer 01", title: "核心框架", description: "总览与爆文模型矩阵" },
  2: { eyebrow: "Layer 02", title: "洞察与工作台", description: "痛点、SEO 与草稿生成" },
  3: { eyebrow: "Layer 03", title: "原始样本", description: "竞品、互动 TOP 与 SERP 三源样本" },
};

const DIM_STATE_STYLE: Record<DimensionState, string> = {
  focus: "bg-obsidian text-papyrus border-obsidian shadow-sm",
  on: "bg-white/80 text-obsidian/55 border-black/[0.06]",
  off: "bg-fog/70 text-obsidian/25 border-black/[0.04] line-through",
};

const SECONDARY_BUTTON =
  "rounded-full border border-black/[0.06] bg-white/75 px-4 py-2 text-[12px] font-semibold text-obsidian/50 shadow-sm transition hover:bg-moss hover:text-obsidian disabled:cursor-not-allowed disabled:opacity-50";

export function CanvasView({
  canvas,
  collapsed,
  onExportExcel,
  onExportJson,
  onRefresh,
  refreshBusy,
  refreshDisabled,
  onModuleAction,
  busyModuleIds,
  paragraphEnv,
}: CanvasViewProps) {
  if (collapsed) return null;

  const grouped: Record<1 | 2 | 3, PrototypeModule[]> = { 1: [], 2: [], 3: [] };
  for (const mod of canvas.modules) {
    grouped[mod.layer].push(mod);
  }

  return (
    <div className="relative flex-1 overflow-y-auto bg-[radial-gradient(circle_at_80%_0%,rgba(185,206,209,0.25),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.72),rgba(247,247,245,0.94))] px-6 py-6 lg:px-8">
      <div className="pointer-events-none absolute right-[7%] top-12 h-72 w-72 rounded-full border border-dew/30 opacity-50" />
      <div className="pointer-events-none absolute bottom-[12%] left-[10%] h-56 w-56 rounded-full bg-moss/45 blur-3xl" />

      <div className="relative mx-auto flex max-w-[1180px] flex-col gap-6">
        <header className="sticky top-0 z-20 rounded-[28px] border border-black/[0.05] bg-white/76 p-4 shadow-[0_22px_60px_rgba(26,26,26,0.075)] backdrop-blur-xl">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.24em] text-obsidian/28">RedMuse Canvas</div>
              <div className="font-serif text-[28px] font-semibold leading-tight tracking-[-0.04em] text-obsidian">{canvas.title}</div>
              <div className="mt-1 text-[12px] leading-5 text-obsidian/42">{canvas.meta}</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-dew/35 bg-dew/15 px-3.5 py-2 text-[11px] font-semibold text-obsidian/55">
                {canvas.themeBadge}
              </span>
              <button
                type="button"
                onClick={onRefresh}
                disabled={!onRefresh || refreshDisabled || refreshBusy}
                className={SECONDARY_BUTTON}
                title="重新拉取当前任务的最新画布"
              >
                {refreshBusy ? "刷新中..." : "刷新画布"}
              </button>
              <button
                type="button"
                onClick={onExportExcel}
                className="rounded-full border border-obsidian bg-obsidian px-4 py-2 text-[12px] font-semibold text-papyrus shadow-sm transition hover:bg-obsidian/86"
              >
                导出 Excel
              </button>
              <button
                type="button"
                onClick={onExportJson}
                className={SECONDARY_BUTTON}
              >
                导出 JSON
              </button>
            </div>
          </div>
          <DimensionBar dimensions={canvas.dimensions} />
        </header>

        {(Object.keys(LAYER_LABELS) as unknown as Array<"1" | "2" | "3">).map((k) => {
          const layer = Number(k) as 1 | 2 | 3;
          const modules = grouped[layer];
          const label = LAYER_LABELS[layer];
          if (!modules?.length) return null;
          return (
            <section key={layer} className="scroll-mt-32">
              <div className="mb-3 flex items-end justify-between gap-4 border-t border-black/[0.06] pt-5">
                <div>
                  <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-obsidian/28">{label.eyebrow}</div>
                  <div className="mt-1 font-serif text-[22px] font-semibold tracking-[-0.03em] text-obsidian">{label.title}</div>
                </div>
                <div className="hidden text-right text-[12px] text-obsidian/36 sm:block">{label.description}</div>
              </div>
              <div className="space-y-4">
                {modules.map((mod) => (
                  <ModuleCard
                    key={mod.moduleId}
                    module={mod}
                    busy={busyModuleIds?.has(mod.moduleId)}
                    onActionClick={onModuleAction}
                    paragraphEnv={paragraphEnv}
                  />
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}

function DimensionBar({ dimensions }: { dimensions: PrototypeDimension[] }) {
  if (!dimensions.length) return null;

  return (
    <div className="mt-4 flex flex-wrap gap-2 border-t border-black/[0.04] pt-4">
      {dimensions.map((dim) => (
        <span
          key={dim.id}
          className={`rounded-full border px-3 py-1.5 text-[11px] font-semibold ${DIM_STATE_STYLE[dim.state]}`}
        >
          {dim.label}
        </span>
      ))}
    </div>
  );
}
