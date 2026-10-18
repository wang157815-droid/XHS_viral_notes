"use client";

import { ListChecks } from "lucide-react";

/** 聊天输入框里的 Plan 模式开关：开启后 Agent 先列执行计划再执行。 */
export function PlanModeToggle({
  value,
  onChange,
  disabled,
}: {
  value: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={value}
      disabled={disabled}
      onClick={() => onChange(!value)}
      title={value ? "Plan 模式已开启：先列执行计划再执行" : "开启 Plan 模式：让 Agent 先列执行计划"}
      className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-3 text-[12px] font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
        value
          ? "border-dew bg-dew/15 text-obsidian"
          : "border-black/[0.08] bg-transparent text-obsidian/50 hover:bg-moss/40 hover:text-obsidian/80"
      }`}
    >
      <ListChecks className="size-3.5" aria-hidden="true" />
      Plan
    </button>
  );
}
