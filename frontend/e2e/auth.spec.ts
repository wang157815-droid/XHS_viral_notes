import { test, expect } from "@playwright/test";

import { installApiMock, seedAuth, collectConsoleErrors, duplicateKeyWarnings } from "./helpers/api-mock";

test.describe("登录与鉴权门", () => {
  test("未登录访问 /workspace 会被重定向到 /login", async ({ page }) => {
    await installApiMock(page);
    // 不 seedAuth：localStorage 无 token
    await page.goto("/workspace");

    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { name: "欢迎使用 RedMuse" })).toBeVisible();
    await expect(page.getByPlaceholder("例如 admin")).toBeVisible();
  });

  test("账号密码登录成功后进入工作台", async ({ page }) => {
    const errors = collectConsoleErrors(page);
    await installApiMock(page);

    await page.goto("/login");
    await page.getByPlaceholder("例如 admin").fill("admin");
    await page.getByPlaceholder("请输入密码").fill("secret123");
    await page.getByRole("button", { name: "登录", exact: true }).click();

    await expect(page).toHaveURL(/\/workspace/);
    // 初始态首页标题
    await expect(
      page.getByRole("heading", { name: "洞见爆文规律，开启灵动创作" }),
    ).toBeVisible();

    expect(duplicateKeyWarnings(errors)).toEqual([]);
  });

  test("已登录访问 /login 会自动跳转工作台", async ({ page }) => {
    await installApiMock(page);
    await seedAuth(page);

    await page.goto("/login");
    await expect(page).toHaveURL(/\/workspace/);
  });
});
