"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";

import { listConversations } from "@/lib/conversation-api";
import type { ConversationSummary } from "@/lib/contracts";

interface SearchModalProps {
  onClose: () => void;
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch {
    return "";
  }
}

function formatUpdateTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  } catch {
    return "";
  }
}

export function SearchModal({ onClose }: SearchModalProps) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);

  const [mounted, setMounted] = useState(false);
  const [query, setQuery] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [results, setResults] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [sortDesc, setSortDesc] = useState(true);
  const [lastUpdate, setLastUpdate] = useState(() => new Date().toISOString());

  useEffect(() => { setMounted(true); }, []);

  // 防抖
  useEffect(() => {
    const id = window.setTimeout(() => setDebouncedQ(query.trim()), 300);
    return () => window.clearTimeout(id);
  }, [query]);

  const loadResults = useCallback(async () => {
    setLoading(true);
    const res = await listConversations({ limit: 60, keyword: debouncedQ || undefined });
    if (res.ok) {
      const items = res.data.items ?? [];
      setResults(sortDesc ? items : [...items].reverse());
      setLastUpdate(new Date().toISOString());
    }
    setLoading(false);
  }, [debouncedQ, sortDesc]);

  useEffect(() => { void loadResults(); }, [loadResults]);

  // 聚焦输入框
  useEffect(() => { inputRef.current?.focus(); }, []);

  // ESC 关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const handleCardClick = (conv: ConversationSummary) => {
    router.push(`/workspace?conversation_id=${conv.conversation_id}`);
    onClose();
  };

  const modal = (
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/20 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="relative mx-6 flex w-full max-w-[860px] flex-col overflow-hidden rounded-[20px] border border-black/[0.08] bg-[#F7F7F5] shadow-[0_32px_80px_rgba(26,26,26,0.18)]">

        {/* ── 搜索输入行 ── */}
        <div className="px-5 pt-5 pb-3">
          <div className="flex items-center gap-3 rounded-full border-2 border-obsidian/80 bg-white px-4 py-2.5 shadow-sm">
            <svg className="h-4 w-4 shrink-0 text-obsidian/50" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
              <circle cx="11" cy="11" r="8" /><path strokeLinecap="round" d="M21 21l-4.35-4.35" />
            </svg>
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索任务标题或内容…"
              className="min-w-0 flex-1 bg-transparent text-[14px] text-obsidian outline-none placeholder:text-obsidian/30"
            />
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-black/[0.10] bg-black/[0.05] px-2 py-0.5 font-mono text-[11px] text-obsidian/45 transition hover:bg-black/[0.10]"
            >
              ESC
            </button>
          </div>
        </div>

        {/* ── 区域标题 ── */}
        <div className="flex items-center justify-between px-6 py-2">
          <span className="text-[12px] font-semibold text-obsidian/45">
            {query ? "搜索结果" : "最近任务"}
          </span>
          <button
            type="button"
            onClick={() => setSortDesc((v) => !v)}
            className="text-[12px] text-obsidian/40 transition hover:text-obsidian/70"
          >
            {sortDesc ? "降序排列" : "升序排列"}
          </button>
        </div>

        {/* ── 2 列结果网格 ── */}
        <div className="relative overflow-hidden">
          {/* 右侧滚动条轨道装饰 */}
          <div className="pointer-events-none absolute right-0 top-0 h-full w-3 bg-gradient-to-l from-[#F7F7F5]" />
          <div className="grid max-h-[440px] grid-cols-2 gap-2 overflow-y-auto px-5 pb-3">
            {loading && results.length === 0 ? (
              <div className="col-span-2 py-10 text-center text-[13px] text-obsidian/30">加载中…</div>
            ) : results.length === 0 ? (
              <div className="col-span-2 py-10 text-center text-[13px] text-obsidian/30">暂无匹配结果</div>
            ) : (
              results.map((conv) => (
                <button
                  key={conv.conversation_id}
                  type="button"
                  onClick={() => handleCardClick(conv)}
                  className="group flex flex-col gap-1.5 rounded-xl bg-white/80 px-5 py-4 text-left transition hover:bg-white hover:shadow-sm"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="line-clamp-1 text-[14px] font-semibold text-obsidian group-hover:text-dew">
                      {conv.title || "（无标题）"}
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-obsidian/30">
                      {formatTime(conv.updated_at)}
                    </span>
                  </div>
                  {(conv.last_message_preview || conv.summary) ? (
                    <span className="line-clamp-1 text-[12px] text-obsidian/45">
                      {conv.last_message_preview || conv.summary}
                    </span>
                  ) : null}
                </button>
              ))
            )}
          </div>
        </div>

        {/* ── 底栏 ── */}
        <div className="flex items-center justify-between border-t border-black/[0.06] bg-white/50 px-5 py-3">
          <div className="flex items-center gap-4 text-[11px] text-obsidian/35">
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-dew" />
              {results.length} 条记录
            </span>
            <span className="flex items-center gap-1">
              <svg className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="10" /><path d="M12 6v6l4 2" />
              </svg>
              最近更新：{formatUpdateTime(lastUpdate)}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-[12px] font-semibold text-obsidian/50 transition hover:text-obsidian"
          >
            关闭
          </button>
        </div>

      </div>
    </div>
  );

  if (!mounted) return null;
  return createPortal(modal, document.body);
}
