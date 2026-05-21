"use client";

import { useEffect, useMemo, useState } from "react";

import { apiGet } from "@/lib/api-client";

export interface KbDocRow {
  doc_id: string;
  name: string;
}

/** 光标前最后一个 `@` 起至光标为「提及片段」（中间无空白）时返回片段起点与查询串 */
export function getActiveKbMention(text: string, caret: number): { start: number; query: string } | null {
  const c = Math.min(Math.max(0, caret), text.length);
  const before = text.slice(0, c);
  const at = before.lastIndexOf("@");
  if (at < 0) return null;
  const frag = before.slice(at + 1);
  if (/\s/.test(frag)) return null;
  return { start: at, query: frag };
}

export function useKbDocuments(load: boolean) {
  const [docs, setDocs] = useState<KbDocRow[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!load) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      const res = await apiGet<{ items: KbDocRow[] }>("/knowledge/documents", { withAuth: true });
      if (cancelled) return;
      if (res.ok) setDocs(res.data.items ?? []);
      else setDocs([]);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  return { docs, loading };
}

export function KnowledgeMentionList({
  open,
  placement,
  loading,
  docs,
  query,
  selectedIds,
  onPick,
  canRead,
}: {
  open: boolean;
  placement: "above" | "below";
  loading: boolean;
  docs: KbDocRow[];
  query: string;
  selectedIds: Set<string>;
  onPick: (doc: KbDocRow) => void;
  canRead: boolean;
}) {
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return docs;
    return docs.filter(
      (d) => d.name.toLowerCase().includes(q) || d.doc_id.toLowerCase().includes(q),
    );
  }, [docs, query]);

  if (!open) return null;

  const pos = placement === "above" ? "bottom-full mb-1.5" : "top-full mt-1.5";

  return (
    <div
      className={`absolute ${pos} left-0 z-[45] w-full min-w-[220px] max-w-[min(100%,360px)]`}
      onMouseDown={(e) => e.preventDefault()}
      role="listbox"
      aria-label="知识库文档"
    >
      <div className="max-h-[min(40vh,280px)] overflow-hidden rounded-lg border border-black/[0.08] bg-white py-0.5 text-[13px] shadow-md">
        {!canRead ? (
          <p className="px-3 py-2.5 text-center text-[12px] text-obsidian/45">当前角色无权读取知识库列表。</p>
        ) : loading ? (
          <p className="px-3 py-2.5 text-center text-[12px] text-obsidian/45">加载中…</p>
        ) : filtered.length === 0 ? (
          <p className="px-3 py-2.5 text-center text-[12px] text-obsidian/45">
            {docs.length === 0 ? "暂无文档，请先到知识库页上传。" : "没有匹配的文档"}
          </p>
        ) : (
          <ul className="max-h-[min(40vh,260px)] overflow-y-auto px-0.5 py-0.5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {filtered.map((doc) => {
              const checked = selectedIds.has(doc.doc_id);
              return (
                <li key={doc.doc_id} className="px-0.5">
                  <button
                    type="button"
                    role="option"
                    onClick={() => onPick(doc)}
                    className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition ${checked ? "bg-dew/15 font-semibold text-obsidian" : "text-obsidian/75 hover:bg-moss/25"}`}
                  >
                    <span
                      className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[3px] border text-[10px] leading-none ${checked ? "border-dew bg-dew text-white" : "border-black/15"}`}
                    >
                      {checked ? "✓" : ""}
                    </span>
                    <span className="min-w-0 truncate">{doc.name}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
