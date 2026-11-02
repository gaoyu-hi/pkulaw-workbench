import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import BatchCollectorPanel from "../src/renderer/src/BatchCollectorPanel";
import SingleCollectorPanel from "../src/renderer/src/SingleCollectorPanel";
import type { BackendState } from "../src/shared/protocol";

const backend: BackendState = {
  status: "ready",
  pid: 100,
  protocolVersion: "1.0.0",
  engineVersion: "0.2.2",
  message: "ready"
};

describe("collector panel copy and entry gates", () => {
  it("uses keyword by default and uses exact full-title copy only on single tasks", () => {
    const single = renderToStaticMarkup(
      React.createElement(SingleCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );
    const batch = renderToStaticMarkup(
      React.createElement(BatchCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );

    expect(single).toContain('value="keyword" selected=""');
    expect(single).toContain("精确完整标题");
    expect(single).toContain("同名但稳定政策 ID 不同");
    expect(batch).toContain("固定使用关键词检索");
    expect(batch).not.toContain("精确完整标题");
  });

  it("keeps collection disabled until a fresh zero-download preview exists", () => {
    const single = renderToStaticMarkup(
      React.createElement(SingleCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );
    const batch = renderToStaticMarkup(
      React.createElement(BatchCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );

    expect(single).toContain("预览（0 下载）");
    expect(single).toContain("请先完成 0 下载预览");
    expect(batch).toContain("预览（0 下载）");
    expect(batch).toContain("请先完成 0 下载预览");
  });

  it("explains result-window probing before the task starts", () => {
    const single = renderToStaticMarkup(
      React.createElement(SingleCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );

    expect(single).toContain("结果窗口");
    expect(single).toContain("0 下载探测");
  });
});
