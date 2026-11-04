import { describe, expect, it } from "vitest";
import type { BrowserHostState, TaskRecord } from "../src/shared/protocol";
import {
  holdingTaskLabel,
  leaseBlocksNewBrowserTask,
  newOperationId
} from "../src/shared/browser-lease";

function browserState(
  lease: BrowserHostState["browserLease"]
): BrowserHostState {
  return {
    status: "ready",
    institutionId: "fixture",
    sessionKey: "fixture",
    url: "http://127.0.0.1/",
    title: "fixture",
    viewLoaded: true,
    targetBound: true,
    targetUrl: "http://127.0.0.1/",
    selectorProfile: "fixture",
    selectorVersion: "1",
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
    browserLease: lease
  };
}

const availableLease: BrowserHostState["browserLease"] = {
  available: true,
  leaseId: "",
  taskId: "",
  mode: "",
  createdAt: "",
  updatedAt: ""
};

describe("browser lease UI contract", () => {
  it("blocks new work and explains the holding task and mode", () => {
    const state = browserState({
      available: false,
      leaseId: "lease-1",
      taskId: "task-1",
      mode: "batch",
      createdAt: "2026-11-01T00:00:00Z",
      updatedAt: "2026-11-01T00:00:01Z"
    });
    const tasks = [
      {
        id: "task-1",
        name: "批量采集测试"
      } as TaskRecord
    ];

    expect(leaseBlocksNewBrowserTask(state)).toBe(true);
    expect(leaseBlocksNewBrowserTask(state, "task-1")).toBe(false);
    expect(holdingTaskLabel(state, tasks)).toContain("批量采集测试");
    expect(holdingTaskLabel(state, tasks)).toContain("批量采集");
  });

  it("allows local work when no browser lease is held", () => {
    const state = browserState(availableLease);
    expect(leaseBlocksNewBrowserTask(state)).toBe(false);
    expect(holdingTaskLabel(state, [])).toContain("空闲");
  });

  it("creates unique operation ids for request-level idempotency", () => {
    const ids = new Set(Array.from({ length: 100 }, () => newOperationId()));
    expect(ids.size).toBe(100);
  });
});
