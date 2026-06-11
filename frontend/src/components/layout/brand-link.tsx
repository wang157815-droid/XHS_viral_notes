import Image from "next/image";
import Link from "next/link";

/** 产品介绍静态页（public/intro.html） */
export const INTRO_PAGE_HREF = "/intro.html";

type BrandLinkProps = {
  className?: string;
};

/** 左上角品牌：logo + Red Muse，点击跳转产品介绍页 */
export function BrandLink({ className = "" }: BrandLinkProps) {
  return (
    <Link
      href={INTRO_PAGE_HREF}
      target="_blank"
      rel="noopener noreferrer"
      className={`flex shrink-0 items-center gap-2 rounded-md transition-opacity hover:opacity-75 ${className}`}
      title="Red Muse 产品介绍（新标签页打开）"
      aria-label="在新标签页查看 Red Muse 产品介绍"
    >
      <Image src="/logo.png" alt="" width={44} height={44} className="object-contain" priority />
      <span className="font-serif text-[15px] font-medium italic leading-none tracking-[0.02em] text-obsidian">
        Red Muse
      </span>
    </Link>
  );
}
