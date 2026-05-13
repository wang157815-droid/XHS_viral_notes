"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PageHeader } from "@/components/layout/page-header";
import { apiGet, apiUpload } from "@/lib/api-client";
import { useSession } from "@/lib/session-context";

import { ChunksModal } from "./chunks-modal";

interface Doc {
  doc_id: string;
  name: string;
  format: string;
  size_bytes: number;
  chunks: number;
  vector_status?: string;
  vector_message?: string;
  uploaded_at?: string;
}

interface ListDocsResp {
  items: Doc[];
}

export default function KnowledgePage() {
  const { can } = useSession();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(false);
  const [viewingDoc, setViewingDoc] = useState<{ id: string; name: string } | null>(null);
  const canUpload = can("knowledge.write");
  const canDelete = can("knowledge.delete");

  const loadDocs = useCallback(async () => {
    setLoading(true);
    const res = await apiGet<ListDocsResp>("/knowledge/documents", { withAuth: true });
    if (res.ok) setDocs(res.data.items || []);
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

  const handleUpload = async (file: File) => {
    if (!canUpload) {
      window.alert("当前角色无权上传知识库文档");
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    const res = await apiUpload<Doc>("/knowledge/documents/upload", fd, { withAuth: true });
    if (!res.ok) {
      window.alert(`上传失败：${res.error.code} · ${res.error.message}`);
      return;
    }
    await loadDocs();
  };

  const handleDeleteDoc = async (docId: string) => {
    if (!window.confirm("确认删除该文档？已向量化的分块会一并清除。")) return;
    const { apiDelete } = await import("@/lib/api-client");
    const res = await apiDelete(`/knowledge/documents/${docId}`, { withAuth: true });
    if (!res.ok) {
      window.alert(`删除失败：${res.error.code} · ${res.error.message}`);
      return;
    }
    await loadDocs();
  };

  return (
    <>
      <PageHeader title="知识库 · RAG 文档" />
      <div className="relative flex-1 overflow-y-auto bg-[radial-gradient(circle_at_85%_8%,rgba(185,206,209,0.22),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.7),rgba(247,247,245,0.94))] px-6 py-6 lg:px-8">
        <div className="pointer-events-none absolute left-[8%] top-24 h-44 w-44 rounded-full bg-moss/45 blur-3xl" />
        <DocumentsView
          docs={docs}
          loading={loading}
          canUpload={canUpload}
          canDelete={canDelete}
          onUpload={handleUpload}
          onRefresh={loadDocs}
          onDelete={handleDeleteDoc}
          onView={(doc) => setViewingDoc({ id: doc.doc_id, name: doc.name })}
        />
      </div>
      {viewingDoc ? (
        <ChunksModal
          docId={viewingDoc.id}
          docName={viewingDoc.name}
          onClose={() => setViewingDoc(null)}
        />
      ) : null}
    </>
  );
}

function DocumentsView({
  docs,
  loading,
  canUpload,
  canDelete,
  onUpload,
  onRefresh,
  onDelete,
  onView,
}: {
  docs: Doc[];
  loading: boolean;
  canUpload: boolean;
  canDelete: boolean;
  onUpload: (file: File) => void | Promise<void>;
  onRefresh: () => void | Promise<void>;
  onDelete: (docId: string) => void | Promise<void>;
  onView: (doc: Doc) => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);

  const triggerPick = () => inputRef.current?.click();

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    try {
      await onUpload(file);
    } finally {
      setUploading(false);
    }
  };

  const sortedDocs = useMemo(
    () =>
      [...docs].sort((a, b) => {
        const ta = a.uploaded_at ? Date.parse(a.uploaded_at) : 0;
        const tb = b.uploaded_at ? Date.parse(b.uploaded_at) : 0;
        return tb - ta;
      }),
    [docs],
  );

  return (
    <section className="canvas-card relative mx-auto max-w-[1180px] border border-black/[0.04] bg-gradient-to-br from-white/92 via-white/84 to-moss/[0.08] p-6 shadow-[0_22px_60px_rgba(26,26,26,0.075)] backdrop-blur-xl">
      <header className="mb-4 flex items-center justify-between gap-3">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-[0.24em] text-obsidian/28">Knowledge Base</div>
          <div className="mt-1 font-serif text-[24px] font-semibold tracking-[-0.03em] text-obsidian">RAG 文档</div>
          <div className="mt-1 text-[12px] leading-5 text-obsidian/42">
            支持 PDF / Word / Markdown / TXT，单文件 ≤ 10MB；上传后自动解析、分块并写入 pgvector。
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void onRefresh()}
            className="rounded-full border border-black/[0.06] bg-white/75 px-4 py-2 text-[12px] font-semibold text-obsidian/50 shadow-sm transition hover:bg-moss hover:text-obsidian"
          >
            刷新
          </button>
          <button
            type="button"
            onClick={triggerPick}
            disabled={uploading || !canUpload}
            className="rounded-full border border-obsidian bg-obsidian px-4 py-2 text-[12px] font-semibold text-papyrus shadow-sm transition hover:bg-obsidian/86 disabled:cursor-not-allowed disabled:border-fog disabled:bg-fog disabled:text-obsidian/24"
          >
            {uploading ? "上传中..." : canUpload ? "+ 上传文档" : "无上传权限"}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.doc,.docx,.md,.markdown,.txt"
            className="hidden"
            onChange={(e) => void onPick(e)}
          />
        </div>
      </header>

      {loading ? (
        <div className="py-12 text-center text-[13px] text-obsidian/32">加载中...</div>
      ) : docs.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-[24px] border border-dashed border-black/[0.09] bg-gradient-to-b from-fog/55 to-white/35 py-16 text-center">
          <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.25" className="mb-3 text-obsidian/16">
            <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
            <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
          </svg>
          <p className="max-w-sm text-[14px] text-obsidian/42">还没有任何文档资产</p>
          <p className="mt-1 text-[12px] text-obsidian/30">点击「+ 上传文档」添加 PDF / Word / Markdown / TXT，自动分块并向量化。</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {sortedDocs.map((doc) => (
            <DocCard
              key={doc.doc_id}
              doc={doc}
              canDelete={canDelete}
              onDelete={() => void onDelete(doc.doc_id)}
              onView={() => onView(doc)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function DocCard({
  doc,
  canDelete,
  onDelete,
  onView,
}: {
  doc: Doc;
  canDelete: boolean;
  onDelete: () => void;
  onView: () => void;
}) {
  const icon = getDocIconStyle(doc.format);
  const status = (doc.vector_status || "").toLowerCase();
  const statusBadge = (() => {
    if (status === "indexed") return { bg: "#E8F0E8", color: "#49715A", label: "已向量化" };
    if (status === "pending") return { bg: "#FFF8E6", color: "#8B6914", label: "向量化中" };
    if (status === "skipped") return { bg: "#F5F3F0", color: "#5A5550", label: "未向量化" };
    if (status === "failed") return { bg: "#FFF5F3", color: "#9A5558", label: "向量化失败" };
    return null;
  })();

  const uploadedLabel = formatUploadedAt(doc.uploaded_at);

  return (
    <article className="group relative flex flex-col gap-3 overflow-hidden rounded-[26px] border border-black/[0.05] bg-gradient-to-br from-white/96 via-white/88 to-moss/[0.06] p-4 pl-[18px] shadow-[var(--redmuse-soft-shadow)] transition hover:-translate-y-0.5 hover:shadow-[0_22px_56px_rgba(26,26,26,0.08)] before:pointer-events-none before:absolute before:left-0 before:top-3 before:bottom-3 before:w-[3px] before:rounded-full before:bg-gradient-to-b before:from-dew/90 before:to-moss/50">
      <div className="flex items-start gap-3">
        <span
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-2xl text-[12px] font-bold shadow-[0_6px_16px_rgba(26,26,26,0.06)] ring-1 ring-black/[0.04]"
          style={{ background: icon.bg, color: icon.color }}
        >
          {(doc.format || "?").toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate font-serif text-[15px] font-semibold tracking-tight text-obsidian" title={doc.name}>
            {doc.name}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px] text-obsidian/34">
            {uploadedLabel ? <span>{uploadedLabel}</span> : null}
            {uploadedLabel ? <span className="text-obsidian/20">·</span> : null}
            <span>{formatBytes(doc.size_bytes)}</span>
            <span>·</span>
            <span>{doc.chunks} 分块</span>
            {statusBadge ? (
              <span
                className="rounded-full px-2 py-0.5 text-[11px] font-semibold"
                style={{ background: statusBadge.bg, color: statusBadge.color }}
              >
                {statusBadge.label}
              </span>
            ) : null}
          </div>
          {doc.vector_message ? (
            <div className="mt-1 truncate text-[11px] text-obsidian/34" title={doc.vector_message}>
              {doc.vector_message}
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-black/[0.04] pt-3">
        <button
          type="button"
          onClick={onView}
          className="rounded-full border border-black/[0.06] bg-white/75 px-3.5 py-1.5 text-[12px] font-semibold text-obsidian/50 hover:bg-moss hover:text-obsidian"
        >
          查看分块
        </button>
        {canDelete ? (
          <button
            type="button"
            onClick={onDelete}
            className="rounded-full border border-[#E8CFC8] bg-[#FFF5F3] px-3.5 py-1.5 text-[12px] font-semibold text-[#9A5558] hover:bg-[#FFF0EE]"
          >
            删除
          </button>
        ) : null}
      </div>
    </article>
  );
}

function getDocIconStyle(format: string): { bg: string; color: string } {
  switch ((format || "").toLowerCase()) {
    case "pdf":
      return { bg: "#FFF5F3", color: "#9A5558" };
    case "docx":
    case "doc":
      return { bg: "#F0F4FF", color: "#4A7AE8" };
    case "md":
    case "markdown":
      return { bg: "#E8F0E8", color: "#49715A" };
    default:
      return { bg: "#F0F0F0", color: "rgba(26,26,26,0.48)" };
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatUploadedAt(iso?: string): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(t);
}
