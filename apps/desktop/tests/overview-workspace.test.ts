import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import App from "../src/renderer/src/App";
import InstitutionWorkspace, {
  InstitutionStatusCard
} from "../src/renderer/src/InstitutionWorkspace";
import type { BrowserHostState } from "../src/shared/protocol";

const browser: BrowserHostState = {
  status: "ready",
  institutionId: "institution-1",
  sessionKey: "session-1",
  url: "https://www.pkulaw.com/",
  title: "北大法宝",
  viewLoaded: true,
  targetBound: true,
  targetUrl: "https://www.pkulaw.com/",
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
  canGoBack: false,
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

describe("overview institution workspace", () => {
  it("removes the old browser navigation and decorative hero", () => {
    const markup = renderToStaticMarkup(React.createElement(App));

    expect(markup).not.toContain("内置浏览器");
    expect(markup).not.toContain("从采集到可审计交付");
  });

  it("shows institution configuration and an embedded browser before login", () => {
    const markup = renderToStaticMarkup(
      React.createElement(InstitutionWorkspace, {
        appInfo: null,
        backend: {
          status: "ready",
          pid: 1,
          protocolVersion: "1.0.0",
          engineVersion: "0.2.2",
          message: "ready"
        },
        onNavigate: () => undefined
      })
    );

    expect(markup).toContain("机构配置");
    expect(markup).toContain("授权入口");
    expect(markup).toContain("打开机构页面");
    expect(markup).toContain("切换账号");
    expect(markup).toContain('data-browser-visible="true"');
  });

  it("collapses an authenticated institution into a status card", () => {
    const markup = renderToStaticMarkup(
      React.createElement(InstitutionStatusCard, {
        browser,
        institutionName: "中国地质大学（武汉）",
        entryUrl: "https://www.pkulaw.com/",
        onOpen: () => undefined,
        onSwitch: () => undefined,
        onRefresh: () => undefined
      })
    );

    expect(markup).toContain("中国地质大学（武汉）");
    expect(markup).toContain("机构已认证");
    expect(markup).toContain("选择器诊断通过");
    expect(markup).toContain("切换账号");
  });

  it("gives all three overview entry actions equal visual weight", () => {
    const markup = renderToStaticMarkup(
      React.createElement(InstitutionWorkspace, {
        appInfo: null,
        backend: {
          status: "ready",
          pid: 1,
          protocolVersion: "1.0.0",
          engineVersion: "0.2.2",
          message: "ready"
        },
        onNavigate: () => undefined
      })
    );
    const quickEntry = markup.slice(markup.indexOf("overview-quick-entry"));

    expect(
      quickEntry.match(/class="primary-button overview-quick-action"/g)
    ).toHaveLength(3);
    expect(quickEntry).toContain("进入逐篇采集");
    expect(quickEntry).toContain("进入批量采集");
    expect(quickEntry).toContain("运行环境诊断");
  });
});
