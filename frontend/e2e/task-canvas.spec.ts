import { test, expect } from "@playwright/test";

import {
  installApiMock,
  seedAuth,
  collectConsoleErrors,
  duplicateKeyWarnings,
} from "./helpers/api-mock";
import {
  conversationHandoffStream,
  makeCanvas,
  makeConversation,
  taskDoneStream,
} from "./helpers/mock-data";

const CONV_ID = "conv_canvas_e2e";
const TASK_ID = "task_canvas_e2e_001";

test.describe("任务 → SSE → Canvas 渲染", () => {
  test("对话触发分析任务后可展开并查看画布模块", async ({ page }) => {
    const errors = collectConsoleErrors(page);

    await installApiMock(page, {
      createdConversation: makeConversation({ conversation_id: CONV_ID, title: "防脱精华爆款分析" }),
      conversationStream: (body) =>
        conversationHandoffStream({
          conversationId: CONV_ID,
          userContent: String(body.content ?? ""),
          assistantContent: "已为你发起分析任务，正在生成画布。",
          taskId: TASK_ID,
        }),
      tasks: {
        [TASK_ID]: { task_id: TASK_ID, status: "completed", raw_input: "分析近半年防脱精华视频类爆款笔记" },
      },
      canvases: {
        [TASK_ID]: makeCanvas(TASK_ID),
      },
      taskStreams: {
        [TASK_ID]: taskDoneStream(TASK_ID),
      },
    });

    await seedAuth(page);
    await page.goto("/workspace");

    const input = "分析近半年防脱精华视频类爆款笔记";
    await page.getByPlaceholder(/请输入分析目标/).fill(input);
    await page.getByRole("button", { name: "开始分析" }).click();

    // 助手回复渲染
    await expect(page.getByText("已为你发起分析任务，正在生成画布。")).toBeVisible();

    // 任务完成后右上角出现「收起/展开画布」按钮，点击展开画布
    const canvasToggle = page.getByTitle("收起/展开画布");
    await expect(canvasToggle).toBeVisible({ timeout: 15_000 });
    await canvasToggle.click();

    // 画布标题与模块标题渲染（exact 避免与空态占位文案/正文子串冲突）
    await expect(page.getByText("防脱精华 · 爆文洞察")).toBeVisible();
    await expect(page.getByText("爆文模型矩阵", { exact: true })).toBeVisible();
    await expect(page.getByText("高频痛点 TOP", { exact: true })).toBeVisible();

    // 导出按钮（画布工具条）可见，验证 CanvasView 已挂载
    await expect(page.getByRole("button", { name: "导出 Excel" })).toBeVisible();

    expect(duplicateKeyWarnings(errors)).toEqual([]);
  });
});
