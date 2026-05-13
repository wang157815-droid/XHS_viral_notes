"use client";

import type { ReactNode } from "react";

import type { AdvancedConfig } from "./chat-panel";
import { SUGGESTION_TAGS } from "./mock-canvas-data";

interface PromptComposerProps {
  value: string;
  advanced: AdvancedConfig;
  expanded: boolean;
  disabled?: boolean;
  busy?: boolean;
  notice?: ReactNode;
  onValueChange: (value: string) => void;
  onAdvancedChange: (value: AdvancedConfig) => void;
  onToggleExpanded: () => void;
  onSubmit: () => void | Promise<void>;
  onPickSuggestion: (text: string) => void | Promise<void>;
}

const PARAM_GROUPS: Array<{
  key: keyof AdvancedConfig;
  label: string;
  options: string[];
}> = [
  { key: "note_type", label: "笔记类型", options: ["不限", "视频", "图文"] },
  { key: "time_range", label: "时间范围", options: ["不限", "一天内", "一周内", "半年内"] },
  { key: "sample_count", label: "采集数量", options: ["100", "200", "500"] },
  { key: "viral_ratio", label: "爆款比例", options: ["前50%", "前30%", "前20%"] },
];

export function PromptComposer({
  value,
  advanced,
  expanded,
  disabled,
  busy,
  notice,
  onValueChange,
  onAdvancedChange,
  onToggleExpanded,
  onSubmit,
  onPickSuggestion,
}: PromptComposerProps) {
  const canSend = Boolean(value.trim()) && !disabled && !busy;

  return (
    <div className="relative flex min-h-full flex-1 flex-col items-center justify-center overflow-hidden px-8 py-12">
      <div className="pointer-events-none absolute right-[8%] top-[16%] h-64 w-64 rounded-full border border-dew/35 opacity-50" />
      <div className="pointer-events-none absolute bottom-[12%] left-[8%] h-40 w-40 rounded-full bg-moss/45 blur-3xl" />

      <div className="relative z-10 mb-12 max-w-[720px] text-center">
        <div className="mb-5 text-[10px] font-bold uppercase tracking-[0.28em] text-obsidian/30">RedMuse Insight Studio</div>
        <h1 className="font-serif text-[44px] font-semibold leading-[1.08] tracking-[-0.05em] text-obsidian md:text-[58px]">
          洞见爆文规律，开启灵动创作
        </h1>
        <p className="mx-auto mt-5 max-w-[560px] text-[14px] leading-7 text-obsidian/45">
          输入你关注的品类、品牌或内容方向，RedMuse 会连接数据采集、智能分析与创作画布，生成可执行的爆文模型。
        </p>
      </div>

      <div className="relative z-20 w-full max-w-[780px]">
        <div className="rounded-[30px] border border-black/[0.05] bg-white px-4 py-3 shadow-[0_28px_80px_rgba(26,26,26,0.09)] transition-all duration-300">
          <div className="flex items-end gap-2">
            <button
              type="button"
              onClick={onToggleExpanded}
              disabled={disabled || busy}
              className={`flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full transition ${expanded ? "bg-obsidian text-papyrus" : "bg-fog text-obsidian/55 hover:bg-moss hover:text-obsidian"} disabled:cursor-not-allowed disabled:opacity-50`}
              title="配置分析参数"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 5v14M5 12h14" />
              </svg>
            </button>

            <textarea
              value={value}
              onChange={(event) => onValueChange(event.target.value)}
              disabled={disabled || busy}
              placeholder="请输入分析目标，例如：分析近半年防脱精华的视频类爆款笔记..."
              className="max-h-[128px] min-h-11 flex-1 resize-none bg-transparent px-3 py-3 text-[15px] leading-6 text-obsidian outline-none placeholder:text-obsidian/22 disabled:cursor-not-allowed disabled:opacity-60"
              rows={1}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  if (canSend) void onSubmit();
                }
              }}
            />

            <button
              type="button"
              onClick={() => void onSubmit()}
              disabled={!canSend}
              className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full bg-dew text-white transition hover:bg-[#9CBBC0] disabled:cursor-not-allowed disabled:bg-fog disabled:text-obsidian/20"
              title="开始分析"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M13 5l7 7-7 7M5 12h14" />
              </svg>
            </button>
          </div>

          {expanded ? (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-black/[0.04] pt-3">
              {PARAM_GROUPS.map((group) => (
                <ParamSelect
                  key={group.key}
                  label={group.label}
                  value={advanced[group.key]}
                  options={group.options}
                  onChange={(next) => onAdvancedChange({ ...advanced, [group.key]: next })}
                />
              ))}
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-black/[0.04] pt-3">
              {PARAM_GROUPS.map((group) => (
                <button
                  key={group.key}
                  type="button"
                  onClick={onToggleExpanded}
                  className="rounded-full bg-obsidian/[0.06] px-3 py-1.5 text-[12px] font-medium text-obsidian/60 transition hover:bg-moss hover:text-obsidian"
                >
                  {group.label}: {advanced[group.key]}
                </button>
              ))}
            </div>
          )}
        </div>

        {notice ? <div className="mt-4">{notice}</div> : null}

        <div className="mt-8 flex flex-wrap justify-center gap-3">
          {SUGGESTION_TAGS.slice(0, 3).map((text) => (
            <button
              key={text}
              type="button"
              onClick={() => void onPickSuggestion(text)}
              disabled={disabled || busy}
              className="rounded-full border border-black/[0.05] bg-white/75 px-6 py-3 text-[13px] text-obsidian/42 shadow-sm transition hover:bg-dew hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {text}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ParamSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex h-9 items-center gap-2 rounded-full bg-obsidian/[0.06] px-3 text-[12px] text-obsidian/55">
      <span className="font-semibold text-obsidian/36">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="bg-transparent font-semibold text-obsidian outline-none"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}
