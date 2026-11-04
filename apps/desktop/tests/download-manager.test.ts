import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  DownloadManager,
  sanitizeDownloadFilename,
  type DownloadEvent
} from "../src/main/download-manager";

class FakeSession extends EventEmitter {}

class FakeDownloadItem extends EventEmitter {
  private filename: string;
  private savePath = "";

  constructor(filename: string) {
    super();
    this.filename = filename;
  }

  getFilename(): string {
    return this.filename;
  }

  getURL(): string {
    return "https://www.pkulaw.com/download/file";
  }

  getReceivedBytes(): number {
    return 0;
  }

  getTotalBytes(): number {
    return 10;
  }

  getState(): string {
    return "progressing";
  }

  setSavePath(value: string): void {
    this.savePath = value;
  }

  getSavePath(): string {
    return this.savePath;
  }

  cancel(): void {
    this.emit("done", {}, "cancelled");
  }
}

describe("DownloadManager filename safety", () => {
  const temporaryDirectories: string[] = [];

  afterEach(() => {
    for (const directory of temporaryDirectories.splice(0)) {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });

  it("sanitizes Windows reserved names and trailing characters", () => {
    expect(sanitizeDownloadFilename("CON")).toBe("_CON");
    expect(sanitizeDownloadFilename("NUL.doc")).toBe("_NUL.doc");
    expect(sanitizeDownloadFilename("COM1.docx")).toBe("_COM1.docx");
    expect(sanitizeDownloadFilename("policy. . ")).toBe("policy");
    expect(sanitizeDownloadFilename("bad<>:?\"|name.doc")).toBe("bad_name.doc");
  });

  it("bounds long names while preserving a short extension", () => {
    const result = sanitizeDownloadFilename(`${"政".repeat(300)}.docx`, 60);
    expect(result.length).toBeLessThanOrEqual(60);
    expect(result.endsWith(".docx")).toBe(true);
  });

  it("uses a unique target directory for equal filenames", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "pkulaw-download-"));
    temporaryDirectories.push(root);
    const session = new FakeSession();
    const events: DownloadEvent[] = [];
    const manager = new DownloadManager(
      session as never,
      root,
      (event) => events.push(event)
    );

    session.emit("will-download", {}, new FakeDownloadItem("same.doc"), {});
    session.emit("will-download", {}, new FakeDownloadItem("same.doc"), {});

    expect(events).toHaveLength(2);
    expect(manager.hasActiveDownloads()).toBe(true);
    expect(events[0].downloadId).not.toBe(events[1].downloadId);
    expect(path.dirname(events[0].path)).not.toBe(path.dirname(events[1].path));
    manager.dispose();
  });

  it("emits a structured failure when the download root is unusable", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "pkulaw-download-"));
    temporaryDirectories.push(root);
    const fileRoot = path.join(root, "not-a-directory");
    fs.writeFileSync(fileRoot, "x");
    const session = new FakeSession();
    const events: DownloadEvent[] = [];
    const manager = new DownloadManager(
      session as never,
      fileRoot,
      (event) => events.push(event)
    );

    session.emit("will-download", {}, new FakeDownloadItem("policy.doc"), {});

    expect(events.map((event) => event.event)).toEqual(["started", "failed"]);
    expect(events[1].error).toContain("DOWNLOAD_DIRECTORY_UNAVAILABLE");
    manager.dispose();
  });

  it("forwards progress and both completed and failed terminal states", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "pkulaw-download-"));
    temporaryDirectories.push(root);
    const session = new FakeSession();
    const events: DownloadEvent[] = [];
    const manager = new DownloadManager(
      session as never,
      root,
      (event) => events.push(event)
    );
    const completedItem = new FakeDownloadItem("completed.doc");
    const failedItem = new FakeDownloadItem("failed.doc");

    session.emit("will-download", {}, completedItem, {});
    completedItem.emit("updated", {}, "progressing");
    expect(manager.hasActiveDownloads()).toBe(true);
    completedItem.emit("done", {}, "completed");
    session.emit("will-download", {}, failedItem, {});
    failedItem.emit("done", {}, "interrupted");

    expect(events.map((event) => event.event)).toEqual([
      "started",
      "progress",
      "completed",
      "started",
      "failed"
    ]);
    expect(events[2].state).toBe("completed");
    expect(events[4].state).toBe("interrupted");
    expect(manager.hasActiveDownloads()).toBe(false);
    manager.dispose();
  });

  it("falls back to a safe filename for empty and very short limits", () => {
    expect(sanitizeDownloadFilename("")).toBe("download.bin");
    expect(sanitizeDownloadFilename(".", 1)).toBe("download.bin");
  });
});
