import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import TaskWorkspaceFrame from "../src/renderer/src/TaskWorkspaceFrame";
import {
  idleManualRecovery,
  type ManualRecoveryView
} from "../src/shared/task-workspace";
import type { BrowserHostState } from "../src/shared/protocol";

const browser: BrowserHostState = {
  status: "ready",
  institutionId: "institution-1",
  sessionKey: "session-1",
  url: "https://www.pkulaw.com/law/chl",
  title: "中央法规",
  viewLoaded: true,
  targetBound: true,
  targetUrl: "https://www.pkulaw.com/law/chl",
  selectorProfile: "pkulaw-v6",
  selectorVersion: "v1",
  selectorReady: true,
  selectorMessage: "选择器诊断通过",
  selectorDiagnostics: null,
  authenticated: true,
  loginVisible: false,
  captchaVisible: false,
  captchaSelector: "",
  captchaBox: {},
  captchaCheckedAt: "",
  zoomFactor: 1,
  canGoBack: true,
  childViews: 0,
  tabs: [],
  message: "已连接",
  lastDownload: null,
  browserLease: {
    available: true,
    leaseId: "lease-1",
    taskId: "task-1",
    mode: "single",
    createdAt: "",
    updatedAt: ""
  }
};

function renderFrame(
  settingsOpen: boolean,
  manualRecovery: ManualRecoveryView = idleManualRecovery,
  pauseDetailOpen = false
): string {
  return renderToStaticMarkup(
    React.createElement(
      TaskWorkspaceFrame,
      {
        mode: "single",
        browser,
        settingsOpen,
        pauseDetailOpen,
        manualRecovery,
        onToggleSettings: () => undefined,
        taskPanel: React.createElement(
          "div",
          { "data-testid": "task-panel" },
          "运行任务面板"
        ),
        settingsPanel: React.createElement(
          "div",
          { "data-testid": "settings-panel" },
          "任务设置内容"
        )
      }
    )
  );
}

describe("TaskWorkspaceFrame", () => {
  it("shows task progress and browser together", () => {
    const markup = renderFrame(false);

    expect(markup).toContain('data-testid="task-panel"');
    expect(markup).toContain('data-browser-visible="true"');
    expect(markup).toContain("任务设置");
    expect(markup).not.toContain("新建任务");
  });

  it("hides the browser while task settings are open", () => {
    const markup = renderFrame(true);

    expect(markup).toContain('data-testid="settings-panel"');
    expect(markup).not.toContain('data-testid="task-panel"');
    expect(markup).toContain('data-browser-visible="false"');
    expect(markup).toContain('class="task-workspace-body settings-open"');
    expect(markup.indexOf("task-workspace-primary")).toBeLessThan(
      markup.indexOf("settings-panel")
    );
  });

  it("hides the native browser while pause details are open", () => {
    const markup = renderFrame(false, idleManualRecovery, true);

    expect(markup).toContain('data-browser-visible="false"');
    expect(markup).toContain("dialog-open");
  });

  it("restores the native browser when pause details close", () => {
    const markup = renderFrame(false, idleManualRecovery, false);

    expect(markup).toContain('data-browser-visible="true"');
    expect(markup).not.toContain("dialog-open");
  });

  it("shows automatic recovery failure without a duplicate retry action", () => {
    const markup = renderFrame(false, {
      phase: "failed",
      detail: "自动恢复失败，请重新检测浏览器状态。",
      canRetry: true
    });

    expect(markup).toContain("自动恢复失败");
    expect(markup).not.toContain("重新检测并继续");
    expect(markup).toContain('data-browser-visible="true"');
  });
});
