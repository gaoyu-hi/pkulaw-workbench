import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import BatchCollectorPanel from "../src/renderer/src/BatchCollectorPanel";
import SingleCollectorPanel from "../src/renderer/src/SingleCollectorPanel";
import YearRangeControl from "../src/renderer/src/YearRangeControl";
import {
  effectiveProvince,
  showsProvinceControl
} from "../src/renderer/src/collector-library-policy";
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

  it("derives province behavior from the selected regulation library", () => {
    expect(showsProvinceControl("central")).toBe(false);
    expect(showsProvinceControl("local")).toBe(true);
    expect(showsProvinceControl("both")).toBe(true);
    expect(effectiveProvince("central", "北京市")).toBe("全部省份");
    expect(effectiveProvince("local", "北京市")).toBe("北京市");
    expect(effectiveProvince("both", "北京市")).toBe("北京市");
  });

  it("hides the province selector for central laws only", () => {
    useMemoryStorage({
      "pkulaw.collector.single.v1.library": "central",
      "pkulaw.collector.batch.v1.library": "central"
    });
    const centralSingle = renderToStaticMarkup(
      React.createElement(SingleCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );
    const centralBatch = renderToStaticMarkup(
      React.createElement(BatchCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );

    expect(centralSingle).not.toContain(
      'data-testid="collector-province-row"'
    );
    expect(centralBatch).not.toContain(
      'data-testid="collector-province-row"'
    );

    useMemoryStorage({
      "pkulaw.collector.single.v1.library": "local",
      "pkulaw.collector.batch.v1.library": "both"
    });
    const localSingle = renderToStaticMarkup(
      React.createElement(SingleCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );
    const bothBatch = renderToStaticMarkup(
      React.createElement(BatchCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );

    expect(localSingle).toContain(
      'data-testid="collector-province-row"'
    );
    expect(bothBatch).toContain(
      'data-testid="collector-province-row"'
    );
  });

  it("starts fresh production collectors with empty query fields", () => {
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

    expect(single).toContain('<textarea rows="4"></textarea>');
    expect(batch).toContain('<textarea rows="4"></textarea>');
    expect(single).not.toContain("placeholder=");
    expect(batch).not.toContain("placeholder=");
  });

  it("restores collector drafts after a panel remount", () => {
    useMemoryStorage({
      "pkulaw.collector.single.v1.queries": "人工智能\n数据安全",
      "pkulaw.collector.single.v1.yearSelection": {
        mode: "single",
        year: 1999
      },
      "pkulaw.collector.batch.v1.queries": "碳中和\n生态环境"
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
    expect(batch).toContain("碳中和\n生态环境");
  });

  it("migrates only the retired product default to an empty query", () => {
    useMemoryStorage({
      "pkulaw.collector.single.v1.queries": "人工智能",
      "pkulaw.collector.batch.v1.queries": "人工智能"
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

    expect(single).not.toContain("人工智能");
    expect(batch).not.toContain("人工智能");
    expect(single).toContain('<textarea rows="4"></textarea>');
    expect(batch).toContain('<textarea rows="4"></textarea>');
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

  it("offers three single modes and two batch modes by default", () => {
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
    expect(single).toContain(
      '<option value="keyword" selected="">关键词</option>'
    );
    expect(single).toContain('<option value="fulltext">全文</option>');
    expect(single).toContain(
      '<option value="title">精确完整标题</option>'
    );
    expect(single).toContain("精确完整标题");
    expect(batch).toContain('value="keyword" selected=""');
    expect(batch).toContain(
      '<option value="keyword" selected="">关键词</option>'
    );
    expect(batch).toContain('<option value="fulltext">全文</option>');
    expect(batch).not.toContain("精确完整标题");
  });

  it("restores full-text mode on single and batch collectors", () => {
    useMemoryStorage({
      "pkulaw.collector.single.v1.matchMode": "fulltext",
      "pkulaw.collector.batch.v1.matchMode": "fulltext"
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

    expect(single).toContain('<option value="fulltext" selected="">全文</option>');
    expect(batch).toContain('<option value="fulltext" selected="">全文</option>');
  });

  it("uses one direct-start action without a separate preview step", () => {
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

    expect(single).not.toContain("预览（0 下载）");
    expect(batch).not.toContain("预览（0 下载）");
    expect(single).toContain("创建并开始逐篇采集");
    expect(batch).toContain("创建并开始批量采集");
  });

  it("explains result-window probing before the task starts", () => {
    const single = renderToStaticMarkup(
      React.createElement(SingleCollectorPanel, {
        appInfo: null,
        backend,
        onOpenBrowser: () => undefined
      })
    );

    expect(single).toContain("启动前自动检查结果窗口");
    expect(single).toContain("核验期间下载为 0");
  });

  it("keeps single and batch primary actions in a fixed form footer", () => {
    useMemoryStorage({
      "pkulaw.collector.single.v1.library": "both",
      "pkulaw.collector.batch.v1.library": "both"
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

    for (const [markup, action] of [
      [single, "创建并开始逐篇采集"],
      [batch, "创建并开始批量采集"]
    ] as const) {
      const bodyIndex = markup.indexOf("collector-form-body");
      const footerIndex = markup.indexOf("collector-action-footer");
      const actionIndex = markup.indexOf(action);

      expect(markup).toContain("collector-form-shell");
      expect(markup).toContain("collector-year-row");
      expect(markup).toContain('data-testid="collector-province-row"');
      expect(bodyIndex).toBeGreaterThan(-1);
      expect(footerIndex).toBeGreaterThan(bodyIndex);
      expect(actionIndex).toBeGreaterThan(footerIndex);
      expect(
        markup.match(/collector-form-guidance/g)?.length || 0
      ).toBeLessThanOrEqual(1);
    }
  });
});
