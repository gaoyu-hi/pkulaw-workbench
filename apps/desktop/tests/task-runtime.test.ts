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
  TaskPrimaryActionBar,
  TaskErrorCard,
  RuntimeAlertsCard,
  TaskRuntimePanel,
  TaskTimelinePanel
} from "../src/renderer/src/TaskRuntimePanel";
import TaskPauseDetailDialog from "../src/renderer/src/TaskPauseDetailDialog";
import { derivePrimaryTaskAction } from "../src/renderer/src/task-presentation";

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
  it("derives exactly one continuation action for each recovery state", () => {
    const idleRecovery = {
      phase: "idle" as const,
      detail: "",
      canRetry: false
    };
    const failedRecovery = {
      phase: "failed" as const,
      detail: "自动恢复失败",
      canRetry: true
    };

    expect(
      derivePrimaryTaskAction(
        {
          ...baseTask,
          status: "waiting_manual",
          error: {
            code: "YEAR_FILTER_UNAVAILABLE",
            message: "年份不可用"
          }
        },
        { recovery: idleRecovery }
      )
    ).toBe("none");
    expect(
      derivePrimaryTaskAction(
        {
          ...baseTask,
          status: "waiting_manual",
          error: {
            code: "MANUAL_ACTION_REQUIRED",
            message: "请人工处理"
          }
        },
        { recovery: idleRecovery }
      )
    ).toBe("continue");
    expect(
      derivePrimaryTaskAction(baseTask, { recovery: failedRecovery })
    ).toBe("redetect");
    expect(
      derivePrimaryTaskAction(baseTask, {
        recovery: {
          phase: "failed",
          detail: "仍在恢复宽限期内",
          canRetry: false
        }
      })
    ).toBe("none");
    expect(
      derivePrimaryTaskAction(
        {
          ...baseTask,
          status: "stopped",
          checkpoint: {
            scanCompleted: false,
            scanJournal: {
              lastStableSession: { page: 2 }
            }
          }
        },
        { recovery: idleRecovery }
      )
    ).toBe("resume-from-checkpoint");
    expect(
      derivePrimaryTaskAction(
        {
          ...baseTask,
          error: {
            code: "PAGE_LIMIT_REACHED",
            message: "分页上限"
          }
        },
        { recovery: idleRecovery }
      )
    ).toBe("page-limit");
    expect(
      derivePrimaryTaskAction(
        {
          ...baseTask,
          error: {
            code: "BATCH_SCALE_CONFIRMATION_REQUIRED",
            message: "确认规模"
          }
        },
        { recovery: idleRecovery }
      )
    ).toBe("scale");
    expect(
      derivePrimaryTaskAction(baseTask, {
        recovery: idleRecovery,
        candidateWaiting: true
      })
    ).toBe("candidate");
  });

  it("shows processing and failure feedback beside one primary action", () => {
    const processing = renderToStaticMarkup(
      React.createElement(TaskPrimaryActionBar, {
        action: "continue",
        label: "已处理，继续",
        busy: true,
        feedback: {
          status: "processing",
          message: "正在处理：已提交继续请求"
        },
        onAction: () => undefined
      })
    );
    const failed = renderToStaticMarkup(
      React.createElement(TaskPrimaryActionBar, {
        action: "resume-from-checkpoint",
        label: "从断点继续扫描",
        busy: false,
        feedback: {
          status: "error",
          message: "任务操作失败：来源页面尚未稳定"
        },
        onAction: () => undefined
      })
    );

    expect(processing).toContain("正在处理");
    expect(processing).toContain("已处理，继续");
    expect(processing.match(/<button/g)).toHaveLength(1);
    expect(failed).toContain("任务操作失败：来源页面尚未稳定");
    expect(failed).toContain("从断点继续扫描");
    expect(failed.match(/<button/g)).toHaveLength(1);
  });

  it("disables the boundary primary action while busy and enables it when idle", () => {
    const busy = renderToStaticMarkup(
      React.createElement(TaskPrimaryActionBar, {
        action: "capture-boundary",
        label: "继续完整补采",
        busy: true,
        feedback: null,
        onAction: () => undefined
      })
    );
    const idle = renderToStaticMarkup(
      React.createElement(TaskPrimaryActionBar, {
        action: "capture-boundary",
        label: "继续完整补采",
        busy: false,
        feedback: null,
        onAction: () => undefined
      })
    );

    expect(busy).toContain("继续完整补采");
    expect(busy).toMatch(/<button[^>]*disabled/);
    expect(idle).toContain("继续完整补采");
    expect(idle).not.toMatch(/disabled/);
  });

  it("renders a complete pause detail dialog", () => {
    const html = renderToStaticMarkup(
      React.createElement(TaskPauseDetailDialog, {
        detail: {
          code: "YEAR_FILTER_UNAVAILABLE",
          title: "年份筛选不可用",
          message: "底层错误：currentYear=2013 actualYear=全部年份",
          summary: "程序没有成功切换到 2013 年，页面仍显示全部年份。",
          stage: "应用筛选",
          checkpoint: "查询 碳中和 · 第 2 页",
          recommendation: "底层恢复建议",
          nextStep: "请检查年份筛选，再从 2013 年断点继续。",
          raw: {
            error: {
              code: "YEAR_FILTER_UNAVAILABLE",
              message: "当前检索没有可用年份"
            },
            checkpoint: {
              currentPage: 2,
              availableYears: []
            }
          }
        },
        onClose: () => undefined
      })
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain("YEAR_FILTER_UNAVAILABLE");
    expect(html).toContain(
      "程序没有成功切换到 2013 年，页面仍显示全部年份。"
    );
    expect(html).toContain("查询 碳中和 · 第 2 页");
    expect(html).toContain("请检查年份筛选，再从 2013 年断点继续。");
    expect(html).toContain("availableYears");
    expect(html).toContain("关闭详情");
  });

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

  it("renders search identity, workflow, failure and intent together", () => {
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
    expect(html).toContain('data-testid="task-failure-summary"');
    expect(html).toContain("查看停止原因");
  });

  it("keeps one stopped reason and detail action outside the scroll area", () => {
    const html = renderToStaticMarkup(
      React.createElement(TaskRuntimePanel, {
        task: {
          ...baseTask,
          status: "stopped",
          currentStage: "FILTER_NOT_APPLIED",
          error: {
            code: "YEAR_FILTER_UNAVAILABLE",
            message: "无法确认新检索已经替换旧结果",
            details: {
              filterKey: "year",
              expected: "2013",
              actual: "全部年份"
            }
          }
        }
      })
    );
    const summaryIndex = html.indexOf("task-failure-summary");
    const scrollIndex = html.indexOf("task-runtime-scroll");
    const searchIndex = html.indexOf("task-search-context");

    expect(html.match(/data-testid="task-failure-summary"/g)).toHaveLength(1);
    expect(html).toContain("未能切换到 2013 年");
    expect(html).toContain("查看停止原因");
    expect(html.match(/查看停止原因/g)).toHaveLength(1);
    expect(summaryIndex).toBeGreaterThan(-1);
    expect(scrollIndex).toBeGreaterThan(summaryIndex);
    expect(searchIndex).toBeGreaterThan(scrollIndex);
    expect(html).not.toContain('data-testid="pause-reason-card"');
    expect(html).not.toContain('data-testid="task-error-card"');
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

  it("shows the Pkulaw field semantics for keyword and full-text tasks", () => {
    const keyword = renderToStaticMarkup(
      React.createElement(SearchContextCard, {
        task: {
          ...baseTask,
          mode: "single",
          input: {
            queries: ["矿山"],
            matchMode: "keyword",
            library: "central"
          },
          checkpoint: {
            searchId: "search-keyword",
            currentAction: "search",
            searchIdentity: {
              query: "矿山",
              matchMode: "keyword",
              library: "central",
              page: 1,
              totalPages: 1,
              actualPageSize: 20,
              groupingMode: "ungrouped",
              resultCount: 4,
              resultSignature: "c".repeat(64)
            }
          }
        }
      })
    );
    const fulltext = renderToStaticMarkup(
      React.createElement(SearchContextCard, {
        task: {
          ...baseTask,
          mode: "single",
          input: {
            queries: ["矿山"],
            matchMode: "fulltext",
            library: "central"
          },
          checkpoint: {
            searchId: "search-fulltext",
            currentAction: "search",
            searchIdentity: {
              query: "矿山",
              matchMode: "fulltext",
              library: "central",
              page: 1,
              totalPages: 1,
              actualPageSize: 20,
              groupingMode: "ungrouped",
              resultCount: 9,
              resultSignature: "d".repeat(64)
            }
          }
        }
      })
    );

    expect(keyword).toContain("关键词（标题 + 精确）");
    expect(fulltext).toContain("全文（全文 + 精确）");
  });

  it("shows requested and effective ranges after year adjustment", () => {
    const html = renderToStaticMarkup(
      React.createElement(SearchContextCard, {
        task: {
          ...baseTask,
          mode: "single",
          input: {
            queries: ["碳中和"],
            matchMode: "keyword",
            library: "central"
          },
          checkpoint: {
            searchId: "search-year-window",
            currentAction: "scan",
            originalYearSelection: {
              mode: "range",
              startYear: 1949,
              endYear: 2023
            },
            effectiveYearSelection: {
              mode: "range",
              startYear: 2022,
              endYear: 2023
            },
            yearWindowAdjustment: {
              adjusted: true,
              availableYears: ["2022", "2023"]
            },
            searchIdentity: {
              query: "碳中和",
              matchMode: "keyword",
              library: "central",
              year: "2022",
              page: 1,
              totalPages: 1,
              actualPageSize: 100,
              groupingMode: "ungrouped",
              resultCount: 2,
              resultSignature: "e".repeat(64)
            }
          }
        }
      })
    );

    expect(html).toContain("请求年份 1949–2023");
    expect(html).toContain("实际年份 2022–2023");
    expect(html).toContain("已按检索中真实可用的年份自动调整");
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
