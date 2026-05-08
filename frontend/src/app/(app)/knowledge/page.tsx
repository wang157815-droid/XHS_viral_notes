"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ChunksModal } from "./chunks-modal";
import { PageHeader } from "@/components/layout/page-header";
import { apiDelete, apiGet, apiPost, apiPut, apiUpload } from "@/lib/api-client";
import { useSession } from "@/lib/session-context";

interface Domain {
  domain_id: string;
  name: string;
  enabled: boolean;
  keywords: string[];
  rule_count: number;
  priority: "high" | "medium" | "low" | string;
}

interface KnowledgeDocument {
  doc_id: string;
  name: string;
  size_bytes?: number;
  format: "pdf" | "docx" | "md" | "txt" | string;
  uploaded_at?: string;
  chunks?: number;
  keywords?: string[];
  domains?: string[];
  vector_status?: "indexed" | "skipped" | "failed" | string;
  vector_message?: string;
}

type Tab = "domains" | "docs";

export default function KnowledgePage() {
  const { user } = useSession();
  const isAdmin = user?.role === "admin";
  const [tab, setTab] = useState<Tab>("domains");
  const [domains, setDomains] = useState<Domain[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [editing, setEditing] = useState<Partial<Domain> | null>(null);
  const [toast, setToast] = useState<{ type: "ok" | "err"; message: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [viewing, setViewing] = useState<{ doc_id: string; name: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadDomains = useCallback(async () => {
    const res = await apiGet<{ items: Domain[] }>("/knowledge/domains", { withAuth: true });
    if (res.ok) setDomains(res.data.items ?? []);
  }, []);

  const loadDocuments = useCallback(async () => {
    const res = await apiGet<{ items: KnowledgeDocument[] }>("/knowledge/documents", { withAuth: true });
    if (res.ok) setDocuments(res.data.items ?? []);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadDomains();
    void loadDocuments();
  }, [loadDomains, loadDocuments]);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  const handleCreateOrUpdate = useCallback(
    async (domain: Partial<Domain>) => {
      if (!domain.name?.trim()) {
        setToast({ type: "err", message: "请填写领域名称" });
        return;
      }
      const body = {
        name: domain.name.trim(),
        keywords: domain.keywords ?? [],
        priority: domain.priority ?? "medium",
        enabled: domain.enabled ?? true,
      };
      const res = domain.domain_id
        ? await apiPut<Domain>(`/knowledge/domains/${encodeURIComponent(domain.domain_id)}`, body, {
            withAuth: true,
          })
        : await apiPost<Domain>("/knowledge/domains", body, { withAuth: true });
      if (!res.ok) {
        setToast({ type: "err", message: `保存失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setEditing(null);
      setToast({ type: "ok", message: domain.domain_id ? "领域已更新" : "领域已创建" });
      await loadDomains();
    },
    [loadDomains],
  );

  const handleDeleteDomain = useCallback(
    async (domainId: string) => {
      if (typeof window !== "undefined" && !window.confirm("确定删除此领域？")) return;
      const res = await apiDelete(`/knowledge/domains/${encodeURIComponent(domainId)}`, { withAuth: true });
      if (!res.ok) {
        setToast({ type: "err", message: `删除失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setToast({ type: "ok", message: "已删除" });
      await loadDomains();
    },
    [loadDomains],
  );

  const handleToggleDomain = useCallback(
    async (domain: Domain) => {
      await handleCreateOrUpdate({ ...domain, enabled: !domain.enabled });
    },
    [handleCreateOrUpdate],
  );

  const handleDeleteDoc = useCallback(
    async (docId: string) => {
      if (typeof window !== "undefined" && !window.confirm("确定删除该文档？")) return;
      const res = await apiDelete(`/knowledge/documents/${encodeURIComponent(docId)}`, { withAuth: true });
      if (!res.ok) {
        setToast({ type: "err", message: `删除失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      setToast({ type: "ok", message: "已删除" });
      await loadDocuments();
    },
    [loadDocuments],
  );

  const handleUploadFile = useCallback(async () => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChosen = useCallback(
    async (ev: React.ChangeEvent<HTMLInputElement>) => {
      const file = ev.target.files?.[0];
      ev.target.value = ""; // 清空，允许同名文件再次触发 change
      if (!file) return;

      const sizeMb = file.size / 1024 / 1024;
      if (sizeMb > 10) {
        setToast({ type: "err", message: `文件过大（${sizeMb.toFixed(1)} MB），上限 10 MB` });
        return;
      }

      setUploading(true);
      const form = new FormData();
      form.append("file", file);
      form.append("domain_ids", "");
      const res = await apiUpload<KnowledgeDocument>("/knowledge/documents/upload", form, {
        withAuth: true,
      });
      setUploading(false);

      if (!res.ok) {
        setToast({ type: "err", message: `上传失败：${res.error.code} · ${res.error.message}` });
        return;
      }
      const note =
        res.data.vector_status === "indexed"
          ? `已入库 ${res.data.chunks ?? 0} 个分块`
          : res.data.vector_message || "已登记（未向量化）";
      setToast({ type: "ok", message: `上传成功：${res.data.name} · ${note}` });
      await loadDocuments();
    },
    [loadDocuments],
  );

  return (
    <>
      <PageHeader
        title="知识库管理"
        actions={
          tab === "domains" ? (
            <button
              type="button"
              onClick={() => setEditing({ name: "", keywords: [], priority: "medium", enabled: true })}
              className="rounded-lg bg-[#FF4757] px-4 py-2 text-[13px] font-semibold text-white transition hover:bg-[#E8404F]"
            >
              + 新增领域
            </button>
          ) : null
        }
      />

      <main className="flex-1 overflow-y-auto px-8 py-6">
        {toast ? (
          <div
            className={`pointer-events-auto fixed right-6 top-6 z-50 rounded-md border px-3 py-2 text-[12px] shadow-lg ${
              toast.type === "ok"
                ? "border-[#D7EAD9] bg-[#F0FAF1] text-[#3D8C40]"
                : "border-[#FDD8D8] bg-[#FFF2F2] text-[#C62828]"
            }`}
          >
            {toast.message}
          </div>
        ) : null}

        <div className="mb-6 flex gap-0 border-b-2 border-[#F0EEEB]">
          <TabButton active={tab === "domains"} onClick={() => setTab("domains")}>
            领域知识
          </TabButton>
          <TabButton active={tab === "docs"} onClick={() => setTab("docs")}>
            RAG 文档
          </TabButton>
        </div>

        {tab === "domains" ? (
          <DomainsView
            domains={domains}
            onEdit={setEditing}
            onDelete={handleDeleteDomain}
            onToggle={handleToggleDomain}
          />
        ) : (
          <DocumentsView
            documents={documents}
            uploading={uploading}
            isAdmin={isAdmin}
            onDelete={handleDeleteDoc}
            onUpload={handleUploadFile}
            onViewChunks={(doc) => setViewing({ doc_id: doc.doc_id, name: doc.name })}
          />
        )}

        {viewing && isAdmin ? (
          <ChunksModal
            docId={viewing.doc_id}
            docName={viewing.name}
            onClose={() => setViewing(null)}
          />
        ) : null}

        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.doc,.md,.markdown,.txt"
          onChange={handleFileChosen}
          className="hidden"
        />

        {editing ? (
          <DomainEditor
            initial={editing}
            onCancel={() => setEditing(null)}
            onSubmit={handleCreateOrUpdate}
          />
        ) : null}
      </main>
    </>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`-mb-[2px] cursor-pointer border-b-2 px-5 py-3 text-[14px] transition-colors duration-150 ${
        active
          ? "border-[#FF4757] font-semibold text-[#FF4757]"
          : "border-transparent text-[#8A8580] hover:text-[#5A5550]"
      }`}
    >
      {children}
    </button>
  );
}

function DomainsView({
  domains,
  onEdit,
  onDelete,
  onToggle,
}: {
  domains: Domain[];
  onEdit: (d: Partial<Domain>) => void;
  onDelete: (id: string) => void;
  onToggle: (d: Domain) => void;
}) {
  if (!domains.length) {
    return (
      <div className="rounded-xl border border-dashed border-[#E8E5E0] bg-white py-20 text-center text-[14px] text-[#A8A4A0]">
        暂无领域，点击右上角「+ 新增领域」创建
      </div>
    );
  }
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
      {domains.map((d) => (
        <DomainCard key={d.domain_id} domain={d} onEdit={onEdit} onDelete={onDelete} onToggle={onToggle} />
      ))}
    </div>
  );
}

function DomainCard({
  domain,
  onEdit,
  onDelete,
  onToggle,
}: {
  domain: Domain;
  onEdit: (d: Partial<Domain>) => void;
  onDelete: (id: string) => void;
  onToggle: (d: Domain) => void;
}) {
  return (
    <div className="rounded-[12px] border border-[#F0EEEB] bg-white p-5 transition hover:border-[#FFD6CC]">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[15px] font-bold">{domain.name}</div>
        <span
          className={`rounded px-2 py-0.5 text-[11px] ${
            domain.enabled ? "bg-[#F0FAF0] text-[#3D8C40]" : "bg-[#F5F3F0] text-[#A8A4A0]"
          }`}
        >
          {domain.enabled ? "已启用" : "已禁用"}
        </span>
      </div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {(domain.keywords ?? []).map((kw) => (
          <span key={kw} className="rounded-md bg-[#F5F3F0] px-2.5 py-0.5 text-[12px] text-[#5A5550]">
            {kw}
          </span>
        ))}
        {!(domain.keywords ?? []).length ? (
          <span className="text-[12px] text-[#A8A4A0]">暂无关键词</span>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-4 text-[12px] text-[#A8A4A0]">
        <span>{domain.rule_count} 条规则</span>
        <span>优先级: {priorityLabel(domain.priority)}</span>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => onEdit(domain)}
          className="rounded-md border border-[#E8E5E0] bg-transparent px-3 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
        >
          编辑
        </button>
        <button
          type="button"
          onClick={() => onToggle(domain)}
          className="rounded-md border border-[#E8E5E0] bg-transparent px-3 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
        >
          {domain.enabled ? "禁用" : "启用"}
        </button>
        <button
          type="button"
          onClick={() => onDelete(domain.domain_id)}
          className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-3 py-1.5 text-[12px] text-[#E04040] transition hover:bg-[#FFE8E0]"
        >
          删除
        </button>
      </div>
    </div>
  );
}

function priorityLabel(p: string): string {
  if (p === "high") return "高";
  if (p === "low") return "低";
  return "中";
}

function DocumentsView({
  documents,
  uploading,
  isAdmin,
  onDelete,
  onUpload,
  onViewChunks,
}: {
  documents: KnowledgeDocument[];
  uploading: boolean;
  isAdmin: boolean;
  onDelete: (id: string) => void;
  onUpload: () => void;
  onViewChunks: (doc: KnowledgeDocument) => void;
}) {
  return (
    <div>
      <button
        type="button"
        onClick={onUpload}
        disabled={uploading}
        className="mb-6 flex w-full cursor-pointer flex-col items-center justify-center rounded-[14px] border-2 border-dashed border-[#E8E5E0] bg-white p-10 transition hover:border-[#FFD6CC] hover:bg-[#FFF8F5] disabled:cursor-not-allowed disabled:opacity-70"
      >
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-[14px] bg-[#FFF0EE] text-[#FF4757]">
          {uploading ? (
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-[#FF4757] border-t-transparent" />
          ) : (
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 16V4m0 0l-4 4m4-4l4 4M4 14v4a2 2 0 002 2h12a2 2 0 002-2v-4" />
            </svg>
          )}
        </div>
        <div className="text-[14px] text-[#5A5550]">
          {uploading ? "正在上传并解析..." : "点击上传文件"}
        </div>
        <div className="mt-1 text-[12px] text-[#A8A4A0]">
          支持 PDF、Word、Markdown、TXT，单个文件不超过 10MB
        </div>
      </button>

      {!documents.length ? (
        <div className="rounded-xl border border-dashed border-[#E8E5E0] bg-white py-12 text-center text-[14px] text-[#A8A4A0]">
          暂无文档
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {documents.map((doc) => (
            <DocCard
              key={doc.doc_id}
              doc={doc}
              canViewChunks={isAdmin}
              onDelete={onDelete}
              onViewChunks={() => onViewChunks(doc)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DocCard({
  doc,
  canViewChunks,
  onDelete,
  onViewChunks,
}: {
  doc: KnowledgeDocument;
  canViewChunks: boolean;
  onDelete: (id: string) => void;
  onViewChunks: () => void;
}) {
  const iconStyle = getDocIconStyle(doc.format);
  const vecStyle = (() => {
    switch (doc.vector_status) {
      case "indexed":
        return { bg: "#F0FAF0", color: "#3D8C40", label: "已向量化" };
      case "failed":
        return { bg: "#FFF0EE", color: "#E04040", label: "向量化失败" };
      case "skipped":
      default:
        return { bg: "#FFF8E6", color: "#8B6914", label: "未向量化" };
    }
  })();

  return (
    <div className="flex items-center gap-4 rounded-[12px] border border-[#F0EEEB] bg-white px-5 py-[18px]">
      <div
        className="flex h-[42px] w-[42px] flex-shrink-0 items-center justify-center rounded-[10px] text-[13px] font-bold uppercase"
        style={{ background: iconStyle.bg, color: iconStyle.color }}
      >
        {String(doc.format || "DOC").slice(0, 3)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[14px] font-semibold">{doc.name}</span>
          <span
            className="rounded px-2 py-0.5 text-[11px]"
            style={{ background: vecStyle.bg, color: vecStyle.color }}
            title={doc.vector_message}
          >
            {vecStyle.label}
          </span>
        </div>
        <div className="mt-1 flex flex-wrap gap-3 text-[12px] text-[#A8A4A0]">
          {doc.size_bytes ? <span>{formatBytes(doc.size_bytes)}</span> : null}
          {doc.uploaded_at ? <span>上传于 {doc.uploaded_at.slice(0, 10)}</span> : null}
          {doc.chunks ? <span>{doc.chunks} 个分块</span> : null}
          {doc.keywords?.length ? (
            <span>关键词：{doc.keywords.slice(0, 5).join("、")}</span>
          ) : null}
        </div>
      </div>
      <div className="flex gap-1.5">
        {(doc.domains ?? []).map((d) => (
          <span key={d} className="rounded bg-[#FFF0EE] px-2 py-0.5 text-[11px] text-[#FF4757]">
            {d}
          </span>
        ))}
      </div>
      <div className="flex gap-1.5">
        {canViewChunks ? (
          <button
            type="button"
            onClick={onViewChunks}
            title="仅管理员可查看"
            className="rounded-md border border-[#E8E5E0] bg-transparent px-3 py-1.5 text-[12px] text-[#5A5550] transition hover:bg-[#F5F3F0]"
          >
            查看分块
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => onDelete(doc.doc_id)}
          className="rounded-md border border-[#FFD6CC] bg-[#FFF5F3] px-3 py-1.5 text-[12px] text-[#E04040] transition hover:bg-[#FFE8E0]"
        >
          删除
        </button>
      </div>
    </div>
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

function DomainEditor({
  initial,
  onCancel,
  onSubmit,
}: {
  initial: Partial<Domain>;
  onCancel: () => void;
  onSubmit: (d: Partial<Domain>) => void;
}) {
  const [name, setName] = useState(initial.name ?? "");
  const [priority, setPriority] = useState(initial.priority ?? "medium");
  const [enabled, setEnabled] = useState(initial.enabled ?? true);
  const [keywords, setKeywords] = useState<string[]>(initial.keywords ?? []);
  const [kwInput, setKwInput] = useState("");

  const addKw = () => {
    const v = kwInput.trim();
    if (!v) return;
    if (!keywords.includes(v)) setKeywords([...keywords, v]);
    setKwInput("");
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30" onClick={onCancel}>
      <div
        className="w-[480px] rounded-[14px] border border-[#F0EEEB] bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 text-[16px] font-bold">
          {initial.domain_id ? "编辑领域" : "新增领域"}
        </div>
        <div className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-[12px] font-semibold text-[#5A5550]">领域名称</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="h-10 w-full rounded-lg border border-[#E8E5E0] bg-[#FAFAF8] px-3 text-[13px] outline-none focus:border-[#FF4757]"
              placeholder="例如：美妆护肤"
            />
          </label>
          <div>
            <span className="mb-1 block text-[12px] font-semibold text-[#5A5550]">关键词</span>
            <div className="flex gap-2">
              <input
                value={kwInput}
                onChange={(e) => setKwInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addKw();
                  }
                }}
                className="h-9 flex-1 rounded-lg border border-[#E8E5E0] bg-[#FAFAF8] px-3 text-[13px] outline-none focus:border-[#FF4757]"
                placeholder="回车添加"
              />
              <button
                type="button"
                onClick={addKw}
                className="rounded-lg border border-[#E8E5E0] bg-white px-3 text-[12px] text-[#5A5550] hover:bg-[#F5F3F0]"
              >
                添加
              </button>
            </div>
            {keywords.length ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {keywords.map((kw) => (
                  <span
                    key={kw}
                    className="flex items-center gap-1 rounded-md bg-[#F5F3F0] px-2.5 py-0.5 text-[12px] text-[#5A5550]"
                  >
                    {kw}
                    <button
                      type="button"
                      onClick={() => setKeywords(keywords.filter((k) => k !== kw))}
                      className="text-[#A8A4A0] hover:text-[#FF4757]"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
          </div>
          <label className="flex items-center gap-3 text-[13px]">
            <span className="w-16 text-[12px] font-semibold text-[#5A5550]">优先级</span>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as Domain["priority"])}
              className="h-9 flex-1 cursor-pointer rounded-lg border border-[#E8E5E0] bg-white px-3 text-[13px] outline-none"
            >
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-[13px] text-[#5A5550]">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="accent-[#FF4757]"
            />
            启用
          </label>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-[#E8E5E0] bg-white px-4 py-2 text-[13px] text-[#5A5550] hover:bg-[#F5F3F0]"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() =>
              onSubmit({ ...initial, name, priority: priority as Domain["priority"], enabled, keywords })
            }
            className="rounded-lg bg-[#FF4757] px-4 py-2 text-[13px] font-semibold text-white hover:bg-[#E8404F]"
          >
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
