import { test, expect } from "@playwright/test";

import {
  installApiMock,
  seedAuth,
  collectConsoleErrors,
  duplicateKeyWarnings,
  type ConversationDetail,
} from "./helpers/api-mock";
import {
  makeConversation,
  makeConversationSummary,
  makeUserMessage,
  makeAssistantMessage,
} from "./helpers/mock-data";

const CONV_A = "conv_switch_a";
const CONV_B = "conv_switch_b";

const TITLE_A = "防脱精华会话";
const TITLE_B = "美白精华会话";

const A_USER = "防脱精华怎么选";
const A_ASST = "建议优先看活性成分浓度";
const B_USER = "美白精华推荐一下";
const B_ASST = "维C和烟酰胺是首选";

function buildDetails(): Record<string, ConversationDetail> {
  return {
    [CONV_A]: {
      conversation: makeConversation({ conversation_id: CONV_A, title: TITLE_A }),
      messages: [
        makeUserMessage(CONV_A, A_USER, { message_id: "a_u1" }),
        makeAssistantMessage(CONV_A, A_ASST, { message_id: "a_a1" }),
      ],
    },
    [CONV_B]: {
      conversation: makeConversation({ conversation_id: CONV_B, title: TITLE_B }),
      messages: [
        makeUserMessage(CONV_B, B_USER, { message_id: "b_u1" }),
        makeAssistantMessage(CONV_B, B_ASST, { message_id: "b_a1" }),
      ],
    },
  };
}

test.describe("会话切换（URL 三 effect 脆弱点）+ 页面导航", () => {
  test("侧边栏来回切换会话，消息互不串流且无重复 key 告警", async ({ page }) => {
    const errors = collectConsoleErrors(page);

    await installApiMock(page, {
      conversations: [
        makeConversationSummary({ conversation_id: CONV_A, title: TITLE_A }),
        makeConversationSummary({ conversation_id: CONV_B, title: TITLE_B }),
      ],
      conversationDetails: buildDetails(),
    });

    await seedAuth(page);
    await page.goto("/workspace");

    const sidebar = page.locator("aside");
    await expect(sidebar.getByRole("button", { name: TITLE_A })).toBeVisible();

    // 打开会话 A
    await sidebar.getByRole("button", { name: TITLE_A }).click();
    await expect(page).toHaveURL(new RegExp(`conversation=${CONV_A}`));
    await expect(page.getByText(A_USER, { exact: true })).toBeVisible();
    await expect(page.getByText(A_ASST)).toBeVisible();

    // 切换到会话 B：A 的消息应被替换掉
    await sidebar.getByRole("button", { name: TITLE_B }).click();
    await expect(page).toHaveURL(new RegExp(`conversation=${CONV_B}`));
    await expect(page.getByText(B_USER, { exact: true })).toBeVisible();
    await expect(page.getByText(B_ASST)).toBeVisible();
    await expect(page.getByText(A_ASST)).toHaveCount(0);

    // 切回会话 A（命中前端缓存）：恢复 A 的消息，B 消失
    await sidebar.getByRole("button", { name: TITLE_A }).click();
    await expect(page).toHaveURL(new RegExp(`conversation=${CONV_A}`));
    await expect(page.getByText(A_ASST)).toBeVisible();
    await expect(page.getByText(B_ASST)).toHaveCount(0);

    expect(duplicateKeyWarnings(errors)).toEqual([]);
  });

  test("点击「发现热点」重置回初始态（new/surface reset effect）", async ({ page }) => {
    const errors = collectConsoleErrors(page);

    await installApiMock(page, {
      conversations: [makeConversationSummary({ conversation_id: CONV_A, title: TITLE_A })],
      conversationDetails: buildDetails(),
    });

    await seedAuth(page);
    await page.goto("/workspace");

    const sidebar = page.locator("aside");
    await sidebar.getByRole("button", { name: TITLE_A }).click();
    await expect(page.getByText(A_ASST)).toBeVisible();

    // 触发新建/重置：进入热点发现的全新工作台
    await sidebar.getByRole("button", { name: "发现热点" }).click();

    // 回到初始态：首页标题出现，旧会话消息清空，URL 仅保留 surface
    await expect(
      page.getByRole("heading", { name: "洞见爆文规律，开启灵动创作" }),
    ).toBeVisible();
    await expect(page.getByText(A_ASST)).toHaveCount(0);
    await expect(page).toHaveURL(/surface=hotspot/);

    expect(duplicateKeyWarnings(errors)).toEqual([]);
  });
});
