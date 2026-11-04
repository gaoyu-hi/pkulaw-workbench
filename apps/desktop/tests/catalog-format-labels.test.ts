import { describe, expect, it } from "vitest";

import { CATALOG_FORMAT_LABELS } from "../src/shared/catalog-formats";

describe("catalog format labels", () => {
  it("distinguishes native and locally derived catalogs", () => {
    expect(CATALOG_FORMAT_LABELS.xls).toBe("网站原生 XLS");
    expect(CATALOG_FORMAT_LABELS.xlsx).toBe(
      "本地转换 XLSX（源自网站 XLS）"
    );
    expect(CATALOG_FORMAT_LABELS["html-xls"]).toBe(
      "兼容 HTML-XLS（本地生成）"
    );
  });
});
