import { defineConfig, devices } from "@playwright/test";

/**
 * RedMuse 前端 E2E 配置。
 *
 * 设计要点：
 * - 所有后端接口都在浏览器侧通过 page.route 拦截 mock，不依赖真实后端（8100）。
 *   因此 webServer 只需启动前端 dev（3000）。
 * - 通过 NEXT_PUBLIC_API_BASE_URL 指向同源 /api/v1，避免跨域预检；
 *   即便复用了已存在的、仍指向 8100 的 dev server，mock 也带了 CORS 头兜底。
 * - Windows 下首个编译较慢，故 webServer.timeout 放宽到 180s。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  // 单一 dev server，串行执行最稳定，避免首屏并发编译抖动。
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 60_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: {
      NEXT_PUBLIC_API_BASE_URL: "http://localhost:3000/api/v1",
    },
  },
});
