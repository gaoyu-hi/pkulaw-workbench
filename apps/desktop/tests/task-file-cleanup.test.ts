import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { CleanupFileRecord, CleanupPreview } from "../src/shared/protocol";
import {
  TrashCleanupError,
  trashRegisteredFiles
} from "../src/main/task-file-cleanup";

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) =>
      rm(directory, { recursive: true, force: true })
    )
  );
});

function cleanupFile(
  filePath: string,
  overrides: Partial<CleanupFileRecord> = {}
): CleanupFileRecord {
  return {
    path: filePath,
    rawPath: filePath,
    taskIds: ["task-1"],
    sources: ["artifact"],
    exists: true,
    canTrash: true,
    reason: "",
    ...overrides
  };
}

function preview(files: CleanupFileRecord[]): CleanupPreview {
  return {
    tasks: [],
    files,
    eligibleFiles: files.filter((file) => file.canTrash && !file.reason),
    protectedFiles: files.filter((file) => !file.canTrash || Boolean(file.reason)),
    directories: [],
    summary: {
      taskCount: 1,
      registeredFileCount: files.length,
      eligibleFileCount: files.filter((file) => file.canTrash && !file.reason).length,
      existingEligibleFileCount: files.filter(
        (file) => file.canTrash && !file.reason && file.exists
      ).length,
      protectedFileCount: files.filter((file) => !file.canTrash || Boolean(file.reason)).length,
      directoryCount: 0
    }
  };
}

describe("task file cleanup", () => {
  it("deduplicates files, skips missing and protected entries, and calls trash", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "pkulaw-cleanup-"));
    temporaryDirectories.push(directory);
    const file = path.join(directory, "中文 空格.txt");
    const missing = path.join(directory, "missing.txt");
    await writeFile(file, "ok", "utf8");
    const calls: string[] = [];

    const result = await trashRegisteredFiles(
      preview([
        cleanupFile(file),
        cleanupFile(file),
        cleanupFile(missing, { exists: false }),
        cleanupFile(path.join(directory, "shared.txt"), {
          canTrash: false,
          reason: "SHARED_WITH_OTHER_TASK"
        })
      ]),
      {
        protectedRoots: [directory],
        trashItem: async (target) => {
          calls.push(target);
        }
      }
    );

    expect(calls).toEqual([path.resolve(file)]);
    expect(result.trashedFiles).toBe(1);
    expect(result.missingFiles).toBe(1);
  });

  it("stops on a trash failure and reports already processed files", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "pkulaw-cleanup-"));
    temporaryDirectories.push(directory);
    const first = path.join(directory, "first.txt");
    const second = path.join(directory, "second.txt");
    await writeFile(first, "one", "utf8");
    await writeFile(second, "two", "utf8");
    const calls: string[] = [];

    await expect(
      trashRegisteredFiles(preview([cleanupFile(first), cleanupFile(second)]), {
        protectedRoots: [directory],
        trashItem: async (target) => {
          calls.push(target);
          if (target === path.resolve(second)) {
            throw new Error("file is in use");
          }
        }
      })
    ).rejects.toMatchObject({
      name: "TrashCleanupError",
      details: {
        failedPath: path.resolve(second),
        trashedPaths: [path.resolve(first)],
        missingPaths: []
      }
    });
    expect(calls).toEqual([path.resolve(first), path.resolve(second)]);
  });

  it("rejects traversal-like paths before calling trash", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "pkulaw-cleanup-"));
    temporaryDirectories.push(directory);
    const target = path.join(directory, "inside.txt");
    await writeFile(target, "ok", "utf8");
    const rawPath = `${directory}\\..\\inside.txt`;
    let called = false;

    await expect(
      trashRegisteredFiles(
        preview([cleanupFile(target, { rawPath })]),
        {
          protectedRoots: [directory],
          trashItem: async () => {
            called = true;
          }
        }
      )
    ).rejects.toBeInstanceOf(TrashCleanupError);
    expect(called).toBe(false);
  });

  it("handles a long Chinese and spaced path without touching the directory", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "pkulaw-cleanup-"));
    temporaryDirectories.push(directory);
    const nested = path.join(directory, "政策 采集".repeat(20));
    const target = path.join(nested, "登记 文件.txt");
    await mkdir(nested, { recursive: true });
    await writeFile(target, "long path", "utf8");
    const calls: string[] = [];

    const result = await trashRegisteredFiles(
      preview([cleanupFile(target)]),
      {
        protectedRoots: [directory],
        trashItem: async (pathValue) => {
          calls.push(pathValue);
        }
      }
    );

    expect(result.trashedFiles).toBe(1);
    expect(calls).toEqual([path.resolve(target)]);
  });

  it("rejects directories even if a malformed preview marks them eligible", async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), "pkulaw-cleanup-"));
    temporaryDirectories.push(directory);
    const nested = path.join(directory, "must-stay");
    await mkdir(nested);
    let called = false;

    await expect(
      trashRegisteredFiles(preview([cleanupFile(nested)]), {
        protectedRoots: [directory],
        trashItem: async () => {
          called = true;
        }
      })
    ).rejects.toBeInstanceOf(TrashCleanupError);
    expect(called).toBe(false);
  });
});
