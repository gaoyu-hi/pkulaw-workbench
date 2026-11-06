import { createElement } from "react";
import TestRenderer, { act } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SingleCollectorPanel from "../src/renderer/src/SingleCollectorPanel";
import type { TaskSettingsRevisionPreview } from "../src/shared/protocol";

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

function buttonByText(
  renderer: TestRenderer.ReactTestRenderer,
  text: string
) {
  return renderer.root
    .findAllByType("button")
    .map((button) => ({
      node: button,
      text: String(
        button.children
          .map((child) => String(child))
          .join("")
      )
    }))
    .find((item) => item.text.includes(text))?.node;
}

describe("SingleCollectorPanel settings commit", () => {
  const pkulaw = {
    listTasks: vi.fn(),
    getTaskDetails: vi.fn(),
    getBrowserState: vi.fn(),
    onBrowserState: vi.fn(),
    onEngineEvent: vi.fn(),
    prepareTaskSettingsRevision: vi.fn(),
    commitTaskSettingsRevision: vi.fn(),
    pauseSingleTask: vi.fn(),
    applyTaskSettings: vi.fn(),
    resumeTaskAfterManual: vi.fn()
  };

  beforeEach(() => {
    (globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    (globalThis as unknown as { window: unknown }).window = {
      pkulaw,
      setInterval: globalThis.setInterval,
      clearInterval: globalThis.clearInterval
    };
    pkulaw.listTasks.mockResolvedValue([
      {
        id: "task-1",
        name: "暂停中的逐篇任务",
        mode: "single",
        status: "paused",
        outputDir: "D:/输出",
        updatedAt: "2026-11-06T10:00:00Z",
        createdAt: "2026-11-06T09:00:00Z"
      }
    ]);
    pkulaw.getTaskDetails.mockResolvedValue({
      task: {
        id: "task-1",
        name: "暂停中的逐篇任务",
        mode: "single",
        status: "paused",
        input: {
          queries: ["人工智能"],
          keywords: ["人工智能"],
          matchMode: "keyword",
          library: "central",
          yearSelection: { mode: "all" },
          province: "全部省份",
          timeliness: "全部（仅记录元数据，不筛选）",
          pageSize: 20,
          pageSelection: { mode: "last" },
          searchStrategyVersion: 2,
          outputDir: "D:/输出"
        },
        outputDir: "D:/输出",
        checkpoint: {
          configGeneration: 1,
          currentAction: "paused"
        },
        progress: 0.5,
        currentStage: "paused",
        updatedAt: "2026-11-06T10:00:00Z",
        error: null
      },
      items: []
    });
    pkulaw.getBrowserState.mockResolvedValue(browser);
    pkulaw.onBrowserState.mockReturnValue(() => undefined);
    pkulaw.onEngineEvent.mockReturnValue(() => undefined);
    pkulaw.prepareTaskSettingsRevision.mockResolvedValue(preview);
    pkulaw.commitTaskSettingsRevision.mockRejectedValue(
      new Error("TASK_OUTPUT_DELETE_BLOCKED：永久删除被阻止")
    );
  });

  afterEach(() => {
    (globalThis as unknown as { window?: unknown }).window = undefined;
    vi.clearAllMocks();
  });

  it("keeps the revision dialog open after the parent commit is rejected", async () => {
    let renderer: TestRenderer.ReactTestRenderer | undefined;
    await act(async () => {
      renderer = TestRenderer.create(
        createElement(SingleCollectorPanel, {
          appInfo: {
            defaultSingleOutputDir: "D:/输出"
          } as never,
          backend: { status: "ready" } as never,
          onOpenBrowser: vi.fn()
        })
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const active = renderer as TestRenderer.ReactTestRenderer;
    expect(pkulaw.listTasks).toHaveBeenCalled();
    expect(pkulaw.getTaskDetails).toHaveBeenCalledWith("task-1");

    const settingsToggle = buttonByText(active, "任务设置");
    expect(settingsToggle).toBeDefined();
    await act(async () => {
      (settingsToggle?.props.onClick as () => void)?.();
      await Promise.resolve();
      await Promise.resolve();
    });

    const saveButton = buttonByText(active, "保存修改");
    expect(saveButton).toBeDefined();
    await act(async () => {
      (saveButton?.props.onClick as () => void)?.();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(pkulaw.prepareTaskSettingsRevision).toHaveBeenCalledTimes(1);

    const deleteButton = buttonByText(active, "永久删除旧结果并重新扫描");
    expect(deleteButton).toBeDefined();
    await act(async () => {
      (deleteButton?.props.onClick as () => void)?.();
      await Promise.resolve();
    });

    const confirmButton = buttonByText(
      active,
      "确认永久删除并重新扫描"
    );
    expect(confirmButton).toBeDefined();
    await act(async () => {
      (confirmButton?.props.onClick as () => void)?.();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(pkulaw.commitTaskSettingsRevision).toHaveBeenCalledTimes(1);
    expect(
      JSON.stringify(
        renderer?.toJSON() ?? ""
      )
    ).toContain("TASK_OUTPUT_DELETE_BLOCKED");
    expect(renderer?.root.findAllByType("section").length).toBeGreaterThan(0);

    await act(async () => {
      renderer?.unmount();
    });
  });
});
