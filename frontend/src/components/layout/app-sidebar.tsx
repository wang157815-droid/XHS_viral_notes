"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { roleLabel } from "@/lib/rbac";
import { useSession } from "@/lib/session-context";

const NAV_PRIMARY = [
  {
    key: "workspace",
    label: "分析工作台",
    href: "/workspace",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4 shrink-0">
        <path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
  },
  {
    key: "history",
    label: "历史中心",
    href: "/history",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4 shrink-0">
        <path d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
  },
];

const NAV_SECONDARY = [
  {
    key: "knowledge",
    label: "知识库",
    href: "/knowledge",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-4 w-4 shrink-0">
        <path d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
    ),
  },
];

/** 与参考 RED-MUSE- Sidebar 底部 Cookie 文案色系一致 */
const STATUS_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  valid: { bg: "rgba(74, 93, 74, 0.12)", color: "#4a5d4a", label: "Cookie 有效" },
  expiring_soon: { bg: "rgba(232, 168, 76, 0.15)", color: "#b8860b", label: "即将过期" },
  expired: { bg: "rgba(242, 142, 130, 0.14)", color: "#c2716b", label: "Cookie 过期" },
  unknown: { bg: "rgba(26, 26, 26, 0.06)", color: "#8a8580", label: "状态未知" },
};

function HamburgerIcon() {
  return (
    <span className="flex w-4 flex-col justify-center gap-[5px]">
      <span className="h-[1.5px] w-full rounded-full bg-obsidian/70 transition-colors group-hover:bg-obsidian" />
      <span className="h-[1.5px] w-full rounded-full bg-obsidian/70 transition-colors group-hover:bg-obsidian" />
      <span className="h-[1.5px] w-full rounded-full bg-obsidian/70 transition-colors group-hover:bg-obsidian" />
    </span>
  );
}

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, cookieHealth } = useSession();
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem("redmuse_sidebar_collapsed") === "1";
    } catch {
      return false;
    }
  });

  const status = cookieHealth?.status ?? "unknown";
  const statusStyle = STATUS_STYLE[status] ?? STATUS_STYLE.unknown;

  useEffect(() => {
    document.documentElement.style.setProperty(
      "--workspace-chat-width",
      collapsed ? "568px" : "380px",
    );
  }, [collapsed]);

  const toggleCollapsed = () => {
    setCollapsed((v) => {
      const next = !v;
      try {
        window.localStorage.setItem("redmuse_sidebar_collapsed", next ? "1" : "0");
      } catch {}
      document.documentElement.style.setProperty(
        "--workspace-chat-width",
        next ? "568px" : "380px",
      );
      return next;
    });
  };

  const handleNewAnalysis = () => {
    router.push(`/workspace?new=${Date.now()}`);
  };

  return (
    <aside
      className={`sticky top-0 flex h-screen flex-shrink-0 flex-col overflow-hidden border-r border-fog bg-moss/20 transition-[width] duration-300 ease-in-out ${
        collapsed ? "w-[76px]" : "w-[259px]"
      }`}
    >
      {/* 顶栏：折叠 + 品牌（对齐 RED-MUSE- 汉堡 + 右侧区域） */}
      <div
        className={`flex flex-shrink-0 items-center py-2 transition-all ${collapsed ? "justify-center px-2" : "justify-between px-4"}`}
      >
        <button
          type="button"
          onClick={toggleCollapsed}
          title={collapsed ? "展开侧边栏" : "收起侧边栏"}
          className="group flex h-8 w-8 shrink-0 flex-col items-center justify-center rounded-lg transition-colors hover:bg-white/50"
        >
          <HamburgerIcon />
        </button>
        {!collapsed ? (
          <span className="font-serif text-[15px] font-semibold tracking-tight text-obsidian/85">RedMuse</span>
        ) : null}
      </div>

      {/* 新建分析（主 CTA，雾蓝绿描边 + 纸张白底） */}
      <div className={`mb-2 transition-all ${collapsed ? "px-2" : "px-4"}`}>
        <button
          type="button"
          onClick={handleNewAnalysis}
          title="新建分析"
          className={`flex h-[38px] w-full items-center justify-center gap-2 rounded-xl border border-dew/40 bg-white/75 text-[13px] font-semibold text-obsidian shadow-[var(--redmuse-editorial-shadow)] transition hover:border-dew/60 hover:bg-white hover:shadow-[var(--redmuse-warm-shadow)] ${
            collapsed ? "px-0" : "px-4"
          }`}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="text-dew">
            <path d="M12 5v14M5 12h14" />
          </svg>
          {!collapsed ? "新建分析" : null}
        </button>
      </div>

      <nav className={`flex-1 space-y-1 overflow-y-auto py-1 ${collapsed ? "px-2" : "px-4"}`}>
        {NAV_PRIMARY.map((item) => (
          <NavLink key={item.key} item={item} pathname={pathname} collapsed={collapsed} />
        ))}
        {!collapsed ? (
          <div className="px-1 pb-1 pt-4 text-[10px] font-bold uppercase tracking-[0.12em] text-obsidian/30">管理</div>
        ) : (
          <div className="my-2 h-px bg-fog/80" />
        )}
        {NAV_SECONDARY.map((item) => (
          <NavLink key={item.key} item={item} pathname={pathname} collapsed={collapsed} />
        ))}
      </nav>

      {/* 底栏：用户 + 设置 + Cookie（对齐 RED-MUSE- border-fog / bg-white/10） */}
      <div className="mt-auto border-t border-fog bg-white/10 backdrop-blur-sm">
        <div
          className={`flex flex-col justify-center gap-2 py-3 transition-all ${collapsed ? "items-center px-1" : "px-[22px] py-3"}`}
        >
          <div className={`flex w-full items-center ${collapsed ? "flex-col gap-2" : "justify-between"}`}>
            <div className="flex min-w-0 items-center gap-2.5">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-dew text-[13px] font-semibold text-white shadow-sm">
                {(user?.nickname || "小").charAt(0)}
              </div>
              {!collapsed ? (
                <div className="min-w-0 leading-tight">
                  <div className="truncate text-[12px] font-bold tracking-tight text-obsidian">
                    {user?.nickname || "未命名用户"}
                  </div>
                  <div className="text-[10px] uppercase tracking-tight text-obsidian/40">{roleLabel(user?.role)}</div>
                </div>
              ) : null}
            </div>
            <Link
              href="/settings"
              title="系统设置"
              prefetch
              className={`rounded-lg p-1.5 text-obsidian/35 transition hover:bg-white/50 hover:text-obsidian ${
                pathname === "/settings" ? "bg-white/60 text-dew" : ""
              }`}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </Link>
          </div>

          <div
            className="flex items-center gap-1.5 rounded-lg px-1 py-1.5 text-[10px] font-semibold tracking-wide transition-colors"
            style={{ background: statusStyle.bg, color: statusStyle.color }}
            title={statusStyle.label}
          >
            <span className="h-1 w-1 shrink-0 rounded-full" style={{ background: statusStyle.color }} />
            {!collapsed ? (
              <>
                <span className="min-w-0 truncate">{statusStyle.label}</span>
                {cookieHealth?.saved_days != null ? (
                  <span className="shrink-0 text-obsidian/35">· {cookieHealth.saved_days} 天</span>
                ) : null}
              </>
            ) : (
              <span className="max-w-[52px] truncate text-[9px] font-bold leading-none">
                {statusStyle.label.replace("Cookie ", "")}
              </span>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}

function NavLink({
  item,
  pathname,
  collapsed,
}: {
  item: { key: string; label: string; href: string; icon: React.ReactNode };
  pathname: string;
  collapsed: boolean;
}) {
  const active = pathname === item.href || pathname.startsWith(item.href + "/");
  return (
    <Link
      href={item.href}
      prefetch
      title={item.label}
      className={`mb-0.5 flex h-[35px] items-center rounded-xl text-[13px] transition-all duration-150 ${
        collapsed ? "justify-center px-0" : "gap-3 px-4"
      } ${
        active
          ? "bg-white font-bold text-dew shadow-sm"
          : "font-medium text-obsidian/60 hover:bg-white/50 hover:text-obsidian"
      }`}
    >
      <span className={active ? "text-dew" : "text-obsidian/40 [&>svg]:stroke-current"}>{item.icon}</span>
      {!collapsed ? <span className={active ? "text-dew" : "text-obsidian/70"}>{item.label}</span> : null}
    </Link>
  );
}
