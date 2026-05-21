"use client";

/**
 * Composer 内：已选本地文件的附件展示。
 * 对齐主流 AI 产品（Open-WebUI / ChatGPT）：
 * - 图片：小缩略图预览
 * - 文档：横向文件卡片（图标 + 文件名 + 大小 + 解析状态）
 * - 后台静默上传，无显式"上传中"文字，仅用 subtle loading 指示
 */

import { useEffect, useMemo } from "react";

export type ComposerFileDraft = {
  id: string;
  file: File;
  uploadStatus?: "uploading" | "done" | "error";
  uploadError?: string;
  uploaded?: {
    file_id: string;
    filename: string;
    mime_type: string;
    size_bytes: number;
    storage_subpath: string;
  };
  /** 后端上传接口返回的解析元数据（预解析后的字数/页数等） */
  parsed?: {
    word_count?: number;
    pages?: number;
    format?: string;
  } | null;
};

/** 与后端 conversations 上传白名单一致 */
export function isAllowedComposerAttachmentFile(file: File): boolean {
  const name = file.name.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|pdf|txt|md|markdown|doc|docx)$/i.test(name)) return true;
  if (file.type.startsWith("image/")) return true;
  if (file.type === "application/pdf") return true;
  if (file.type.startsWith("text/")) return true;
  return false;
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileTypeLabel(file: File): string {
  const name = file.name.toLowerCase();
  if (/\.pdf$/i.test(name) || file.type === "application/pdf") return "PDF";
  if (/\.(doc|docx)$/i.test(name)) return "DOC";
  if (/\.(md|markdown|txt)$/i.test(name)) return "TXT";
  if (file.type.startsWith("image/") || /\.(png|jpe?g|gif|webp)$/i.test(name)) return "IMG";
  return "FILE";
}

/** 文件类型图标 */
function FileTypeIcon({ type, className = "" }: { type: string; className?: string }) {
  const cls = `shrink-0 ${className}`;
  if (type === "PDF") {
    return (
      <svg className={cls} width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <polyline points="14 2 14 8 20 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M9 13h6M9 17h3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  }
  if (type === "DOC") {
    return (
      <svg className={cls} width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <polyline points="14 2 14 8 20 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M16 13H8M16 17H8M10 9H8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  }
  if (type === "TXT") {
    return (
      <svg className={cls} width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <polyline points="14 2 14 8 20 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M16 13H8M16 17H8M10 9H8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg className={cls} width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <polyline points="13 2 13 9 20 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** 图片缩略图（blob URL） */
function ImageThumb({
  draft,
  disabled,
  onRemove,
}: {
  draft: ComposerFileDraft;
  disabled?: boolean;
  onRemove: () => void;
}) {
  const preview = useMemo(() => URL.createObjectURL(draft.file), [draft.file]);
  useEffect(() => () => URL.revokeObjectURL(preview), [preview]);

  return (
    <div className="group relative h-[68px] w-[68px] shrink-0 overflow-hidden rounded-xl border border-black/[0.08] bg-[#F5F3F0] shadow-sm">
      <img src={preview} alt="" className="h-full w-full object-cover" />
      {draft.uploadStatus === "uploading" ? (
        <div className="absolute inset-0 flex items-center justify-center bg-black/20">
          <span className="h-5 w-5 animate-spin rounded-full border-2 border-white border-t-transparent" />
        </div>
      ) : null}
      {!disabled ? (
        <button
          type="button"
          onClick={onRemove}
          className="absolute right-0.5 top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-black/55 text-[12px] leading-none text-white opacity-0 shadow transition hover:bg-black/70 group-hover:opacity-100"
          aria-label="移除附件"
        >
          ×
        </button>
      ) : null}
    </div>
  );
}

/** 文档文件卡片（横向） */
function DocCard({
  draft,
  disabled,
  onRemove,
}: {
  draft: ComposerFileDraft;
  disabled?: boolean;
  onRemove: () => void;
}) {
  const typeLabel = fileTypeLabel(draft.file);
  const sizeText = formatFileSize(draft.file.size);
  const isUploading = draft.uploadStatus === "uploading";
  const isError = draft.uploadStatus === "error";
  const wordCount = draft.parsed?.word_count;

  return (
    <div
      className={`group relative flex min-w-0 max-w-[240px] flex-1 items-center gap-2.5 rounded-xl border px-3 py-2.5 shadow-sm transition ${
        isError
          ? "border-red-200 bg-red-50"
          : "border-black/[0.08] bg-white hover:border-black/[0.14]"
      }`}
    >
      {/* 文件图标 + loading 环 */}
      <div className="relative shrink-0">
        <span className={isError ? "text-red-400" : "text-obsidian/50"}>
          <FileTypeIcon type={typeLabel} />
        </span>
        {isUploading ? (
          <span className="absolute -right-1 -top-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-dew">
            <span className="h-2 w-2 animate-spin rounded-full border-[1.5px] border-white border-t-transparent" />
          </span>
        ) : null}
      </div>

      {/* 文件名 + 元信息 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-[13px] font-medium leading-tight text-obsidian/85" title={draft.file.name}>
          {draft.file.name}
        </span>
        <span className="mt-0.5 flex items-center gap-1.5 text-[11px] leading-none text-obsidian/40">
          <span>{sizeText}</span>
          {wordCount ? (
            <>
              <span className="h-3 w-px bg-obsidian/15" />
              <span className="text-dew">{wordCount.toLocaleString()} 字</span>
            </>
          ) : null}
          {isError ? <span className="text-red-400">上传失败</span> : null}
        </span>
      </div>

      {/* 删除按钮 */}
      {!disabled ? (
        <button
          type="button"
          onClick={onRemove}
          className="shrink-0 rounded-full p-0.5 text-obsidian/30 opacity-0 transition hover:bg-black/[0.06] hover:text-obsidian/60 group-hover:opacity-100"
          aria-label="移除附件"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path d="M18 6L6 18M6 6l12 12" />
          </svg>
        </button>
      ) : null}
    </div>
  );
}

export function ComposerDraftThumbnailsRow({
  drafts,
  disabled,
  onRemove,
  showDropHint = false,
}: {
  drafts: ComposerFileDraft[];
  uploading?: boolean;
  disabled?: boolean;
  onRemove: (id: string) => void;
  /** 为 true 时提示可拖入添加（与外层 drop 区域一致） */
  showDropHint?: boolean;
}) {
  if (!drafts.length) return null;

  const images = drafts.filter(
    (d) => d.file.type.startsWith("image/") || /\.(png|jpe?g|gif|webp)$/i.test(d.file.name),
  );
  const docs = drafts.filter(
    (d) => !(d.file.type.startsWith("image/") || /\.(png|jpe?g|gif|webp)$/i.test(d.file.name)),
  );

  return (
    <div className="flex w-full flex-col gap-2 border-b border-black/[0.06] pb-2.5 pt-1">
      <div className="flex items-center gap-2 text-[11px] text-obsidian/38">
        <span>已选 {drafts.length} 个文件</span>
        {showDropHint ? <span>· 可拖入添加</span> : null}
      </div>

      {/* 图片缩略图行 */}
      {images.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {images.map((d) => (
            <ImageThumb key={d.id} draft={d} disabled={disabled} onRemove={() => onRemove(d.id)} />
          ))}
        </div>
      ) : null}

      {/* 文档卡片行 */}
      {docs.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {docs.map((d) => (
            <DocCard key={d.id} draft={d} disabled={disabled} onRemove={() => onRemove(d.id)} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
