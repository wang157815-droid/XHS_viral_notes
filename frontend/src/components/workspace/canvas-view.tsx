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

const LAYER_LABELS: Record<1 | 2 | 3, string> = {
  1: "LAYER 1 · 核心框架(总览 + 爆文模型矩阵)",
  2: "LAYER 2 · 洞察与工作台(痛点 / SEO / 草稿)",
  3: "LAYER 3 · 原始样本(三源)",
};

const DIM_STATE_STYLE: Record<DimensionState, string> = {
  focus: "bg-[#FF4757] text-white border-[#FF4757]",
  on: "bg-[#FFF0EE] text-[#FF4757] border-[#FFD6CC]",
  off: "bg-[#F5F3F0] text-[#A8A4A0] border-[#E8E5E0] line-through",
};

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
    <div className="flex-1 overflow-y-auto bg-[#FAFAF8] px-8 py-6">
      <div className="h-[3px] bg-gradient-to-r from-[#8B6914] via-[#D4A574] to-[#F5E6CC]" />

      <header className="mb-4 mt-5 flex items-center justify-between">
        <div>
          <div className="text-[20px] font-bold">{canvas.title}</div>
          <div className="mt-1 text-[12px] text-[#A8A4A0]">{canvas.meta}</div>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-md border border-[#F5E6CC] bg-[#FFF8F0] px-3 py-1 text-[11px] text-[#8B6914]">
            {canvas.themeBadge}
          </span>
          <button
            type="button"
            onClick={onRefresh}
            disabled={!onRefresh || refreshDisabled || refreshBusy}
            className="rounded-lg border border-[#E8E5E0] bg-white px-[14px] py-[7px] text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0] disabled:cursor-not-allowed disabled:opacity-50"
            title="重新拉取当前任务的最新画布"
          >
            {refreshBusy ? "刷新中..." : "刷新画布"}
          </button>
          <button
            type="button"
            onClick={onExportExcel}
            className="rounded-lg border border-[#FF4757] bg-[#FF4757] px-[14px] py-[7px] text-[12px] text-white transition hover:bg-[#E8404F]"
          >
            导出 Excel
          </button>
          <button
            type="button"
            onClick={onExportJson}
            className="rounded-lg border border-[#E8E5E0] bg-white px-[14px] py-[7px] text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
          >
            导出 JSON
          </button>
        </div>
      </header>

      <DimensionBar dimensions={canvas.dimensions} />

      {(Object.keys(LAYER_LABELS) as unknown as Array<"1" | "2" | "3">).map((k) => {
        const layer = Number(k) as 1 | 2 | 3;
        const modules = grouped[layer];
        if (!modules?.length) return null;
        return (
          <section key={layer}>
            <div className="mt-6 mb-2.5 pl-1 text-[11px] font-bold tracking-[0.5px] text-[#A8A4A0]">
              {LAYER_LABELS[layer]}
            </div>
            {modules.map((mod) => (
              <ModuleCard
                key={mod.moduleId}
                module={mod}
                busy={busyModuleIds?.has(mod.moduleId)}
                onActionClick={onModuleAction}
                paragraphEnv={paragraphEnv}
              />
            ))}
          </section>
        );
      })}
    </div>
  );
}

function DimensionBar({ dimensions }: { dimensions: PrototypeDimension[] }) {
  return (
    <div className="mb-5 flex flex-wrap gap-1.5">
      {dimensions.map((dim) => (
        <span
          key={dim.id}
          className={`rounded-md border px-3 py-[5px] text-[11px] font-semibold ${DIM_STATE_STYLE[dim.state]}`}
        >
          {dim.label}
        </span>
      ))}
    </div>
  );
}
