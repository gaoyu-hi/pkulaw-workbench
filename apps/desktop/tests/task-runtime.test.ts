import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type {
  BrowserHostState,
  TaskRecord
} from "../src/shared/protocol";
import {
  BrowserLeaseCard,
  DownloadIntentCard,
  ProgressSnapshotCard,
  SearchContextCard,
  TaskErrorCard,
  RuntimeAlertsCard,
  TaskRuntimePanel,
  TaskTimelinePanel
} from "../src/renderer/src/TaskRuntimePanel";

const baseTask: TaskRecord = {
  id: "task-1",
  name: "批量测试任务",
  mode: "batch",
  status: "waiting_manual",
  input: {
    queries: ["矿山"],
    matchMode: "keyword",
    library: "central",
    pageSize: 50
  },
  outputDir: "D:/中文 空格/非常长的输出目录",
  progress: 0.5,
  currentStage: "waiting-manual",
  progressSnapshot: {
    overall: 0.5,
    stage: "catalog",
    completedUnits: 2,
    totalUnits: 5,
    succeeded: 1,
    failed: 1,
    pendingReview: 0
  },
  error: {
    code: "PAGE_SELECTOR_MISMATCH",
    message: "页面结构持续缺失，任务不会猜测控件。",
    details: {
      actualFile: "D:/下载/目录.xls",
      expectedFormat: "BIFF XLS",
      actualFormat: "HTML",
      intentId: "intent-1",
      downloadId: "download-1",
      bodyCompleteness: "suspicious"
    }
  },
  checkpoint: {
    searchId: "search-1",
    currentAction: "catalog-download",
    pauseReason: "检测到页面结构变化",
    searchIdentity: {
      query: "矿山",
      library: "central",
      actualYear: "全部年份",
      actualProvince: "全部省份",
      actualTimeliness: "现行有效",
      page: 2,
      totalPages: 5,
      actualPageSize: 50,
      groupingMode: "ungrouped",
      resultCount: 214,
      resultSignature: "b".repeat(64)
    },
    currentDownloadIntent: {
      intentId: "intent-1",
      kind: "batch-catalog",
      maxDownloads: 1,
      expectedSuffixes: [".xlsx"],
      expectedSourceHosts: ["www.pkulaw.com"]
    },
    currentDownloadStatus: "pending"
  },
  archivedAt: null,
  archiveReason: "",
  startedAt: "2026-11-01T00:00:00Z",
  finishedAt: null,
  createdAt: "2026-11-01T00:00:00Z",
  updatedAt: "2026-11-01T00:00:01Z"
};

describe("task runtime components", () => {
  it("renders the unified error card with stage and recovery advice", () => {
    const html = renderToStaticMarkup(
      React.createElement(TaskErrorCard, {
        error: baseTask.error,
        status: baseTask.status,
        currentStage: baseTask.currentStage,
        checkpoint: baseTask.checkpoint,
        completed: 2,
        total: 5
      })
    );
    expect(html).toContain("PAGE_SELECTOR_MISMATCH");
    expect(html).toContain("恢复建议");
    expect(html).toContain("已完成 2/5 条");
    expect(html).toContain("实际文件");
    expect(html).toContain("D:/下载/目录.xls");
    expect(html).toContain("格式契约");
    expect(html).toContain("BIFF XLS");
    expect(html).toContain("HTML");
    expect(html).toContain("intent-1");
    expect(html).toContain("download-1");
    expect(html).toContain("正文完整性");
    expect(html).toContain("suspicious");
    expect(html).toContain("动作策略");
    expect(html).toContain("完整错误");
  });

  it("renders one progress snapshot with outcome counts", () => {
    const html = renderToStaticMarkup(
      React.createElement(ProgressSnapshotCard, {
        status: "partial",
        progress: 1,
        currentStage: "partial",
        progressSnapshot: {
          overall: 1,
          stage: "partial",
          completedUnits: 3,
          totalUnits: 3,
          succeeded: 0,
          failed: 0,
          pendingReview: 3
        }
      })
    );

    expect(html).toContain("99%");
    expect(html).toContain("成功 0");
    expect(html).toContain("待核验 3");
    expect(html).toContain("共 3");
    expect(html).toContain("不是 100% 成功");
  });

  it("renders search identity, workflow, pause and intent together", () => {
    const html = renderToStaticMarkup(
      React.createElement(TaskRuntimePanel, {
        task: baseTask,
        completed: 2,
        total: 5
      })
    );
    expect(html).toContain("当前检索身份");
    expect(html).toContain("当前下载意图：批量目录");
    expect(html).toContain("workflow-stage-strip");
    expect(html).toContain("自动暂停");
  });

  it("uses exact full-title wording consistently in task details", () => {
    const html = renderToStaticMarkup(
      React.createElement(SearchContextCard, {
        task: {
          ...baseTask,
          mode: "single",
          input: {
            queries: ["中华人民共和国国防动员法(2026修订)"],
            matchMode: "title",
            library: "central"
          },
          checkpoint: {
            searchId: "search-exact-title",
            currentAction: "download",
            searchIdentity: {
              query: "中华人民共和国国防动员法(2026修订)",
              matchMode: "title",
              library: "central",
              page: 1,
              totalPages: 1,
              actualPageSize: 50,
              groupingMode: "ungrouped",
              resultCount: 1,
              resultSignature: "a".repeat(64)
            }
          }
        }
      })
    );

    expect(html).toContain("精确完整标题");
    expect(html).not.toMatch(/>完整标题</);
  });

  it("shows the same visible-captcha gate as the browser card", () => {
    const html = renderToStaticMarkup(
      React.createElement(TaskRuntimePanel, {
        task: {
          ...baseTask,
          currentStage: "waiting-manual",
          checkpoint: {
            ...baseTask.checkpoint,
            pauseReason: "验证码可见"
          }
        },
        browserCaptchaVisible: true,
        browserCaptchaCheckedAt: "2026-11-02T08:30:00Z",
        browserCaptchaSelector: ".drag_text"
      })
    );

    expect(html).toContain("等待人工完成滑块或验证码");
    expect(html).toContain(".drag_text");
    expect(html).toContain("继续按钮已同步锁定");
  });

  it("renders a deduplicated timeline", () => {
    const html = renderToStaticMarkup(
      React.createElement(TaskTimelinePanel, {
        events: [
          {
            id: 1,
            taskId: "task-1",
            event: "task.updated",
            data: {
              status: "running",
              currentStage: "searching",
              currentAction: "search"
            },
            createdAt: "2026-11-01T00:00:01Z"
          },
          {
            id: 2,
            taskId: "task-1",
            event: "task.updated",
            data: {
              status: "running",
              currentStage: "searching",
              currentAction: "search"
            },
            createdAt: "2026-11-01T00:00:02Z"
          }
        ]
      })
    );
    expect(html).toContain("阶段时间线");
    expect(html).toContain("1 个阶段");
  });

  it("renders full browser lease ownership details", () => {
    const browser: BrowserHostState = {
      status: "ready",
      institutionId: "fixture",
      sessionKey: "fixture",
      url: "https://www.pkulaw.com/",
      title: "法宝",
      viewLoaded: true,
      targetBound: true,
      targetUrl: "https://www.pkulaw.com/",
      selectorProfile: "pkulaw-v6",
      selectorVersion: "3",
      selectorReady: true,
      selectorMessage: "ok",
      selectorDiagnostics: null,
      authenticated: true,
      loginVisible: false,
      captchaVisible: false,
      zoomFactor: 1,
      canGoBack: false,
      childViews: 0,
      tabs: [],
      message: "ready",
      lastDownload: null,
      browserLease: {
        available: false,
        leaseId: "lease-1",
        taskId: "task-1",
        mode: "batch",
        createdAt: "2026-11-01T00:00:00Z",
        updatedAt: "2026-11-01T00:00:01Z"
      }
    };
    const html = renderToStaticMarkup(
      React.createElement(BrowserLeaseCard, {
        browser,
        tasks: [baseTask]
      })
    );
    expect(html).toContain("批量测试任务");
    expect(html).toContain("lease-1");
    expect(html).toContain("批量采集");
  });

  it("renders no guessed success when download intent is absent", () => {
    const html = renderToStaticMarkup(
      React.createElement(DownloadIntentCard, { source: {} })
    );
    expect(html).toBe("");
  });

  it("renders an ignored download mismatch as a warning", () => {
    const html = renderToStaticMarkup(
      React.createElement(RuntimeAlertsCard, {
        alerts: [
          {
            id: "alert-1",
            title: "下载事件与当前意图不匹配",
            detail: "不匹配文件不会交给当前任务。",
            severity: "warning",
            createdAt: "2026-11-01T00:00:00Z"
          }
        ]
      })
    );
    expect(html).toContain("运行时告警");
    expect(html).toContain("不匹配文件不会交给当前任务");
  });
});
