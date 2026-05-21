"use client";

/**
 * 对话用户消息中的附件展示（对齐 Open WebUI UserMessage：先展示 files/图片，再展示文字）。
 * 图片通过带 Bearer 的 fetch 拉取二进制并转为 blob URL，因 img 标签无法直接附带 Authorization。
 */

import { useEffect, useState } from "react";

import { API_BASE } from "@/lib/api-client";
import { getAuthToken } from "@/lib/auth-storage";

export type AttachmentRecord = {
  file_id?: string;
  filename?: string;
  mime_type?: string;
  storage_subpath?: string;
  size_bytes?: number;
};

export function conversationUploadPreviewUrl(storageSubpath: string): string {
  return `${API_BASE}/conversations/uploads/content?${new URLSearchParams({ subpath: storageSubpath }).toString()}`;
}

function formatBytes(n: number | undefined): string {
  if (n == null || Number.isNaN(n)) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function AuthenticatedImagePreview({ subpath, name }: { subpath: string; name: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => {
    let dead = false;
    let objectUrl: string | null = null;
    void (async () => {
      try {
        const token = getAuthToken();
        const res = await fetch(conversationUploadPreviewUrl(subpath), {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          cache: "no-store",
        });
        if (!res.ok || dead) {
          if (!dead) setErr(true);
          return;
        }
        const blob = await res.blob();
        objectUrl = URL.createObjectURL(blob);
        if (!dead) setUrl(objectUrl);
      } catch {
        if (!dead) setErr(true);
      }
    })();
    return () => {
      dead = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [subpath]);

  if (err) {
    return <span className="text-[12px] text-obsidian/45">图片加载失败</span>;
  }
  if (!url) {
    return <div className="h-24 w-32 max-w-full animate-pulse rounded-lg bg-fog" aria-hidden />;
  }
  return (
    <img
      src={url}
      alt={name || "上传的图片"}
      className="max-h-56 max-w-[min(100%,28rem)] rounded-lg border border-black/[0.06] bg-white object-contain shadow-sm"
    />
  );
}

function FileAttachmentCard({ name, mime, size }: { name: string; mime: string; size?: number }) {
  return (
    <div className="inline-flex max-w-full items-center gap-2 rounded-xl border border-black/[0.08] bg-white px-3 py-2 text-left text-[12px] text-obsidian/75 shadow-sm">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-obsidian/[0.06] text-[10px] font-semibold text-obsidian/50">
        {name.includes(".") ? name.split(".").pop()?.slice(0, 4).toUpperCase() : "FILE"}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium text-obsidian/85">{name}</span>
        <span className="block truncate text-[11px] text-obsidian/45">
          {mime || "文件"}
          {size != null ? ` · ${formatBytes(size)}` : ""}
        </span>
      </span>
    </div>
  );
}

/** 用户气泡内：附件区域（在 Markdown 正文之上） */
export function ConversationUserAttachments({ items }: { items: AttachmentRecord[] }) {
  if (!items.length) return null;
  return (
    <div className="mb-2 flex w-full flex-col items-end gap-2">
      {items.map((raw, idx) => {
        const sub = String(raw.storage_subpath ?? "").trim();
        const name = String(raw.filename ?? "附件");
        const mime = String(raw.mime_type ?? "").toLowerCase();
        const key = String(raw.file_id ?? `${sub}-${idx}`);
        const isImage =
          mime.startsWith("image/") || /\.(png|jpe?g|gif|webp)$/i.test(name);
        if (!sub) {
          return (
            <div key={key} className="text-[11px] text-obsidian/40">
              附件缺少路径信息
            </div>
          );
        }
        if (isImage) {
          return (
            <div key={key} className="flex justify-end">
              <AuthenticatedImagePreview subpath={sub} name={name} />
            </div>
          );
        }
        return (
          <div key={key} className="flex justify-end">
            <FileAttachmentCard name={name} mime={mime} size={typeof raw.size_bytes === "number" ? raw.size_bytes : undefined} />
          </div>
        );
      })}
    </div>
  );
}
