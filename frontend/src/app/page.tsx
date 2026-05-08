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
    <div className="flex min-h-screen items-center justify-center bg-[#FAFAF8] text-[#8A8580]">
      <span className="text-sm">正在进入 RedMuse...</span>
    </div>
  );
}
