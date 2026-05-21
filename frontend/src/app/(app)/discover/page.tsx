"use client";

import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";

export default function DiscoverPage() {
  const ts = Date.now();
  return (
    <>
      <PageHeader title="热点发现" />
      <div className="relative flex-1 overflow-y-auto bg-[radial-gradient(circle_at_85%_8%,rgba(185,206,209,0.22),transparent_32%),linear-gradient(180deg,rgba(255,255,255,0.7),rgba(247,247,245,0.94))] px-6 py-8 lg:px-10">
        <p className="max-w-xl text-[14px] leading-7 text-obsidian/55">
          面向趋势与热点话题的对话场景。新建会话将带上「热点」元数据，并可在对话中触发联网检索（需在环境变量中配置{" "}
          <code className="rounded bg-obsidian/[0.06] px-1">WEB_SEARCH_API_KEY</code>）。
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href={`/workspace?new=${ts}&surface=hotspot`}
            className="rounded-xl bg-dew px-5 py-2.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-[#9CBBC0]"
          >
            新建热点会话
          </Link>
          <Link href="/workspace" className="rounded-xl border border-black/[0.08] bg-white/80 px-5 py-2.5 text-[13px] font-medium text-obsidian/60 transition hover:bg-white">
            返回工作台
          </Link>
        </div>
      </div>
    </>
  );
}
