import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { Session, WebContents } from "electron";

export type DownloadEvent = {
  downloadId: string;
  event: "started" | "progress" | "completed" | "failed";
  url: string;
  filename: string;
  path: string;
  state: string;
  receivedBytes: number;
  totalBytes: number;
  error?: string;
};

const WINDOWS_RESERVED = /^(?:CON|PRN|AUX|NUL|CLOCK\$|COM[1-9]|LPT[1-9])(?:\.|$)/i;

export function sanitizeDownloadFilename(value: string, maxLength = 180): string {
  const normalized = String(value || "")
    .normalize("NFKC")
    .replace(/[<>:"/\\|?*\u0000-\u001f]+/g, "_")
    .replace(/^[ .]+/, "")
    .replace(/[. ]+$/g, "");
  let filename = normalized || "download.bin";
  if (WINDOWS_RESERVED.test(filename)) {
    filename = `_${filename}`;
  }
  const safeLength = Math.max(16, Math.min(240, Math.floor(maxLength)));
  if (filename.length > safeLength) {
    const extension = path.extname(filename).slice(0, 24);
    const stemLength = Math.max(1, safeLength - extension.length);
    filename = `${filename.slice(0, stemLength).replace(/[. ]+$/g, "")}${extension}`;
  }
  return filename.replace(/[. ]+$/g, "") || "download.bin";
}

export class DownloadManager {
  private readonly active = new Map<string, DownloadEvent>();

  constructor(
    private readonly browserSession: Session,
    private readonly rootDirectory: string,
    private readonly onEvent: (event: DownloadEvent) => void
  ) {
    this.browserSession.on("will-download", this.handleDownload);
  }

  dispose(): void {
    this.browserSession.removeListener("will-download", this.handleDownload);
  }

  hasActiveDownloads(): boolean {
    return [...this.active.values()].some(
      (event) => event.event === "started" || event.event === "progress"
    );
  }

  private handleDownload = (
    _event: Electron.Event,
    item: Electron.DownloadItem,
    _webContents: WebContents
  ): void => {
    const downloadId = crypto.randomUUID();
    const filename = sanitizeDownloadFilename(item.getFilename());
    const targetDirectory = path.join(this.rootDirectory, downloadId);
    const targetPath = path.join(targetDirectory, filename);
    try {
      fs.mkdirSync(targetDirectory, { recursive: true });
    } catch (error) {
      const started: DownloadEvent = {
        downloadId,
        event: "started",
        url: item.getURL(),
        filename,
        path: targetPath,
        state: "failed",
        receivedBytes: 0,
        totalBytes: item.getTotalBytes(),
        error: `DOWNLOAD_DIRECTORY_UNAVAILABLE: ${String(error)}`
      };
      this.active.set(downloadId, started);
      this.onEvent(started);
      const failed: DownloadEvent = {
        ...started,
        event: "failed",
      };
      this.active.set(downloadId, failed);
      this.onEvent(failed);
      item.cancel();
      return;
    }
    item.setSavePath(targetPath);

    const base: DownloadEvent = {
      downloadId,
      event: "started",
      url: item.getURL(),
      filename,
      path: targetPath,
      state: item.getState(),
      receivedBytes: item.getReceivedBytes(),
      totalBytes: item.getTotalBytes()
    };
    this.active.set(downloadId, base);
    this.onEvent(base);

    item.on("updated", (_downloadEvent, state) => {
      const progress: DownloadEvent = {
        ...base,
        event: "progress",
        state,
        receivedBytes: item.getReceivedBytes(),
        totalBytes: item.getTotalBytes()
      };
      this.active.set(downloadId, progress);
      this.onEvent(progress);
    });
    item.once("done", (_downloadEvent, state) => {
      const completed: DownloadEvent = {
        ...base,
        event: state === "completed" ? "completed" : "failed",
        state,
        receivedBytes: item.getReceivedBytes(),
        totalBytes: item.getTotalBytes()
      };
      this.active.set(downloadId, completed);
      this.onEvent(completed);
    });
  };
}
