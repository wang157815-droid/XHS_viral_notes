"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { useSession } from "@/lib/session-context";

const NAV_PRIMARY = [
  {
    key: "workspace",
    label: "分析工作台",
    href: "/workspace",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
  },
  {
    key: "history",
    label: "历史中心",
    href: "/history",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
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
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
      </svg>
    ),
  },
];

const STATUS_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  valid: { bg: "#F0FAF0", color: "#3D8C40", label: "Cookie 有效" },
  expiring_soon: { bg: "#FFF8E6", color: "#8B6914", label: "即将过期" },
  expired: { bg: "#FFF0EE", color: "#FF4757", label: "Cookie 过期" },
  unknown: { bg: "#F5F3F0", color: "#8A8580", label: "状态未知" },
};

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, cookieHealth, logout } = useSession();
  const [collapsed, setCollapsed] = useState(false);

  const status = cookieHealth?.status ?? "unknown";
  const statusStyle = STATUS_STYLE[status] ?? STATUS_STYLE.unknown;

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem("redmuse_sidebar_collapsed");
      const initialCollapsed = raw === "1";
      setCollapsed(initialCollapsed);
      // 收起侧边栏后，把释放宽度优先给中间对话区（而不是画布）。
      document.documentElement.style.setProperty(
        "--workspace-chat-width",
        initialCollapsed ? "568px" : "380px",
      );
    } catch {}
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((v) => {
      const next = !v;
      try {
        window.localStorage.setItem("redmuse_sidebar_collapsed", next ? "1" : "0");
      } catch {}
      // 收起侧边栏时，中间对话区增宽；展开时恢复默认宽度。
      document.documentElement.style.setProperty(
        "--workspace-chat-width",
        next ? "568px" : "380px",
      );
      return next;
    });
  };

  /**
   * 新建分析：
   * - 已在 /workspace：追加 `?new=<timestamp>` 触发 workspace 监听重置
   * - 不在 /workspace：push 到 `/workspace?new=<timestamp>`
   */
  const handleNewAnalysis = () => {
    const next = `/workspace?new=${Date.now()}`;
    router.push(next);
  };

  return (
    <aside
      className={`sticky top-0 flex h-screen flex-shrink-0 flex-col border-r border-[#F0EEEB] bg-white ${
        collapsed ? "w-[72px]" : "w-[260px]"
      }`}
    >
      <div className={`flex items-center justify-between gap-3 border-b border-[#F0EEEB] pb-4 pt-5 ${collapsed ? "px-3" : "px-5"}`}>
        <div className={`flex items-center ${collapsed ? "gap-0" : "gap-3"}`}>
          <div className="flex h-9 w-9 items-center justify-center rounded-[10px] bg-[#FF4757] text-base font-bold text-white">
          R
        </div>
          {!collapsed ? <span className="text-[17px] font-bold">RedMuse</span> : null}
        </div>
        <button
          type="button"
          onClick={toggleCollapsed}
          title={collapsed ? "展开侧边栏" : "收起侧边栏"}
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-[#E8E5E0] bg-white text-[#8A8580] transition hover:bg-[#F5F3F0] hover:text-[#5A5550]"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            {collapsed ? <path d="M9 18l6-6-6-6" /> : <path d="M15 18l-6-6 6-6" />}
          </svg>
        </button>
      </div>

      <div className={`${collapsed ? "px-2" : "px-3"} pt-3`}>
        <button
          type="button"
          onClick={handleNewAnalysis}
          title="新建分析"
          className={`flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-[#FFD6CC] bg-[#FFF8F5] py-[10px] font-semibold text-[#FF4757] transition hover:border-solid hover:bg-[#FFF0EE] ${
            collapsed ? "text-[0px]" : "text-sm"
          }`}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
          {!collapsed ? "新建分析" : null}
        </button>
      </div>

      <nav className="flex-1 px-[10px] py-3">
        {NAV_PRIMARY.map((item) => (
          <NavLink key={item.key} item={item} pathname={pathname} collapsed={collapsed} />
        ))}
        <div className="mx-[14px] my-2 h-px bg-[#F0EEEB]" />
        {!collapsed ? (
          <div className="px-[14px] pb-1.5 pt-2 text-[11px] font-semibold tracking-[0.5px] text-[#A8A4A0]">
            管理
          </div>
        ) : null}
        {NAV_SECONDARY.map((item) => (
          <NavLink key={item.key} item={item} pathname={pathname} collapsed={collapsed} />
        ))}
      </nav>

      <div className={`border-t border-[#F0EEEB] ${collapsed ? "p-3" : "p-4"}`}>
        <div className="flex items-center justify-between">
          <div className="flex flex-1 items-center gap-2 p-1.5">
            <div className="flex h-[34px] w-[34px] items-center justify-center rounded-full bg-gradient-to-br from-[#FFB8B0] to-[#FF7A70] text-[14px] font-semibold text-white">
              {(user?.nickname || "小").charAt(0)}
            </div>
            {!collapsed ? (
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-semibold">
                  {user?.nickname || "小红书用户"}
                </div>
                <div className="text-[11px] text-[#A8A4A0]">
                  {user?.role === "admin" ? "管理员" : "普通用户"}
                </div>
              </div>
            ) : null}
          </div>
          <Link
            href="/settings"
            title="系统设置"
            prefetch
            className={`flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[7px] border transition ${
              pathname === "/settings"
                ? "border-[#FF4757] bg-[#FFF0EE] text-[#FF4757]"
                : "border-[#E8E5E0] bg-transparent text-[#A8A4A0] hover:bg-[#F5F3F0] hover:text-[#5A5550]"
            }`}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </Link>
        </div>
        <button
          type="button"
          onClick={() => void logout()}
          title="退出登录"
          className="mt-2 flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-[#A8A4A0] transition hover:bg-[#FAFAF8] hover:text-[#5A5550]"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
          </svg>
          {!collapsed ? "退出登录" : null}
        </button>
        <div
          className="mt-2 flex items-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] transition-colors duration-200"
          style={{ background: statusStyle.bg, color: statusStyle.color }}
        >
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: statusStyle.color }} />
          {!collapsed ? (
            <>
              {statusStyle.label}
              {cookieHealth?.saved_days != null ? (
                <span className="text-[#A8A4A0]">· {cookieHealth.saved_days} 天</span>
              ) : null}
            </>
          ) : (
            <span>{statusStyle.label.replace("Cookie", "")}</span>
          )}
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
      className={`mb-0.5 flex items-center gap-3 rounded-lg px-[14px] py-[11px] text-[14px] transition-colors duration-150 ${
        active ? "bg-[#FFF0EE] font-semibold text-[#FF4757]" : "text-[#5A5550] hover:bg-[#F5F3F0]"
      }`}
    >
      <span className="h-5 w-5 flex-shrink-0">{item.icon}</span>
      {!collapsed ? item.label : null}
    </Link>
  );
}
