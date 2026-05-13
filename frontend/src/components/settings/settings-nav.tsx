"use client";

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

/**
 * 参考 RED-MUSE- SettingsView：左侧分区导航 + 底栏退出。
 * 右侧为单面板：点击项切换分区并更新 hash。
 */
export function SettingsNav({ entries, activeKey, onNavigate, onLogout }: SettingsNavProps) {
  return (
    <aside className="flex w-full shrink-0 flex-col border-b border-black/[0.06] bg-moss/10 md:w-[min(259px,32vw)] md:border-b-0 md:border-r md:border-black/[0.06]">
      <nav className="flex gap-2 overflow-x-auto px-3 py-3 md:flex-col md:space-y-2 md:overflow-visible md:px-4 md:py-8 md:pr-3">
        {entries.map((item) => {
          const active = activeKey === item.key;
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => onNavigate(item.key, item.targetId)}
              className={`flex h-[37px] shrink-0 items-center gap-3 rounded-full px-4 text-left text-[13px] font-bold transition md:w-full md:px-5 ${
                active
                  ? "border border-black/[0.05] bg-white text-obsidian shadow-md"
                  : "border border-transparent text-obsidian/40 hover:bg-white/50 hover:text-obsidian"
              }`}
            >
              <span
                className={`hidden h-1.5 w-1.5 shrink-0 rounded-full md:inline-block ${
                  active ? "bg-dew" : "bg-obsidian/20"
                }`}
              />
              <span className="truncate">{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="mt-auto border-t border-black/[0.06] p-4 md:px-5 md:pb-6 md:pt-4">
        <button
          type="button"
          onClick={() => void onLogout()}
          className="w-full rounded-full px-4 py-2.5 text-center text-[12px] font-bold uppercase tracking-[0.14em] text-obsidian/35 transition hover:bg-white/40 hover:text-[#c2716b]"
        >
          退出登录
        </button>
      </div>
    </aside>
  );
}
