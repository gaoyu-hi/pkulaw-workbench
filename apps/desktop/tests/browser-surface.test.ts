import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import BrowserSurface, {
  browserBoundsForVisibility
} from "../src/renderer/src/BrowserSurface";
import type { BrowserHostState } from "../src/shared/protocol";

const browser: BrowserHostState = {
  status: "ready",
  institutionId: "institution-1",
  sessionKey: "session-1",
  url: "https://www.pkulaw.com/law/chl",
  title: "中央法规数据库",
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
    leaseId: "",
    taskId: "",
    mode: "",
    createdAt: "",
    updatedAt: ""
  }
};

describe("BrowserSurface", () => {
  it("renders the browser toolbar and exposes host visibility", () => {
    const markup = renderToStaticMarkup(
      React.createElement(BrowserSurface, {
        browser,
        visible: true,
        variant: "task",
        onRefresh: () => undefined,
        onContinue: () => undefined
      })
    );

    expect(markup).toContain('data-browser-visible="true"');
    expect(markup).toContain("https://www.pkulaw.com/law/chl");
    expect(markup).toContain("100%");
  });

  it("renders a hidden host when the surface is collapsed", () => {
    const markup = renderToStaticMarkup(
      React.createElement(BrowserSurface, {
        browser,
        visible: false,
        variant: "task",
        onRefresh: () => undefined,
        onContinue: () => undefined
      })
    );

    expect(markup).toContain('data-browser-visible="false"');
  });

  it("collapses native view bounds to a hidden one-pixel rectangle", () => {
    expect(
      browserBoundsForVisibility(
        {
          left: 320,
          top: 120,
          width: 800,
          height: 600
        },
        false
      )
    ).toEqual({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      visible: false
    });
  });
});
