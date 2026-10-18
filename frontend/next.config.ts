import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    root: __dirname,
  },
  // 关闭 Next.js dev 模式左下角的 Dev Tools 浮标（Route / Bundler / Preferences 等）
  devIndicators: false,
  // Sandbox preview only: allow the proxied preview origin to load dev assets/HMR
  ...(process.env.BASE44_PREVIEW_MODE === "1" && process.env.BASE44_PUBLIC_HOST_SUFFIX
    ? { allowedDevOrigins: [`3000-${process.env.BASE44_PUBLIC_HOST_SUFFIX}`] }
    : {}),
};

export default nextConfig;
