"use client";

import { useState } from "react";

import type { AdvancedConfig } from "@/lib/contracts";
import { DEFAULT_ADVANCED } from "@/lib/contracts";

/** 与参考模版加号菜单顶部顺序一致 */
export const ADVANCED_MENU_ROWS: Array<{
  key: keyof AdvancedConfig;
  label: string;
  options: string[];
}> = [
  { key: "note_type", label: "笔记类型", options: ["不限", "视频", "图文"] },
  { key: "sample_count", label: "采集数量", options: ["50", "80", "100"] },
  { key: "time_range", label: "时间范围", options: ["不限", "一天内", "一周内", "半年内"] },
  { key: "viral_ratio", label: "爆款比例", options: ["前50%", "前30%", "前20%"] },
  { key: "min_interaction", label: "互动量", options: ["不限", "1000+", "5000+", "10000+"] },
];

export function SlidersIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      className={className}
      aria-hidden
    >
      <line x1="4" y1="21" x2="4" y2="14" />
      <line x1="4" y1="10" x2="4" y2="3" />
      <line x1="12" y1="21" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12" y2="3" />
      <line x1="20" y1="21" x2="20" y2="16" />
      <line x1="20" y1="12" x2="20" y2="3" />
      <line x1="1" y1="14" x2="7" y2="14" />
      <line x1="9" y1="8" x2="15" y2="8" />
      <line x1="17" y1="16" x2="23" y2="16" />
    </svg>
  );
}

export function advancedOverridesDefault(advanced: AdvancedConfig): boolean {
  return (
    advanced.note_type !== DEFAULT_ADVANCED.note_type ||
    advanced.time_range !== DEFAULT_ADVANCED.time_range ||
    advanced.sample_count !== DEFAULT_ADVANCED.sample_count ||
    advanced.viral_ratio !== DEFAULT_ADVANCED.viral_ratio ||
    advanced.min_interaction !== DEFAULT_ADVANCED.min_interaction
  );
}

export function changedAdvancedLabels(advanced: AdvancedConfig): Array<{ key: string; label: string; value: string }> {
  const out: Array<{ key: string; label: string; value: string }> = [];
  for (const row of ADVANCED_MENU_ROWS) {
    if (advanced[row.key] !== DEFAULT_ADVANCED[row.key]) {
      out.push({ key: row.key, label: row.label, value: advanced[row.key] });
    }
  }
  return out;
}

function FlyoutChevron({ open }: { open: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 text-obsidian/35 transition-transform duration-200 ease-out motion-reduce:transition-none ${
        open ? "-rotate-180" : "rotate-0"
      }`}
      aria-hidden
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

/** 加号菜单内：左侧四行悬停展开右侧选项浮层（行与浮层同一悬停区，避免移入浮层时误关） */
export function AdvancedParamsMenuSection({
  advanced,
  onChange,
}: {
  advanced: AdvancedConfig;
  onChange: (v: AdvancedConfig) => void;
}) {
  const [flyoutKey, setFlyoutKey] = useState<keyof AdvancedConfig | null>(null);

  return (
    <div
      className="border-b border-black/[0.06] py-0.5"
      onMouseDown={(e) => e.stopPropagation()}
    >
      {ADVANCED_MENU_ROWS.map((row) => {
        const isOpen = flyoutKey === row.key;
        const current = advanced[row.key];
        return (
          <div
            key={row.key}
            className={`relative ${isOpen ? "z-[60]" : ""}`}
            onMouseEnter={() => setFlyoutKey(row.key)}
            onMouseLeave={() => setFlyoutKey((k) => (k === row.key ? null : k))}
          >
            <div
              className={`flex w-full cursor-default items-center gap-2.5 px-3 py-2.5 text-left text-[12px] transition-[color,background-color] duration-200 ease-out ${
                isOpen
                  ? "bg-black/[0.045] text-obsidian/75"
                  : "text-obsidian/70 hover:bg-moss/30"
              }`}
            >
              <SlidersIcon className="h-4 w-4 shrink-0 text-obsidian/38" />
              <span className="min-w-0 shrink-0 text-obsidian/48">{row.label}</span>
              <span className="ml-auto min-w-0 truncate text-right text-[12px] font-medium text-obsidian/42">
                {current}
              </span>
              <FlyoutChevron open={isOpen} />
            </div>
            {isOpen ? (
              <div
                className="absolute left-full top-0 z-[60] flex pl-1"
                role="presentation"
              >
                <div
                  className="adv-flyout-panel min-w-[132px] overflow-hidden rounded-[8px] border border-black/[0.08] bg-white py-1 text-[13px] shadow-[0_12px_40px_rgba(26,26,26,0.1)]"
                  role="listbox"
                  aria-label={row.label}
                >
                  {row.options.map((opt) => {
                    const selected = opt === current;
                    return (
                      <button
                        key={opt}
                        type="button"
                        role="option"
                        aria-selected={selected}
                        className={`flex w-full px-3 py-2 text-left transition-[color,background-color] duration-150 ease-out ${
                          selected
                            ? "bg-fog font-medium text-sky-700"
                            : "text-obsidian/80 hover:bg-moss/35"
                        }`}
                        onClick={() => {
                          onChange({ ...advanced, [row.key]: opt });
                          setFlyoutKey(null);
                        }}
                      >
                        {opt}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
