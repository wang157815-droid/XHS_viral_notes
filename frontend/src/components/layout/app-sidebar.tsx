"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { deleteConversation, listConversations, patchConversationTitle } from "@/lib/conversation-api";
import type { ConversationSummary } from "@/lib/contracts";
import { roleLabelEn } from "@/lib/rbac";
import { useSession } from "@/lib/session-context";
import { SearchModal } from "./search-modal";

/**
 * 对齐参考 `RED-MUSE-\src\components\Sidebar.tsx` 布局；
 * 中部列表为「历史对话」（仅会话，不再展示分析任务列表）。
 */

type TimeBucket = "today" | "yesterday" | "this_week" | "last_week" | "three_month" | "older";

const BUCKET_ORDER: TimeBucket[] = ["today", "yesterday", "this_week", "last_week", "three_month", "older"];

const BUCKET_LABEL: Record<TimeBucket, string> = {
  today: "今天",
  yesterday: "昨天",
  this_week: "本周",
  last_week: "上周",
  three_month: "近三月",
  older: "更早",
};

const DAY_MS = 86_400_000;

function conversationTimeBucket(updatedAtIso: string): TimeBucket {
  const t = Date.parse(updatedAtIso);
  if (Number.isNaN(t)) return "older";
  const itemDay = new Date(t);
  itemDay.setHours(0, 0, 0, 0);
  const startToday = new Date();
  startToday.setHours(0, 0, 0, 0);
  const diffDays = Math.round((startToday.getTime() - itemDay.getTime()) / DAY_MS);
  if (diffDays < 0) return "today";
  if (diffDays === 0) return "today";
  if (diffDays === 1) return "yesterday";
  if (diffDays >= 2 && diffDays <= 6) return "this_week";
  if (diffDays >= 7 && diffDays <= 13) return "last_week";
  if (diffDays <= 90) return "three_month";
  return "older";
}

function HamburgerIcon() {
  return (
    <span className="flex w-4 flex-col justify-center gap-[5px]">
      <span className="h-[1.5px] w-full rounded-full bg-obsidian/70 transition-colors group-hover:bg-obsidian" />
      <span className="h-[1.5px] w-full rounded-full bg-obsidian/70 transition-colors group-hover:bg-obsidian" />
      <span className="h-[1.5px] w-full rounded-full bg-obsidian/70 transition-colors group-hover:bg-obsidian" />
    </span>
  );
}

function IcSearch({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}

function IcPlus({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function IcFlame({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg" className={className} fill="currentColor">
      <path d="M514 924.9l-3.1-0.2c-157-0.4-284.7-128.2-284.7-285.4 0-5.4 0-10.9 0.4-16.3 4.6-159.4 84.2-202.4 93.3-206.8 10-4.9 22.1-4.8 32.3 0.6 10.2 5.4 17.1 15.3 18.8 26.6 0.1 0.6 7.8 46.6 32.2 71.4 1.3-36.1 5-79.3 15-119.1 20.1-79.2 77.4-162.1 146-211.2 10.9-7.9 25.2-8.9 37.3-2.7 12 6.2 19.4 18.4 19.4 31.8 0 61.5 31 103.3 70.3 156.1 44.9 60.5 95.8 128.9 104.3 240.1v0.1c0.2 2.2 1.6 21.5 1.6 29.4 0 156.1-126.3 283.6-282.1 285.3l-1 0.3zM330.8 459.2c-17.4 13.2-58.9 56-62.1 165.8-0.3 5.2-0.3 9.8-0.3 14.4 0 134 109.1 243.1 243.1 243.1h0.6C645.9 882 754.7 773 754.7 639.3c0-4.8-0.9-18.6-1.4-26.3-7.6-99-54.6-162.2-96-218-38.3-51.4-74.4-100-78.4-168.7-56.6 44.3-103 113.6-119.8 179.8-12.4 49.3-14.2 105.7-14.5 143.8l-0.2 26-25.4-5.4c-61.6-13.3-82.5-84.8-88.2-111.3z m258-240.4z" />
      <path d="M529.2 831.6c-11 0-20.3-8.5-21.1-19.6-0.8-11.6 7.9-21.8 19.6-22.6 50.9-3.7 94.1-39.2 107.6-88.4 3.1-11.3 14.8-17.9 26-14.8 11.3 3.1 17.9 14.7 14.8 26-18.2 66.5-76.6 114.4-145.3 119.4h-1.6z" />
    </svg>
  );
}

function IcBarChart({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M18 20V10M12 20V4M6 20v-6" />
    </svg>
  );
}

function IcDatabase({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
      <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3" />
    </svg>
  );
}

function IcSettings({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function IcUser({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

function IcChevronRight({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}

function IcEdit({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M12 20h9M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z" />
    </svg>
  );
}

function IcTrash({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className={className}>
      <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14zM10 11v6M14 11v6" />
    </svg>
  );
}

export function AppSidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { user, cookieHealth, can, refreshCookie } = useSession();
  const canReadConversations = can("conversation.read_own");
  const canWriteConversations = can("conversation.write_own");

  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem("redmuse_sidebar_collapsed") === "1";
    } catch {
      return false;
    }
  });
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [convLoading, setConvLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");

  const surface = searchParams.get("surface");

  const activeMenuId = useMemo(() => {
    if (pathname.startsWith("/knowledge")) return "kb";
    if (pathname === "/discover") return "discovery";
    if (pathname === "/post-investment") return "data";
    if (pathname.startsWith("/workspace")) {
      if (surface === "hotspot") return "discovery";
      if (surface === "post_investment") return "data";
      return "insight";
    }
    return null;
  }, [pathname, surface]);

  const loadConversations = useCallback(async () => {
    if (!canReadConversations) return;
    setConvLoading(true);
    const res = await listConversations({ limit: 120 });
    if (res.ok) setConversations(res.data.items ?? []);
    setConvLoading(false);
  }, [canReadConversations]);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    const onRefresh = () => void loadConversations();
    window.addEventListener("redmuse-conversations-refresh", onRefresh);
    return () => window.removeEventListener("redmuse-conversations-refresh", onRefresh);
  }, [loadConversations]);

  useEffect(() => {
    document.documentElement.style.setProperty(
      "--workspace-chat-width",
      collapsed ? "568px" : "380px",
    );
  }, [collapsed]);

  const groupedConversations = useMemo(() => {
    const map = new Map<TimeBucket, ConversationSummary[]>();
    for (const b of BUCKET_ORDER) map.set(b, []);
    const sorted = [...conversations].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
    const seenIds = new Set<string>();
    for (const c of sorted) {
      if (seenIds.has(c.conversation_id)) continue;
      seenIds.add(c.conversation_id);
      map.get(conversationTimeBucket(c.updated_at))!.push(c);
    }
    return map;
  }, [conversations]);

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
      if (next) setSearchOpen(false);
      return next;
    });
  };

  const goWorkspaceNew = (surfaceParam: "insight" | "hotspot" | "post_investment") => {
    const qs =
      surfaceParam === "insight"
        ? `new=${Date.now()}&surface=insight`
        : `new=${Date.now()}&surface=${surfaceParam}`;
    router.push(`/workspace?${qs}`);
  };

  const openConversation = (conversationId: string) => {
    router.push(`/workspace?conversation=${encodeURIComponent(conversationId)}`);
  };

  const commitRename = async (id: string) => {
    const t = editDraft.trim();
    setEditingId(null);
    if (!t || !canWriteConversations) return;
    const res = await patchConversationTitle(id, t);
    if (res.ok) void loadConversations();
  };

  const handleDeleteConversation = async (id: string, title: string) => {
    if (!canWriteConversations) return;
    if (!window.confirm(`删除对话「${title || "未命名"}」？`)) return;
    const res = await deleteConversation(id);
    if (res.ok) void loadConversations();
  };

  const [cookieVerifyBusy, setCookieVerifyBusy] = useState(false);

  const cookieStatus = cookieHealth?.status;
  const cookieLabel =
    !cookieHealth ? "…" : cookieStatus === "valid"
      ? "有效"
      : cookieStatus === "expiring_soon"
        ? "临期"
        : cookieStatus === "expired"
          ? "异常"
          : "未知";

  const cookieDotClass =
    !cookieHealth || cookieStatus === "unknown"
      ? "bg-obsidian/35"
      : cookieStatus === "valid"
        ? "animate-pulse bg-[#4A5D4A]"
        : cookieStatus === "expiring_soon"
          ? "bg-[#8B6914]"
          : "bg-[#F28E82]";

  const cookieLineColor =
    cookieStatus === "valid"
      ? "text-[#4A5D4A]/60"
      : cookieStatus === "expiring_soon"
        ? "text-[#8B6914]"
        : cookieStatus === "expired"
          ? "text-[#F28E82]"
          : "text-obsidian/40";

  type MenuId = "insight" | "discovery" | "data" | "kb";
  const primaryItem = {
    id: "insight" as const,
    label: "开始洞察",
    icon: (p: { className?: string }) => <IcPlus {...p} />,
    onClick: () => goWorkspaceNew("insight"),
  };
  const secondaryItems: Array<{
    id: Exclude<MenuId, "insight">;
    label: string;
    icon: (p: { className?: string }) => ReactNode;
    onClick?: () => void;
    href?: string;
  }> = [
    { id: "discovery", label: "发现热点", icon: (p) => <IcFlame {...p} />, onClick: () => goWorkspaceNew("hotspot") },
    { id: "data", label: "投后数据", icon: (p) => <IcBarChart {...p} />, onClick: () => goWorkspaceNew("post_investment") },
    { id: "kb", label: "知识库", icon: (p) => <IcDatabase {...p} />, href: "/knowledge" },
  ];

  return (
    <aside
      className={`sticky top-0 flex h-screen shrink-0 flex-col overflow-hidden border-r border-fog bg-moss/20 transition-[width] duration-300 ease-in-out ${
        collapsed ? "w-[76px]" : "w-[259px] min-w-[240px] max-w-[400px]"
      }`}
    >
      {/* A. 顶栏：与右侧 ChatPanel header 同高（py-3 + 底部分隔线），主区入口整体低于「顶部线」 */}
      <div
        className={`flex min-h-[56px] flex-shrink-0 items-center py-3 transition-all ${
          collapsed ? "justify-center px-3 sm:px-4" : "justify-between px-3 sm:px-4"
        }`}
      >
        <button
          type="button"
          onClick={toggleCollapsed}
          title={collapsed ? "展开侧边栏" : "收起侧边栏"}
          className="group flex h-8 w-8 shrink-0 flex-col items-start justify-center gap-1 rounded-lg p-1 transition-all hover:bg-white/50"
        >
          <HamburgerIcon />
        </button>
        {!collapsed ? (
          <>
            <button
              type="button"
              title="搜索历史对话"
              onClick={() => setSearchOpen((v) => !v)}
              className="ml-auto flex h-8 w-8 items-center justify-center rounded-lg p-1 transition-all hover:bg-white/50"
            >
              <IcSearch className="h-4 w-4 text-obsidian/70" />
            </button>
          </>
        ) : null}
      </div>

      {searchOpen ? <SearchModal onClose={() => setSearchOpen(false)} /> : null}

      {/* B–E. 主入口：全宽「开始洞察」+ 下方三项（对齐参考产品布局） */}
      <div className={`mb-4 px-4 py-1.5 transition-all`}>
        {collapsed ? (
          <div className="flex flex-col items-center gap-1.5">
            {[primaryItem, ...secondaryItems].map((item) => {
              const isActive = activeMenuId === item.id;
              const icon = item.icon({
                className: `h-4 w-4 shrink-0 transition-colors ${isActive ? "text-dew" : "text-obsidian/45 group-hover:text-dew"}`,
              });
              const bubble = `flex h-9 w-9 items-center justify-center rounded-xl transition-all ${
                isActive ? "bg-white text-dew shadow-sm" : "text-obsidian/55 hover:bg-white/50 hover:text-obsidian"
              }`;
              if ("href" in item && item.href) {
                return (
                  <Link key={item.id} href={item.href} prefetch title={item.label} className={`group ${bubble}`}>
                    {icon}
                  </Link>
                );
              }
              return (
                <button key={item.id} type="button" title={item.label} onClick={item.onClick} className={`group ${bubble}`}>
                  {icon}
                </button>
              );
            })}
          </div>
        ) : (
          <div className="space-y-2">
            <button
              type="button"
              onClick={primaryItem.onClick}
              className={`group flex h-9 w-full items-center justify-start gap-1.5 rounded-full border border-black/[0.06] px-3 text-[13px] font-semibold tracking-tight shadow-sm transition-all ${
                activeMenuId === primaryItem.id
                  ? "bg-white text-dew ring-1 ring-dew/20"
                  : "bg-white text-obsidian/65 hover:border-black/[0.08] hover:bg-white hover:text-obsidian/80"
              }`}
            >
              {primaryItem.icon({
                className: `h-3.5 w-3.5 shrink-0 ${activeMenuId === primaryItem.id ? "text-dew" : "text-obsidian/50 group-hover:text-dew"}`,
              })}
              {primaryItem.label}
            </button>
            <div className="space-y-0.5">
              {secondaryItems.map((item) => {
                const isActive = activeMenuId === item.id;
                const iconNode = item.icon({
                  className: `h-4 w-4 shrink-0 transition-colors ${isActive ? "text-dew" : "text-obsidian/40 group-hover:text-dew"}`,
                });
                const inner = (
                  <>
                    {iconNode}
                    <span
                      className={`truncate text-[13px] font-bold tracking-tight ${isActive ? "text-dew" : "text-obsidian/70"}`}
                    >
                      {item.label}
                    </span>
                  </>
                );
                const rowClass = `group flex h-[34px] w-full items-center gap-3 rounded-xl px-3 transition-all ${
                  isActive ? "bg-white text-dew shadow-sm" : "text-obsidian/60 hover:bg-white/50 hover:text-obsidian"
                }`;
                if (item.href) {
                  return (
                    <Link key={item.id} href={item.href} prefetch className={rowClass}>
                      {inner}
                    </Link>
                  );
                }
                return (
                  <button key={item.id} type="button" onClick={item.onClick} className={rowClass}>
                    {inner}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* F. 历史对话（标题 + 分组 + 行内 hover 操作） */}
      <div
        className={`hover-scrollbar min-h-0 flex-1 overflow-y-auto px-4 transition-all ${
          collapsed ? "pointer-events-none opacity-0" : "opacity-100"
        }`}
      >
        {!collapsed && canReadConversations ? (
          <>
            <div className="mb-4 flex items-center justify-between px-4">
              <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-obsidian/30">历史对话</div>
            </div>
            {convLoading ? (
              <div className="px-4 py-2 text-[11px] text-obsidian/40">加载中…</div>
            ) : (
              <div className="space-y-6">
                {BUCKET_ORDER.map((bucket) => {
                  const list = groupedConversations.get(bucket) ?? [];
                  if (!list.length) return null;
                  return (
                    <div key={bucket}>
                      <div className="mb-2 px-4">
                        <span className="text-[11px] font-medium text-obsidian/40">{BUCKET_LABEL[bucket]}</span>
                      </div>
                      <div className="space-y-0.5">
                        {list.map((c) => {
                          const rawTitle = c.title?.trim() || c.last_message_preview?.trim() || "未命名对话";
                          const short = rawTitle.length > 22 ? `${rawTitle.slice(0, 22)}…` : rawTitle;
                          const isEditing = editingId === c.conversation_id;
                          const isActive = searchParams.get("conversation") === c.conversation_id;
                          return (
                            <div key={c.conversation_id} className="group relative flex w-full items-center gap-1">
                              {isEditing ? (
                                <input
                                  value={editDraft}
                                  onChange={(e) => setEditDraft(e.target.value)}
                                  onBlur={() => void commitRename(c.conversation_id)}
                                  onKeyDown={(e) => {
                                    if (e.key === "Enter") {
                                      e.preventDefault();
                                      void commitRename(c.conversation_id);
                                    }
                                    if (e.key === "Escape") {
                                      e.preventDefault();
                                      setEditingId(null);
                                    }
                                  }}
                                  className="min-w-0 flex-1 rounded-lg border border-dew/40 bg-white/90 px-3 py-2 text-[12px] text-obsidian outline-none"
                                  autoFocus
                                />
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => openConversation(c.conversation_id)}
                                  className={`min-w-0 flex-1 truncate rounded-lg px-4 py-2 text-left text-[12px] transition-colors ${
                                    isActive
                                      ? "bg-black/[0.06] font-medium text-obsidian"
                                      : "text-obsidian/70 hover:bg-white/50"
                                  }`}
                                >
                                  {short}
                                </button>
                              )}
                              {!isEditing ? (
                                <div className="flex shrink-0 items-center gap-2 pr-1 opacity-0 transition-opacity group-hover:opacity-100">
                                  {canWriteConversations ? (
                                    <button
                                      type="button"
                                      title="重命名"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        setEditingId(c.conversation_id);
                                        setEditDraft(c.title?.trim() || "");
                                      }}
                                      className="p-1 text-obsidian/30 transition-colors hover:text-dew"
                                    >
                                      <IcEdit className="h-3 w-3" />
                                    </button>
                                  ) : null}
                                  {canWriteConversations ? (
                                    <button
                                      type="button"
                                      title="删除对话"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        void handleDeleteConversation(c.conversation_id, rawTitle);
                                      }}
                                      className="p-1 text-obsidian/30 transition-colors hover:text-[#F28E82]"
                                    >
                                      <IcTrash className="h-3 w-3" />
                                    </button>
                                  ) : null}
                                  <IcChevronRight className="h-3 w-3 text-obsidian/30" />
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {!convLoading && conversations.length === 0 ? (
              <div className="px-4 py-2 text-[11px] text-obsidian/35">暂无对话记录</div>
            ) : null}
          </>
        ) : null}
        {!collapsed && !canReadConversations ? (
          <div className="px-4 py-2 text-[11px] text-obsidian/35">当前角色无法读取对话列表</div>
        ) : null}
      </div>

      {/* G. 底栏（对齐参考） */}
      <div className="mt-auto border-t border-fog bg-white/10 backdrop-blur-sm">
        <div
          className={`flex h-[65px] flex-col justify-center gap-1.5 transition-all ${collapsed ? "items-center px-0" : "pl-[22px] pr-[22px]"}`}
        >
          <div className={`flex w-full items-center ${collapsed ? "flex-col justify-center gap-2" : "justify-between"}`}>
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-full bg-dew text-white shadow-sm">
                <IcUser className="h-4 w-4" />
              </div>
              {!collapsed ? (
                <div className="flex min-w-0 max-w-[148px] flex-col leading-tight">
                  <span className="truncate text-[12px] font-bold tracking-tight text-obsidian">
                    {user?.nickname?.trim() || "Red Muse"}
                  </span>
                  <span className="truncate text-[9px] font-semibold tracking-[0.08em] text-obsidian/40">
                    {roleLabelEn(user?.role)}
                  </span>
                </div>
              ) : null}
            </div>
            <Link
              href="/settings"
              title="系统设置"
              prefetch
              className="group rounded-lg p-1.5 text-obsidian/30 transition-colors hover:bg-white/50 hover:text-obsidian"
            >
              <IcSettings className="h-3.5 w-3.5 transition-transform duration-300 group-hover:rotate-45" />
            </Link>
          </div>

          {!collapsed ? (
            <div
              className="flex items-center justify-between px-0.5"
              title={cookieHealth?.message || "点击「校验」重新检测小红书 Cookie"}
            >
              <div className="flex min-w-0 flex-1 items-center gap-1.5">
                <div className={`h-1 w-1 shrink-0 rounded-full ${cookieDotClass}`} />
                <span className={`truncate text-[9px] font-bold tracking-[0.05em] ${cookieLineColor}`}>
                  COOKIE {cookieLabel}
                </span>
              </div>
              <button
                type="button"
                disabled={cookieVerifyBusy}
                title="重新检测 Cookie 状态（会请求服务端校验）"
                onClick={async () => {
                  setCookieVerifyBusy(true);
                  try {
                    await refreshCookie(true);
                  } finally {
                    setCookieVerifyBusy(false);
                  }
                }}
                className="shrink-0 border-b border-transparent text-[9px] font-medium text-obsidian/30 transition-colors hover:border-dew hover:text-dew disabled:cursor-wait disabled:opacity-60"
              >
                {cookieVerifyBusy ? "校验中…" : "校验"}
              </button>
            </div>
          ) : null}
        </div>
      </div>
    </aside>
  );
}
