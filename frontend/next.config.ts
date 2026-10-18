import type { NextConfig } from "next";

const previewMode = process.env.BASE44_PREVIEW_MODE === "1";

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    root: __dirname,
  },
  // 关闭 Next.js dev 模式左下角的 Dev Tools 浮标（Route / Bundler / Preferences 等）
  devIndicators: false,
  // Base44 沙箱预览：允许预览域名访问 dev 资源，并把 /api 代理到后端（单源）
  ...(previewMode && process.env.BASE44_PUBLIC_HOST_SUFFIX
    ? { allowedDevOrigins: ["3000-" + process.env.BASE44_PUBLIC_HOST_SUFFIX] }
    : {}),
  ...(previewMode && process.env.BACKEND_INTERNAL_URL
    ? {
        async rewrites() {
          return [
            { source: "/api/:path*", destination: `${process.env.BACKEND_INTERNAL_URL}/api/:path*` },
          ];
        },
      }
    : {}),
};

export default nextConfig;
