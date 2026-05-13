"use client";

import type { ReactNode } from "react";

export interface PageHeaderProps {
  title: string;
  actions?: ReactNode;
}

/**
 * 管理页顶部白底 header。仅由 page 自己渲染，
 * Dashboard layout 只负责 sidebar + main 容器，避免 title/actions 的跨组件传递。
 */
export function PageHeader({ title, actions }: PageHeaderProps) {
  return (
    <header className="glass-panel flex items-center justify-between gap-3 border-b border-black/[0.06] px-8 py-5">
      <div className="font-serif text-[22px] font-semibold tracking-[-0.02em] text-obsidian">{title}</div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </header>
  );
}
