import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

describe("data panel source provenance contract", () => {
  it("renders body source, converter and review filters", () => {
    const source = fs.readFileSync(
      path.join(import.meta.dirname, "../src/renderer/src/DataPanel.tsx"),
      "utf8"
    );

    expect(source).toContain("matchesDataDocument");
    expect(source).toContain("bodyCompletenessLabel");
    expect(source).toContain("reviewStatusLabel");
    expect(source).toContain("北大法宝ZIP");
    expect(source).toContain("正文完整性筛选");
    expect(source).toContain("核验状态筛选");
    expect(source).toContain("正文来源、转换器与核验详情");
    expect(source).toContain("失败原因");
  });
});
