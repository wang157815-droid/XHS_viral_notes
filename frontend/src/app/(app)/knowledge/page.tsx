"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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
      <div className="flex-1 overflow-y-auto bg-[#FAFAF8] px-8 py-6">
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

  return (
    <section className="rounded-[14px] border border-[#F0EEEB] bg-white p-6 shadow-sm">
      <header className="mb-4 flex items-center justify-between gap-3">
        <div>
          <div className="text-[15px] font-bold">RAG 文档</div>
          <div className="mt-1 text-[12px] text-[#A8A4A0]">
            支持 PDF / Word / Markdown / TXT，单文件 ≤ 10MB；上传后自动解析、分块并写入 pgvector。
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void onRefresh()}
            className="rounded-lg border border-[#E8E5E0] bg-white px-3 py-1.5 text-[12px] text-[#5A5550] hover:bg-[#F5F3F0]"
          >
            刷新
          </button>
          <button
            type="button"
            onClick={triggerPick}
            disabled={uploading || !canUpload}
            className="rounded-lg bg-[#FF4757] px-4 py-1.5 text-[13px] font-semibold text-white hover:bg-[#E8404F] disabled:cursor-not-allowed disabled:bg-[#FFB6BD]"
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
        <div className="py-12 text-center text-[13px] text-[#A8A4A0]">加载中...</div>
      ) : docs.length === 0 ? (
        <div className="rounded-[12px] border border-dashed border-[#E8E5E0] bg-[#FAFAF8] py-12 text-center text-[13px] text-[#A8A4A0]">
          还没有任何文档，点击右上角「+ 上传文档」开始构建 RAG 知识库。
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {docs.map((doc) => (
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
    if (status === "indexed") return { bg: "#F0FAF0", color: "#3D8C40", label: "已向量化" };
    if (status === "pending") return { bg: "#FFF8E6", color: "#8B6914", label: "向量化中" };
    if (status === "skipped") return { bg: "#F5F3F0", color: "#5A5550", label: "未向量化" };
    if (status === "failed") return { bg: "#FFF2F2", color: "#C62828", label: "向量化失败" };
    return null;
  })();

  return (
    <article className="flex flex-col gap-3 rounded-[12px] border border-[#F0EEEB] bg-white p-4 transition hover:border-[#E0DCD6] hover:shadow-sm">
      <div className="flex items-start gap-3">
        <span
          className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg text-[12px] font-bold"
          style={{ background: icon.bg, color: icon.color }}
        >
          {(doc.format || "?").toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[14px] font-semibold" title={doc.name}>
            {doc.name}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[12px] text-[#A8A4A0]">
            <span>{formatBytes(doc.size_bytes)}</span>
            <span>·</span>
            <span>{doc.chunks} 分块</span>
            {statusBadge ? (
              <span
                className="rounded px-1.5 py-0.5 text-[11px]"
                style={{ background: statusBadge.bg, color: statusBadge.color }}
              >
                {statusBadge.label}
              </span>
            ) : null}
          </div>
          {doc.vector_message ? (
            <div className="mt-1 truncate text-[11px] text-[#A8A4A0]" title={doc.vector_message}>
              {doc.vector_message}
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-[#F5F3F0] pt-3">
        <button
          type="button"
          onClick={onView}
          className="rounded border border-[#E8E5E0] bg-white px-3 py-1 text-[12px] text-[#5A5550] hover:bg-[#F5F3F0]"
        >
          查看分块
        </button>
        {canDelete ? (
          <button
            type="button"
            onClick={onDelete}
            className="rounded border border-[#FFD6D6] bg-[#FFF2F2] px-3 py-1 text-[12px] text-[#C62828] hover:bg-[#FFE0E0]"
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
      return { bg: "#FFF0EE", color: "#FF4757" };
    case "docx":
    case "doc":
      return { bg: "#F0F4FF", color: "#4A7AE8" };
    case "md":
    case "markdown":
      return { bg: "#F0FAF0", color: "#3D8C40" };
    default:
      return { bg: "#F5F3F0", color: "#8A8580" };
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
