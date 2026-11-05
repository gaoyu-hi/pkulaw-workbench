import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { CleanupPreview } from "../shared/protocol";

export interface TrashCleanupOptions {
  protectedRoots?: string[];
  trashItem: (targetPath: string) => Promise<void>;
}

export interface TrashCleanupResult {
  trashedFiles: number;
  missingFiles: number;
  trashedPaths: string[];
  missingPaths: string[];
}

export class TrashCleanupError extends Error {
  readonly details: {
    failedPath: string;
    trashedPaths: string[];
    missingPaths: string[];
    cause: string;
  };

  constructor(
    failedPath: string,
    cause: unknown,
    trashedPaths: string[],
    missingPaths: string[]
  ) {
    const message = cause instanceof Error ? cause.message : String(cause);
    super(`无法将登记文件移入系统回收站：${failedPath} (${message})`);
    this.name = "TrashCleanupError";
    this.details = {
      failedPath,
      trashedPaths,
      missingPaths,
      cause: message
    };
  }
}

function normalizedPath(value: string): string {
  return path.normalize(path.resolve(value));
}

function samePath(left: string, right: string): boolean {
  return process.platform === "win32"
    ? left.toLowerCase() === right.toLowerCase()
    : left === right;
}

function isWithin(target: string, root: string): boolean {
  const relative = path.relative(root, target);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function protectedRoots(options: TrashCleanupOptions): string[] {
  return [
    path.parse(process.cwd()).root,
    os.homedir(),
    ...(options.protectedRoots || [])
  ]
    .filter(Boolean)
    .map(normalizedPath);
}

function assertSafeTrashPath(rawPath: string, target: string, roots: string[]): void {
  if (!path.isAbsolute(rawPath)) {
    throw new Error("登记文件路径不是绝对路径");
  }
  if (rawPath.split(/[\\/]+/).includes("..")) {
    throw new Error("登记文件路径包含路径穿越片段");
  }
  if (roots.some((root) => samePath(target, root))) {
    throw new Error("禁止将受保护的目录或磁盘根移入回收站");
  }
  if (roots.some((root) => isWithin(target, root))) {
    // Files inside a user home are allowed only when they are explicit
    // registered files, but the home directory itself is never a target.
  }
}

export async function trashRegisteredFiles(
  preview: CleanupPreview,
  options: TrashCleanupOptions
): Promise<TrashCleanupResult> {
  const roots = protectedRoots(options);
  const seen = new Set<string>();
  const candidates = preview.eligibleFiles
    .filter((item) => item.canTrash && !item.reason)
    .map((item) => ({
      rawPath: item.rawPath || item.path,
      target: normalizedPath(item.path)
    }))
    .filter((item) => {
      const key =
        process.platform === "win32"
          ? item.target.toLowerCase()
          : item.target;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

  const trashedPaths: string[] = [];
  const missingPaths: string[] = [];
  for (const target of candidates) {
    try {
      assertSafeTrashPath(target.rawPath, target.target, roots);
      let stats: fs.Stats;
      try {
        stats = await fsp.lstat(target.target);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") {
          missingPaths.push(target.target);
          continue;
        }
        throw error;
      }
      if (!stats.isFile()) {
        throw new Error("登记目标不是普通文件，拒绝移入回收站");
      }
      await options.trashItem(target.target);
      trashedPaths.push(target.target);
    } catch (error) {
      throw new TrashCleanupError(
        target.target,
        error,
        trashedPaths,
        missingPaths
      );
    }
  }

  return {
    trashedFiles: trashedPaths.length,
    missingFiles: missingPaths.length,
    trashedPaths,
    missingPaths
  };
}
