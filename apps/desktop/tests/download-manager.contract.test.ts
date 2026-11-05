import { describe, expect, it } from "vitest";

const baseline = process.env.PKULAW_PHASE_A_BASELINE === "1";

describe.skipIf(!baseline)("Phase A download manager contracts", () => {
  it("exports Windows-safe filename normalization", async () => {
    const module = await import("../src/main/download-manager");
    expect(
      (module as Record<string, unknown>).sanitizeDownloadFilename
    ).toBeTypeOf("function");
  });
});
