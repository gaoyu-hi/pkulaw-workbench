import { spawnSync } from "node:child_process";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { ensureWritableDirectory } from "../src/main/path-safety";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  for (const directory of temporaryDirectories.splice(0)) {
    await fsp.rm(directory, { recursive: true, force: true });
  }
});

describe("ensureWritableDirectory", () => {
  it("creates Chinese, space and long path segments", async () => {
    const root = await fsp.mkdtemp(path.join(os.tmpdir(), "pkulaw 路径-"));
    temporaryDirectories.push(root);
    const longDirectory = path.join(
      root,
      "政策 采集",
      "非常长的目录名称".repeat(8),
      "任务输出"
    );

    const resolved = await ensureWritableDirectory(longDirectory);

    expect(resolved).toBe(path.resolve(longDirectory));
    expect((await fsp.stat(resolved)).isDirectory()).toBe(true);
  });

  it("rejects a file used as the output directory", async () => {
    const root = await fsp.mkdtemp(path.join(os.tmpdir(), "pkulaw-file-"));
    temporaryDirectories.push(root);
    const filePath = path.join(root, "not-a-directory");
    await fsp.writeFile(filePath, "occupied", "utf8");

    await expect(ensureWritableDirectory(filePath)).rejects.toThrow(
      "输出目录不可写"
    );
  });

  it.skipIf(process.platform !== "win32")(
    "rejects a Windows ACL read-only directory",
    async () => {
      const root = await fsp.mkdtemp(path.join(os.tmpdir(), "pkulaw-readonly-"));
      temporaryDirectories.push(root);
      const readOnly = path.join(root, "只读 输出");
      await fsp.mkdir(readOnly);
      const identity = spawnSync("whoami.exe", [], {
        encoding: "utf8",
        windowsHide: true
      });
      if (identity.status !== 0 || !identity.stdout.trim()) {
        throw new Error("Unable to resolve the current Windows identity");
      }
      const user = identity.stdout.trim();
      const apply = spawnSync(
        "icacls.exe",
        [
          readOnly,
          "/inheritance:r",
          "/grant:r",
          `${user}:(OI)(CI)(F)`,
          "/deny",
          `${user}:(W)`
        ],
        { encoding: "utf8", windowsHide: true }
      );
      if (apply.status !== 0) {
        throw new Error(`Unable to create read-only ACL: ${apply.stderr}`);
      }
      try {
        await expect(ensureWritableDirectory(readOnly)).rejects.toThrow(
          "输出目录不可写"
        );
      } finally {
        spawnSync(
          "icacls.exe",
          [readOnly, "/remove:d", user],
          { encoding: "utf8", windowsHide: true }
        );
        spawnSync(
          "icacls.exe",
          [
            readOnly,
            "/inheritance:e",
            "/grant",
            `${user}:(OI)(CI)F`
          ],
          { encoding: "utf8", windowsHide: true }
        );
      }
    }
  );
});
