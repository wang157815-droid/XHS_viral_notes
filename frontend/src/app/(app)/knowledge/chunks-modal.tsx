"use client";

import { useCallback, useEffect, useState } from "react";

import { apiGet, apiPost } from "@/lib/api-client";

interface Chunk {
  chunk_id: string;
  chunk_index: number | null;
  text: string;
  metadata?: Record<string, unknown>;
  distance?: number | null;
  similarity?: number | null;
}

interface ChunksResponse {
  doc_id: string;
  name: string;
  format: string;
  source: "chroma" | "reparsed" | string;
  total: number;
  chunks: Chunk[];
}

interface SearchResponse {
  query: string;
  top_k: number;
  hits: Chunk[];
  total: number;
}

interface ChunksModalProps {
  docId: string;
  docName: string;
  onClose: () => void;
}

export function ChunksModal({ docId, docName, onClose }: ChunksModalProps) {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<ChunksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [queryText, setQueryText] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  const loadChunks = useCallback(async () => {
    setLoading(true);
    setError(null);
    const res = await apiGet<ChunksResponse>(
      `/knowledge/documents/${encodeURIComponent(docId)}/chunks?limit=200`,
      { withAuth: true },
    );
    if (!res.ok) {
      setError(`${res.error.code} · ${res.error.message}`);
      setData(null);
    } else {
      setData(res.data);
    }
    setLoading(false);
  }, [docId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadChunks();
  }, [loadChunks]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  const handleSearch = async () => {
    const q = queryText.trim();
    if (!q) return;
    setSearching(true);
    setSearchError(null);
    const res = await apiPost<SearchResponse, { query: string; top_k: number; doc_id?: string }>(
      "/knowledge/search",
      { query: q, top_k: 5, doc_id: docId },
      { withAuth: true },
    );
    setSearching(false);
    if (!res.ok) {
      setSearchError(`${res.error.code} · ${res.error.message}`);
      setSearchResult(null);
    } else {
      setSearchResult(res.data);
    }
  };

  const sourceBadge = (() => {
    if (!data) return null;
    if (data.source === "chroma") {
      return { bg: "#F0FAF0", color: "#3D8C40", label: "来源：ChromaDB 向量库" };
    }
    if (data.source === "reparsed") {
      return { bg: "#FFF8E6", color: "#8B6914", label: "来源：磁盘重新解析（未向量化）" };
    }
    return { bg: "#F5F3F0", color: "#5A5550", label: `来源：${data.source}` };
  })();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="flex h-[80vh] w-[880px] max-w-[92vw] flex-col overflow-hidden rounded-[14px] border border-[#F0EEEB] bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b border-[#F0EEEB] px-6 py-4">
          <div className="min-w-0">
            <div className="truncate text-[16px] font-bold">{docName}</div>
            <div className="mt-1 flex items-center gap-2 text-[12px] text-[#A8A4A0]">
              <span>文档分块</span>
              {data ? <span>共 {data.total} 块</span> : null}
              {sourceBadge ? (
                <span
                  className="rounded px-2 py-0.5 text-[11px]"
                  style={{ background: sourceBadge.bg, color: sourceBadge.color }}
                >
                  {sourceBadge.label}
                </span>
              ) : null}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-md border border-[#E8E5E0] bg-white text-[#8A8580] transition hover:bg-[#F5F3F0] hover:text-[#5A5550]"
            aria-label="关闭"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12M18 6l-12 12" />
            </svg>
          </button>
        </header>

        <div className="border-b border-[#F0EEEB] bg-[#FAFAF8] px-6 py-3">
          <div className="flex items-center gap-2">
            <input
              value={queryText}
              onChange={(e) => setQueryText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleSearch();
                }
              }}
              placeholder="测试检索：输入查询文本，回车搜索该文档的 Top 5 分块"
              className="h-9 flex-1 rounded-lg border border-[#E8E5E0] bg-white px-3 text-[13px] outline-none focus:border-[#FF4757]"
            />
            <button
              type="button"
              onClick={() => void handleSearch()}
              disabled={searching || !queryText.trim()}
              className="rounded-lg bg-[#FF4757] px-4 py-1.5 text-[13px] font-semibold text-white transition hover:bg-[#E8404F] disabled:cursor-not-allowed disabled:bg-[#FFB6BD]"
            >
              {searching ? "检索中..." : "检索"}
            </button>
            {searchResult ? (
              <button
                type="button"
                onClick={() => {
                  setSearchResult(null);
                  setSearchError(null);
                }}
                className="rounded-lg border border-[#E8E5E0] bg-white px-3 py-1.5 text-[12px] text-[#5A5550] hover:bg-[#F5F3F0]"
              >
                显示全部分块
              </button>
            ) : null}
          </div>
          {searchError ? (
            <div className="mt-2 text-[12px] text-[#C62828]">检索失败：{searchError}</div>
          ) : null}
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading ? (
            <div className="py-20 text-center text-[13px] text-[#A8A4A0]">加载中...</div>
          ) : error ? (
            <div className="rounded-md border border-[#FDD8D8] bg-[#FFF2F2] px-4 py-3 text-[13px] text-[#C62828]">
              {error}
            </div>
          ) : searchResult ? (
            <SearchHits query={searchResult.query} hits={searchResult.hits} />
          ) : data ? (
            <ChunkList chunks={data.chunks} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ChunkList({ chunks }: { chunks: Chunk[] }) {
  if (!chunks.length) {
    return <div className="py-10 text-center text-[13px] text-[#A8A4A0]">该文档无分块</div>;
  }
  return (
    <div className="space-y-3">
      {chunks.map((c) => (
        <ChunkCard key={c.chunk_id} chunk={c} />
      ))}
    </div>
  );
}

function SearchHits({ query, hits }: { query: string; hits: Chunk[] }) {
  return (
    <div>
      <div className="mb-3 text-[12px] text-[#8A8580]">
        查询 <span className="font-semibold text-[#3a3936]">「{query}」</span>
        &nbsp;·&nbsp; 命中 {hits.length} 条
      </div>
      {!hits.length ? (
        <div className="rounded-md border border-dashed border-[#E8E5E0] bg-white px-4 py-8 text-center text-[13px] text-[#A8A4A0]">
          没有匹配的分块
        </div>
      ) : (
        <div className="space-y-3">
          {hits.map((c) => (
            <ChunkCard key={c.chunk_id} chunk={c} highlightQuery={query} />
          ))}
        </div>
      )}
    </div>
  );
}

function ChunkCard({ chunk, highlightQuery }: { chunk: Chunk; highlightQuery?: string }) {
  return (
    <div className="rounded-lg border border-[#F0EEEB] bg-white px-4 py-3">
      <div className="mb-1.5 flex items-center gap-2 text-[11px] text-[#A8A4A0]">
        <span className="rounded bg-[#F5F3F0] px-2 py-0.5 font-mono text-[10px] text-[#5A5550]">
          #{chunk.chunk_index ?? "-"}
        </span>
        <span className="font-mono">{chunk.chunk_id}</span>
        {typeof chunk.similarity === "number" ? (
          <span
            className="rounded px-1.5 py-0.5 text-[10px]"
            style={{
              background: chunk.similarity > 0.6 ? "#F0FAF0" : "#FFF8E6",
              color: chunk.similarity > 0.6 ? "#3D8C40" : "#8B6914",
            }}
          >
            相似度 {(chunk.similarity * 100).toFixed(1)}%
          </span>
        ) : null}
      </div>
      <div className="whitespace-pre-wrap break-words text-[13px] leading-[1.7] text-[#2D2A26]">
        {highlightQuery ? highlightText(chunk.text, highlightQuery) : chunk.text}
      </div>
    </div>
  );
}

function highlightText(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const parts = text.split(new RegExp(`(${escapeRegex(query)})`, "gi"));
  return parts.map((p, i) =>
    p.toLowerCase() === query.toLowerCase() ? (
      <mark key={i} className="rounded bg-[#FFF2B8] px-0.5 text-[#3a3936]">
        {p}
      </mark>
    ) : (
      <span key={i}>{p}</span>
    ),
  );
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
