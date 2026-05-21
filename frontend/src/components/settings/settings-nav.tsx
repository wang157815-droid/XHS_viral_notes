"use client";

import type { ReactNode } from "react";

export type SettingsNavKey =
  | "account"
  | "xhs"
  | "ai"
  | "crawler"
  | "observability"
  | "users"
  | "maintenance";

export interface SettingsNavEntry {
  key: SettingsNavKey;
  label: string;
  targetId: string;
}

interface SettingsNavProps {
  entries: SettingsNavEntry[];
  activeKey: SettingsNavKey;
  onNavigate: (key: SettingsNavKey, targetId: string) => void;
  onLogout: () => void | Promise<void>;
}

const iconClass = "h-[18px] w-[18px] shrink-0";

function IcUser({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

function IcSmartphone({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <rect x="5" y="2" width="14" height="20" rx="2" ry="2" />
      <path d="M12 18h.01" />
    </svg>
  );
}

function IcCpu({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <rect x="9" y="9" width="6" height="6" />
      <path d="M9 2v2M15 2v2M9 20v2M15 20v2M22 9h-2M22 15h-2M4 9H2M4 15H2" />
    </svg>
  );
}

function IcClock({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 3" />
    </svg>
  );
}

function IcActivity({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  );
}

function IcUsers({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M23 21v-2a4 4 0 00-3-3.87" />
      <path d="M16 3.13a4 4 0 010 7.75" />
    </svg>
  );
}

function IcWrench({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z" />
    </svg>
  );
}

function IcLogOut({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" />
      <path d="M16 17l5-5-5-5M21 12H9" />
    </svg>
  );
}

function navIconForKey(key: SettingsNavKey): ReactNode {
  const cn = iconClass;
  switch (key) {
    case "account":
      return <IcUser className={cn} />;
    case "xhs":
      return <IcSmartphone className={cn} />;
    case "ai":
      return <IcCpu className={cn} />;
    case "crawler":
      return <IcClock className={cn} />;
    case "observability":
      return <IcActivity className={cn} />;
    case "users":
      return <IcUsers className={cn} />;
    case "maintenance":
      return <IcWrench className={cn} />;
    default:
      return null;
  }
}

/**
 * 参考 RED-MUSE- SettingsView：左侧分区导航 + 底栏退出。
 * 右侧为单面板：点击项切换分区并更新 hash。
 */
export function SettingsNav({ entries, activeKey, onNavigate, onLogout }: SettingsNavProps) {
  return (
    <aside className="flex w-full shrink-0 flex-col border-b border-black/[0.06] bg-transparent md:w-[min(259px,32vw)] md:border-b-0 md:border-r md:border-black/[0.06]">
      <nav className="flex gap-2 overflow-x-auto px-3 py-3 md:flex-col md:space-y-2 md:overflow-visible md:px-4 md:py-8 md:pr-3">
        {entries.map((item) => {
          const active = activeKey === item.key;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onNavigate(item.key, item.targetId)}
              className={`group flex h-[37px] shrink-0 items-center gap-3 rounded-full px-4 text-left text-[13px] font-bold transition md:w-full md:px-5 ${
                active
                  ? "border border-black/[0.05] bg-white text-obsidian shadow-md"
                  : "border border-transparent text-obsidian/40 hover:bg-white/50 hover:text-obsidian"
              }`}
            >
              <span className={active ? "text-obsidian" : "text-obsidian/35 group-hover:text-obsidian/55"}>
                {navIconForKey(item.key)}
              </span>
              <span className="truncate">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto border-t border-black/[0.06] p-4 md:px-5 md:pb-6 md:pt-4">
        <button
          type="button"
          onClick={() => void onLogout()}
          className="flex w-full items-center justify-center gap-2 rounded-full border border-black/[0.06] bg-white px-4 py-2.5 text-[12px] font-bold tracking-[0.08em] text-[#c2716b] shadow-sm transition hover:bg-[#FFF5F4] hover:text-[#b35a52]"
        >
          <IcLogOut className="h-[18px] w-[18px] shrink-0" />
          <span>退出登录</span>
        </button>
      </div>
    </aside>
  );
}
