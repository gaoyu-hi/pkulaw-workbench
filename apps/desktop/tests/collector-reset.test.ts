import { describe, expect, it } from "vitest";
import {
  resetBatchCollectorForm,
  resetSingleCollectorForm,
  shouldAutoSelectTask,
  shouldResetCollector
} from "../src/renderer/src/collector-reset";

function recordingSetters() {
  const calls: Record<string, unknown> = {};
  return {
    calls,
    setQueries: (value: string) => {
      calls.queries = value;
    },
    setMatchMode: (value: unknown) => {
      calls.matchMode = value;
    },
    setLibrary: (value: unknown) => {
      calls.library = value;
    },
    setYearSelection: (value: unknown) => {
      calls.yearSelection = value;
    },
    setProvince: (value: string) => {
      calls.province = value;
    },
    setTimeliness: (value: string) => {
      calls.timeliness = value;
    },
    setPageSize: (value: number) => {
      calls.pageSize = value;
    },
    setPageSelection: (value: unknown) => {
      calls.pageSelection = value;
    },
    setCatalogFormat: (value: unknown) => {
      calls.catalogFormat = value;
    },
    setOutputDir: (value: string) => {
      calls.outputDir = value;
    }
  };
}

describe("collector completion reset", () => {
  it("resets only on a same-task transition into completed", () => {
    expect(shouldResetCollector("running", "completed")).toBe(true);
    expect(shouldResetCollector("paused", "completed")).toBe(true);
    expect(shouldResetCollector("failed", "completed")).toBe(true);
    expect(shouldResetCollector("completed", "completed")).toBe(false);
    expect(shouldResetCollector("running", "failed")).toBe(false);
    expect(shouldResetCollector(undefined, "completed")).toBe(false);
  });

  it("keeps a completed task suppressed until the user creates or selects another task", () => {
    expect(shouldAutoSelectTask("task-a", "task-a")).toBe(false);
    expect(shouldAutoSelectTask("task-b", "task-a")).toBe(true);
    expect(shouldAutoSelectTask(undefined, "task-a")).toBe(false);
    expect(shouldAutoSelectTask("task-a", "")).toBe(true);
  });

  it("resets the single collector to defaults", () => {
    const set = recordingSetters();
    resetSingleCollectorForm("D:/single-default", set);
    expect(set.calls).toMatchObject({
      queries: "",
      matchMode: "keyword",
      library: "both",
      yearSelection: { mode: "all" },
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 20,
      pageSelection: { mode: "last" },
      outputDir: "D:/single-default"
    });
  });

  it("resets the batch collector to defaults", () => {
    const set = recordingSetters();
    resetBatchCollectorForm("D:/batch-default", set);
    expect(set.calls).toMatchObject({
      queries: "",
      matchMode: "keyword",
      library: "central",
      yearSelection: { mode: "all" },
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 50,
      pageSelection: { mode: "last" },
      catalogFormat: "xlsx",
      outputDir: "D:/batch-default"
    });
  });
});
