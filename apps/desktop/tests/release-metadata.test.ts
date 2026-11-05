import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(import.meta.dirname, "../../..");

function read(relativePath: string): string {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

describe("release metadata", () => {
  it("unifies the product version and active source copy", () => {
    const rootPackage = JSON.parse(read("package.json")) as {
      version: string;
    };
    const desktopPackage = JSON.parse(
      read("apps/desktop/package.json")
    ) as { version: string };
    const activeSources = [
      "apps/desktop/src/main/index.ts",
      "apps/desktop/src/renderer/src/App.tsx",
      "apps/desktop/src/renderer/src/DataPanel.tsx",
      "apps/desktop/src/renderer/src/SettingsPanel.tsx",
      "apps/desktop/src/renderer/src/TaskCenterPanel.tsx"
    ];

    expect(rootPackage.version).toBe("0.2.4");
    expect(desktopPackage.version).toBe("0.2.4");
    expect(read("apps/desktop/src/main/index.ts")).toContain(
      'appInfo.version !== "0.2.4"'
    );
    for (const file of activeSources) {
      expect(read(file), file).not.toContain("0.2.1");
    }
  });

  it("records the current SQLite schema in portable verification", () => {
    const verifier = read("scripts/verify-portable.mjs");

    expect(verifier).toContain("sqliteSchemaVersion: 6");
    expect(verifier).not.toContain("sqliteSchemaVersion: 5");
  });

  it("preserves real portable data after building an archive without it", () => {
    const builder = read("scripts/build-portable.mjs");

    expect(builder).toContain(".portable-data-preserve-current");
    expect(builder).toContain("preservePortableData");
    expect(builder).toContain(
      "await fsp.rename(portableDataBackup, portableDataPath)"
    );
  });
});
