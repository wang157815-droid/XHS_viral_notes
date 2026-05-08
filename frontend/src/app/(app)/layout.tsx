"use client";

import type { ReactNode } from "react";

import { AppSidebar } from "@/components/layout/app-sidebar";
import { AuthGate } from "@/lib/session-context";
import { WorkspaceProvider } from "@/lib/workspace-context";

/**
 * 应用主 Layout：workspace / history / knowledge / settings 共用。
 *
 * 关键设计：
 * - 位于 Next.js route group `(app)`，四个页面之间切换时本 layout 不会重新挂载
 * - 侧边栏 AppSidebar 保持常驻，避免用户数据 / Cookie 状态 / 宽度跳变
 * - WorkspaceProvider 与 Sidebar 同层常驻：工作台任务状态 / SSE 订阅跨页保持，
 *   用户从 /workspace 切到其他页再切回，任务不会丢
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AuthGate>
      <WorkspaceProvider>
        <div className="flex min-h-screen bg-[#FAFAF8] text-[#2D2A26]">
          <AppSidebar />
          <div className="flex h-screen flex-1 flex-col overflow-hidden">{children}</div>
        </div>
      </WorkspaceProvider>
    </AuthGate>
  );
}
