import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import BatchCollectorPanel from "../src/renderer/src/BatchCollectorPanel";
import SingleCollectorPanel from "../src/renderer/src/SingleCollectorPanel";
import YearRangeControl from "../src/renderer/src/YearRangeControl";
import type { BackendState } from "../src/shared/protocol";

const backend: BackendState = {
  status: "ready",
  pid: 100,
  protocolVersion: "1.0.0",
  engineVersion: "0.2.2",
  message: "ready"
};

describe("collector panel copy and entry gates", () => {
  afterEach(() => {
    Reflect.deleteProperty(globalThis, "localStorage");
  });

  function useMemoryStorage(entries: Record<string, unknown> = {}): void {
    const values = new Map(
      Object.entries(entries).map(([key, value]) => [
        key,
        JSON.stringify(value)
      ])
    );
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        clear: () => values.clear(),
        key: (index: number) => [...values.keys()][index] ?? null,
        get length() {
          return values.size;
        }
      }
    });
  }

  it("does not prefill production collectors with fixture query text", () => {
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

    expect(single).not.toContain("同名政策");
    expect(batch).not.toContain("批量测试");
  });

  it("restores collector drafts after a panel remount", () => {
    useMemoryStorage({
      "pkulaw.collector.single.v1.queries": "人工智能\n数据安全",
      "pkulaw.collector.single.v1.yearSelection": {
        mode: "single",
        year: 1999
      },
      "pkulaw.collector.batch.v1.queries": "生态环境"
    });

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

    expect(single).toContain("人工智能\n数据安全");
    expect(single).toContain('value="1999"');
    expect(batch).toContain("生态环境");
  });

  it("uses manual four-digit year inputs instead of a 2000-year select", () => {
    const single = renderToStaticMarkup(
      React.createElement(YearRangeControl, {
        value: { mode: "single", year: 1999 },
        onChange: () => undefined
      })
    );
    const range = renderToStaticMarkup(
      React.createElement(YearRangeControl, {
        value: { mode: "range", startYear: 1949, endYear: 1999 },
        onChange: () => undefined
      })
    );

    expect(single).toContain('inputMode="numeric"');
    expect(single).toContain('value="1999"');
    expect(range.match(/inputMode="numeric"/g)).toHaveLength(2);
    expect(range).toContain('value="1949"');
    expect(range).toContain('value="1999"');
    expect(range).toContain(
      `title="请输入 1949–${new Date().getFullYear()} 的四位年份"`
    );
  });

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
