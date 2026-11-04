import { app, screen } from "electron";
import fsp from "node:fs/promises";
import path from "node:path";
import type {
  AppInfo,
  DiagnosticsCheck,
  DiagnosticsReport
} from "../shared/protocol";
import type { BrowserHost } from "./browser-host";
import type { PythonService } from "./python-service";

interface EngineDiagnostics {
  generatedAt: string;
  checks: DiagnosticsCheck[];
  engine: DiagnosticsReport["engine"];
  paths: DiagnosticsReport["paths"];
  database: DiagnosticsReport["database"];
  dependencies: Record<string, string>;
  legacyConverters: Record<string, string>;
  disk: DiagnosticsReport["disk"];
  platform: DiagnosticsReport["platform"];
}

async function checkWritableDirectory(directory: string): Promise<boolean> {
  const marker = path.join(directory, `.pkulaw-desktop-write-${process.pid}-${Date.now()}`);
  try {
    await fsp.mkdir(directory, { recursive: true });
    await fsp.writeFile(marker, "ok", "utf8");
    await fsp.unlink(marker);
    return true;
  } catch {
    try {
      await fsp.unlink(marker);
    } catch {
      // The probe file may not have been created.
    }
    return false;
  }
}

async function probeNetwork(target: string): Promise<DiagnosticsReport["desktop"]["network"]> {
  if (!target) {
    return {
      status: "skipped",
      target: "",
      detail: "尚未选择机构入口",
      latencyMs: 0
    };
  }
  const started = Date.now();
  try {
    const response = await fetch(target, {
      method: "GET",
      redirect: "manual",
      signal: AbortSignal.timeout(7_000)
    });
    const latencyMs = Date.now() - started;
    return {
      status: "ok",
      target,
      detail: `可达，HTTP ${response.status}${response.status === 403 ? "（可能需要校园网或 VPN）" : ""}`,
      latencyMs
    };
  } catch (error) {
    return {
      status: "error",
      target,
      detail: `无法连接：${error instanceof Error ? error.message : String(error)}`,
      latencyMs: Date.now() - started
    };
  }
}

export async function runDiagnostics(
  appInfo: AppInfo,
  pythonService: PythonService,
  browserHost: BrowserHost
): Promise<DiagnosticsReport> {
  const engine = (await pythonService.request(
    "system.diagnostics",
    {},
    20_000
  )) as unknown as EngineDiagnostics;
  const networkTarget =
    browserHost.getCurrentProfile()?.entryUrl || "";
  const downloadDirectory = path.join(
    appInfo.dataDirectory,
    "downloads",
    "staging"
  );
  const [
    network,
    dataDirectoryWritable,
    logsDirectoryWritable,
    downloadDirectoryWritable,
    tempDirectoryWritable
  ] = await Promise.all([
    probeNetwork(networkTarget),
    checkWritableDirectory(appInfo.dataDirectory),
    checkWritableDirectory(appInfo.logsDirectory),
    checkWritableDirectory(downloadDirectory),
    checkWritableDirectory(engine.paths.tempDirectory)
  ]);
  const display = screen.getPrimaryDisplay();
  const browser = browserHost.getState();
  const checks = [...engine.checks];
  checks.push(
    {
      id: "desktop-data-directory",
      label: "桌面数据目录写入",
      status: dataDirectoryWritable ? "ok" : "error",
      detail: dataDirectoryWritable ? appInfo.dataDirectory : "目录不可写"
    },
    {
      id: "desktop-logs-directory",
      label: "日志目录可写",
      status: logsDirectoryWritable ? "ok" : "error",
      detail: logsDirectoryWritable ? appInfo.logsDirectory : "日志目录不可写"
    },
    {
      id: "desktop-download-directory",
      label: "下载临时目录可写",
      status: downloadDirectoryWritable ? "ok" : "error",
      detail: downloadDirectoryWritable ? downloadDirectory : "下载临时目录不可写"
    },
    {
      id: "desktop-system-temp",
      label: "系统临时目录可写",
      status: tempDirectoryWritable ? "ok" : "error",
      detail: tempDirectoryWritable ? engine.paths.tempDirectory : "系统临时目录不可写"
    },
    {
      id: "network",
      label: "机构入口网络可达",
      status: network.status,
      detail: network.detail
    },
    {
      id: "packaged-backend",
      label: "Python 运行时",
      status: appInfo.backendMode === "bundled" ? "ok" : "warning",
      detail:
        appInfo.backendMode === "bundled"
          ? "使用应用内置 Python 后端"
          : "当前为源码开发模式，使用外部 Python"
    },
    {
      id: "selector-profile",
      label: "北大法宝选择器适配",
      status: !browser.targetBound
        ? "skipped"
        : browser.selectorReady
          ? "ok"
          : "warning",
      detail: browser.targetBound
        ? `${browser.selectorProfile || "未识别"} ${browser.selectorVersion || ""}：${browser.selectorMessage}`
        : "尚未绑定内置网页"
    }
  );

  return {
    ...engine,
    generatedAt: new Date().toISOString(),
    checks,
    paths: {
      ...engine.paths,
      downloadDirectory
    },
    desktop: {
      electronVersion: process.versions.electron,
      chromeVersion: process.versions.chrome,
      nodeVersion: process.versions.node,
      packaged: app.isPackaged,
      platform: process.platform,
      arch: process.arch,
      scaleFactor: display.scaleFactor,
      displaySize: display.size,
      dataDirectoryWritable,
      network
    }
  };
}
