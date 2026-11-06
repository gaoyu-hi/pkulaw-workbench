import { describe, expect, it } from "vitest";
import type {
  FreshTaskSeed,
  TaskCapabilities,
  TaskRecord
} from "../src/shared/protocol";
import {
  prefillBatchCollectorForm,
  prefillSingleCollectorForm
} from "../src/renderer/src/collector-reset";
import {
  collectionActions,
  derivePrimaryTaskAction,
  terminalActions
} from "../src/renderer/src/task-presentation";

const task = {
  id: "old-task",
  name: "已停止任务",
  mode: "single",
  status: "stopped",
  input: {},
  outputDir: "D:/output",
  progress: 0.2,
  currentStage: "stopped",
  error: { code: "TASK_STOPPED", message: "stopped" },
  checkpoint: { currentPage: 2 },
  archivedAt: null,
  archiveReason: "",
  startedAt: null,
  finishedAt: null,
  createdAt: "",
  updatedAt: ""
} satisfies TaskRecord;

const capabilities = {
  canContinueManual: false,
  canResumeFromCheckpoint: true,
  canEditSettings: false,
  canCreateFreshTask: true,
  leaseHeld: false,
  leaseTaskId: "",
  resumeBlockReason: ""
} satisfies TaskCapabilities;

const seed = {
  mode: "single",
  input: {
    queries: ["残疾人体育"],
    keywords: ["残疾人体育"],
    matchMode: "keyword",
    library: "local",
    yearMode: "range",
    startYear: 2020,
    endYear: 2024,
    province: "北京市",
    timeliness: "全部（仅记录元数据，不筛选）",
    pageSize: 20,
    pageMode: "limit",
    pageSelection: { mode: "limit", maxPages: 3 },
    maxPages: 3,
    catalogFormat: "xls"
  },
  outputDir: "D:/output",
  oldTaskId: "old-task",
  oldTaskPreserved: true,
  mayDuplicateDownloads: true,
  leaseHeld: false
} satisfies FreshTaskSeed;

function captureSingleSetters() {
  const values: Record<string, unknown> = {};
  return {
    values,
    setters: {
      setQueries: (value: string) => { values.queries = value; },
      setMatchMode: (value: string) => { values.matchMode = value; },
      setLibrary: (value: string) => { values.library = value; },
      setYearSelection: (value: unknown) => { values.yearSelection = value; },
      setProvince: (value: string) => { values.province = value; },
      setTimeliness: (value: string) => { values.timeliness = value; },
      setPageSize: (value: number) => { values.pageSize = value; },
      setPageSelection: (value: unknown) => { values.pageSelection = value; },
      setOutputDir: (value: string) => { values.outputDir = value; }
    }
  };
}

describe("fresh task actions", () => {
  it("uses backend capabilities for terminal actions", () => {
    expect(terminalActions({ task, capabilities })).toContainEqual(
      expect.objectContaining({ kind: "fresh-task", enabled: true })
    );
    expect(collectionActions(task, capabilities).continue).toBe(false);
    expect(
      derivePrimaryTaskAction(task, capabilities)
    ).toBe("resume-from-checkpoint");
  });

  it("prefills a fresh single form without checkpoint identity", () => {
    const captured = captureSingleSetters();

    prefillSingleCollectorForm(seed, captured.setters);

    expect(captured.values).toMatchObject({
      queries: "残疾人体育",
      library: "local",
      province: "北京市",
      pageSize: 20,
      outputDir: "D:/output",
      yearSelection: {
        mode: "range",
        startYear: 2020,
        endYear: 2024
      },
      pageSelection: { mode: "limit", maxPages: 3 }
    });
    expect(captured.values).not.toHaveProperty("checkpoint");
    expect(captured.values).not.toHaveProperty("searchId");
  });

  it("keeps batch format while using the same sanitized seed", () => {
    const captured = captureSingleSetters();
    let catalogFormat = "";

    prefillBatchCollectorForm(
      { ...seed, mode: "batch" },
      {
        ...captured.setters,
        setCatalogFormat: (value) => { catalogFormat = value; }
      }
    );

    expect(captured.values.queries).toBe("残疾人体育");
    expect(catalogFormat).toBe("xls");
  });
});
