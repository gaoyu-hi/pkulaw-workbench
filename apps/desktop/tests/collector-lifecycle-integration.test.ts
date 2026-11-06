import { createElement } from "react";
import TestRenderer, { act } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BatchCollectorPanel from "../src/renderer/src/BatchCollectorPanel";
import SingleCollectorPanel from "../src/renderer/src/SingleCollectorPanel";
import TaskSettingsRevisionDialog from "../src/renderer/src/TaskSettingsRevisionDialog";
import type {
  ProtocolError,
  TaskSettingsRevisionPreview
} from "../src/shared/protocol";

const browser = {
  status: "ready",
  institutionId: "inst-1",
  sessionKey: "",
  url: "",
  title: "",
  viewLoaded: true,
  targetBound: true,
  targetUrl: "",
  selectorProfile: "",
  selectorVersion: "",
  selectorReady: true,
  selectorMessage: "",
  selectorDiagnostics: null,
  authenticated: true,
  loginVisible: false,
  captchaVisible: false,
  captchaSelector: "",
  captchaBox: {},
  captchaCheckedAt: "",
  zoomFactor: 1,
  canGoBack: false,
  childViews: 0,
  tabs: [],
  message: "",
  lastDownload: null,
  browserLease: {
    available: true,
    leaseId: "",
    taskId: "",
    mode: "",
    createdAt: "",
    updatedAt: ""
  }
};

const preview: TaskSettingsRevisionPreview = {
  token: "token-1",
  changed: true,
  generation: 2,
  artifactSummary: {
    completedPolicyCount: 3,
    completedBatchCount: 0,
    registeredFileCount: 3,
    eligibleFileCount: 3,
    protectedFileCount: 0,
    directoryCount: 1,
    estimatedFreedBytes: 4096
  },
  protectedFiles: []
};

function taskRecord(
  id: string,
  mode: "single" | "batch",
  status: string
) {
  return {
    id,
    name: `${mode === "single" ? "逐篇" : "批量"}任务 ${id}`,
    mode,
    status,
    outputDir: "D:/输出",
    updatedAt: "2026-11-06T10:00:00Z",
    createdAt: "2026-11-06T09:00:00Z"
  };
}

function taskDetails(
  mode: "single" | "batch",
  status: string,
  error: ProtocolError | null = null
) {
  const task = taskRecord("task-1", mode, status);
  return {
    task: {
      ...task,
      input: {
        queries: ["人工智能"],
        keywords: ["人工智能"],
        matchMode: "keyword",
        library: mode === "single" ? "both" : "central",
        yearSelection: { mode: "all" },
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: mode === "single" ? 20 : 50,
        pageSelection: { mode: "last" },
        searchStrategyVersion: 2,
        outputDir: "D:/输出"
      },
      outputDir: "D:/输出",
      checkpoint: { configGeneration: 1, currentAction: status },
      progress: status === "completed" ? 1 : 0.5,
      currentStage: status,
      updatedAt: "2026-11-06T10:00:00Z",
      error
    },
    items: [],
    batches: []
  };
}

function updateResult(mode: "single" | "batch") {
  return {
    changed: true,
    generation: 2,
    task: taskRecord("task-1", mode, "running"),
    details: taskDetails(mode, "running")
  };
}

function buttonByText(
  renderer: TestRenderer.ReactTestRenderer,
  text: string
) {
  return renderer.root
    .findAllByType("button")
    .map((button) => ({
      node: button,
      text: String(button.children.map((child) => String(child)).join(""))
    }))
    .find((item) => item.text.includes(text))?.node;
}

function taskButtonByText(
  renderer: TestRenderer.ReactTestRenderer,
  text: string
) {
  return renderer.root
    .findAllByType("button")
    .find((button) =>
      button.findAllByType("span").some((span) =>
        String(span.children.map((child) => String(child)).join("")).includes(
          text
        )
      )
    );
}

describe("collector lifecycle integration", () => {
  const pkulaw = {
    listTasks: vi.fn(),
    getTaskDetails: vi.fn(),
    getBatchTaskDetails: vi.fn(),
    getBrowserState: vi.fn(),
    onBrowserState: vi.fn(),
    onEngineEvent: vi.fn(),
    startCollectorTask: vi.fn(),
    applyTaskSettings: vi.fn(),
    prepareTaskSettingsRevision: vi.fn(),
    commitTaskSettingsRevision: vi.fn(),
    pauseSingleTask: vi.fn(),
    pauseBatchTask: vi.fn(),
    resumeSingleTask: vi.fn(),
    resumeBatchTask: vi.fn(),
    stopSingleTask: vi.fn(),
    stopBatchTask: vi.fn(),
    retrySingleTask: vi.fn(),
    retryBatchTask: vi.fn(),
    continueSingleTask: vi.fn(),
    continueBatchTask: vi.fn(),
    continueSingleToLast: vi.fn(),
    continueBatchToLast: vi.fn(),
    confirmBatchScale: vi.fn(),
    continueCaptureBoundary: vi.fn(),
    chooseCandidate: vi.fn(),
    browserContinue: vi.fn(),
    resumeTaskAfterManual: vi.fn(),
    selectOutputDirectory: vi.fn()
  };

  let engineHandler: ((event: Record<string, unknown>) => void) | undefined;

  beforeEach(() => {
    (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    (globalThis as unknown as { window: unknown }).window = {
      pkulaw,
      setInterval: globalThis.setInterval,
      clearInterval: globalThis.clearInterval
    };
    (globalThis as unknown as { localStorage?: unknown }).localStorage = {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined
    };
    pkulaw.onBrowserState.mockReturnValue(() => undefined);
    pkulaw.onEngineEvent.mockImplementation(
      (handler: (event: Record<string, unknown>) => void) => {
        engineHandler = handler;
        return () => undefined;
      }
    );
    pkulaw.getBrowserState.mockResolvedValue(browser);
    pkulaw.prepareTaskSettingsRevision.mockResolvedValue(preview);
    vi.clearAllMocks();
  });

  afterEach(() => {
    engineHandler = undefined;
    vi.clearAllMocks();
  });

  it("single completion resets to a blank form and keeps history selectable", async () => {
    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "single", "running")
    ]);
    pkulaw.getTaskDetails.mockResolvedValue(
      taskDetails("single", "running")
    );

    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(SingleCollectorPanel, {
          appInfo: { defaultSingleOutputDir: "D:/输出" } as never,
          backend: { status: "ready" } as never,
          onOpenBrowser: vi.fn()
        })
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const active = renderer as TestRenderer.ReactTestRenderer;
    expect(pkulaw.getTaskDetails).toHaveBeenCalledWith("task-1");

    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "single", "completed")
    ]);
    pkulaw.getTaskDetails.mockResolvedValue(
      taskDetails("single", "completed")
    );
    await act(async () => {
      engineHandler?.({
        event: "single.completed",
        data: { taskId: "task-1" }
      });
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const textareas = active.root.findAllByType("textarea");
    expect(String(textareas[0]?.props.value ?? "")).toBe("");
    expect(JSON.stringify(active.toJSON())).toContain("逐篇任务 task-1");

    pkulaw.getTaskDetails.mockClear();
    const historyButton = taskButtonByText(active, "逐篇任务 task-1");
    await act(async () => {
      historyButton?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(pkulaw.getTaskDetails).toHaveBeenCalledWith("task-1");
    expect(JSON.stringify(active.toJSON())).toContain("已完成");

    renderer?.unmount();
  });

  it("batch completion resets to a blank form and keeps history selectable", async () => {
    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "batch", "running")
    ]);
    pkulaw.getBatchTaskDetails.mockResolvedValue(
      taskDetails("batch", "running")
    );

    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(BatchCollectorPanel, {
          appInfo: { defaultBatchOutputDir: "D:/输出" } as never,
          backend: { status: "ready" } as never,
          onOpenBrowser: vi.fn()
        })
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const active = renderer as TestRenderer.ReactTestRenderer;
    expect(pkulaw.getBatchTaskDetails).toHaveBeenCalledWith("task-1");

    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "batch", "completed")
    ]);
    pkulaw.getBatchTaskDetails.mockResolvedValue(
      taskDetails("batch", "completed")
    );
    await act(async () => {
      engineHandler?.({
        event: "batch.completed",
        data: { taskId: "task-1" }
      });
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const textareas = active.root.findAllByType("textarea");
    expect(String(textareas[0]?.props.value ?? "")).toBe("");
    expect(JSON.stringify(active.toJSON())).toContain("批量任务 task-1");

    pkulaw.getBatchTaskDetails.mockClear();
    const historyButton = taskButtonByText(active, "批量任务 task-1");
    await act(async () => {
      historyButton?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(pkulaw.getBatchTaskDetails).toHaveBeenCalledWith("task-1");
    expect(JSON.stringify(active.toJSON())).toContain("已完成");

    renderer?.unmount();
  });

  it("single paused preserve commits the same task with preserve policy", async () => {
    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "single", "paused")
    ]);
    pkulaw.getTaskDetails.mockResolvedValue(
      taskDetails("single", "paused")
    );
    pkulaw.commitTaskSettingsRevision.mockResolvedValue(
      updateResult("single")
    );

    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(SingleCollectorPanel, {
          appInfo: { defaultSingleOutputDir: "D:/输出" } as never,
          backend: { status: "ready" } as never,
          onOpenBrowser: vi.fn()
        })
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    const active = renderer as TestRenderer.ReactTestRenderer;

    const settingsToggle = buttonByText(active, "任务设置");
    await act(async () => {
      settingsToggle?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });
    const save = buttonByText(active, "保存修改");
    await act(async () => {
      save?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });

    const dialog = active.root.findByType(TaskSettingsRevisionDialog);
    const preserve = buttonByText(active, "保留已完成文件并重新扫描");
    await act(async () => {
      await dialog.props.onCommit("preserve");
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(pkulaw.commitTaskSettingsRevision).toHaveBeenCalledWith(
      "task-1",
      "token-1",
      "preserve",
      expect.any(String)
    );
    expect(active.root.findAllByType(TaskSettingsRevisionDialog)).toHaveLength(0);
    expect(JSON.stringify(active.toJSON())).toContain("运行中");
    renderer?.unmount();
  });

  it("single paused delete commits the same task with delete policy", async () => {
    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "single", "paused")
    ]);
    pkulaw.getTaskDetails.mockResolvedValue(
      taskDetails("single", "paused")
    );
    pkulaw.commitTaskSettingsRevision.mockResolvedValue(
      updateResult("single")
    );

    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(SingleCollectorPanel, {
          appInfo: { defaultSingleOutputDir: "D:/输出" } as never,
          backend: { status: "ready" } as never,
          onOpenBrowser: vi.fn()
        })
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    const active = renderer as TestRenderer.ReactTestRenderer;

    await act(async () => {
      buttonByText(active, "任务设置")?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      buttonByText(active, "保存修改")?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });

    const dialog = active.root.findByType(TaskSettingsRevisionDialog);
    await act(async () => {
      buttonByText(active, "永久删除旧结果并重新扫描")?.props.onClick();
      await Promise.resolve();
    });
    await act(async () => {
      await dialog.props.onCommit("delete");
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(pkulaw.commitTaskSettingsRevision).toHaveBeenCalledWith(
      "task-1",
      "token-1",
      "delete",
      expect.any(String)
    );
    expect(active.root.findAllByType(TaskSettingsRevisionDialog)).toHaveLength(0);
    expect(JSON.stringify(active.toJSON())).toContain("运行中");
    renderer?.unmount();
  });

  it("batch paused preserve commits the same task with preserve policy", async () => {
    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "batch", "paused")
    ]);
    pkulaw.getBatchTaskDetails.mockResolvedValue(
      taskDetails("batch", "paused")
    );
    pkulaw.commitTaskSettingsRevision.mockResolvedValue(
      updateResult("batch")
    );

    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(BatchCollectorPanel, {
          appInfo: { defaultBatchOutputDir: "D:/输出" } as never,
          backend: { status: "ready" } as never,
          onOpenBrowser: vi.fn()
        })
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    const active = renderer as TestRenderer.ReactTestRenderer;

    await act(async () => {
      buttonByText(active, "任务设置")?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      buttonByText(active, "保存修改")?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });

    const dialog = active.root.findByType(TaskSettingsRevisionDialog);
    await act(async () => {
      await dialog.props.onCommit("preserve");
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(pkulaw.commitTaskSettingsRevision).toHaveBeenCalledWith(
      "task-1",
      "token-1",
      "preserve",
      expect.any(String)
    );
    expect(active.root.findAllByType(TaskSettingsRevisionDialog)).toHaveLength(0);
    expect(JSON.stringify(active.toJSON())).toContain("运行中");
    renderer?.unmount();
  });

  it("batch paused delete commits the same task with delete policy", async () => {
    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "batch", "paused")
    ]);
    pkulaw.getBatchTaskDetails.mockResolvedValue(
      taskDetails("batch", "paused")
    );
    pkulaw.commitTaskSettingsRevision.mockResolvedValue(
      updateResult("batch")
    );

    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(BatchCollectorPanel, {
          appInfo: { defaultBatchOutputDir: "D:/输出" } as never,
          backend: { status: "ready" } as never,
          onOpenBrowser: vi.fn()
        })
      );
      await Promise.resolve();
      await Promise.resolve();
    });
    const active = renderer as TestRenderer.ReactTestRenderer;

    await act(async () => {
      buttonByText(active, "任务设置")?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      buttonByText(active, "保存修改")?.props.onClick();
      await Promise.resolve();
      await Promise.resolve();
    });

    const dialog = active.root.findByType(TaskSettingsRevisionDialog);
    await act(async () => {
      buttonByText(active, "永久删除旧结果并重新扫描")?.props.onClick();
      await Promise.resolve();
    });
    await act(async () => {
      await dialog.props.onCommit("delete");
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(pkulaw.commitTaskSettingsRevision).toHaveBeenCalledWith(
      "task-1",
      "token-1",
      "delete",
      expect.any(String)
    );
    expect(active.root.findAllByType(TaskSettingsRevisionDialog)).toHaveLength(0);
    expect(JSON.stringify(active.toJSON())).toContain("运行中");
    renderer?.unmount();
  });

  it("single boundary continue submits exactly one engine call on rapid double click", async () => {
    pkulaw.listTasks.mockResolvedValue([
      taskRecord("task-1", "single", "waiting_manual")
    ]);
    const boundaryDetails = taskDetails("single", "waiting_manual", {
      code: "CAPTURE_BOUNDARY_REACHED",
      message: "已达到物理采集边界"
    });
    pkulaw.getTaskDetails.mockResolvedValue(boundaryDetails);
    pkulaw.continueCaptureBoundary.mockResolvedValue(
      taskDetails("single", "running")
    );

    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(SingleCollectorPanel, {
          appInfo: { defaultSingleOutputDir: "D:/输出" } as never,
          backend: { status: "ready" } as never,
          onOpenBrowser: vi.fn()
        })
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const active = renderer as TestRenderer.ReactTestRenderer;
    const boundaryButton = buttonByText(active, "继续完整补采");
    expect(boundaryButton).toBeDefined();

    act(() => {
      (boundaryButton?.props.onClick as () => void)?.();
      (boundaryButton?.props.onClick as () => void)?.();
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(pkulaw.continueCaptureBoundary).toHaveBeenCalledTimes(1);
    renderer?.unmount();
  });
});
