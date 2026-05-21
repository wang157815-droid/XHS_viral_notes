"use client";

import { ModuleCard, type CanvasParagraphEnv } from "./module-card";
import type {
  PrototypeAction,
  PrototypeCanvasModel,
  PrototypeModule,
  StaticParagraph,
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

  // 提取 mod-overview-stats 的统计行，用于顶部卡片内联展示
  const statsModule = canvas.modules.find((m) => m.moduleId === "mod-overview-stats");
  const statsLines = (statsModule?.sections ?? []).filter(
    (s): s is StaticParagraph => s.kind === "static",
  );

  const grouped: Record<1 | 2 | 3, PrototypeModule[]> = { 1: [], 2: [], 3: [] };
  for (const mod of canvas.modules) {
    // 统计总览已内联到顶部卡片，不再单独渲染为卡片
    if (mod.moduleId === "mod-overview-stats") continue;
    grouped[mod.layer].push(mod);
  }

  return (
    <div className="relative flex-1 overflow-y-auto bg-[radial-gradient(circle_at_80%_0%,rgba(185,206,209,0.25),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.72),rgba(247,247,245,0.94))] px-4 py-4 lg:px-6">
      <div className="pointer-events-none absolute right-[7%] top-12 h-72 w-72 rounded-full border border-dew/30 opacity-50" />
      <div className="pointer-events-none absolute bottom-[12%] left-[10%] h-56 w-56 rounded-full bg-moss/45 blur-3xl" />

      <div className="relative mx-auto flex max-w-[1180px] flex-col gap-4">
        <header className="rounded-[20px] border border-black/[0.05] bg-white/76 px-4 py-3 shadow-[0_12px_36px_rgba(26,26,26,0.065)] backdrop-blur-xl">
          <div className="flex flex-col gap-2.5 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <div className="font-serif text-[20px] font-semibold leading-tight tracking-[-0.04em] text-obsidian">{canvas.title}</div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="rounded-full border border-dew/35 bg-dew/15 px-3 py-1.5 text-[11px] font-semibold text-obsidian/55">
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
                className="rounded-full border border-obsidian bg-obsidian px-3.5 py-1.5 text-[11px] font-semibold text-papyrus shadow-sm transition hover:bg-obsidian/86"
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
          <OverviewStatsBar lines={statsLines} />
        </header>

        {(Object.keys(LAYER_LABELS) as unknown as Array<"1" | "2" | "3">).map((k) => {
          const layer = Number(k) as 1 | 2 | 3;
          const modules = grouped[layer];
          if (!modules?.length) return null;
          return (
            <section key={layer} className="scroll-mt-20">
              {layer > 1 && <div className="border-t border-black/[0.06] pt-3" />}
              <div className="space-y-2.5">
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

function OverviewStatsBar({ lines }: { lines: StaticParagraph[] }) {
  if (!lines.length) return null;

  return (
    <div className="mt-2 space-y-1 border-t border-black/[0.04] pt-2">
      {lines.map((line) => (
        <p
          key={line.id}
          className="text-[12px] leading-relaxed text-obsidian/60"
          // eslint-disable-next-line react/no-danger
          dangerouslySetInnerHTML={{ __html: line.html }}
        />
      ))}
    </div>
  );
}
