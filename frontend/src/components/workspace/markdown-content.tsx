"use client";

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/** /api/v1/... 相对路径补全为后端完整 URL；不安全协议返回空串 */
function resolveHref(href: string): string {
  if (/^(https?:\/\/|mailto:)/i.test(href)) return href;
  if (href.startsWith("/api/")) {
    const base = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8100/api/v1").replace(/\/api\/v1\/?$/, "");
    return `${base}${href}`;
  }
  if (/^\/[^/]/.test(href)) return href;
  if (href.startsWith("#")) return href;
  return "";
}

/**
 * 带 JWT 认证的文件下载：适用于后端 API 导出链接。
 * fetch 时携带 Authorization 头，响应以 Blob 触发浏览器下载。
 */
async function downloadWithAuth(url: string, filename: string): Promise<void> {
  try {
    const { getAuthToken } = await import("@/lib/auth-storage");
    const token = getAuthToken();
    const res = await fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) {
      console.error(`[download] 请求失败 ${res.status}`, await res.text().catch(() => ""));
      alert(`下载失败（${res.status}），请确认已登录`);
      return;
    }
    const blob = await res.blob();
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(blobUrl);
  } catch (err) {
    console.error("[download] 异常", err);
    alert("下载失败，请稍后重试");
  }
}

function downloadFilename(rawHref: string): string {
  const rawSuffix = rawHref.split("/").pop() ?? "";
  if (rawSuffix === "comment_excel") return "评论分析报告.xlsx";
  if (rawSuffix === "comment_md") return "评论舆情分析报告.md";
  return rawSuffix.endsWith(".xlsx") ? rawSuffix : rawSuffix || "report.xlsx";
}

const LINK_CLASS = "text-[#c2716b] underline decoration-dew/50 underline-offset-2";

function MarkdownLink({ href, children }: { href?: string; children?: ReactNode }) {
  const rawHref = href ?? "";
  const safeHref = resolveHref(rawHref);
  if (!safeHref) return <>{children}</>;
  if (rawHref.startsWith("/api/")) {
    return (
      <button
        type="button"
        onClick={() => void downloadWithAuth(safeHref, downloadFilename(rawHref))}
        className={`cursor-pointer border-0 bg-transparent p-0 font-inherit text-inherit ${LINK_CLASS}`}
      >
        {children}
      </button>
    );
  }
  return (
    <a href={safeHref} target="_blank" rel="noreferrer" className={LINK_CLASS}>
      {children}
    </a>
  );
}

const components: Components = {
  h1: ({ children }) => <h3 className="mt-3 text-[1.25rem] font-semibold leading-snug text-[#2D2A26] first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mt-3 text-[1.125rem] font-semibold leading-snug text-[#2D2A26] first:mt-0">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-2.5 text-[16px] font-semibold leading-snug text-[#2D2A26] first:mt-0">{children}</h4>,
  h4: ({ children }) => <h5 className="mt-2 text-[15px] font-semibold leading-snug text-[#2D2A26] first:mt-0">{children}</h5>,
  h5: ({ children }) => <h6 className="mt-2 text-[14px] font-semibold leading-snug text-[#2D2A26] first:mt-0">{children}</h6>,
  h6: ({ children }) => <h6 className="mt-2 text-[13px] font-semibold leading-snug text-[#5A5550] first:mt-0">{children}</h6>,
  p: ({ children }) => <p className="leading-[1.75]">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => <del className="text-obsidian/55">{children}</del>,
  a: ({ href, children }) => <MarkdownLink href={href}>{children}</MarkdownLink>,
  hr: () => <hr className="border-[#E8E5E0]" />,
  ul: ({ children, className }) => (
    <ul className={className?.includes("contains-task-list") ? "space-y-1 pl-1" : "list-disc space-y-1 pl-5"}>{children}</ul>
  ),
  ol: ({ children, start }) => (
    <ol start={start} className="list-decimal space-y-1 pl-5">
      {children}
    </ol>
  ),
  li: ({ children, className }) => (
    <li className={className?.includes("task-list-item") ? "flex list-none items-start gap-2 [&>input]:mt-[0.45em]" : "[&>ol]:mt-1 [&>ul]:mt-1"}>
      {children}
    </li>
  ),
  input: ({ checked, type }) =>
    type === "checkbox" ? <input type="checkbox" checked={!!checked} readOnly disabled className="accent-[#c2716b]" /> : null,
  blockquote: ({ children }) => (
    <blockquote className="space-y-1.5 border-l-2 border-[#FFD6CC] pl-3 text-[#5A5550]">{children}</blockquote>
  ),
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-lg border border-[#E8E5E0] bg-[#2D2A26] px-3 py-2 text-[13px] leading-[1.65] text-white [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-white">
      {children}
    </pre>
  ),
  code: ({ children, className }) => (
    <code className={`rounded bg-[#F0EEEB] px-1 py-0.5 text-[13px] text-[#8B6914] ${className ?? ""}`}>{children}</code>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-[13.5px] leading-[1.6]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-[#FAF8F5]">{children}</thead>,
  tr: ({ children }) => <tr className="border-b border-[#F0EDE8] align-top last:border-0">{children}</tr>,
  th: ({ children, style }) => (
    <th style={style} className="whitespace-nowrap border-b border-[#E8E5E0] px-3 py-2 font-semibold text-[#2D2A26]">
      {children}
    </th>
  ),
  td: ({ children, style }) => (
    <td style={style} className="px-3 py-2 text-obsidian/80">
      {children}
    </td>
  ),
  img: ({ src, alt }) => {
    const safe = typeof src === "string" ? resolveHref(src) : "";
    // eslint-disable-next-line @next/next/no-img-element
    return safe ? <img src={safe} alt={alt ?? ""} className="max-w-full rounded-lg" /> : null;
  },
};

/** 聊天消息的完整 Markdown（GFM）渲染：表格、任务列表、删除线、嵌套列表、代码块、自动链接等 */
export function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="space-y-2.5 text-left text-[15px] leading-[1.75] text-obsidian/90">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
