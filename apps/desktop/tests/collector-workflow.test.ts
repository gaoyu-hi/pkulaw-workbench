import { describe, expect, it } from "vitest";
import {
  canStartCollector,
  collectorFormKey,
  collectorPreviewCardView,
  defaultCollectorMatchMode
} from "../src/renderer/src/collector-workflow";
import type { TaskPreviewSummary } from "../src/shared/protocol";

const preview: TaskPreviewSummary = {
  formFingerprint: "form-1",
  previewFingerprint: "preview-1",
  generatedAt: "2026-11-02T08:00:00Z",
  mode: "single",
  queryCount: 1,
  libraryCount: 2,
  yearCount: 1,
  combinationCount: 2,
  totalResults: 5,
  totalPages: 3,
  estimatedBatches50: 1,
  windowStatus: "probe_required",
  downloadsStarted: 0,
  downloadIntentsCreated: 0,
  tasksCreated: 0,
  combinations: [
    {
      query: "碳中和",
      matchMode: "keyword",
      library: "local",
      year: "全部年份",
      status: "ready",
      resultCount: 17224,
      totalPages: 173,
      reportedTotalPages: 20,
      expectedTotalPages: 173,
      accessibleCapacity: 2000,
      actualPageSize: 100,
      groupingMode: "ungrouped",
      searchId: "search-1",
      resultSignature: "signature-1",
      actualYear: "全部年份",
      actualProvince: "全部省份",
      actualTimeliness: "全部（仅记录元数据，不筛选）",
      windowStatus: "truncated",
      windowCode: "RESULT_WINDOW_TRUNCATED"
    }
  ]
};

describe("collector workflow", () => {
  it("defaults both new task forms to keyword matching", () => {
    expect(defaultCollectorMatchMode("single")).toBe("keyword");
    expect(defaultCollectorMatchMode("batch")).toBe("keyword");
  });

  it("invalidates a preview when any form field changes", () => {
    const first = collectorFormKey("single", {
      queries: ["碳中和"],
      matchMode: "keyword",
      library: "both",
      yearSelection: { mode: "all" },
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 50
    });
    const second = collectorFormKey("single", {
      queries: ["碳中和 "],
      matchMode: "keyword",
      library: "both",
      yearSelection: { mode: "all" },
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 50
    });

    expect(first).toBe(second);
    expect(
      collectorFormKey("single", {
        queries: ["碳中和"],
        matchMode: "title",
        library: "both",
        yearSelection: { mode: "all" },
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 50
      })
    ).not.toBe(first);
  });

  it("requires a fresh successful preview and an available browser lease", () => {
    expect(
      canStartCollector({
        preview,
        previewFormKey: "form-1",
        currentFormKey: "form-1",
        busy: false,
        backendReady: true,
        browserLeaseBlocked: false
      })
    ).toBe(true);
    expect(
      canStartCollector({
        preview,
        previewFormKey: "form-1",
        currentFormKey: "form-2",
        busy: false,
        backendReady: true,
        browserLeaseBlocked: false
      })
    ).toBe(false);
    expect(
      canStartCollector({
        preview,
        previewFormKey: "form-1",
        currentFormKey: "form-1",
        busy: false,
        backendReady: true,
        browserLeaseBlocked: true
      })
    ).toBe(false);
  });

  it("renders every zero-download preview metric", () => {
    const view = collectorPreviewCardView(preview, true);

    expect(view.combinations).toBe("2");
    expect(view.results).toBe("5");
    expect(view.pages).toBe("3");
    expect(view.batches).toBe("1");
    expect(view.downloads).toBe("0");
    expect(view.stale).toBe(false);
    expect(view.windowNotice).toContain("检测到结果窗口限制");
    expect(view.windowNotice).toContain("20");
    expect(view.windowNotice).toContain("173");
  });
});
