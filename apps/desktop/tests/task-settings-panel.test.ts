import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import TaskSettingsPanel, {
  buildTaskSettingsInput,
  taskSettingsDraftFromInput
} from "../src/renderer/src/TaskSettingsPanel";

const input = {
  queries: ["人工智能", "数据安全"],
  keywords: ["人工智能", "数据安全"],
  matchMode: "title",
  library: "central",
  yearSelection: {
    mode: "range",
    startYear: 1949,
    endYear: 1999
  },
  province: "全部省份",
  timeliness: "全部（仅记录元数据，不筛选）",
  pageSize: 20,
  pageSelection: { mode: "limit", maxPages: 3 },
  outputDir: "D:/输出",
  previewSnapshot: { totalPages: 4 }
};

describe("TaskSettingsPanel", () => {
  it("shows the current single-task settings with apply and close actions", () => {
    const markup = renderToStaticMarkup(
      React.createElement(TaskSettingsPanel, {
        mode: "single",
        taskId: "task-1",
        input,
        busy: false,
        onApply: async () => undefined,
        onClose: () => undefined
      })
    );

    expect(markup).toContain("任务设置");
    expect(markup).toContain("人工智能");
    expect(markup).toContain("数据安全");
    expect(markup).toContain('value="1999"');
    expect(markup).toContain("应用设置");
    expect(markup).toContain("关闭");
    expect(markup).not.toContain("创建并开始");
  });

  it("shows batch format fields and builds a clean revision payload", () => {
    const batchInput = {
      ...input,
      matchMode: "keyword",
      catalogFormat: "html-xls",
      fullFormat: "docx"
    };
    const markup = renderToStaticMarkup(
      React.createElement(TaskSettingsPanel, {
        mode: "batch",
        taskId: "task-2",
        input: batchInput,
        busy: false,
        onApply: async () => undefined,
        onClose: () => undefined
      })
    );
    const draft = taskSettingsDraftFromInput("batch", batchInput);
    const payload = buildTaskSettingsInput("batch", draft);

    expect(markup).toContain("目录格式");
    expect(markup).toContain("兼容 HTML-XLS");
    expect(markup).toContain("Word ZIP");
    expect(markup).toContain('<option value="keyword" selected="">关键词</option>');
    expect(markup).toContain('<option value="fulltext">全文</option>');
    expect(markup).not.toContain("精确完整标题");
    expect(payload).toMatchObject({
      queries: ["人工智能", "数据安全"],
      keywords: ["人工智能", "数据安全"],
      catalogFormat: "html-xls",
      fullFormat: "docx",
      outputDir: "D:/输出"
    });
    expect(payload).not.toHaveProperty("previewSnapshot");
  });

  it("offers all three single modes in task settings", () => {
    const markup = renderToStaticMarkup(
      React.createElement(TaskSettingsPanel, {
        mode: "single",
        taskId: "task-single-modes",
        input: {
          ...input,
          matchMode: "fulltext"
        },
        busy: false,
        onApply: async () => undefined,
        onClose: () => undefined
      })
    );

    expect(markup).toContain('<option value="keyword">关键词</option>');
    expect(markup).toContain('<option value="fulltext" selected="">全文</option>');
    expect(markup).toContain('<option value="title">精确完整标题</option>');
  });

  it("hides province settings for central laws", () => {
    const render = (library: "central" | "local" | "both"): string =>
      renderToStaticMarkup(
        React.createElement(TaskSettingsPanel, {
          mode: "single",
          taskId: `task-${library}`,
          input: {
            ...input,
            library,
            province: "北京市"
          },
          busy: false,
          onApply: async () => undefined,
          onClose: () => undefined
        })
      );

    expect(render("central")).not.toContain(
      'data-testid="collector-province-row"'
    );
    expect(render("local")).toContain(
      'data-testid="collector-province-row"'
    );
    expect(render("both")).toContain(
      'data-testid="collector-province-row"'
    );
  });

  it("normalizes a stale central province when building settings", () => {
    const draft = taskSettingsDraftFromInput("single", {
      ...input,
      library: "central",
      province: "北京市"
    });

    expect(buildTaskSettingsInput("single", draft).province).toBe(
      "全部省份"
    );
  });

  it("disables both actions while a revision is being applied", () => {
    const markup = renderToStaticMarkup(
      React.createElement(TaskSettingsPanel, {
        mode: "single",
        taskId: "task-3",
        input,
        busy: true,
        onApply: async () => undefined,
        onClose: () => undefined
      })
    );

    expect(markup).toContain("正在应用设置");
    expect(markup.match(/disabled=""/g)?.length || 0).toBeGreaterThan(2);
  });

  it("keeps apply settings in a fixed footer after the form body", () => {
    const localInput = {
      ...input,
      library: "local",
      province: "北京市"
    };
    const markup = renderToStaticMarkup(
      React.createElement(TaskSettingsPanel, {
        mode: "single",
        taskId: "task-layout",
        input: localInput,
        busy: false,
        onApply: async () => undefined,
        onClose: () => undefined
      })
    );
    const bodyIndex = markup.indexOf("collector-form-body");
    const footerIndex = markup.indexOf("collector-action-footer");
    const applyIndex = markup.indexOf("应用设置");

    expect(markup).toContain("collector-form-shell");
    expect(markup).toContain("collector-year-row");
    expect(markup).toContain('data-testid="collector-province-row"');
    expect(bodyIndex).toBeGreaterThan(-1);
    expect(footerIndex).toBeGreaterThan(bodyIndex);
    expect(applyIndex).toBeGreaterThan(footerIndex);
    expect(
      markup.match(/collector-form-guidance/g)?.length || 0
    ).toBeLessThanOrEqual(1);
  });
});
