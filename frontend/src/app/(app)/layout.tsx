"use client";

import type { ReactNode } from "react";
import { Suspense } from "react";
import { usePathname } from "next/navigation";

import { AppSidebar } from "@/components/layout/app-sidebar";
import { AuthGate } from "@/lib/session-context";
import { WorkspaceProvider } from "@/lib/workspace-context";

/**
 * 应用主 Layout：workspace / history / knowledge / settings 共用。
 *
 * 关键设计：
 * - 位于 Next.js route group `(app)`，四个页面之间切换时本 layout 不会重新挂载
 * - 主侧栏 AppSidebar 在 workspace / history / knowledge 常驻；**设置中心**参考 RED-MUSE-
 *   为全幅「设置壳」，不显示主侧栏，仅使用设置页内左侧分区导航。
 * - WorkspaceProvider 与 Sidebar 同层常驻：工作台任务状态 / SSE 订阅跨页保持，
 *   用户从 /workspace 切到其他页再切回，任务不会丢
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const settingsShell = pathname === "/settings" || pathname?.startsWith("/settings/");

  return (
    <AuthGate>
      <WorkspaceProvider>
        <div className="flex min-h-screen bg-papyrus text-obsidian selection:bg-dew/30">
          {!settingsShell ? (
            <Suspense
              fallback={
                <aside className="sticky top-0 h-screen w-[259px] shrink-0 border-r border-fog bg-moss/20" aria-hidden />
              }
            >
              <AppSidebar />
            </Suspense>
          ) : null}
          <div className="flex h-screen min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
        </div>
      </WorkspaceProvider>
    </AuthGate>
  );
}
