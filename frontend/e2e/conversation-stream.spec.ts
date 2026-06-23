import { test, expect } from "@playwright/test";

import {
  installApiMock,
  seedAuth,
  collectConsoleErrors,
  duplicateKeyWarnings,
} from "./helpers/api-mock";
import { conversationChatStream, makeConversation } from "./helpers/mock-data";

const CONV_ID = "conv_stream_e2e";

test.describe("对话消息流式渲染（含重复 key 回归）", () => {
  test("连续两轮对话：用户与助手消息均正确渲染且无重复 key 告警", async ({ page }) => {
    const errors = collectConsoleErrors(page);

    let turn = 0;
    await installApiMock(page, {
      createdConversation: makeConversation({ conversation_id: CONV_ID, title: "防脱精华分析" }),
      // 基于请求体动态回放：回显用户输入 + 确定性助手回复，保证两轮内容不同
      conversationStream: (body) => {
        turn += 1;
        const userContent = String(body.content ?? "");
        return conversationChatStream({
          conversationId: CONV_ID,
          userContent,
          assistantContent: `这是第 ${turn} 轮回答：${userContent} 已收到。`,
          userMessageId: `srv_user_${turn}`,
          assistantMessageId: `srv_asst_${turn}`,
        });
      },
    });

    await seedAuth(page);
    await page.goto("/workspace");

    // 初始态：使用首页 PromptComposer 发送第一条
    const firstInput = "你好，介绍一下你自己";
    await page.getByPlaceholder(/请输入分析目标/).fill(firstInput);
    await page.getByRole("button", { name: "开始分析" }).click();

    // 用户气泡与第一轮助手回答
    await expect(page.getByText(firstInput, { exact: true })).toBeVisible();
    await expect(page.getByText("这是第 1 轮回答：")).toBeVisible();

    // 第二条：进入对话态后使用底部输入框
    const footer = page.getByPlaceholder(/输入指令/);
    await expect(footer).toBeVisible();
    const secondInput = "再帮我总结一下重点";
    await footer.fill(secondInput);
    await page.getByRole("button", { name: "发送" }).click();

    await expect(page.getByText(secondInput, { exact: true })).toBeVisible();
    await expect(page.getByText("这是第 2 轮回答：")).toBeVisible();

    // 第一轮内容仍在（连续会话不丢历史）
    await expect(page.getByText("这是第 1 轮回答：")).toBeVisible();

    expect(duplicateKeyWarnings(errors)).toEqual([]);
  });
});
