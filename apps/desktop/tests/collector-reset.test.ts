import { createElement, useEffect } from "react";
import TestRenderer, { act } from "react-test-renderer";
import { describe, expect, it } from "vitest";
import {
  canOpenTaskSettings,
  resetBatchCollectorForm,
  resetSingleCollectorForm,
  shouldAutoSelectTask,
  shouldLoadTaskDetails,
  shouldResetCollector,
  useCollectorAutoLoad
} from "../src/renderer/src/collector-reset";
import type { CollectorAutoLoadController } from "../src/renderer/src/collector-reset";

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

  it("opens task settings only when the task is paused", () => {
    expect(canOpenTaskSettings("paused")).toBe(true);
    expect(canOpenTaskSettings("running")).toBe(false);
    expect(canOpenTaskSettings("waiting_manual")).toBe(false);
    expect(canOpenTaskSettings(undefined)).toBe(false);
  });

  it("keeps a completed task suppressed until the user creates or selects another task", () => {
    expect(shouldAutoSelectTask("task-a", "task-a")).toBe(false);
    expect(shouldAutoSelectTask("task-b", "task-a")).toBe(true);
    expect(shouldAutoSelectTask(undefined, "task-a")).toBe(false);
    expect(shouldAutoSelectTask("task-a", "")).toBe(true);
  });

  it("blocks automatic detail loading of the suppressed completed task from refresh and engine events", () => {
    expect(shouldLoadTaskDetails("task-a", "task-a")).toBe(false);
    expect(shouldLoadTaskDetails("task-b", "task-a")).toBe(true);
    expect(shouldLoadTaskDetails(undefined, "task-a")).toBe(false);
    expect(shouldLoadTaskDetails("task-a", "")).toBe(true);
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

  it("keeps the completed task suppressed across production async ordering", async () => {
    let controller: CollectorAutoLoadController | undefined;

    function HookProbe({
      onReady
    }: {
      onReady: (value: CollectorAutoLoadController) => void;
    }) {
      const value = useCollectorAutoLoad();
      useEffect(() => {
        onReady(value);
      }, [onReady, value]);
      return null;
    }

    const onReady = (value: CollectorAutoLoadController) => {
      controller = value;
    };

    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(HookProbe, { onReady })
      );
      await Promise.resolve();
    });

    expect(controller).toBeDefined();
    const current = controller as CollectorAutoLoadController;

    await act(async () => {
      expect(current.observeStatus("task-a", "running")).toBe(false);
      expect(current.observeStatus("task-a", "completed")).toBe(true);
      await Promise.resolve();
    });

    expect(current.canLoadTaskDetails("task-a")).toBe(false);
    expect(current.canAutoSelectTask("task-a")).toBe(false);
    expect(current.canLoadTaskDetails("task-b")).toBe(true);
    expect(current.canAutoSelectTask("task-b")).toBe(true);

    await act(async () => {
      current.clearForExplicitSelection();
      await Promise.resolve();
    });

    expect(current.canLoadTaskDetails("task-a")).toBe(true);
    expect(current.canAutoSelectTask("task-a")).toBe(true);
    renderer?.unmount();
  });
});
