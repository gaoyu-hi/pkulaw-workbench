import { describe, expect, it } from "vitest";
import type {
  TaskRecord,
  TaskTimelineEvent
} from "../src/shared/protocol";
import {
  buildTaskTimeline,
  collectionActions,
  downloadIntentView,
  explainError,
  hashState,
  pauseView,
  processingActions,
  progressSnapshotView,
  retryActionLabel,
  searchSessionView,
  stageLabel,
  workflowSteps
} from "../src/renderer/src/task-presentation";

function task(
  overrides: Partial<TaskRecord> = {}
): TaskRecord {
  return {
    id: "task-1",
    name: "测试任务",
    mode: "single",
    status: "running",
    input: {
      queries: ["矿山政策"],
      matchMode: "keyword",
      library: "central",
      pageSize: 50
    },
    outputDir: "D:/输出",
    progress: 0.5,
    currentStage: "searching",
    error: null,
    checkpoint: {},
    archivedAt: null,
    archiveReason: "",
    startedAt: "2026-11-01T00:00:00Z",
    finishedAt: null,
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:01Z",
    ...overrides
  };
}

describe("task presentation", () => {
  it("uses one stage vocabulary for indexed and named stages", () => {
    expect(stageLabel("catalog-download")).toContain("目录");
    expect(stageLabel("zip-download")).toContain("ZIP");
    expect(stageLabel("batch-3-8")).toBe("正在处理批次 3/8");
    expect(stageLabel("detail-2-10")).toBe("读取详情 2/10");
  });

  it("never presents a partial task as one hundred percent successful", () => {
    const view = progressSnapshotView({
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
    });

    expect(view.percent).toBe(99);
    expect(view.completedUnits).toBe(3);
    expect(view.totalUnits).toBe(3);
    expect(view.succeeded).toBe(0);
    expect(view.pendingReview).toBe(3);
    expect(view.partialSuccessWarning).toContain("0/3");
  });

  it("falls back safely when an old checkpoint has no progress snapshot", () => {
    const view = progressSnapshotView({
      status: "running",
      progress: 0.35,
      currentStage: "searching"
    });

    expect(view.percent).toBe(35);
    expect(view.stage).toBe("searching");
    expect(view.legacy).toBe(true);
  });

  it("surfaces actual SearchSession and workflow identity", () => {
    const record = task({
      mode: "batch",
      checkpoint: {
        searchId: "search-1",
        currentAction: "zip-download",
        searchIdentity: {
          query: "矿山政策",
          matchMode: "keyword",
          library: "central",
          actualYear: "2024",
          actualProvince: "全部省份",
          actualTimeliness: "现行有效",
          page: 2,
          totalPages: 7,
          actualPageSize: 50,
          groupingMode: "ungrouped",
          resultCount: 321,
          rowCount: 50,
          resultSignature: "a".repeat(64)
        }
      }
    });

    expect(searchSessionView(record)).toMatchObject({
      query: "矿山政策",
      year: "2024",
      page: "2",
      totalPages: "7",
      pageSize: "50",
      groupingMode: "ungrouped"
    });
    const steps = workflowSteps(record);
    expect(steps.find((step) => step.id === "grouping")?.state).toBe("done");
    expect(steps.find((step) => step.id === "zip")?.state).toBe("done");
    expect(steps.find((step) => step.id === "publish")?.state).toBe("current");
  });

  it("shows download intent state and consumed count", () => {
    const intent = downloadIntentView({
      currentDownloadIntent: {
        intentId: "intent-1",
        kind: "batch-zip",
        downloadId: "download-1",
        maxDownloads: 1,
        matchedDownloads: 1,
        expectedSuffixes: [".zip"],
        expectedSourceHosts: ["www.pkulaw.com"],
        path: "D:/临时/全文.zip"
      },
      currentDownloadStatus: "completed"
    });

    expect(intent).toMatchObject({
      intentId: "intent-1",
      kindLabel: "Word ZIP",
      status: "completed",
      matchedDownloads: 1,
      maxDownloads: 1
    });
  });

  it("explains hard stops and recovery actions", () => {
    const selector = explainError(
      {
        code: "PAGE_SELECTOR_MISMATCH",
        message: "缺少关键选择器"
      },
      "stopped",
      "searching",
      { currentAction: "search" }
    );
    expect(selector?.hardStop).toBe(true);
    expect(selector?.recommendation).toContain("失败关闭");
    expect(selector?.actionPolicy).toContain("普通继续不会放行");

    const quota = explainError(
      {
        code: "INSTITUTION_QUOTA_REACHED",
        message: "机构额度不足"
      },
      "failed",
      "word-download",
      {}
    );
    expect(quota?.severity).toBe("fatal");
    expect(quota?.allowedActions).toContain("检查额度");
    expect(
      explainError(
        { code: "PROCESSING_STOPPED", message: "数据处理已停止" },
        "stopped",
        "after-policy",
        {}
      )
    ).toBeNull();
  });

  it("maps SearchSession, filter and page-size failures to one recovery card", () => {
    for (const [code, expected] of [
      ["SEARCH_TRANSITION_UNCONFIRMED", "检索或页面状态"],
      ["RESULTS_STALE", "检索身份"],
      ["FILTER_NOT_APPLIED", "筛选条件"],
      ["PAGE_SIZE_NOT_APPLIED", "筛选条件"]
    ] as const) {
      const explanation = explainError(
        { code, message: `${code} message` },
        "stopped",
        "searching",
        { currentAction: "search" }
      );
      expect(explanation?.title).toContain(expected);
      expect(explanation?.stage).toContain("检索");
    }
  });

  it("distinguishes manual, login, captcha and confirmation pauses", () => {
    expect(
      pauseView("paused", { pauseKind: "manual" }, null)?.title
    ).toContain("手动");
    expect(
      pauseView("waiting_login", {}, { code: "LOGIN_REQUIRED", message: "" })?.title
    ).toContain("登录");
    expect(
      pauseView(
        "waiting_manual",
        { pauseReason: "检测到滑块，请人工处理" },
        null
      )?.kind
    ).toBe("captcha");
    expect(
      pauseView(
        "waiting_manual",
        {},
        { code: "PAGE_LIMIT_REACHED", message: "" }
      )?.kind
    ).toBe("page-limit");
  });

  it("keeps ordinary continue closed for hard stops", () => {
    const selectorTask = task({
      status: "waiting_manual",
      error: {
        code: "PAGE_SELECTOR_MISMATCH",
        message: "selector missing"
      }
    });
    const policy = collectionActions(selectorTask);
    expect(policy.continue).toBe(false);
    expect(policy.stop).toBe(true);

    const pageLimitTask = task({
      status: "waiting_manual",
      error: {
        code: "PAGE_LIMIT_REACHED",
        message: "limit"
      }
    });
    expect(collectionActions(pageLimitTask).continueToLast).toBe(true);
  });

  it("keeps pause, resume and stop buttons mutually sensible", () => {
    expect(
      collectionActions(task({ status: "running" }))
    ).toMatchObject({ pause: true, stop: true, resume: false });
    expect(
      collectionActions(task({ status: "paused" }))
    ).toMatchObject({ pause: false, stop: true, resume: true });
    expect(
      collectionActions(task({ status: "stopping" }))
    ).toMatchObject({ pause: false, stop: false, resume: false });
    expect(
      collectionActions(task({ status: "stopped" }))
    ).toMatchObject({ resume: false });
  });

  it("labels interrupted scans and completed scans without ambiguous retry", () => {
    expect(
      retryActionLabel(
        task({
          status: "stopped",
          checkpoint: {
            scanCompleted: false,
            scanJournal: {
              lastStableSession: { page: 4 }
            }
          }
        })
      )
    ).toBe("从断点继续扫描");
    expect(
      retryActionLabel(
        task({
          status: "partial",
          checkpoint: {
            scanCompleted: true,
            failedItems: 2
          }
        })
      )
    ).toBe("重试失败项");
    expect(
      collectionActions(
        task({
          status: "stopped",
          checkpoint: {
            scanCompleted: false,
            scanJournal: {
              lastStableSession: { page: 4 }
            }
          }
        }),
        { failedItems: 0 }
      ).retry
    ).toBe(true);
  });

  it("uses the same hard-stop rule for data processing", () => {
    expect(processingActions("waiting_manual", "PAGE_SELECTOR_MISMATCH").continue).toBe(false);
    expect(processingActions("waiting_login", "LOGIN_REQUIRED").continue).toBe(true);
    expect(processingActions("waiting_manual", "DOWNLOAD_INTENT_EXHAUSTED").continue).toBe(false);
    expect(
      collectionActions(
        task({
          status: "waiting_manual",
          error: {
            code: "BATCH_ARTIFACT_MISSING",
            message: "artifact missing"
          }
        })
      ).continue
    ).toBe(false);
  });

  it("deduplicates consecutive identical task states", () => {
    const events: TaskTimelineEvent[] = [
      {
        id: 1,
        taskId: "task-1",
        event: "task.created",
        data: { status: "pending" },
        createdAt: "2026-11-01T00:00:00Z"
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
        createdAt: "2026-11-01T00:00:01Z"
      },
      {
        id: 3,
        taskId: "task-1",
        event: "task.updated",
        data: {
          status: "running",
          currentStage: "searching",
          currentAction: "search"
        },
        createdAt: "2026-11-01T00:00:02Z"
      }
    ];

    const timeline = buildTaskTimeline(events);
    expect(timeline).toHaveLength(2);
    expect(timeline[1].label).toContain("检索");
  });

  it("keeps a data-processing event with changed completion counts", () => {
    const events: TaskTimelineEvent[] = [
      {
        id: 1,
        taskId: "task-1",
        event: "data.run.updated",
        data: {
          status: "running",
          currentStage: "document-1-3",
          currentAction: "process-policy",
          progressSnapshot: {
            overall: 0.35,
            stage: "documents",
            completedUnits: 1,
            totalUnits: 3,
            succeeded: 1,
            failed: 0,
            pendingReview: 0
          }
        },
        createdAt: "2026-11-01T00:00:01Z"
      },
      {
        id: 2,
        taskId: "task-1",
        event: "data.run.updated",
        data: {
          status: "running",
          currentStage: "document-1-3",
          currentAction: "process-policy",
          progressSnapshot: {
            overall: 0.35,
            stage: "documents",
            completedUnits: 2,
            totalUnits: 3,
            succeeded: 1,
            failed: 1,
            pendingReview: 0
          }
        },
        createdAt: "2026-11-01T00:00:01.300Z"
      }
    ];

    const timeline = buildTaskTimeline(events);
    expect(timeline).toHaveLength(2);
    expect(timeline[1].detail).toContain("成功 1");
    expect(timeline[1].detail).toContain("失败 1");
  });

  it("collapses a brief manual-wait bounce that returns to the same stage", () => {
    const events: TaskTimelineEvent[] = [
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
          status: "waiting_manual",
          currentStage: "searching",
          currentAction: "search"
        },
        createdAt: "2026-11-01T00:00:01.300Z"
      },
      {
        id: 3,
        taskId: "task-1",
        event: "task.updated",
        data: {
          status: "running",
          currentStage: "searching",
          currentAction: "search"
        },
        createdAt: "2026-11-01T00:00:01.700Z"
      }
    ];

    const timeline = buildTaskTimeline(events);
    expect(timeline).toHaveLength(1);
    expect(timeline[0].status).toBe("running");
  });

  it("classifies SHA-256 states without assuming missing values pass", () => {
    expect(hashState("a".repeat(64))).toBe("complete");
    expect(hashState("")).toBe("missing");
    expect(hashState("a".repeat(64), "OUTPUT_HASH_MISMATCH")).toBe(
      "mismatch"
    );
  });
});
