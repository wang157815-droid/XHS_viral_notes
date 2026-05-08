import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  turbopack: {
    root: __dirname,
  },
  // 关闭 Next.js dev 模式左下角的 Dev Tools 浮标（Route / Bundler / Preferences 等）
  devIndicators: false,
};

export default nextConfig;
