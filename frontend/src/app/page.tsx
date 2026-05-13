"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useSession } from "@/lib/session-context";

export default function Home() {
  const router = useRouter();
  const { ready, user } = useSession();

  useEffect(() => {
    if (!ready) return;
    router.replace(user ? "/workspace" : "/login");
  }, [ready, user, router]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[radial-gradient(circle_at_50%_20%,rgba(185,206,209,0.24),transparent_34%),linear-gradient(180deg,rgba(255,255,255,0.78),rgba(247,247,245,0.96))] text-obsidian/42">
      <span className="text-sm">正在进入 RedMuse...</span>
    </div>
  );
}
