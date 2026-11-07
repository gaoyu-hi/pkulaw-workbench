import {
  app,
  BrowserWindow,
  clipboard,
  desktopCapturer,
  ipcMain,
  session
} from "electron";
import { createHash } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { strFromU8, unzipSync } from "fflate";
import type {
  AppInfo,
  BackendState,
  BatchTaskDetails,
  CollectorStartResult,
  DataProcessingDetails,
  BrowserHostState,
  EngineEvent,
  InstitutionProfile,
  LogRecord,
  TaskDetails,
  TaskPreviewSummary,
  TaskRecord,
  TaskSettingsUpdateResult
} from "../shared/protocol";
import { BrowserHost } from "./browser-host";
import { runDiagnostics } from "./diagnostics";
import { FixtureServer } from "./fixture-server";
import { InstitutionStore } from "./institution-store";
import { registerIpcHandlers } from "./ipc";
import { JsonLineLogger } from "./logger";
import { EngineRequestError, PythonService } from "./python-service";
import { ensureWritableDirectory } from "./path-safety";

let mainWindow: BrowserWindow | null = null;
let pythonService: PythonService | null = null;
let browserHost: BrowserHost | null = null;
let institutionStore: InstitutionStore | null = null;
let fixtureServer: FixtureServer | null = null;
let fixtureProfile: InstitutionProfile | null = null;
let debugPort = 0;
let logger: JsonLineLogger | null = null;
let allowQuit = false;
let shutdownStarted = false;
let smokeFinished = false;
let backendReadyObserved = false;
let backendRecoveryAttempts = 0;
let backendRecoveryTimer: NodeJS.Timeout | null = null;

function resolveAppVersion(): string {
  const candidates = [
    path.resolve(__dirname, "../../package.json"),
    path.resolve(process.cwd(), "package.json")
  ];
  for (const candidate of candidates) {
    try {
      const packageInfo = JSON.parse(fs.readFileSync(candidate, "utf8")) as {
        version?: string;
      };
      if (packageInfo.version) {
        return packageInfo.version;
      }
    } catch {
      // Continue to the next development-path candidate.
    }
  }
  return app.getVersion();
}

function resolveEngineDirectory(): string {
  const candidates = [
    process.env.PKULAW_ENGINE_DIR,
    app.isPackaged ? path.join(process.resourcesPath, "engine") : undefined,
    path.resolve(process.cwd(), "../../engine"),
    path.resolve(app.getAppPath(), "../../engine")
  ].filter((value): value is string => Boolean(value));

  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, "pkulaw_engine", "__main__.py"))) {
      return candidate;
    }
  }
  throw new Error(`Unable to locate Python engine directory. Checked: ${candidates.join(", ")}`);
}

async function stageBundledEngine(
  sourceDirectory: string,
  sourceExecutable: string,
  appVersion: string
): Promise<string> {
  const stat = fs.statSync(sourceExecutable);
  const sourceIdentity =
    `${appVersion}-${stat.size}-${Math.trunc(stat.mtimeMs)}`;
  const stagingRoot = path.join(
    app.getPath("temp"),
    "pkulaw-workbench-engine",
    sourceIdentity
  );
  const stagedExecutable = path.join(stagingRoot, "pkulaw-engine.exe");
  if (fs.existsSync(stagedExecutable)) {
    return stagingRoot;
  }
  const temporaryRoot =
    `${stagingRoot}.tmp-${process.pid}-${Date.now()}`;
  await fsp.rm(temporaryRoot, { recursive: true, force: true });
  await fsp.cp(sourceDirectory, temporaryRoot, {
    recursive: true,
    force: true,
    errorOnExist: false
  });
  const temporaryExecutable = path.join(
    temporaryRoot,
    "pkulaw-engine.exe"
  );
  if (!fs.existsSync(temporaryExecutable)) {
    throw new Error("Bundled Python engine staging did not produce pkulaw-engine.exe");
  }
  await fsp.rm(stagingRoot, { recursive: true, force: true });
  await fsp.rename(temporaryRoot, stagingRoot);
  return stagingRoot;
}

async function resolveBackendLaunch(): Promise<{
  engineDirectory: string;
  pythonPath: string;
  backendExecutable: boolean;
}> {
  const configured = process.env.PKULAW_PYTHON?.trim();
  const bundledExecutable = path.join(
    process.resourcesPath,
    "engine",
    "pkulaw-engine.exe"
  );
  if (configured) {
    const bundled = path.basename(configured).toLowerCase() === "pkulaw-engine.exe";
    return {
      engineDirectory: bundled
        ? path.dirname(configured)
        : resolveEngineDirectory(),
      pythonPath: configured,
      backendExecutable: bundled
    };
  }
  if (app.isPackaged && fs.existsSync(bundledExecutable)) {
    const engineDirectory = await stageBundledEngine(
      path.dirname(bundledExecutable),
      bundledExecutable,
      resolveAppVersion()
    );
    return {
      engineDirectory,
      pythonPath: path.join(engineDirectory, "pkulaw-engine.exe"),
      backendExecutable: true
    };
  }
  return {
    engineDirectory: resolveEngineDirectory(),
    pythonPath: "D:\\Python\\python.exe",
    backendExecutable: false
  };
}

function resolveDataDirectory(): string {
  return path.resolve(
    process.env.PKULAW_DATA_DIR || path.join(app.getPath("userData"), "runtime")
  );
}

function sendToRenderer(channel: string, payload: unknown): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send(channel, payload);
  }
}

function createMainWindow(): BrowserWindow {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 860,
    minHeight: 620,
    show: false,
    backgroundColor: "#f3f5f7",
    title: "法宝工作台",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true
    }
  });

  window.once("ready-to-show", () => window.show());
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    const current = window.webContents.getURL();
    if (current && new URL(url).origin !== new URL(current).origin) {
      event.preventDefault();
      logger?.write("warn", "navigation.blocked", "Blocked unexpected desktop navigation", {
        url
      });
    }
  });
  window.webContents.on("render-process-gone", (_event, details) => {
    logger?.write("error", "renderer.gone", "Renderer process exited", {
      reason: details.reason,
      exitCode: details.exitCode
    });
  });

  return window;
}

function loadRenderer(window: BrowserWindow): void {
  const rendererUrl = process.env.ELECTRON_RENDERER_URL;
  if (rendererUrl) {
    void window.loadURL(rendererUrl);
  } else {
    void window.loadFile(path.join(__dirname, "../renderer/index.html"));
  }
}

async function startBackend(
  service: PythonService,
  appInfo: AppInfo
): Promise<void> {
  try {
    await service.start();
    logger?.write("info", "backend.ready", "Python backend ready", {
      protocolVersion: service.getState().protocolVersion,
      engineVersion: service.getState().engineVersion
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger?.write("error", "backend.start_failed", message);
    try {
      await service.stop();
    } catch (stopError) {
      logger?.write("warn", "backend.failed_cleanup", String(stopError));
    }
    if (process.env.PKULAW_SMOKE === "1") {
      await finishSmoke(false, { error: message, appInfo });
    }
  }
}

async function waitForRenderer(window: BrowserWindow): Promise<void> {
  if (!window.webContents.isLoading()) {
    return;
  }
  await new Promise<void>((resolve) => {
    window.webContents.once("did-finish-load", () => resolve());
  });
}

async function finishSmoke(
  ok: boolean,
  details: Record<string, unknown>
): Promise<void> {
  if (smokeFinished) {
    return;
  }
  smokeFinished = true;
  process.exitCode = ok ? 0 : 1;
  const reportPath =
    process.env.PKULAW_SMOKE_REPORT ||
    path.join(process.env.PKULAW_DATA_DIR || app.getPath("userData"), "smoke-report.json");
  await fsp.mkdir(path.dirname(reportPath), { recursive: true });
  await fsp.writeFile(
    reportPath,
    JSON.stringify(
      {
        ok,
        finishedAt: new Date().toISOString(),
        electron: process.versions.electron,
        chrome: process.versions.chrome,
        node: process.versions.node,
        ...details
      },
      null,
      2
    ),
    "utf8"
  );
  logger?.write(ok ? "info" : "error", "smoke.finished", `Smoke test finished: ${ok}`, details);
  app.quit();
}

async function runStage2Smoke(window: BrowserWindow, service: PythonService): Promise<void> {
  try {
    await waitForRenderer(window);
    const list = (await service.request("task.list", { limit: 20 })) as {
      tasks?: TaskRecord[];
    };
    let tasks = list.tasks || [];
    let createdTask: TaskRecord | null = null;
    if (tasks.length === 0) {
      const created = (await service.request("task.create", {
        name: "阶段 2 浏览器基础设施验证",
        mode: "single",
        input: { fixture: "stage2-browser" },
        outputDir: ""
      })) as { task?: TaskRecord };
      createdTask = created.task || null;
      tasks = createdTask ? [createdTask] : [];
    }

    if (!browserHost || !fixtureProfile) {
      throw new Error("Stage 2 smoke requires fixture browser mode");
    }
    await browserHost.selectInstitution(fixtureProfile.id);
    await new Promise((resolve) => setTimeout(resolve, 500));

    let browser = await browserHost.fixtureAction("login");
    if (!browser.authenticated) {
      throw new Error("Fixture login did not authenticate");
    }
    if (!browser.viewLoaded || !browser.targetBound || !browser.targetUrl) {
      throw new Error("Initial fixture page was not bound to the Python engine");
    }
    const zoomFactors = [0.5, 1, 1.25, 1.5, 2];
    const browserZoomResults: Record<string, number> = {};
    for (const factor of zoomFactors) {
      const actual = await browserHost.setBrowserZoom(factor);
      if (actual !== factor || browserHost.getState().zoomFactor !== factor) {
        throw new Error(
          `Browser zoom was not applied for ${factor}: ${JSON.stringify(
            browserHost.getState()
          )}`
        );
      }
      browserZoomResults[String(factor)] = actual;
    }
    await browserHost.setBrowserZoom(1.25);
    let selectorProfile = await institutionStore!.save({
      name: "选择器诊断夹具",
      entryUrl: `${fixtureProfile.entryUrl}selector-fixture?mode=stable`
    });
    const selectorCases = [
      { mode: "stable", ready: true, strategy: "stable" },
      { mode: "fallback", ready: true, strategy: "fallback:css" },
      { mode: "multiple", ready: true, strategy: "stable" },
      { mode: "missing-input", ready: false, missing: "searchInput" },
      { mode: "missing-submit", ready: false, missing: "searchSubmit" }
    ] as const;
    const selectorResults: Record<string, unknown> = {};
    for (const testCase of selectorCases) {
      selectorProfile = await institutionStore!.save({
        id: selectorProfile.id,
        name: "选择器诊断夹具",
        entryUrl: `${fixtureProfile.entryUrl}selector-fixture?mode=${testCase.mode}`
      });
      browser = await browserHost.selectInstitution(selectorProfile.id);
      const diagnostics = (await service.request(
        "browser.selector-diagnostics"
      )) as Record<string, unknown>;
      const controls = diagnostics.controls as Record<
        string,
        { ok?: boolean; strategy?: string }
      >;
      if (
        Boolean(diagnostics.ready) !== testCase.ready ||
        diagnostics.profileId !== "fixture"
      ) {
        throw new Error(
          `Selector diagnostic mismatch (${testCase.mode}): ${JSON.stringify(diagnostics)}`
        );
      }
      if (
        "strategy" in testCase &&
        controls.searchInput?.strategy !== testCase.strategy
      ) {
        throw new Error(
          `Unexpected selector strategy (${testCase.mode}): ${JSON.stringify(controls.searchInput)}`
        );
      }
      if (
        "missing" in testCase &&
        !(diagnostics.missingControls as string[]).includes(testCase.missing)
      ) {
        throw new Error(
          `Missing selector was not reported (${testCase.mode}): ${JSON.stringify(diagnostics)}`
        );
      }
      selectorResults[testCase.mode] = {
        ready: diagnostics.ready,
        profileId: diagnostics.profileId,
        inputStrategy: controls.searchInput?.strategy || "missing",
        submitStrategy: controls.searchSubmit?.strategy || "missing",
        missingControls: diagnostics.missingControls
      };
    }

    const v6FixtureBase = `${fixtureProfile.entryUrl}pkulaw-v6-fixtures/`;
    const v6Cases = [
      {
        file: "search.html",
        pageKind: "search",
        pageReady: true
      },
      {
        file: "search-missing-submit.html",
        pageKind: "search",
        pageReady: false,
        missingControl: "searchSubmit",
        gateCode: "PAGE_SELECTOR_MISMATCH"
      },
      {
        file: "results.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 1,
        rowCount: 1,
        totalPages: 1,
        nextAvailable: false
      },
      {
        file: "multipage.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 6,
        rowCount: 2,
        totalPages: 3,
        nextAvailable: true
      },
      {
        file: "detail.html",
        pageKind: "detail",
        pageReady: true,
        detailTitle: "中华人民共和国国防动员法(2026修订)",
        downloadAvailable: true
      },
      {
        file: "detail-fulltext-clauses.html",
        pageKind: "detail",
        pageReady: true,
        detailTitle: "西藏自治区固定资产投资项目节能审查和碳排放评价实施办法",
        detailBodySections: 6,
        detailBodyClauses: 38,
        downloadAvailable: true
      },
      {
        file: "stale-page5.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 94,
        rowCount: 2,
        totalPages: 5,
        nextAvailable: false
      },
      {
        file: "same-url-slow-search.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 94,
        rowCount: 1,
        totalPages: 5,
        nextAvailable: false
      },
      {
        file: "same-query-rerun.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 1,
        rowCount: 1,
        totalPages: 1,
        nextAvailable: false
      },
      {
        file: "grouped-results.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 94,
        rowCount: 2,
        totalPages: 10,
        nextAvailable: false
      },
      {
        file: "custom-page-size.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 94,
        rowCount: 1,
        totalPages: 5,
        nextAvailable: false
      },
      {
        file: "filter-tree.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 3,
        rowCount: 1,
        totalPages: 1,
        nextAvailable: false
      },
      {
        file: "selector-flicker.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 1,
        rowCount: 1,
        totalPages: 1,
        nextAvailable: false
      },
      {
        file: "selector-missing.html",
        pageKind: "search",
        pageReady: false,
        missingControl: "libraryRoot",
        gateCode: "PAGE_SELECTOR_MISMATCH"
      },
      {
        file: "download-out-of-order.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 1,
        rowCount: 1,
        totalPages: 1,
        nextAvailable: false
      },
      {
        file: "batch-modal-real-shape.html",
        pageKind: "results",
        pageReady: true,
        resultCount: 1,
        rowCount: 1,
        totalPages: 1,
        nextAvailable: false
      }
    ] as const;
    const v6AuditResults: Record<string, unknown> = {};
    for (const testCase of v6Cases) {
      selectorProfile = await institutionStore!.save({
        name: `V6 脱敏夹具 · ${testCase.file}`,
        entryUrl: `${v6FixtureBase}${testCase.file}`
      });
      browser = await browserHost.selectInstitution(selectorProfile.id);
      const audit = (await service.request(
        "browser.page-audit",
        {},
        15_000
      )) as Record<string, unknown>;
      const diagnostics = audit.diagnostics as Record<string, unknown>;
      const controls = (diagnostics?.controls || {}) as Record<
        string,
        { ok?: boolean }
      >;
      if (
        audit.profileId !== "pkulaw-v6" ||
        audit.version !== "pkulaw-v6-2026-11-01-3" ||
        audit.pageKind !== testCase.pageKind ||
        Boolean(audit.pageReady) !== testCase.pageReady ||
        diagnostics?.pageKind !== testCase.pageKind
      ) {
        throw new Error(
          `V6 page audit mismatch (${testCase.file}): ${JSON.stringify(audit)}`
        );
      }
      if ("resultCount" in testCase) {
        const results = audit.results as Record<string, unknown>;
        const rows = (results?.rows || []) as Array<Record<string, unknown>>;
        if (
          Number(results?.resultCount) !== testCase.resultCount ||
          rows.length !== testCase.rowCount ||
          Number(results?.totalPages) !== testCase.totalPages ||
          Boolean(results?.nextAvailable) !== testCase.nextAvailable
        ) {
          throw new Error(
            `V6 result audit mismatch (${testCase.file}): ${JSON.stringify(results)}`
          );
        }
        if (
          testCase.file === "grouped-results.html" &&
          controls.groupUngroup?.ok !== true
        ) {
          throw new Error("V6 不分组控件未被诊断识别");
        }
        if (
          testCase.file === "custom-page-size.html" &&
          (
            controls.pageSizeTrigger?.ok !== true ||
            controls.pageSizeMenu?.ok !== true ||
            controls.pageSizeItem?.ok !== true ||
            controls.pageSizeHidden?.ok !== true
          )
        ) {
          throw new Error(
            `V6 可见每页条数控件未被完整识别：${JSON.stringify(controls)}`
          );
        }
        if (
          testCase.file === "filter-tree.html" &&
          (
            controls.yearFilter?.ok !== true ||
            controls.provinceFilter?.ok !== true ||
            controls.timelinessFilter?.ok !== true
          )
        ) {
          throw new Error(
            `V6 树形筛选器未被完整识别：${JSON.stringify(controls)}`
          );
        }
        v6AuditResults[testCase.file] = {
          pageKind: audit.pageKind,
          resultCount: results.resultCount,
          rowCount: rows.length,
          topRow: rows[0],
          totalPages: results.totalPages,
          nextAvailable: results.nextAvailable
        };
      } else if ("detailTitle" in testCase) {
        const detail = audit.detail as Record<string, unknown>;
        const bodyCompleteness = detail?.bodyCompleteness as
          | Record<string, unknown>
          | undefined;
        if (
          detail?.title !== testCase.detailTitle ||
          String(detail?.body || "").length < 20 ||
          bodyCompleteness?.status !== "complete" ||
          audit.downloadAvailable !== testCase.downloadAvailable ||
          audit.downloaded !== false
        ) {
          throw new Error(
            `V6 detail audit mismatch (${testCase.file}): ${JSON.stringify(audit)}`
          );
        }
        if (
          "detailBodySections" in testCase &&
          (
            Number(bodyCompleteness?.sectionCount) !==
              testCase.detailBodySections ||
            Number(bodyCompleteness?.clauseCount) !==
              testCase.detailBodyClauses
          )
        ) {
          throw new Error(
            `V6 fulltext body mismatch (${testCase.file}): ${JSON.stringify(
              bodyCompleteness
            )}`
          );
        }
        if (
          testCase.file === "detail.html" &&
          (
            detail?.issuingAuthority !== "全国人大常委会" ||
            detail?.documentNumber !== "中华人民共和国主席令第83号"
          )
        ) {
          throw new Error(
            `V6 detail metadata mismatch (${testCase.file}): ${JSON.stringify(audit)}`
          );
        }
        v6AuditResults[testCase.file] = {
          pageKind: audit.pageKind,
          title: detail.title,
          issuingAuthority: detail.issuingAuthority,
          documentNumber: detail.documentNumber,
          bodyCompleteness: detail.bodyCompleteness,
          downloadAvailable: audit.downloadAvailable,
          downloaded: audit.downloaded
        };
      } else if ("missingControl" in testCase) {
        const pageMissing = (diagnostics?.pageMissingControls || []) as string[];
        const gateError = audit.collectionGateError as Record<string, unknown> | null;
        if (
          !pageMissing.includes(testCase.missingControl) ||
          gateError?.code !== testCase.gateCode
        ) {
          throw new Error(
            `V6 missing-control gate mismatch (${testCase.file}): ${JSON.stringify(audit)}`
          );
        }
        v6AuditResults[testCase.file] = {
          pageKind: audit.pageKind,
          pageReady: audit.pageReady,
          pageMissing,
          gateError
        };
      } else {
        v6AuditResults[testCase.file] = {
          pageKind: audit.pageKind,
          pageReady: audit.pageReady
        };
      }
    }

    const slowProfile = await institutionStore!.save({
      name: "慢加载绑定验证",
      entryUrl: `${fixtureProfile.entryUrl}slow-app`
    });
    browser = await browserHost.selectInstitution(slowProfile.id);
    if (
      !browser.viewLoaded ||
      !browser.targetBound ||
      !browser.targetUrl.includes("/slow-app")
    ) {
      throw new Error("Slow-loading page was not bound after dom-ready");
    }

    const crossOriginProfile = await institutionStore!.save({
      name: "跨域跳转绑定验证",
      entryUrl: `${fixtureProfile.entryUrl}redirect-cross-host`
    });
    browser = await browserHost.selectInstitution(crossOriginProfile.id);
    if (
      !browser.viewLoaded ||
      !browser.targetBound ||
      new URL(browser.targetUrl).hostname !== "localhost"
    ) {
      throw new Error("Cross-origin redirect target was not bound");
    }

    browser = await browserHost.selectInstitution(fixtureProfile.id);
    if (!browser.authenticated || !browser.targetBound) {
      throw new Error("Persistent fixture session was lost after binding tests");
    }
    browser = await browserHost.fixtureAction("captcha");
    if (!browser.captchaVisible || browser.status !== "waiting_manual") {
      throw new Error("Fixture captcha did not enter manual-wait state");
    }
    browser = await browserHost.userContinue();
    if (browser.captchaVisible) {
      throw new Error("Manual continue did not clear fixture captcha");
    }

    await browserHost.fixtureAction("download");
    const downloadDeadline = Date.now() + 8_000;
    let completedDownload: Record<string, unknown> | null = null;
    while (Date.now() < downloadDeadline) {
      const engineState = (await service.request("browser.state")) as {
        browser?: { lastDownload?: Record<string, unknown> | null };
      };
      if (engineState.browser?.lastDownload?.event === "completed") {
        completedDownload = engineState.browser.lastDownload;
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!completedDownload) {
      throw new Error("Electron download did not complete");
    }
    const downloadedPath = String(completedDownload.path || "");
    const downloadedContent = await fsp.readFile(downloadedPath, "utf8");
    if (!downloadedContent.includes("STAGE2_ELECTRON_DOWNLOAD_OK")) {
      throw new Error("Downloaded fixture content was invalid");
    }

    browser = await browserHost.navigate("reload");
    if (!browser.authenticated) {
      throw new Error("Persistent session was lost after reload");
    }

    browser = await browserHost.recreateCurrent();
    if (!browser.authenticated) {
      throw new Error("Persistent session was lost after WebContentsView recreation");
    }
    if (browser.zoomFactor !== 1.25) {
      throw new Error(
        `Browser zoom was not restored after view recreation: ${browser.zoomFactor}`
      );
    }

    await service.stop();
    await service.start();
    browser = await browserHost.reconnectCurrent();
    if (!browser.authenticated) {
      throw new Error("Persistent session was lost after Python backend restart");
    }
    if (browser.zoomFactor !== 1.25) {
      throw new Error(
        `Browser zoom was not restored after Python restart: ${browser.zoomFactor}`
      );
    }
    browser = await browserHost.exerciseRendererRecoveryForSmoke();
    if (!browser.authenticated) {
      throw new Error("Browser recovery did not restore authenticated session");
    }
    if (browser.zoomFactor !== 1.25) {
      throw new Error(
        `Browser zoom was not restored after renderer recovery: ${browser.zoomFactor}`
      );
    }

    const sendZoomShortcut = async (
      keyCode: string
    ): Promise<void> => {
      window.webContents.focus();
      window.webContents.sendInputEvent({
        type: "keyDown",
        keyCode,
        modifiers: ["control"]
      });
      window.webContents.sendInputEvent({
        type: "keyUp",
        keyCode,
        modifiers: ["control"]
      });
      await new Promise((resolve) => setTimeout(resolve, 250));
    };
    await sendZoomShortcut("+");
    const zoomAfterCtrlPlus = browserHost.getState().zoomFactor;
    await sendZoomShortcut("-");
    const zoomAfterCtrlMinus = browserHost.getState().zoomFactor;
    await sendZoomShortcut("0");
    const zoomAfterCtrlZero = browserHost.getState().zoomFactor;
    if (
      zoomAfterCtrlPlus !== 1.35 ||
      zoomAfterCtrlMinus !== 1.25 ||
      zoomAfterCtrlZero !== 1
    ) {
      throw new Error(
        `Browser zoom shortcuts failed: ${JSON.stringify({
          zoomAfterCtrlPlus,
          zoomAfterCtrlMinus,
          zoomAfterCtrlZero
        })}`
      );
    }
    await browserHost.setBrowserZoom(1.25);
    const shellZoomFactor = window.webContents.getZoomFactor();
    if (shellZoomFactor !== 1) {
      throw new Error(
        `Browser zoom leaked into the Electron shell: ${shellZoomFactor}`
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 500));

    const screenshotPath =
      process.env.PKULAW_SMOKE_SCREENSHOT ||
      path.join(process.env.PKULAW_DATA_DIR || app.getPath("userData"), "smoke.png");
    await fsp.mkdir(path.dirname(screenshotPath), { recursive: true });
    const sources = await desktopCapturer.getSources({
      types: ["window"],
      thumbnailSize: { width: 1440, height: 920 },
      fetchWindowIcons: false
    });
    const source =
      sources.find((item) => item.name.includes("法宝工作台")) ||
      sources.find((item) => item.name.includes("Electron"));
    const image = source?.thumbnail || (await window.webContents.capturePage());
    await fsp.writeFile(screenshotPath, image.toPNG());

    browser = await browserHost.fixtureAction("child");
    const childDeadline = Date.now() + 5_000;
    while (Date.now() < childDeadline && !browser.tabs.some((tab) => tab.url.endsWith("/child"))) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      browser = await service.request("browser.state").then((result) => {
        const engineBrowser = result.browser as Record<string, unknown>;
        return {
          ...browserHost!.getState(),
          tabs: (engineBrowser.tabs || []) as BrowserHostState["tabs"]
        };
      });
    }
    if (!browser.tabs.some((tab) => tab.url.endsWith("/child"))) {
      throw new Error("Controlled child window was not visible to DrissionPage");
    }

    const task6Lifecycle = await runTask6LifecycleSmoke(window);
    const task6Ui = await runTask6UiSmoke(window);
    const ping = await service.request("system.ping");
    await finishSmoke(true, {
      backendState: service.getState(),
      ping,
      taskCount: tasks.length,
      createdTaskId: createdTask?.id || null,
      browserState: browserHost.getState(),
      browserZoom: {
        supportedFactors: browserZoomResults,
        persistedFactor: 1.25,
        shellZoomFactor,
        viewRecreationRestored: true,
        pythonRestartRestored: true,
        rendererRecoveryRestored: true,
        shortcuts: {
          ctrlPlus: zoomAfterCtrlPlus,
          ctrlMinus: zoomAfterCtrlMinus,
          ctrlZero: zoomAfterCtrlZero
        }
      },
      slowLoadBound: true,
      crossOriginRedirectBound: true,
      initialTargetBound: true,
      selectorProfileValidated: true,
      selectorDiagnosticCases: selectorResults,
      v6FixtureAudit: v6AuditResults,
      download: {
        state: "completed",
        contentVerified: true,
        path: downloadedPath
      },
      sessionPersistedAfterReload: true,
      sessionPersistedAfterViewRecreation: true,
      browserReconnectedAfterPythonRestart: true,
      rendererCrashRecovered: true,
      childViewDetected: true,
      task6Lifecycle,
      task6Ui,
      screenshotPath
    });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    await finishSmoke(false, { error: message });
  }
}

async function waitForTask(
  service: PythonService,
  taskId: string,
  predicate: (details: TaskDetails) => boolean,
  timeoutMs = 120_000
): Promise<TaskDetails> {
  const deadline = Date.now() + timeoutMs;
  let last: TaskDetails | null = null;
  while (Date.now() < deadline) {
    const result = (await service.request("single.details", { taskId })) as unknown as TaskDetails;
    last = result;
    if (predicate(result)) {
      return result;
    }
    if (["failed", "stopped"].includes(result.task.status)) {
      throw new Error(
        `Task ${taskId} stopped with ${result.task.status}: ${result.task.error?.message || "unknown"}`
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`Task ${taskId} timed out; last state=${JSON.stringify(last?.task || null)}`);
}

async function waitForFixtureBrowserReady(
  timeoutMs = 20_000
): Promise<void> {
  if (!browserHost) {
    throw new Error("Fixture browser host is unavailable");
  }
  const deadline = Date.now() + timeoutMs;
  let last = browserHost.getState();
  while (Date.now() < deadline) {
    last = browserHost.getState();
    if (
      last.viewLoaded &&
      last.targetBound &&
      last.targetUrl &&
      last.authenticated &&
      last.selectorReady
    ) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(
    `Fixture browser did not become ready: ${JSON.stringify(last)}`
  );
}

async function createAndStartSingleTask(
  service: PythonService,
  dataDirectory: string,
  name: string,
  input: Record<string, unknown>
): Promise<string> {
  const outputDir = path.join(dataDirectory, "single-word");
  const created = (await service.request("task.create", {
    name,
    mode: "single",
    input: { ...input, outputDir },
    outputDir
  })) as { task?: TaskRecord };
  if (!created.task) {
    throw new Error("Task creation did not return a task");
  }
  await service.request("single.start", { taskId: created.task.id }, 15_000);
  return created.task.id;
}

async function captureApplicationWindow(
  window: BrowserWindow,
  screenshotPath: string
): Promise<void> {
  await fsp.mkdir(path.dirname(screenshotPath), { recursive: true });
  const sources = await desktopCapturer.getSources({
    types: ["window"],
    thumbnailSize: { width: 1440, height: 920 },
    fetchWindowIcons: false
  });
  const source =
    sources.find((item) => item.name.includes("法宝工作台")) ||
    sources.find((item) => item.name.includes("Electron"));
  const image = source?.thumbnail || (await window.webContents.capturePage());
  await fsp.writeFile(screenshotPath, image.toPNG());
}

function setLogicalContentSize(
  window: BrowserWindow,
  width: number,
  height: number
): void {
  if (window.isFullScreen()) {
    window.setFullScreen(false);
  }
  if (window.isMaximized()) {
    window.unmaximize();
  }
  window.webContents.setZoomFactor(1);
  window.setContentSize(width, height, false);
  const [actualContentWidth, actualContentHeight] =
    window.getContentSize();
  if (actualContentWidth <= 0 || actualContentHeight <= 0) {
    return;
  }
  const zoomFactor = Math.max(
    0.1,
    Math.min(
      1,
      actualContentWidth / width,
      actualContentHeight / height
    )
  );
  if (zoomFactor < 0.999) {
    window.webContents.setZoomFactor(zoomFactor);
  }
}

async function runUnifiedWorkspaceSmoke(
  window: BrowserWindow,
  service: PythonService,
  dataDirectory: string
): Promise<Record<string, unknown>> {
  if (!browserHost || !fixtureProfile || !institutionStore) {
    throw new Error("统一工作区烟测缺少夹具浏览器");
  }
  const wait = (milliseconds: number): Promise<void> =>
    new Promise((resolve) => setTimeout(resolve, milliseconds));
  const navigate = async (label: string): Promise<void> => {
    const clicked = Boolean(
      await window.webContents.executeJavaScript(
        `(() => {
          const button = [...document.querySelectorAll('.nav-item')]
            .find(item => (item.textContent || '').includes(${JSON.stringify(label)}));
          if (!button) return false;
          button.click();
          return true;
        })()`
      )
    );
    if (!clicked) throw new Error(`导航不可用：${label}`);
  };
  const waitForSelector = async (
    selector: string,
    timeout = 12_000
  ): Promise<boolean> => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const found = Boolean(
        await window.webContents.executeJavaScript(
          `Boolean(document.querySelector(${JSON.stringify(selector)}))`
        )
      );
      if (found) return true;
      await wait(200);
    }
    return false;
  };

  setLogicalContentSize(window, 1536, 864);
  await wait(500);
  const yearWindowPreview = (await window.webContents.executeJavaScript(
    `window.pkulaw.previewTask('single', {
      queries: ['碳中和'],
      matchMode: 'keyword',
      library: 'central',
      yearSelection: {
        mode: 'range',
        startYear: 1949,
        endYear: 2025
      },
      province: '全部省份',
      timeliness: '全部（仅记录元数据，不筛选）',
      pageSize: 100
    })`
  )) as TaskPreviewSummary;
  if (
    yearWindowPreview.originalYearSelection?.startYear !== 1949 ||
    yearWindowPreview.effectiveYearSelection?.startYear !== 2018 ||
    yearWindowPreview.effectiveYearSelection?.endYear !== 2025 ||
    yearWindowPreview.yearWindowAdjustment?.adjusted !== true ||
    yearWindowPreview.downloadsStarted !== 0 ||
    yearWindowPreview.downloadIntentsCreated !== 0 ||
    yearWindowPreview.tasksCreated !== 0 ||
    yearWindowPreview.combinations
      .map((combination) => combination.year)
      .join(",") !== "2018,2019,2020,2021,2022,2023,2024,2025"
  ) {
    throw new Error(
      `年份起点自动归一化不通过：${JSON.stringify(yearWindowPreview)}`
    );
  }
  const yearSwitchPreview = (await window.webContents.executeJavaScript(
    `window.pkulaw.previewTask('single', {
      queries: ['残疾人体育'],
      matchMode: 'keyword',
      library: 'local',
      yearSelection: {
        mode: 'range',
        startYear: 2012,
        endYear: 2013
      },
      province: '北京市',
      timeliness: '全部（仅记录元数据，不筛选）',
      pageSize: 10
    })`
  )) as TaskPreviewSummary;
  if (
    yearSwitchPreview.combinations
      .map((combination) => combination.year)
      .join(",") !== "2012,2013" ||
    yearSwitchPreview.totalResults !== 2 ||
    yearSwitchPreview.downloadsStarted !== 0 ||
    yearSwitchPreview.downloadIntentsCreated !== 0 ||
    yearSwitchPreview.tasksCreated !== 0
  ) {
    throw new Error(
      `2012→2013 年份切换零下载预览不通过：${JSON.stringify(
        yearSwitchPreview
      )}`
    );
  }
  await service.request("task.create", {
    name: "阶段十一批量工作区验证",
    mode: "batch",
    input: {
      queries: ["阶段十一批量工作区"],
      matchMode: "keyword",
      library: "central",
      yearSelection: { mode: "single", year: 2024 },
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 50,
      pageSelection: { mode: "limit", maxPages: 1 },
      catalogFormat: "xlsx",
      fullFormat: "docx",
      outputDir: path.join(dataDirectory, "phase11-batch")
    },
    outputDir: path.join(dataDirectory, "phase11-batch")
  });
  const settingsTaskName = "阶段十一暂停设置面板验证";
  const settingsTaskId = await createAndStartSingleTask(
    service,
    dataDirectory,
    settingsTaskName,
    {
      queries: ["分页测试"],
      matchMode: "keyword",
      library: "central",
      year: "全部年份",
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 10,
      maxPages: 3
    }
  );
  await waitForTask(
    service,
    settingsTaskId,
    (details) =>
      ["preflight", "running", "verifying"].includes(details.task.status),
    15_000
  );
  await service.request("single.pause", { taskId: settingsTaskId }, 15_000);
  await waitForTask(
    service,
    settingsTaskId,
    (details) => details.task.status === "paused",
    15_000
  );
  await navigate("逐篇采集");
  if (!(await waitForSelector(".task-workspace-frame"))) {
    throw new Error("统一逐篇工作区未渲染");
  }
  const pausedTaskSelected = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.single-task-tabs button')]
          .find(item => (item.textContent || '').includes(${JSON.stringify(settingsTaskName)}));
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  if (!pausedTaskSelected) {
    throw new Error("暂停任务未出现在逐篇任务列表");
  }
  await wait(350);
  const layout = (await window.webContents.executeJavaScript(
    `(() => {
      const task = document.querySelector('.task-workspace-primary')?.getBoundingClientRect();
      const browser = document.querySelector('.browser-surface.task')?.getBoundingClientRect();
      const browserVisible =
        document.querySelector('.browser-surface.task')?.getAttribute('data-browser-visible');
      const navText = [...document.querySelectorAll('.nav-item')]
        .map(item => item.textContent || '').join('|');
      return {
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        scrollHeight: document.documentElement.scrollHeight,
        scrollWidth: document.documentElement.scrollWidth,
        taskTop: task?.top || 0,
        taskBottom: task?.bottom || 0,
        browserTop: browser?.top || 0,
        browserBottom: browser?.bottom || 0,
        browserVisible,
        hasDedicatedBrowserNav: navText.includes('内置浏览器')
      };
    })()`
  )) as Record<string, unknown>;
  if (
    Number(layout.innerWidth) < 1530 ||
    Number(layout.scrollHeight) > Number(layout.innerHeight) + 1 ||
    Number(layout.scrollWidth) > Number(layout.innerWidth) + 1 ||
    Math.abs(Number(layout.taskTop) - Number(layout.browserTop)) > 2 ||
    Math.abs(Number(layout.taskBottom) - Number(layout.browserBottom)) > 2 ||
    layout.browserVisible !== "true" ||
    layout.hasDedicatedBrowserNav !== false
  ) {
    throw new Error(`基准一屏工作区不通过：${JSON.stringify(layout)}`);
  }
  await navigate("总览");
  if (!(await waitForSelector(".overview-institution-workspace"))) {
    throw new Error("总览机构工作区未渲染");
  }
  const overview = (await window.webContents.executeJavaScript(
    `(() => {
      const actions = [...document.querySelectorAll('.overview-quick-action')]
        .map(item => item.getBoundingClientRect());
      return {
        heroPresent: Boolean(document.querySelector('.hero, .overview-hero')),
        institutionConfigPresent: Boolean(document.querySelector('.overview-institution-workspace')),
        quickActionCount: actions.length,
        quickActionWidths: actions.map(rect => rect.width),
        quickActionHeights: actions.map(rect => rect.height)
      };
    })()`
  )) as Record<string, unknown>;
  const quickWidths = overview.quickActionWidths as number[];
  const quickHeights = overview.quickActionHeights as number[];
  if (
    overview.heroPresent !== false ||
    overview.institutionConfigPresent !== true ||
    overview.quickActionCount !== 3 ||
    Math.max(...quickWidths) - Math.min(...quickWidths) > 2 ||
    Math.max(...quickHeights) - Math.min(...quickHeights) > 1
  ) {
    throw new Error(`总览机构配置布局不通过：${JSON.stringify(overview)}`);
  }

  await navigate("逐篇采集");
  if (!(await waitForSelector(".task-workspace-frame"))) {
    throw new Error("返回逐篇工作区失败");
  }
  const settingsOpened = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.task-workspace-header button')]
          .find(item => (item.textContent || '').trim() === '任务设置');
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  if (!settingsOpened || !(await waitForSelector(".task-settings-panel"))) {
    throw new Error("任务设置面板未打开");
  }
  const settingsHiddenBrowser = await window.webContents.executeJavaScript(
    `document.querySelector('.browser-surface.task')?.getAttribute('data-browser-visible')`
  );
  if (settingsHiddenBrowser !== "false") {
    throw new Error("打开任务设置后浏览器未收起");
  }
  const settingsLayout = (await window.webContents.executeJavaScript(
    `(() => {
      const body = document.querySelector('.task-settings-panel .collector-form-body');
      const footer = document.querySelector('.task-settings-panel .collector-action-footer');
      const apply = [...document.querySelectorAll('.task-settings-actions button')]
        .find(item => (item.textContent || '').trim() === '保存修改');
      const yearRow = document.querySelector('.task-settings-panel .collector-year-row')
        ?.getBoundingClientRect();
      const librarySelect = [...document.querySelectorAll('.task-settings-panel select')]
        .find(select =>
          [...select.options].some(option => option.textContent === '地方法规')
        );
      const provinceRow = document.querySelector('.task-settings-panel .collector-province-row')
        ?.getBoundingClientRect();
      const footerRect = footer?.getBoundingClientRect();
      const applyRect = apply?.getBoundingClientRect();
      return {
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        scrollHeight: document.documentElement.scrollHeight,
        bodyClientHeight: body?.clientHeight || 0,
        bodyScrollHeight: body?.scrollHeight || 0,
        footerTop: footerRect?.top || 0,
        footerBottom: footerRect?.bottom || 0,
        applyTop: applyRect?.top || 0,
        applyBottom: applyRect?.bottom || 0,
        yearTop: yearRow?.top || 0,
        library: librarySelect?.value || '',
        provinceRowPresent: Boolean(provinceRow),
        bodyBeforeFooter: Boolean(
          body && footer &&
          (body.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING)
        )
      };
    })()`
  )) as Record<string, unknown>;
  if (
    Number(settingsLayout.innerWidth) < 1530 ||
    Number(settingsLayout.scrollHeight) >
      Number(settingsLayout.innerHeight) + 1 ||
    Number(settingsLayout.bodyScrollHeight) >
      Number(settingsLayout.bodyClientHeight) + 1 ||
    Number(settingsLayout.footerTop) < 0 ||
    Number(settingsLayout.footerBottom) >
      Number(settingsLayout.innerHeight) + 1 ||
    Number(settingsLayout.applyTop) < 0 ||
    Number(settingsLayout.applyBottom) >
      Number(settingsLayout.innerHeight) + 1 ||
    (
      settingsLayout.library === "central"
        ? settingsLayout.provinceRowPresent !== false
        : settingsLayout.provinceRowPresent !== true
    ) ||
    settingsLayout.bodyBeforeFooter !== true
  ) {
    throw new Error(
      `阶段 I 固定表单操作栏不通过：${JSON.stringify(settingsLayout)}`
    );
  }
  const provinceVisibilitySwitch = (await window.webContents.executeJavaScript(
    `(async () => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const library = [...document.querySelectorAll('.task-settings-panel select')]
        .find(select =>
          [...select.options].some(option => option.textContent === '地方法规')
        );
      if (!library) return {
        localVisible: false,
        centralHidden: false,
        restored: false
      };
      const original = library.value;
      const update = (value) => {
        library.value = value;
        library.dispatchEvent(new Event('change', { bubbles: true }));
      };
      update('local');
      await wait(120);
      const localVisible = Boolean(
        document.querySelector('.task-settings-panel .collector-province-row')
      );
      update('central');
      await wait(120);
      const centralHidden = !document.querySelector(
        '.task-settings-panel .collector-province-row'
      );
      update(original);
      await wait(120);
      const restored =
        library.value === original &&
        Boolean(
          document.querySelector('.task-settings-panel .collector-province-row')
        ) === (original !== 'central');
      return { localVisible, centralHidden, restored };
    })()`
  )) as Record<string, unknown>;
  if (
    provinceVisibilitySwitch.localVisible !== true ||
    provinceVisibilitySwitch.centralHidden !== true ||
    provinceVisibilitySwitch.restored !== true
  ) {
    throw new Error(
      `任务设置中的省份显隐未随法规库切换：${JSON.stringify(
        provinceVisibilitySwitch
      )}`
    );
  }
  const localProvinceVisible = provinceVisibilitySwitch.localVisible === true;
  await captureApplicationWindow(
    window,
    path.join(dataDirectory, "stageI-single-settings.png")
  );
  const settingsClosed = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.task-settings-actions button')]
          .find(item => (item.textContent || '').trim() === '关闭');
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  await wait(350);
  const browserRestored = await window.webContents.executeJavaScript(
    `document.querySelector('.browser-surface.task')?.getAttribute('data-browser-visible')`
  );
  if (!settingsClosed || browserRestored !== "true") {
    throw new Error("关闭任务设置后浏览器未恢复");
  }
  await service.request("single.stop", { taskId: settingsTaskId }, 15_000);
  await waitForTask(
    service,
    settingsTaskId,
    (details) => details.task.status === "stopped",
    15_000
  );
  const settingsLeaseDeadline = Date.now() + 5_000;
  while (
    Date.now() < settingsLeaseDeadline &&
    !browserHost.getState().browserLease.available
  ) {
    await wait(100);
  }
  if (!browserHost.getState().browserLease.available) {
    throw new Error("暂停设置任务停止后浏览器租约未释放");
  }

  const beforeStartCount = ((
    (await service.request("task.list", { limit: 500 })) as {
      tasks?: TaskRecord[];
    }
  ).tasks || []).length;
  const singleInput = {
    name: "阶段十一单按钮启动验证",
    mode: "single",
    outputDir: path.join(dataDirectory, "phase11-single"),
    input: {
      queries: ["分页测试"],
      matchMode: "keyword",
      library: "central",
      yearSelection: { mode: "single", year: 2024 },
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 10,
      pageSelection: { mode: "limit", maxPages: 1 },
      outputDir: path.join(dataDirectory, "phase11-single")
    },
    operationId: `phase11-single-${Date.now()}`
  };
  const firstStart = await window.webContents.executeJavaScript(
    `window.pkulaw.startCollectorTask(${JSON.stringify(singleInput)})`
  ) as CollectorStartResult;
  const secondStart = await window.webContents.executeJavaScript(
    `window.pkulaw.startCollectorTask(${JSON.stringify(singleInput)})`
  ) as CollectorStartResult;
  const firstStarted = await waitForTask(
    service,
    firstStart.task.id,
    (details) =>
      details.task.status !== "pending" &&
      details.task.status !== "preflight",
    45_000
  );
  const afterStart = (await service.request("task.list", { limit: 500 })) as {
    tasks?: TaskRecord[];
  };
  const afterStartCount = (afterStart.tasks || []).length;
  const firstPreview = (firstStarted.task.checkpoint.previewSnapshot || {}) as {
    downloadsStarted?: number;
    downloadIntentsCreated?: number;
    tasksCreated?: number;
  };
  if (
    afterStartCount !== beforeStartCount + 1 ||
    firstStart.task.id !== secondStart.task.id ||
    firstPreview.downloadsStarted !== 0 ||
    firstPreview.downloadIntentsCreated !== 0 ||
    firstPreview.tasksCreated !== 0 ||
    (!firstStart.preparing && !firstStart.started) ||
    secondStart.idempotent !== true
  ) {
    throw new Error(
      `单按钮启动或零下载核验不通过：${JSON.stringify({
        beforeStartCount,
        afterStartCount,
        firstStart,
        secondStart: { taskId: secondStart.task.id, idempotent: secondStart.idempotent }
      })}`
    );
  }

  let settingsTask = (await service.request("single.details", {
    taskId: firstStart.task.id
  })) as unknown as TaskDetails;
  const completedDeadline = Date.now() + 30_000;
  while (
    Date.now() < completedDeadline &&
    !settingsTask.items.some((item) => item.status === "completed") &&
    !["completed", "partial", "failed", "stopped"].includes(settingsTask.task.status)
  ) {
    await wait(100);
    settingsTask = (await service.request("single.details", {
      taskId: firstStart.task.id
    })) as unknown as TaskDetails;
  }
  if (!settingsTask.items.some((item) => item.status === "completed")) {
    throw new Error(`设置版本任务没有完成文件：${settingsTask.task.status}`);
  }
  if (
    ["preflight", "running", "pausing", "paused"].includes(
      settingsTask.task.status
    )
  ) {
    await service.request("single.stop", { taskId: firstStart.task.id });
    settingsTask = await waitForTask(
      service,
      firstStart.task.id,
      (details) => details.task.status === "stopped",
      15_000
    );
  } else if (
    !["completed", "partial", "failed", "stopped"].includes(
      settingsTask.task.status
    )
  ) {
    throw new Error(
      `设置版本任务停在不可修订状态：${settingsTask.task.status}`
    );
  }
  const completedBeforeRevision = settingsTask.items.find(
    (item) => item.status === "completed"
  );
  const completedPathBefore = String(
    completedBeforeRevision?.payload?.downloadedFile ||
      completedBeforeRevision?.payload?.path ||
      ""
  );
  const generationBefore = Number(
    settingsTask.task.checkpoint.configGeneration || 0
  );
  const revisedInput = {
    ...singleInput.input,
    queries: ["阶段十一设置版本新关键词"],
    outputDir: path.join(dataDirectory, "phase11-single-revised"),
    previewSnapshot: { stale: true }
  };
  const revision = await window.webContents.executeJavaScript(
    `window.pkulaw.applyTaskSettings(
      ${JSON.stringify(firstStart.task.id)},
      ${JSON.stringify(revisedInput)},
      ${JSON.stringify(`phase11-settings-${Date.now()}`)}
    )`
  ) as TaskSettingsUpdateResult;
  const completedAfterRevision = (
    (await service.request("single.details", {
      taskId: revision.task.id
    })) as unknown as TaskDetails
  ).items.find((item) => item.status === "completed");
  const completedPathAfter = String(
    completedAfterRevision?.payload?.downloadedFile ||
      completedAfterRevision?.payload?.path ||
      ""
  );
  const revisionJournal = revision.task.checkpoint.scanJournal as
    | Record<string, unknown>
    | undefined;
  const revisionFingerprint = String(
    revision.task.checkpoint.configFingerprint || ""
  );
  const journalFingerprint = String(
    revisionJournal?.configFingerprint || ""
  );
  if (
    revision.task.id !== firstStart.task.id ||
    revision.changed !== true ||
    revision.generation !== generationBefore + 1 ||
    !revisionFingerprint ||
    (Boolean(journalFingerprint) &&
      journalFingerprint !== revisionFingerprint) ||
    (completedPathBefore && completedPathAfter !== completedPathBefore)
  ) {
    throw new Error(
      `同任务设置版本切换不通过：${JSON.stringify({
        generationBefore,
        generationAfter: revision.generation,
        checkpoint: revision.task.checkpoint,
        completedPathBefore,
        completedPathAfter
      })}`
    );
  }

  let revisedState = (await service.request("single.details", {
    taskId: revision.task.id
  })) as unknown as TaskDetails;
  if (
    !["completed", "partial", "failed", "stopped"].includes(
      revisedState.task.status
    )
  ) {
    await service.request("single.stop", { taskId: revision.task.id });
    revisedState = await waitForTask(
      service,
      revision.task.id,
      (details) =>
        ["completed", "partial", "failed", "stopped"].includes(
          details.task.status
        ),
      15_000
    );
  }
  const leaseReleaseDeadline = Date.now() + 5_000;
  while (
    Date.now() < leaseReleaseDeadline &&
    !browserHost.getState().browserLease.available
  ) {
    await wait(100);
  }
  if (!browserHost.getState().browserLease.available) {
    throw new Error("设置版本任务结束后浏览器租约未释放");
  }
  const recoveryProfile = await institutionStore.save({
    name: `预检验证码恢复 ${Date.now()}`,
    entryUrl: new URL("pkulaw/", fixtureProfile.entryUrl).toString()
  });
  await browserHost.selectInstitution(recoveryProfile.id);
  await wait(600);
  const recoveryInput = {
    name: "阶段十一验证码恢复验证",
    mode: "single",
    outputDir: path.join(dataDirectory, "phase11-recovery"),
    input: {
      queries: ["阶段十一验证码恢复"],
      matchMode: "title",
      library: "central",
      yearSelection: { mode: "single", year: 2024 },
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 20,
      pageSelection: { mode: "limit", maxPages: 1 },
      outputDir: path.join(dataDirectory, "phase11-recovery")
    },
    operationId: `phase11-recovery-${Date.now()}`
  };
  const recoveryStart = await window.webContents.executeJavaScript(
    `window.pkulaw.startCollectorTask(${JSON.stringify(recoveryInput)})`
  ) as CollectorStartResult;
  const recoveryWaiting = await waitForTask(
    service,
    recoveryStart.task.id,
    (details) => details.task.status === "waiting_manual",
    15_000
  );
  const recoveryContext = recoveryWaiting.task.checkpoint
    .accessRecovery as Record<string, unknown> | undefined;
  if (
    String(recoveryContext?.taskId || "") !== recoveryStart.task.id
  ) {
    throw new Error("预检人工等待没有保存原任务恢复上下文");
  }
  await navigate("总览");
  await wait(250);
  await navigate("逐篇采集");
  await waitForSelector(".task-workspace-frame");
  const recoveryTaskSelected = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.single-task-tabs button')]
          .find(item =>
            (item.textContent || '').includes(${JSON.stringify(recoveryInput.name)})
          );
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  if (!recoveryTaskSelected) {
    throw new Error("恢复任务未出现在逐篇任务列表");
  }
  await wait(300);
  const selectedRecoveryTask = await window.webContents.executeJavaScript(
    `document.querySelector('.single-run-heading h2')?.textContent || ''`
  );
  if (selectedRecoveryTask !== recoveryInput.name) {
    throw new Error(`逐篇任务未切换到恢复任务：${selectedRecoveryTask}`);
  }
  await window.webContents.executeJavaScript(
    `(() => {
      window.__phase11RecoveryEvents = [];
      window.__phase11RecoveryUnsubscribe?.();
      window.__phase11RecoveryUnsubscribe = window.pkulaw.onEngineEvent(event => {
        if (event.event === 'task.manual-recovered') {
          window.__phase11RecoveryEvents.push(event.data);
        }
      });
    })()`
  );
  const firstSampleWait = window.webContents.executeJavaScript(
    `new Promise((resolve) => {
      const deadline = Date.now() + 8_000;
      const timer = setInterval(() => {
        if (document.querySelector('.manual-recovery-status.observing')) {
          clearInterval(timer);
          resolve(true);
          return;
        }
        if (Date.now() >= deadline) {
          clearInterval(timer);
          resolve(false);
        }
      }, 50);
    })`
  );
  const manualCompletionStartedAt = Date.now();
  await browserHost.fixtureAction("login");
  await browserHost.navigate("home");
  const firstSampleObserved = Boolean(await firstSampleWait);
  const recoveryActionButtonCount = Number(
    await window.webContents.executeJavaScript(
      `document.querySelectorAll('.task-primary-action-bar button').length`
    )
  );
  const stillWaitingAfterFirstSample = (
    (await service.request("single.details", {
      taskId: recoveryStart.task.id
    })) as unknown as TaskDetails
  ).task.status;
  const recoveryEventDeadline = Date.now() + 12_000;
  let recoveryEvents: Array<Record<string, unknown>> = [];
  while (Date.now() < recoveryEventDeadline) {
    recoveryEvents = (await window.webContents.executeJavaScript(
      `window.__phase11RecoveryEvents || []`
    )) as Array<Record<string, unknown>>;
    if (recoveryEvents.length > 0) break;
    await wait(250);
  }
  await wait(1_500);
  recoveryEvents = (await window.webContents.executeJavaScript(
    `window.__phase11RecoveryEvents || []`
  )) as Array<Record<string, unknown>>;
  const recoverySettleMs = Date.now() - manualCompletionStartedAt;
  if (
    !firstSampleObserved ||
    recoveryActionButtonCount !== 0 ||
    stillWaitingAfterFirstSample !== "waiting_manual" ||
    recoverySettleMs < 2_500 ||
    recoveryEvents.length !== 1 ||
    String(recoveryEvents[0]?.taskId || "") !== recoveryStart.task.id
  ) {
    throw new Error(
      `验证码自动恢复不满足一次稳定样本等待或只恢复原任务：${JSON.stringify({
        stillWaitingAfterFirstSample,
        firstSampleObserved,
        recoveryActionButtonCount,
        recoverySettleMs,
        recoveryEvents
      })}`
    );
  }

  await browserHost.selectInstitution(fixtureProfile.id);
  await wait(500);
  const stopSummaryInput = {
    name: `阶段十一停止摘要验证 ${Date.now()}`,
    mode: "single",
    outputDir: path.join(dataDirectory, "phase11-stop-summary"),
    input: {
      queries: ["残疾人体育"],
      matchMode: "keyword",
      library: "local",
      yearSelection: { mode: "single", year: 2011 },
      province: "北京市",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 10,
      pageSelection: { mode: "limit", maxPages: 1 },
      outputDir: path.join(dataDirectory, "phase11-stop-summary")
    },
    operationId: `phase11-stop-summary-${Date.now()}`
  };
  const stopSummaryStart = await window.webContents.executeJavaScript(
    `window.pkulaw.startCollectorTask(${JSON.stringify(stopSummaryInput)})`
  ) as CollectorStartResult;
  const stopSummaryWaiting = await waitForTask(
    service,
    stopSummaryStart.task.id,
    (details) => details.task.status === "waiting_manual",
    45_000
  );
  if (
    stopSummaryStart.started !== false ||
    !stopSummaryStart.task?.id ||
    stopSummaryStart.error !== null ||
    !stopSummaryWaiting.task.error
  ) {
    throw new Error(
      `停止摘要任务未按预期在预检阶段停止：${JSON.stringify(
        stopSummaryStart
      )}`
    );
  }
  await navigate("总览");
  await wait(250);
  await navigate("逐篇采集");
  await waitForSelector(".task-workspace-frame");
  const stopTaskDeadline = Date.now() + 8_000;
  let stopTaskSelected = "";
  while (Date.now() < stopTaskDeadline) {
    stopTaskSelected = String(
      await window.webContents.executeJavaScript(
        `document.querySelector('.single-run-heading h2')?.textContent || ''`
      )
    );
    if (stopTaskSelected === stopSummaryInput.name) break;
    await wait(200);
  }
  if (stopTaskSelected !== stopSummaryInput.name) {
    throw new Error(`停止摘要任务未在逐篇工作区选中：${stopTaskSelected}`);
  }
  const stopSummaryUi = (await window.webContents.executeJavaScript(
    `(() => {
      const summary = document.querySelector('[data-testid="task-failure-summary"]');
      const button = summary?.querySelector('button');
      const scroll = document.querySelector('.task-runtime-scroll');
      const buttonRect = button?.getBoundingClientRect();
      return {
        present: Boolean(summary),
        buttonText: (button?.textContent || '').trim(),
        buttonInside:
          Boolean(buttonRect) &&
          buttonRect.left >= -1 &&
          buttonRect.right <= window.innerWidth + 1 &&
          buttonRect.top >= -1 &&
          buttonRect.bottom <= window.innerHeight + 1,
        summaryText: summary?.textContent || '',
        runtimeScrollPresent: Boolean(scroll),
        pageScrollHeight: document.documentElement.scrollHeight,
        innerHeight: window.innerHeight
      };
    })()`
  )) as Record<string, unknown>;
  if (
    stopSummaryUi.present !== true ||
    stopSummaryUi.buttonText !== "查看停止原因" ||
    stopSummaryUi.buttonInside !== true ||
    String(stopSummaryUi.summaryText).includes("{'ok': False") ||
    stopSummaryUi.runtimeScrollPresent !== true ||
    Number(stopSummaryUi.pageScrollHeight) >
      Number(stopSummaryUi.innerHeight) + 1
  ) {
    throw new Error(
      `停止摘要首屏不通过：${JSON.stringify(stopSummaryUi)}`
    );
  }
  const stopDetailButtonClicked = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = document.querySelector('[data-testid="task-failure-summary"] button');
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  if (
    !stopDetailButtonClicked ||
    !(await waitForSelector(".task-pause-detail-dialog", 5_000))
  ) {
    throw new Error("单击查看停止原因未打开详情");
  }
  const browserHiddenByStopDetail = await window.webContents.executeJavaScript(
    `document.querySelector('.browser-surface.task')?.getAttribute('data-browser-visible')`
  );
  const stopDiagnosticsScreenshotPath = path.join(
    dataDirectory,
    "phase11-stop-diagnostics.png"
  );
  await captureApplicationWindow(window, stopDiagnosticsScreenshotPath);
  const stopDetailClosed = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.task-pause-detail-dialog button')]
          .find(item => (item.textContent || '').trim() === '关闭详情');
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  await wait(350);
  const browserRestoredAfterStopDetail =
    await window.webContents.executeJavaScript(
      `document.querySelector('.browser-surface.task')?.getAttribute('data-browser-visible')`
    );
  if (
    browserHiddenByStopDetail !== "false" ||
    !stopDetailClosed ||
    browserRestoredAfterStopDetail !== "true"
  ) {
    throw new Error(
      `停止详情未正确隐藏/恢复浏览器：${JSON.stringify({
        browserHiddenByStopDetail,
        stopDetailClosed,
        browserRestoredAfterStopDetail
      })}`
    );
  }
  setLogicalContentSize(window, 1536, 864);
  const screenshotPath = path.join(
    dataDirectory,
    "phase11-unified-workspace.png"
  );
  await captureApplicationWindow(window, screenshotPath);
  return {
    layout,
    overview,
    settingsHiddenBrowser,
    browserRestored,
    beforeStartCount,
    afterStartCount,
    singleTaskId: firstStart.task.id,
    configGeneration: revision.generation,
    completedPathBefore,
    completedPathAfter,
    recoveryTaskId: recoveryStart.task.id,
    firstSampleObserved,
    stillWaitingAfterFirstSample,
    recoveryActionButtonCount,
    recoverySettleMs,
    recoveryEvents,
    yearSwitchPreview,
    localProvinceVisible,
    stopSummaryTaskId: stopSummaryStart.task.id,
    stopSummaryUi,
    stopDiagnosticsScreenshotPath,
    browserHiddenByStopDetail,
    browserRestoredAfterStopDetail,
    screenshotPath
  };
}

async function runTask6LifecycleSmoke(window: BrowserWindow): Promise<Record<string, unknown>> {
  const outputDir = path.join(
    resolveDataDirectory(),
    "task6-lifecycle",
    "trash-mode"
  );
  const unregisteredFile = path.join(outputDir, "processed", "keep-unregistered.txt");
  await fsp.mkdir(path.dirname(unregisteredFile), { recursive: true });
  await fsp.writeFile(unregisteredFile, "must remain", "utf8");
  const result = (await window.webContents.executeJavaScript(
    `(async () => {
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const task = await window.pkulaw.createTask({
        name: "任务 6 生命周期烟测",
        mode: "single",
        outputDir: ${JSON.stringify(outputDir)},
        input: { queries: ["生命周期烟测"] }
      });
      const started = await window.pkulaw.startDataProcessing(task.id, {
        governmentFallback: false
      });
      const deadline = Date.now() + 30000;
      let run = started;
      while (
        !["completed", "partial", "failed", "stopped"].includes(run.run.status) &&
        Date.now() < deadline
      ) {
        await wait(100);
        run = await window.pkulaw.getDataProcessing(started.run.id);
      }
      const expectedNoSourceFailure =
        run.run.status === "failed" &&
        String(run.run.error?.message || "").includes(
          "没有具备真实 Word 或 ZIP 下载源的可处理项目"
        );
      if (run.run.status !== "completed" && !expectedNoSourceFailure) {
        throw new Error("任务 6 数据包生成未完成：" + run.run.status);
      }
      await window.pkulaw.archiveTasks([task.id], "任务 6 回收站烟测");
      const activeList = await window.pkulaw.listTasks(200, false);
      const archivedList = await window.pkulaw.listTasks(200, true);
      await window.pkulaw.restoreTasks([task.id]);
      const restoredList = await window.pkulaw.listTasks(200, false);
      await window.pkulaw.archiveTasks([task.id], "任务 6 删除烟测");
      const preview = await window.pkulaw.getCleanupPreview([task.id]);
      const eligiblePaths = preview.eligibleFiles
        .filter((file) => file.exists)
        .map((file) => file.path);
      const purge = await window.pkulaw.purgeTasks([task.id], {
        fileMode: "trash"
      });
      const recycleTask = await window.pkulaw.createTask({
        name: "任务 6 回收站列表烟测",
        mode: "single",
        outputDir: ${JSON.stringify(
          path.join(resolveDataDirectory(), "task6-lifecycle", "recycle-list")
        )},
        input: { queries: ["回收站列表烟测"] }
      });
      await window.pkulaw.archiveTasks([recycleTask.id], "任务 6 界面烟测");
      return {
        taskId: task.id,
        recycleTaskId: recycleTask.id,
        archivedHidden: !activeList.some((item) => item.id === task.id),
        archivedVisible: archivedList.some((item) => item.id === task.id),
        restoredVisible: restoredList.some((item) => item.id === task.id),
        eligiblePaths,
        trashResult: purge
      };
    })()`,
    true
  )) as Record<string, unknown>;

  const eligiblePaths = Array.isArray(result.eligiblePaths)
    ? (result.eligiblePaths as string[])
    : [];
  if (eligiblePaths.some((filePath) => fs.existsSync(filePath))) {
    throw new Error("任务 6 回收站清理后仍有登记文件存在");
  }
  if (!fs.existsSync(outputDir) || !fs.existsSync(unregisteredFile)) {
    throw new Error("任务 6 清理误删了输出根或未登记文件");
  }
  if (result.archivedHidden !== true || result.archivedVisible !== true || result.restoredVisible !== true) {
    throw new Error("任务 6 归档/恢复可见性烟测失败");
  }
  return {
    ...result,
    outputDirectoryPreserved: true,
    unregisteredFilePreserved: true
  };
}

async function runTask6UiSmoke(window: BrowserWindow): Promise<Record<string, unknown>> {
  const markerTaskId = String(
    await window.webContents.executeJavaScript(
      `(async () => {
        const task = await window.pkulaw.createTask({
          name: "任务 6 活动列表烟测",
          mode: "single",
          outputDir: ${JSON.stringify(
            path.join(resolveDataDirectory(), "task6-lifecycle", "active-list")
          )},
          input: { queries: ["活动列表烟测"] }
        });
        return task.id;
      })()`
    )
  );
  const clicked = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.nav-item')]
          .find(item => (item.textContent || '').includes('任务中心'));
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  if (!clicked) {
    throw new Error("任务中心导航不可用");
  }

  const deadline = Date.now() + 10_000;
  let taskUi: Record<string, unknown> = {};
  while (Date.now() < deadline) {
    taskUi = (await window.webContents.executeJavaScript(
      `(() => ({
        scopeTabs: Boolean(document.querySelector('.task-scope-tabs')),
        selectionToolbar: Boolean(document.querySelector('.task-selection-toolbar')),
        rowCheckboxes: document.querySelectorAll('.task-row-checkbox').length,
        rows: document.querySelectorAll('.task-browser-row').length
      }))()`
    )) as Record<string, unknown>;
    if (taskUi.scopeTabs && Number(taskUi.rows) > 0) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (
    !taskUi.scopeTabs ||
    !taskUi.selectionToolbar ||
    Number(taskUi.rowCheckboxes) < 1
  ) {
    throw new Error(`任务中心选择界面未渲染：${JSON.stringify(taskUi)}`);
  }

  const recycleClicked = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.task-scope-tabs button')]
          .find(item => (item.textContent || '').includes('回收站'));
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  if (!recycleClicked) {
    throw new Error("任务回收站页签不可用");
  }

  const recycleDeadline = Date.now() + 10_000;
  let recycleUi: Record<string, unknown> = {};
  while (Date.now() < recycleDeadline) {
    recycleUi = (await window.webContents.executeJavaScript(
      `(() => {
        const row = [...document.querySelectorAll('.task-browser-row')]
          .find(item => (item.textContent || '').includes('任务 6 回收站列表烟测'));
        return {
          archivedTaskVisible: Boolean(row),
          rowCheckboxes: document.querySelectorAll('.task-row-checkbox').length
        };
      })()`
    )) as Record<string, unknown>;
    if (recycleUi.archivedTaskVisible) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!recycleUi.archivedTaskVisible) {
    throw new Error(`回收站任务未显示：${JSON.stringify(recycleUi)}`);
  }

  const archivedTaskSelected = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const row = [...document.querySelectorAll('.task-browser-row')]
          .find(item => (item.textContent || '').includes('任务 6 回收站列表烟测'));
        if (!row) return false;
        row.click();
        return true;
      })()`
    )
  );
  await new Promise((resolve) => setTimeout(resolve, 500));
  const archivedDetailVisible = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const title = document.querySelector('.task-detail-head h3')?.textContent || '';
        return title.includes('任务 6 回收站列表烟测');
      })()`
    )
  );
  if (!archivedTaskSelected || !archivedDetailVisible) {
    throw new Error("回收站任务详情未正确切换");
  }

  await window.webContents.executeJavaScript(
    `(() => {
      const checkbox = document.querySelector('.task-row-checkbox');
      if (checkbox) checkbox.click();
    })()`
  );
  await new Promise((resolve) => setTimeout(resolve, 150));
  const selectedText = String(
    await window.webContents.executeJavaScript(
      "document.querySelector('.task-selection-toolbar strong')?.textContent || ''"
    )
  );
  if (!selectedText.includes("1")) {
    throw new Error(`任务多选数量未更新：${selectedText}`);
  }

  const purgeDialogClicked = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.task-selection-toolbar button')]
          .find(item => (item.textContent || '').includes('彻底删除'));
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  const purgeDialogDeadline = Date.now() + 10_000;
  let purgePreviewVisible = false;
  while (Date.now() < purgeDialogDeadline) {
    purgePreviewVisible = Boolean(
      await window.webContents.executeJavaScript(
        "Boolean(document.querySelector('.cleanup-confirm-panel'))"
      )
    );
    if (purgePreviewVisible) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!purgeDialogClicked || !purgePreviewVisible) {
    throw new Error("任务 6 永久删除确认界面未打开");
  }
  const cleanupConfirmScreenshotPath = path.join(
    resolveDataDirectory(),
    "task6-cleanup-confirm.png"
  );
  await captureApplicationWindow(window, cleanupConfirmScreenshotPath);
  await window.webContents.executeJavaScript(
    `(() => {
      const button = [...document.querySelectorAll('.cleanup-confirm-actions button')]
        .find(item => (item.textContent || '').includes('取消'));
      if (button) button.click();
    })()`
  );

  const screenshotPath = path.join(resolveDataDirectory(), "task6-recycle-bin.png");
  await captureApplicationWindow(window, screenshotPath);
  return {
    markerTaskId,
    ...taskUi,
    ...recycleUi,
    archivedTaskSelected,
    archivedDetailVisible,
    selectedText,
    purgePreviewVisible,
    cleanupConfirmScreenshotPath,
    screenshotPath
  };
}

async function runYearRangeUiSmoke(window: BrowserWindow): Promise<Record<string, unknown>> {
  const clicked = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.nav-item')]
          .find(item => (item.textContent || '').includes('逐篇采集'));
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  if (!clicked) {
    throw new Error("逐篇采集导航不可用");
  }

  const deadline = Date.now() + 10_000;
  let ready = false;
  let emptyTaskList = false;
  while (Date.now() < deadline) {
    const state = (await window.webContents.executeJavaScript(
      `(() => ({
        ready: Boolean(
          document.querySelector('.year-range-control') &&
          document.querySelector('.page-limit-control') &&
          document.querySelector('.single-start')
        ),
        emptyTaskList: Boolean(
          [...document.querySelectorAll('.single-task-tabs .muted')]
            .some(item => (item.textContent || '').includes('暂无逐篇任务'))
        )
      }))()`
    )) as Record<string, unknown>;
    ready = state.ready === true;
    emptyTaskList = state.emptyTaskList === true;
    if (ready) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!ready) {
    throw new Error("年份选择控件未渲染");
  }
  if (!emptyTaskList) {
    throw new Error("年份控件烟测启动时任务列表不为空");
  }

  const rangeSelected = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const mode = document.querySelector('.year-range-control select');
        if (!mode) return false;
        mode.value = 'range';
        mode.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      })()`
    )
  );
  const rangeInputDeadline = Date.now() + 3_000;
  let rangeInputsReady = false;
  while (Date.now() < rangeInputDeadline) {
    rangeInputsReady = Boolean(
      await window.webContents.executeJavaScript(
        `(() => {
          const inputs = [...document.querySelectorAll('.year-range-control input')];
          if (inputs.length < 2) return false;
          const setter = Object.getOwnPropertyDescriptor(
            HTMLInputElement.prototype,
            'value'
          )?.set;
          if (!setter) return false;
          setter.call(inputs[0], '2020');
          inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
          setter.call(inputs[1], '2021');
          inputs[1].dispatchEvent(new Event('input', { bubbles: true }));
          return true;
        })()`
      )
    );
    if (rangeInputsReady) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!rangeInputsReady) {
    throw new Error("年份区间输入框未渲染");
  }
  const rangeHintDeadline = Date.now() + 3_000;
  let rangeHint = "";
  while (Date.now() < rangeHintDeadline) {
    rangeHint = String(
      await window.webContents.executeJavaScript(
        "document.querySelector('.year-range-hint')?.textContent || ''"
      )
    );
    if (rangeHint.includes("2020 至 2021")) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const rangeUi = (await window.webContents.executeJavaScript(
    `(() => ({
      inputCount: document.querySelectorAll('.year-range-control input').length,
      values: [...document.querySelectorAll('.year-range-control input')]
        .map(input => input.value),
      hint: document.querySelector('.year-range-hint')?.textContent || '',
      hasError: Boolean(document.querySelector('.year-range-error'))
    }))()`
  )) as Record<string, unknown>;
  if (
    !rangeSelected ||
    Number(rangeUi.inputCount) < 2 ||
    !Array.isArray(rangeUi.values) ||
    rangeUi.values[0] !== "2020" ||
    rangeUi.values[1] !== "2021" ||
    !String(rangeUi.hint).includes("2020 至 2021") ||
    rangeUi.hasError !== false
  ) {
    throw new Error(`年份区间界面未展开：${JSON.stringify(rangeUi)}`);
  }
  const screenshotPath = path.join(resolveDataDirectory(), "task7-year-range.png");
  await captureApplicationWindow(window, screenshotPath);
  const paginationUi = (await window.webContents.executeJavaScript(
    `(async () => {
      const mode = document.querySelector('.page-limit-control select');
      if (!mode) return { present: false };
      const initialMode = mode.value;
      const modes = [...mode.options].map(option => option.value);
      mode.value = 'limit';
      mode.dispatchEvent(new Event('change', { bubbles: true }));
      await new Promise(resolve => setTimeout(resolve, 120));
      const input = document.querySelector('.page-limit-control input[type="number"]');
      const result = {
        present: true,
        initialMode,
        modes,
        minPages: input ? Number(input.min) : 0
      };
      mode.value = 'last';
      mode.dispatchEvent(new Event('change', { bubbles: true }));
      return result;
    })()`
  )) as Record<string, unknown>;
  if (
    paginationUi.present !== true ||
    !Array.isArray(paginationUi.modes) ||
    !paginationUi.modes.includes("last") ||
    !paginationUi.modes.includes("limit") ||
    Number(paginationUi.minPages || 0) !== 1
  ) {
    throw new Error(`分页模式界面不正确：${JSON.stringify(paginationUi)}`);
  }
  return {
    rangeSelected,
    inputCount: rangeUi.inputCount,
    values: rangeUi.values,
    hint: rangeUi.hint,
    hasError: rangeUi.hasError,
    paginationUi,
    screenshotPath
  };
}

async function runTaskRuntimeUiSmoke(
  window: BrowserWindow,
  options: {
    taskName: string;
    screenshotName: string;
    expectErrorCode?: string;
    expectDownloadKind?: string;
  }
): Promise<Record<string, unknown>> {
  const clickedNav = Boolean(
    await window.webContents.executeJavaScript(
      `(() => {
        const button = [...document.querySelectorAll('.nav-item')]
          .find(item => (item.textContent || '').includes('任务中心'));
        if (!button) return false;
        button.click();
        return true;
      })()`
    )
  );
  if (!clickedNav) throw new Error("阶段 I 任务中心导航不可用");
  const deadline = Date.now() + 20_000;
  let selected = false;
  while (Date.now() < deadline) {
    selected = Boolean(
      await window.webContents.executeJavaScript(
        `(() => {
          const row = [...document.querySelectorAll('.task-browser-row')]
            .find(item => (item.textContent || '').includes(${JSON.stringify(
              options.taskName
            )}));
          if (!row) return false;
          row.click();
          return true;
        })()`
      )
    );
    if (selected) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!selected) {
    throw new Error(`任务中心未找到任务：${options.taskName}`);
  }
  const runtimeDeadline = Date.now() + 20_000;
  let runtime: Record<string, unknown> = {};
  while (Date.now() < runtimeDeadline) {
    runtime = (await window.webContents.executeJavaScript(
      `(() => ({
        taskTitle: document.querySelector('.task-detail-head h3')?.textContent || '',
        panel: document.querySelector('[data-testid="task-runtime-panel"]')?.textContent || '',
        workflow: document.querySelector('[data-testid="workflow-stage-strip"]')?.textContent || '',
        error: document.querySelector('[data-testid="task-error-card"]')?.textContent || '',
        download: document.querySelector('[data-testid="download-intent-card"]')?.textContent || '',
        timeline: document.querySelector('[data-testid="task-timeline"]')?.textContent || '',
        timelineRows: document.querySelectorAll('.timeline-row').length,
        stateRows: document.querySelectorAll('.workflow-step').length
      }))()`
    )) as Record<string, unknown>;
    if (
      String(runtime.taskTitle) === options.taskName &&
      String(runtime.panel).length > 0 &&
      Number(runtime.timelineRows || 0) > 0
    ) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (
    String(runtime.taskTitle) !== options.taskName ||
    !String(runtime.panel) ||
    !String(runtime.workflow) ||
    Number(runtime.timelineRows || 0) < 1 ||
    Number(runtime.stateRows || 0) < 2
  ) {
    throw new Error(`阶段 I 任务运行信息未完整渲染：${JSON.stringify(runtime)}`);
  }
  if (
    options.expectErrorCode &&
    ![runtime.error, runtime.panel, runtime.timeline].some((value) =>
      String(value || "").includes(options.expectErrorCode || "")
    )
  ) {
    throw new Error(
      `阶段 I 运行信息缺少 ${options.expectErrorCode}：${JSON.stringify(runtime)}`
    );
  }
  if (
    options.expectDownloadKind &&
    !String(runtime.download).includes(options.expectDownloadKind)
  ) {
    throw new Error(
      `阶段 I 下载意图缺少 ${options.expectDownloadKind}：${JSON.stringify(runtime)}`
    );
  }
  const screenshotPath = path.join(
    resolveDataDirectory(),
    options.screenshotName
  );
  await captureApplicationWindow(window, screenshotPath);
  return {
    ...runtime,
    screenshotPath
  };
}

async function runStage3Smoke(window: BrowserWindow, service: PythonService): Promise<void> {
  try {
    await waitForRenderer(window);
    if (!browserHost || !fixtureProfile) {
      throw new Error("Stage 3 smoke requires fixture browser mode");
    }
    await browserHost.selectInstitution(fixtureProfile.id);
    const yearRangeUi = await runYearRangeUiSmoke(window);
    const dataDirectory = resolveDataDirectory();
    const loginWaitTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 自动登录暂停验证",
      {
        queries: ["阶段三测试政策"],
        matchMode: "title",
        library: "central",
        year: "2024",
        province: "全部省份",
        timeliness: "现行有效",
        pageSize: 20,
        maxPages: 1
      }
    );
    const loginWait = await waitForTask(
      service,
      loginWaitTaskId,
      (details) => details.task.status === "waiting_login",
      15_000
    );
    const leaseDeadline = Date.now() + 10_000;
    let activeLease = browserHost.getState().browserLease;
    while (
      Date.now() < leaseDeadline &&
      (activeLease.available || activeLease.taskId !== loginWaitTaskId)
    ) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      activeLease = browserHost.getState().browserLease;
    }
    if (activeLease.available || activeLease.taskId !== loginWaitTaskId) {
      throw new Error(`逐篇任务未显示浏览器租约：${JSON.stringify(activeLease)}`);
    }
    const repeatedStart = (await service.request(
      "single.start",
      { taskId: loginWaitTaskId },
      15_000
    )) as unknown as TaskDetails;
    if (
      repeatedStart.task.id !== loginWaitTaskId ||
      repeatedStart.task.status !== "waiting_login"
    ) {
      throw new Error("同一逐篇任务重复启动未返回当前详情");
    }
    const createOperationId = `phase-c-create-${Date.now()}`;
    const pendingSingleInput = {
      name: "阶段 C 浏览器租约互斥验证",
      mode: "single",
      input: {
        queries: ["阶段三测试政策"],
        matchMode: "title",
        library: "central",
        pageSize: 20,
        outputDir: path.join(dataDirectory, "single-lease-blocked")
      },
      outputDir: path.join(dataDirectory, "single-lease-blocked"),
      operationId: createOperationId
    };
    const [createdFirst, createdSecond] = await Promise.all([
      service.request("task.create", pendingSingleInput),
      service.request("task.create", pendingSingleInput)
    ]);
    const firstCreatedTask = createdFirst.task as TaskRecord;
    const secondCreatedTask = createdSecond.task as TaskRecord;
    if (firstCreatedTask.id !== secondCreatedTask.id) {
      throw new Error("同一 operationId 的快速双击创建了不同任务");
    }
    let leaseBusyCode = "";
    try {
      await service.request(
        "single.start",
        {
          taskId: firstCreatedTask.id,
          operationId: `phase-c-start-${Date.now()}`
        },
        15_000
      );
    } catch (error) {
      if (!(error instanceof EngineRequestError)) throw error;
      leaseBusyCode = error.code;
    }
    if (leaseBusyCode !== "BROWSER_LEASE_BUSY") {
      throw new Error(`不同逐篇任务未被租约拒绝：${leaseBusyCode || "started"}`);
    }
    let browser = await browserHost.fixtureAction("login");
    if (!browser.authenticated) {
      throw new Error("Fixture login failed before single workflow");
    }
    await service.request("single.continue", { taskId: loginWaitTaskId });
    const loginWaitResult = await waitForTask(
      service,
      loginWaitTaskId,
      (details) => details.task.status === "completed"
    );
    const loginSearchIdentity = loginWaitResult.task.checkpoint
      .searchIdentity as Record<string, unknown> | undefined;
    const loginDownloadIntent = loginWaitResult.items.find(
      (item) => item.status === "completed"
    )?.payload.downloadIntent as Record<string, unknown> | undefined;
    if (
      loginWait.task.status !== "waiting_login" ||
      loginWaitResult.items.filter((item) => item.status === "completed").length !== 1 ||
      !loginSearchIdentity?.searchId ||
      loginSearchIdentity.page !== 1 ||
      loginSearchIdentity.query !== "阶段三测试政策" ||
      loginSearchIdentity.matchMode !== "title" ||
      loginSearchIdentity.actualYear !== "2024" ||
      loginSearchIdentity.actualProvince !== "全部省份" ||
      loginSearchIdentity.actualTimeliness !== "现行有效" ||
      Number(loginSearchIdentity.actualPageSize || 0) !== 20 ||
      loginSearchIdentity.groupingMode !== "ungrouped" ||
      !loginDownloadIntent?.intentId ||
      loginDownloadIntent.kind !== "word"
    ) {
      throw new Error(
        `Automatic login pause or SearchSession checkpoint failed: ${JSON.stringify(
          loginSearchIdentity
        )}`
      );
    }

    const paginationTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 分页与逐条下载验证",
      {
        queries: ["分页测试"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 3
      }
    );
    const pauseCandidate = await waitForTask(
      service,
      paginationTaskId,
      (details) =>
        ["preflight", "running"].includes(details.task.status) &&
        details.items.filter((item) => item.status === "completed").length >= 2,
      30_000
    );
    const pauseRequest = (await service.request("single.pause", {
      taskId: paginationTaskId
    })) as unknown as TaskDetails;
    if (!["pausing", "paused"].includes(pauseRequest.task.status)) {
      throw new Error(`Pause request was not accepted: ${pauseRequest.task.status}`);
    }
    const paused = await waitForTask(
      service,
      paginationTaskId,
      (details) => details.task.status === "paused",
      15_000
    );
    const completedAtPause = paused.items.filter(
      (item) => item.status === "completed"
    ).length;
    const scannedPagesAtPause = Number(
      paused.task.checkpoint.scannedPages || 0
    );
    const duplicatePause = (await service.request("single.pause", {
      taskId: paginationTaskId
    })) as unknown as TaskDetails;
    if (
      duplicatePause.task.status !== "paused" ||
      duplicatePause.task.updatedAt !== paused.task.updatedAt
    ) {
      throw new Error("Repeated pause was not idempotent");
    }
    await new Promise((resolve) => setTimeout(resolve, 900));
    const stillPaused = (await service.request(
      "single.details",
      { taskId: paginationTaskId }
    )) as unknown as TaskDetails;
    if (
      stillPaused.task.status !== "paused" ||
      stillPaused.items.filter((item) => item.status === "completed").length !==
        completedAtPause
    ) {
      throw new Error("Paused task continued issuing work before resume");
    }
    await service.request("single.resume", { taskId: paginationTaskId }, 15_000);
    const resumed = await waitForTask(
      service,
      paginationTaskId,
      (details) => details.task.status === "completed",
      180_000
    );
    const paginationResult = await waitForTask(
      service,
      paginationTaskId,
      (details) => details.task.status === "completed"
    );
    if (
      !paused.task.checkpoint.pauseReason ||
      paused.task.checkpoint.pauseKind !== "manual" ||
      Number(resumed.task.checkpoint.scannedPages || 0) !== scannedPagesAtPause
    ) {
      throw new Error("Manual pause checkpoint changed or search was restarted");
    }

    const stopTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 停止与断点恢复验证",
      {
        queries: ["分页测试"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 2
      }
    );
    const interrupted = await waitForTask(
      service,
      stopTaskId,
      (details) =>
        details.task.status !== "completed" &&
        details.items.filter((item) => item.status === "completed").length >= 2,
      30_000
    );
    const filesBeforeStop = interrupted.items.filter(
      (item) => item.status === "completed"
    ).length;
    await service.request("single.stop", { taskId: stopTaskId });
    await waitForTask(
      service,
      stopTaskId,
      (details) => details.task.status === "stopped",
      15_000
    );
    await service.request("single.resume", { taskId: stopTaskId }, 15_000);
    const stoppedResumeResult = await waitForTask(
      service,
      stopTaskId,
      (details) => details.task.status === "completed"
    );
    if (
      stoppedResumeResult.items.filter((item) => item.status === "completed").length !==
      12
    ) {
      throw new Error("Stopped task did not resume to completion with its checkpoint");
    }

    const paginationItems = paginationResult.items.filter(
      (item) => item.status === "completed"
    );
    if (paginationItems.length !== 12) {
      throw new Error(`Expected 12 paginated Word files, received ${paginationItems.length}`);
    }
    for (const item of paginationItems) {
      const filePath = String(item.payload.downloadedFile || "");
      if (!filePath || !fs.existsSync(filePath)) {
        throw new Error(`Paginated policy file is missing: ${item.title}`);
      }
    }

    const rangeTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 年份区间与无结果年份验证",
      {
        queries: ["分页测试"],
        matchMode: "keyword",
        library: "central",
        yearSelection: { mode: "range", startYear: 2019, endYear: 2021 },
        yearMode: "range",
        yearStart: 2019,
        yearEnd: 2021,
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 3
      }
    );
    const rangeResult = await waitForTask(
      service,
      rangeTaskId,
      (details) => details.task.status === "completed",
      120_000
    );
    const rangeStats = Array.isArray(rangeResult.task.checkpoint.yearStats)
      ? (rangeResult.task.checkpoint.yearStats as Array<Record<string, unknown>>)
      : [];
    if (
      rangeResult.items.length !== 4 ||
      rangeStats.find((item) => item.year === "2019")?.status !== "no_result" ||
      rangeStats.find((item) => item.year === "2020")?.status !== "completed" ||
      rangeStats.find((item) => item.year === "2021")?.status !== "completed"
    ) {
      throw new Error(
        `年份区间执行或年度统计不正确：${JSON.stringify(rangeStats)}`
      );
    }

    const rangePauseTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 年份断点暂停验证",
      {
        queries: ["分页测试"],
        matchMode: "keyword",
        library: "central",
        yearSelection: { mode: "range", startYear: 2020, endYear: 2021 },
        yearMode: "range",
        yearStart: 2020,
        yearEnd: 2021,
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 3
      }
    );
    await waitForTask(
      service,
      rangePauseTaskId,
      (details) =>
        details.task.status === "running" &&
        Boolean(details.task.checkpoint.currentYear),
      30_000
    );
    await service.request("single.pause", { taskId: rangePauseTaskId });
    const rangePaused = await waitForTask(
      service,
      rangePauseTaskId,
      (details) => details.task.status === "paused",
      20_000
    );
    if (
      !rangePaused.task.checkpoint.currentYear ||
      !rangePaused.task.checkpoint.page
    ) {
      throw new Error("年份暂停未保留当前年份和页码");
    }
    const pausedYearStats = Array.isArray(rangePaused.task.checkpoint.yearStats)
      ? (rangePaused.task.checkpoint.yearStats as Array<Record<string, unknown>>)
      : [];
    if (
      pausedYearStats.find(
        (item) => item.year === rangePaused.task.checkpoint.currentYear
      )?.status !== "paused"
    ) {
      throw new Error("年份暂停状态未写入年度统计");
    }
    await service.request("single.resume", { taskId: rangePauseTaskId }, 15_000);
    const rangeResumed = await waitForTask(
      service,
      rangePauseTaskId,
      (details) => details.task.status === "completed",
      120_000
    );
    if (rangeResumed.items.length !== 4) {
      throw new Error("年份断点恢复结果数量不正确");
    }
    const resumedYearStats = Array.isArray(rangeResumed.task.checkpoint.yearStats)
      ? (rangeResumed.task.checkpoint.yearStats as Array<Record<string, unknown>>)
      : [];
    if (
      resumedYearStats.some((item) =>
        ["paused", "running"].includes(String(item.status || ""))
      )
    ) {
      throw new Error("年份恢复后年度统计仍有暂停或运行中状态");
    }

    const wordPauseTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 Word 下载后暂停边界验证",
      {
        queries: ["暂停边界测试政策"],
        matchMode: "title",
        library: "central",
        year: "2024",
        province: "全部省份",
        timeliness: "现行有效",
        pageSize: 20,
        maxPages: 1
      }
    );
    await waitForTask(
      service,
      wordPauseTaskId,
      (details) =>
        details.task.status === "running" &&
        details.task.checkpoint.currentAction === "word-download",
      20_000
    );
    await service.request("single.pause", { taskId: wordPauseTaskId });
    const wordPaused = await waitForTask(
      service,
      wordPauseTaskId,
      (details) => details.task.status === "paused",
      20_000
    );
    const pausedWordItem = wordPaused.items.find(
      (item) => item.status === "completed"
    );
    const pausedWordPath = String(pausedWordItem?.payload.downloadedFile || "");
    if (
      !pausedWordItem ||
      !pausedWordPath ||
      !fs.existsSync(pausedWordPath) ||
      wordPaused.task.checkpoint.currentAction !== "after-word-download"
    ) {
      throw new Error("Manual pause took effect before Word download and validation completed");
    }
    await service.request("single.resume", { taskId: wordPauseTaskId }, 15_000);
    await waitForTask(
      service,
      wordPauseTaskId,
      (details) => details.task.status === "completed"
    );

    const filteredTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 地方与筛选验证",
      {
        queries: ["阶段三地方政策"],
        matchMode: "title",
        library: "local",
        year: "2022",
        province: "内蒙古自治区",
        timeliness: "现行有效",
        pageSize: 20,
        maxPages: 2
      }
    );
    const filteredResult = await waitForTask(
      service,
      filteredTaskId,
      (details) => details.task.status === "completed"
    );
    if (
      filteredResult.items.length !== 1 ||
      filteredResult.items[0].payload.selectedCandidate?.library !== "local"
    ) {
      throw new Error("Local library or filter verification failed");
    }

    const retryTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 失败项重试验证",
      {
        queries: ["失败重试政策"],
        matchMode: "title",
        library: "central",
        year: "2024",
        province: "全部省份",
        timeliness: "现行有效",
        pageSize: 20,
        maxPages: 2
      }
    );
    const partialResult = await waitForTask(
      service,
      retryTaskId,
      (details) => details.task.status === "partial"
    );
    if (!partialResult.items.some((item) => item.status === "failed")) {
      throw new Error("Expected a failed item before retry");
    }
    await service.request("single.retry", { taskId: retryTaskId }, 15_000);
    const retriedResult = await waitForTask(
      service,
      retryTaskId,
      (details) => details.task.status === "completed"
    );
    if (!retriedResult.items.every((item) => item.status === "completed")) {
      throw new Error("Retry did not complete the previously failed item");
    }

    const quotaTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 机构额度硬停止验证",
      {
        queries: ["额度测试"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 20,
        maxPages: 2
      }
    );
    const quotaResult = await waitForTask(
      service,
      quotaTaskId,
      (details) => details.task.status === "stopped",
      15_000
    );
    if (quotaResult.task.error?.code !== "INSTITUTION_QUOTA_REACHED") {
      throw new Error("Quota fixture did not hard-stop the task");
    }
    await browserHost.navigate("home");

    const captchaTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 滑块等待验证",
      {
        queries: ["滑块测试"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 20,
        maxPages: 2
      }
    );
    const captchaResult = await waitForTask(
      service,
      captchaTaskId,
      (details) => details.task.status === "waiting_manual",
      15_000
    );
    await service.request("single.stop", { taskId: captchaTaskId });
    await waitForTask(
      service,
      captchaTaskId,
      (details) => details.task.status === "stopped",
      15_000
    );
    await browserHost.navigate("home");

    const duplicateTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 同名版本消歧验证",
      {
        queries: ["同名政策"],
        matchMode: "title",
        library: "both",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 2
      }
    );
    const waiting = await waitForTask(
      service,
      duplicateTaskId,
      (details) =>
        details.task.status === "waiting_manual" &&
        details.items.some(
          (item) =>
            item.status === "waiting_manual" &&
            (item.payload.candidates?.length || 0) > 1
        ),
      20_000
    );
    const ambiguous = waiting.items.find(
      (item) =>
        item.status === "waiting_manual" &&
        (item.payload.candidates?.length || 0) > 1
    );
    if (!ambiguous) {
      throw new Error("Duplicate-title candidate item was not created");
    }
    const chosen = ambiguous.payload.candidates?.find(
      (candidate) => candidate.policyId === "cn-duplicate-2024"
    );
    if (!chosen) {
      throw new Error("Expected duplicate candidate was not available");
    }
    await service.request("single.choose-candidate", {
      taskId: duplicateTaskId,
      itemId: ambiguous.id,
      candidateKey: chosen.policyId || chosen.url
    });
    const duplicateResult = await waitForTask(
      service,
      duplicateTaskId,
      (details) => details.task.status === "completed"
    );
    const completedItem = duplicateResult.items.find((item) => item.status === "completed");
    const wordPath = String(completedItem?.payload.downloadedFile || "");
    if (!wordPath || !fs.existsSync(wordPath)) {
      throw new Error("Duplicate-title selected Word file is missing");
    }
    const archive = unzipSync(new Uint8Array(await fsp.readFile(wordPath)));
    const documentXml = strFromU8(archive["word/document.xml"]);
    if (!documentXml.includes("同名政策 2024 年版本")) {
      throw new Error("Selected Word file does not contain the chosen 2024 candidate body");
    }
    const selectedDetail = completedItem?.payload.detail as
      | { documentNumber?: string; effectLevel?: string }
      | undefined;
    if (
      selectedDetail?.documentNumber !== "国发〔2024〕9号" ||
      selectedDetail?.effectLevel !== "行政法规"
    ) {
      throw new Error("Selected detail metadata was not persisted correctly");
    }

    const legacyDocTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 旧版 DOC 下载验证",
      {
        queries: ["旧版 Word 格式测试政策"],
        matchMode: "title",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 20,
        maxPages: 1
      }
    );
    const legacyDocResult = await waitForTask(
      service,
      legacyDocTaskId,
      (details) => details.task.status === "completed",
      20_000
    );
    const legacyDocItem = legacyDocResult.items.find(
      (item) => item.status === "completed"
    );
    const legacyDocPath = String(legacyDocItem?.payload.downloadedFile || "");
    if (!legacyDocPath || !legacyDocPath.toLowerCase().endsWith(".doc")) {
      throw new Error("Legacy OLE .doc was not moved to a formal .doc file");
    }
    const legacyDocBytes = await fsp.readFile(legacyDocPath);
    if (
      !legacyDocBytes.subarray(0, 8).equals(
        Buffer.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1])
      )
    ) {
      throw new Error("Legacy .doc fixture has an invalid OLE header");
    }
    const legacyDocSha256 = await sha256File(legacyDocPath);
    if (legacyDocItem?.payload.sha256 !== legacyDocSha256) {
      throw new Error("Legacy .doc SHA-256 was not persisted correctly");
    }

    const invalidWordTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 无效 Word 校验验证",
      {
        queries: ["正文提取异常政策"],
        matchMode: "title",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 20,
        maxPages: 1
      }
    );
    const invalidWordResult = await waitForTask(
      service,
      invalidWordTaskId,
      (details) =>
        ["partial", "failed"].includes(details.task.status),
      20_000
    );
    const invalidWordItem = invalidWordResult.items.find(
      (item) => item.status === "failed"
    );
    const invalidWordValidation = invalidWordItem?.payload
      .downloadValidation as
      | {
          status?: string;
          filePath?: string;
          extension?: string;
          size?: number;
          sha256?: string;
        }
      | undefined;
    if (
      invalidWordItem?.error?.code !== "INVALID_WORD_FILE" ||
      invalidWordValidation?.status !== "quarantined" ||
      !invalidWordValidation.filePath ||
      !fs.existsSync(invalidWordValidation.filePath) ||
      !String(invalidWordValidation.filePath).includes("99_失败样本") ||
      Number(invalidWordValidation.size || 0) <= 0 ||
      !invalidWordValidation.sha256
    ) {
      throw new Error(
        `Invalid Word quarantine evidence missing: ${JSON.stringify(
          invalidWordItem
        )}`
      );
    }

    await institutionStore!.save({
      id: fixtureProfile.id,
      name: "缺少年份筛选夹具",
      entryUrl: `${fixtureProfile.entryUrl}pkulaw/?noYear=1`
    });
    await browserHost.selectInstitution(fixtureProfile.id);
    const noYearTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 3 年份筛选缺失验证",
      {
        queries: ["阶段三测试政策"],
        matchMode: "title",
        library: "central",
        yearSelection: { mode: "single", year: 2024 },
        yearMode: "single",
        year: "2024",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 20,
        maxPages: 1
      }
    );
    const noYearResult = await waitForTask(
      service,
      noYearTaskId,
      (details) => details.task.status === "stopped",
      20_000
    );
    if (noYearResult.task.error?.code !== "YEAR_FILTER_UNAVAILABLE") {
      throw new Error(
        `年份筛选缺失未返回明确错误：${noYearResult.task.error?.code || "none"}`
      );
    }
    await institutionStore!.save({
      id: fixtureProfile.id,
      name: "本地测试机构",
      entryUrl: `${fixtureProfile.entryUrl}pkulaw/`
    });
    await browserHost.selectInstitution(fixtureProfile.id);
    await waitForFixtureBrowserReady();

    const defaultLastTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "任务 8 默认逐组合扫描到尾页",
      {
        queries: ["分页测试"],
        matchMode: "title",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        pageSelection: { mode: "last" },
        pageMode: "last"
      }
    );
    const defaultLastResult = await waitForTask(
      service,
      defaultLastTaskId,
      (details) =>
        ["completed", "partial"].includes(details.task.status),
      60_000
    );
    const defaultCombinationTotals = Array.isArray(
      defaultLastResult.task.checkpoint.combinationTotals
    )
      ? (defaultLastResult.task.checkpoint.combinationTotals as Array<
          Record<string, unknown>
        >)
      : [];
    if (
      Number(defaultLastResult.task.checkpoint.scannedPages || 0) !== 2 ||
      defaultCombinationTotals.length !== 1 ||
      Number(defaultCombinationTotals[0].totalPages || 0) !== 2
    ) {
      throw new Error(
        `默认尾页模式未按组合扫描完毕：${JSON.stringify(
          defaultLastResult.task.checkpoint
        )}`
      );
    }

    const pageLimitTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "任务 8 分页上限确认",
      {
        queries: ["分页测试"],
        matchMode: "title",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        pageSelection: { mode: "limit", maxPages: 1 },
        pageMode: "limit",
        maxPages: 1
      }
    );
    const pageLimitWaiting = await waitForTask(
      service,
      pageLimitTaskId,
      (details) =>
        details.task.status === "waiting_manual" &&
        details.task.error?.code === "PAGE_LIMIT_REACHED",
      30_000
    );
    const pageLimitSearchIdentity = pageLimitWaiting.task.checkpoint
      .searchIdentity as Record<string, unknown> | undefined;
    if (
      Number(pageLimitWaiting.task.checkpoint.scannedPages || 0) !== 1 ||
      Number(pageLimitWaiting.task.checkpoint.unscannedPages || 0) !== 1 ||
      Number(pageLimitWaiting.task.checkpoint.totalPages || 0) !== 2 ||
      Number(pageLimitWaiting.task.checkpoint.currentPage || 0) !== 1 ||
      !pageLimitSearchIdentity?.searchId ||
      pageLimitSearchIdentity.page !== 1 ||
      pageLimitSearchIdentity.totalPages !== 2 ||
      pageLimitSearchIdentity.query !== "分页测试"
    ) {
      throw new Error(
        `分页上限断点不完整：${JSON.stringify(
          pageLimitWaiting.task.checkpoint
        )}`
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
    const pageLimitScreenshotPath = path.join(
      dataDirectory,
      "task8-page-limit-confirmation.png"
    );
    await captureApplicationWindow(window, pageLimitScreenshotPath);
    await service.request("single.continue-to-last", {
      taskId: pageLimitTaskId
    });
    const pageLimitContinued = await waitForTask(
      service,
      pageLimitTaskId,
      (details) =>
        ["completed", "partial"].includes(details.task.status),
      60_000
    );
    if (
      pageLimitContinued.task.checkpoint.pageMode !== "last" ||
      pageLimitContinued.task.checkpoint.pageLimitDecision !==
        "continue_to_last" ||
      Number(pageLimitContinued.task.checkpoint.scannedPages || 0) !== 2
    ) {
      throw new Error("继续到尾页决定未持久化或未扫描到尾页");
    }

    const pageLimitStopTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "任务 8 分页停止与恢复",
      {
        queries: ["测试"],
        matchMode: "title",
        library: "central",
        yearSelection: { mode: "single", year: 2024 },
        yearMode: "single",
        year: "2024",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        pageSelection: { mode: "limit", maxPages: 1 },
        pageMode: "limit",
        maxPages: 1
      }
    );
    await waitForTask(
      service,
      pageLimitStopTaskId,
      (details) =>
        details.task.status === "waiting_manual" &&
        details.task.error?.code === "PAGE_LIMIT_REACHED",
      30_000
    );
    await service.request("single.stop", { taskId: pageLimitStopTaskId });
    const pageLimitStopped = await waitForTask(
      service,
      pageLimitStopTaskId,
      (details) => details.task.status === "stopped",
      20_000
    );
    if (
      pageLimitStopped.task.checkpoint.currentYear !== "2024" ||
      Number(pageLimitStopped.task.checkpoint.currentPage || 0) !== 1 ||
      Number(pageLimitStopped.task.checkpoint.totalPages || 0) <= 1
    ) {
      throw new Error(
        `分页停止未保留年度和页码：${JSON.stringify(
          pageLimitStopped.task.checkpoint
        )}`
      );
    }
    await service.request(
      "single.resume",
      { taskId: pageLimitStopTaskId },
      15_000
    );
    await waitForTask(
      service,
      pageLimitStopTaskId,
      (details) =>
        details.task.status === "waiting_manual" &&
        details.task.error?.code === "PAGE_LIMIT_REACHED",
      30_000
    );
    await service.request("single.continue-to-last", {
      taskId: pageLimitStopTaskId
    });
    const pageLimitStoppedResumed = await waitForTask(
      service,
      pageLimitStopTaskId,
      (details) =>
        ["completed", "partial"].includes(details.task.status),
      60_000
    );
    if (
      pageLimitStoppedResumed.task.checkpoint.currentYear !== "2024" ||
      pageLimitStoppedResumed.task.checkpoint.pageMode !== "last"
    ) {
      throw new Error("分页停止恢复后年度或尾页决定丢失");
    }

    const previewTaskCountBefore = Number(
      (
        (await service.request("task.list", { limit: 500 })) as {
          tasks?: unknown[];
        }
      ).tasks?.length || 0
    );
    const previewDownloadBefore = JSON.stringify(
      browserHost.getState().lastDownload || null
    );
    const previewCommon = {
      library: "both",
      yearSelection: { mode: "single", year: 2024 },
      province: "全部省份",
      timeliness: "全部（仅记录元数据，不筛选）",
      pageSize: 20
    };
    const singlePreview = (await service.request(
      "task.preview",
      {
        mode: "single",
        input: {
          queries: ["阶段三测试政策"],
          matchMode: "title",
          ...previewCommon
        }
      },
      30_000
    )) as Record<string, unknown>;
    const batchPreview = (await service.request(
      "task.preview",
      {
        mode: "batch",
        input: {
          queries: ["政策"],
          matchMode: "keyword",
          ...previewCommon
        }
      },
      30_000
    )) as Record<string, unknown>;
    if (
      Number(singlePreview.combinationCount || 0) !== 2 ||
      Number(singlePreview.totalResults || 0) < 1 ||
      Number(singlePreview.downloadsStarted || 0) !== 0 ||
      Number(singlePreview.downloadIntentsCreated || 0) !== 0 ||
      Number(batchPreview.combinationCount || 0) !== 2 ||
      Number(batchPreview.totalResults || 0) < 1 ||
      Number(batchPreview.downloadsStarted || 0) !== 0 ||
      Number(batchPreview.downloadIntentsCreated || 0) !== 0
    ) {
      throw new Error(
        `Collector preview violated zero-download contract: ${JSON.stringify({
          singlePreview,
          batchPreview
        })}`
      );
    }
    let previewFailureCode = "";
    try {
      await service.request("task.preview", {
        mode: "batch",
        input: {
          queries: ["阶段三测试政策"],
          matchMode: "title",
          ...previewCommon
        }
      });
    } catch (error) {
      if (!(error instanceof EngineRequestError)) throw error;
      previewFailureCode = error.code;
    }
    if (previewFailureCode !== "BATCH_EXACT_TITLE_UNSUPPORTED") {
      throw new Error(
        `Batch exact-title preview was not rejected: ${
          previewFailureCode || "accepted"
        }`
      );
    }
    const previewTaskCountAfter = Number(
      (
        (await service.request("task.list", { limit: 500 })) as {
          tasks?: unknown[];
        }
      ).tasks?.length || 0
    );
    const previewDownloadAfter = JSON.stringify(
      browserHost.getState().lastDownload || null
    );
    const previewLeaseDeadline = Date.now() + 5_000;
    while (
      Date.now() < previewLeaseDeadline &&
      !browserHost.getState().browserLease.available
    ) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (
      previewTaskCountAfter !== previewTaskCountBefore ||
      previewDownloadAfter !== previewDownloadBefore ||
      !browserHost.getState().browserLease.available
    ) {
      throw new Error(
        `Collector preview left task, download or lease residue: ${JSON.stringify({
          previewTaskCountBefore,
          previewTaskCountAfter,
          previewDownloadBefore,
          previewDownloadAfter,
          lease: browserHost.getState().browserLease
        })}`
      );
    }

    const captureCollectorStartUi = async (
      navigationLabel: string,
      panelHeading: string,
      startLabel: string,
      screenshotName: string
    ): Promise<Record<string, unknown>> => {
      const clicked = Boolean(
        await window.webContents.executeJavaScript(
          `(() => {
            const button = [...document.querySelectorAll('.nav-item')]
              .find(item => (item.textContent || '').includes(${JSON.stringify(
                navigationLabel
              )}));
            if (!button) return false;
            button.click();
            return true;
          })()`
        )
      );
      if (!clicked) {
        throw new Error(`Collector navigation was unavailable: ${navigationLabel}`);
      }
      const deadline = Date.now() + 10_000;
      let prepared = false;
      let freshTaskOpened = false;
      let lastState: Record<string, unknown> = {};
      while (Date.now() < deadline) {
        lastState = (await window.webContents.executeJavaScript(
          `(() => {
            const heading = [...document.querySelectorAll('h2')]
              .find(item => (item.textContent || '').includes(${JSON.stringify(
                panelHeading
              )}));
            const form = heading?.closest('.collector-form-shell');
            const primaryButtons = form
              ? [...form.querySelectorAll('.collector-action-row > .primary-button')]
              : [];
            const startButton = primaryButtons[0];
            const freshButton = [...document.querySelectorAll(
              '.terminal-task-actions button'
            )].find(item =>
              (item.textContent || '').includes('新建任务') &&
              !item.disabled
            );
            return {
              heading: Boolean(heading),
              form: Boolean(form),
              startButton: Boolean(startButton && !startButton.disabled),
              startLabels: primaryButtons
                .map(item => (item.textContent || '').trim()),
              previewButton: Boolean(
                document.querySelector('[data-testid="collector-preview-button"]')
              ),
              freshButton: Boolean(freshButton)
            };
          })()`
        )) as Record<string, unknown>;
        prepared =
          lastState.heading === true &&
          lastState.form === true &&
          lastState.startButton === true;
        if (prepared) break;
        if (lastState.freshButton === true && !freshTaskOpened) {
          freshTaskOpened = Boolean(
            await window.webContents.executeJavaScript(
              `(() => {
                const button = [...document.querySelectorAll(
                  '.terminal-task-actions button'
                )].find(item =>
                  (item.textContent || '').includes('新建任务') &&
                  !item.disabled
                );
                if (!button) return false;
                button.click();
                return true;
              })()`
            )
          );
        }
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      if (!prepared) {
        throw new Error(
          `Collector start UI was unavailable: ${panelHeading}; ${JSON.stringify(
            lastState
          )}`
        );
      }
      if (
        lastState.previewButton !== false ||
        !Array.isArray(lastState.startLabels) ||
        lastState.startLabels.length !== 1 ||
        lastState.startLabels[0] !== startLabel
      ) {
        throw new Error(
          `Collector start UI was incorrect: ${panelHeading}; ${JSON.stringify(
            lastState
          )}`
        );
      }
      const pathValue = path.join(dataDirectory, screenshotName);
      await captureApplicationWindow(window, pathValue);
      return {
        screenshotPath: pathValue,
        heading: panelHeading,
        startLabel,
        startButtonCount: 1,
        previewButtonCount: 0,
        freshTaskOpened,
        downloadsStarted: 0
      };
    };
    const singlePreviewUi = await captureCollectorStartUi(
      "逐篇采集",
      "创建采集任务",
      "创建并开始逐篇采集",
      "stageG-single-preview.png"
    );
    const batchPreviewUi = await captureCollectorStartUi(
      "批量采集",
      "创建批量任务",
      "创建并开始批量采集",
      "stageG-batch-preview.png"
    );
    const previewUiTaskCountAfter = Number(
      (
        (await service.request("task.list", { limit: 500 })) as {
          tasks?: unknown[];
        }
      ).tasks?.length || 0
    );
    const previewUiDownloadAfter = JSON.stringify(
      browserHost.getState().lastDownload || null
    );
    if (
      previewUiTaskCountAfter !== previewTaskCountBefore ||
      previewUiDownloadAfter !== previewDownloadBefore ||
      !browserHost.getState().browserLease.available
    ) {
      throw new Error(
        `Collector preview UI left task, download or lease residue: ${JSON.stringify({
          previewTaskCountBefore,
          previewUiTaskCountAfter,
          previewDownloadBefore,
          previewUiDownloadAfter,
          lease: browserHost.getState().browserLease
        })}`
      );
    }

    const taskRuntimeUi = await runTaskRuntimeUiSmoke(window, {
      taskName: "阶段 3 机构额度硬停止验证",
      screenshotName: "stageI-task-error-recovery.png",
      expectErrorCode: "INSTITUTION_QUOTA_REACHED"
    });
    await new Promise((resolve) => setTimeout(resolve, 900));
    const screenshotPath =
      process.env.PKULAW_SMOKE_SCREENSHOT ||
      path.join(dataDirectory, "smoke.png");
    await captureApplicationWindow(window, screenshotPath);

    await finishSmoke(true, {
      backendState: service.getState(),
      browserState: browserHost.getState(),
      loginWaitTaskId,
      automaticLoginPauseObserved: loginWait.task.status === "waiting_login",
      automaticLoginContinueRecovered: loginWaitResult.task.status === "completed",
      browserLeaseObserved: activeLease,
      repeatedStartIdempotent: true,
      doubleClickCreatedTaskId: firstCreatedTask.id,
      differentSingleTaskRejected: leaseBusyCode,
      loginSearchIdentity,
      loginDownloadIntent,
      searchSessionCheckpointValidated: true,
      paginationTaskId,
      paginationPagesScanned: paginationResult.task.checkpoint.scannedPages,
      paginationItems: paginationItems.length,
      manualPauseObserved: paused.task.status === "paused",
      manualPauseCompletedItems: completedAtPause,
      manualPauseCheckpointKind: paused.task.checkpoint.pauseKind,
      manualPauseStayedIdle: true,
      manualResumeDidNotResearch:
        Number(resumed.task.checkpoint.scannedPages || 0) === scannedPagesAtPause,
      wordPauseTaskId,
      wordPauseAfterCompletion: true,
      stopTaskId,
      resumeAfterStop: true,
      filesBeforeStop,
      filesAfterResume: stoppedResumeResult.items.filter(
        (item) => item.status === "completed"
      ).length,
      filteredTaskId,
      filteredItems: filteredResult.items.length,
      retryTaskId,
      retryRecovered: true,
      quotaTaskId,
      quotaHardStopped: true,
      captchaTaskId,
      captchaManualWaitObserved: captchaResult.task.status === "waiting_manual",
      duplicateTaskId,
      duplicateCandidateCount: ambiguous.payload.candidates?.length || 0,
      duplicateChosenPolicyId: chosen.policyId,
      detailMetadataValidated: true,
      singleWordValidated: true,
      legacyDocValidated: true,
      legacyDocBytes: legacyDocBytes.byteLength,
      legacyDocSha256,
      invalidWordValidated: true,
      invalidWordQuarantined: invalidWordValidation.status === "quarantined",
      invalidWordSha256: invalidWordValidation.sha256,
      yearRangeUi,
      rangeYearTaskId: rangeTaskId,
      rangeYearStats: rangeStats,
      rangeYearPauseCheckpoint: rangePaused.task.checkpoint,
      yearFilterUnavailableValidated: true,
      defaultLastValidated: true,
      defaultCombinationTotalPages: Number(
        defaultCombinationTotals[0]?.totalPages || 0
      ),
      pageLimitTaskId,
      pageLimitSearchIdentity,
      pageLimitWaitingCheckpoint: pageLimitWaiting.task.checkpoint,
      pageLimitContinued: ["completed", "partial"].includes(
        pageLimitContinued.task.status
      ),
      pageLimitStoppedCheckpoint: pageLimitStopped.task.checkpoint,
      pageLimitStopResumeValidated:
        ["completed", "partial"].includes(pageLimitStoppedResumed.task.status),
      pageLimitScreenshotPath,
      collectorPreview: {
        single: singlePreview,
        batch: batchPreview,
        singleUi: singlePreviewUi,
        batchUi: batchPreviewUi,
        rejectedMatchModeCode: previewFailureCode,
        downloadsStarted: 0,
        downloadIntentsCreated: 0,
        tasksCreated: 0,
        leaseReleased: browserHost.getState().browserLease.available
      },
      taskRuntimeUi,
      screenshotPath
    });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    await finishSmoke(false, { error: message });
  }
}

async function waitForBatchTask(
  service: PythonService,
  taskId: string,
  predicate: (details: BatchTaskDetails) => boolean,
  timeoutMs = 180_000
): Promise<BatchTaskDetails> {
  const deadline = Date.now() + timeoutMs;
  let last: BatchTaskDetails | null = null;
  while (Date.now() < deadline) {
    const details = (await service.request(
      "batch.details",
      { taskId }
    )) as unknown as BatchTaskDetails;
    last = details;
    if (predicate(details)) {
      return details;
    }
    if (details.task.status === "failed") {
      throw new Error(`Batch task failed: ${details.task.error?.message || "unknown error"}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`Batch task ${taskId} timed out: ${JSON.stringify(last?.task || null)}`);
}

async function waitForBatchAction(
  service: PythonService,
  taskId: string,
  action: string,
  timeoutMs = 60_000
): Promise<BatchTaskDetails> {
  return waitForBatchTask(
    service,
    taskId,
    (details) =>
      String(details.task.checkpoint.currentAction || "") === action,
    timeoutMs
  );
}

async function createAndStartBatchTask(
  service: PythonService,
  dataDirectory: string,
  name: string,
  input: Record<string, unknown>,
  outputDirectory?: string
): Promise<string> {
  const outputDir =
    outputDirectory || path.join(dataDirectory, "batch-files");
  const created = (await service.request("task.create", {
    name,
    mode: "batch",
    input: {
      ...input,
      matchMode: "keyword",
      outputDir
    },
    outputDir
  })) as { task?: TaskRecord };
  if (!created.task) {
    throw new Error("Batch task creation did not return a task");
  }
  await service.request("batch.start", { taskId: created.task.id }, 15_000);
  return created.task.id;
}

async function runStage4Smoke(window: BrowserWindow, service: PythonService): Promise<void> {
  try {
    await waitForRenderer(window);
    if (!browserHost || !fixtureProfile) {
      throw new Error("Stage 4 smoke requires fixture browser mode");
    }
    await browserHost.selectInstitution(fixtureProfile.id);
    const dataDirectory = resolveDataDirectory();
    const loginPauseTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "阶段 4 批量自动登录暂停验证",
      {
        queries: ["阶段三测试政策"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 1,
        catalogFormat: "xls",
        fullFormat: "docx"
      },
      path.join(dataDirectory, "batch-files", "control-login")
    );
    const loginPaused = await waitForBatchTask(
      service,
      loginPauseTaskId,
      (details) => details.task.status === "waiting_login",
      20_000
    );
    const batchLeaseDeadline = Date.now() + 10_000;
    let batchLease = browserHost.getState().browserLease;
    while (
      Date.now() < batchLeaseDeadline &&
      (batchLease.available || batchLease.taskId !== loginPauseTaskId)
    ) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      batchLease = browserHost.getState().browserLease;
    }
    if (batchLease.available || batchLease.taskId !== loginPauseTaskId) {
      throw new Error(`批量任务未显示浏览器租约：${JSON.stringify(batchLease)}`);
    }
    const blockedBatch = (await service.request("task.create", {
      name: "阶段 C 批量租约互斥验证",
      mode: "batch",
      input: {
        queries: ["阶段三测试政策"],
          matchMode: "keyword",
        library: "central",
        catalogFormat: "xlsx",
        fullFormat: "docx",
        outputDir: path.join(dataDirectory, "batch-lease-blocked")
      },
      outputDir: path.join(dataDirectory, "batch-lease-blocked"),
      operationId: `phase-c-batch-create-${Date.now()}`
    })) as { task: TaskRecord };
    let batchLeaseBusyCode = "";
    try {
      await service.request(
        "batch.start",
        { taskId: blockedBatch.task.id },
        15_000
      );
    } catch (error) {
      if (!(error instanceof EngineRequestError)) throw error;
      batchLeaseBusyCode = error.code;
    }
    if (batchLeaseBusyCode !== "BROWSER_LEASE_BUSY") {
      throw new Error(`不同批量任务未被租约拒绝：${batchLeaseBusyCode || "started"}`);
    }
    let browser = await browserHost.fixtureAction("login");
    await service.request("batch.continue", { taskId: loginPauseTaskId });
    const loginContinued = await waitForBatchTask(
      service,
      loginPauseTaskId,
      (details) => details.task.status === "completed",
      60_000
    );
    const loginBatchProcessingSearchCount = Number(
      loginContinued.task.checkpoint.batchProcessingSearchCount || 0
    );
    const loginBatchReuseCount = Number(
      loginContinued.task.checkpoint.batchReuseCount || 0
    );
    if (
      loginPaused.task.status !== "waiting_login" ||
      loginContinued.items.filter((item) => item.status === "completed").length !== 1 ||
      loginBatchProcessingSearchCount !== 0 ||
      loginBatchReuseCount < 1
    ) {
      throw new Error("Batch automatic login pause did not recover through continue");
    }
    const loginCatalogReport = JSON.parse(
      await fsp.readFile(loginContinued.batches[0].reportPath, "utf8")
    );
    if (
      loginCatalogReport.catalog?.format !== "xls" ||
      loginCatalogReport.catalog?.native !== true ||
      loginCatalogReport.catalog?.sourceFormat !== "xls" ||
      loginCatalogReport.catalog?.finalFormat !== "xls" ||
      loginCatalogReport.catalog?.converted !== false
    ) {
      throw new Error(
        `Native XLS catalog contract failed: ${JSON.stringify(
          loginCatalogReport.catalog
        )}`
      );
    }

    const captchaTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "阶段 4 批量自动滑块暂停验证",
      {
        queries: ["滑块测试"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 20,
        maxPages: 2,
        catalogFormat: "xlsx",
        fullFormat: "docx"
      },
      path.join(dataDirectory, "batch-files", "control-captcha")
    );
    const captchaPaused = await waitForBatchTask(
      service,
      captchaTaskId,
      (details) => details.task.status === "waiting_manual",
      20_000
    );
    await service.request("batch.stop", { taskId: captchaTaskId }, 15_000);
    await waitForBatchTask(
      service,
      captchaTaskId,
      (details) => details.task.status === "stopped",
      20_000
    );
    if (captchaPaused.task.status !== "waiting_manual") {
      throw new Error("Batch captcha pause did not expose waiting_manual");
    }
    await browserHost.navigate("home");

    browser = await browserHost.getState();
    if (!browser.authenticated) {
      throw new Error("Fixture login failed before batch workflow");
    }
    const batchTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "阶段 4 分批与失败隔离验证",
      {
        queries: ["批量测试"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 50,
        maxPages: 3,
        catalogFormat: "xlsx",
        fullFormat: "docx"
      },
      path.join(dataDirectory, "batch-files", "control-main")
    );
    const partial = await waitForBatchTask(
      service,
      batchTaskId,
      (details) => details.task.status === "partial"
    );
    if (partial.batches.length !== 2) {
      throw new Error(`Expected 2 batches, received ${partial.batches.length}`);
    }
    const completedBeforeRetry = partial.batches.filter(
      (batch) => batch.status === "completed"
    );
    const failedBeforeRetry = partial.batches.filter(
      (batch) => batch.status === "failed"
    );
    if (completedBeforeRetry.length !== 1 || failedBeforeRetry.length !== 1) {
      throw new Error("Batch failure was not isolated to one batch");
    }
    if (
      completedBeforeRetry[0].selected.length !== 50 ||
      failedBeforeRetry[0].selected.length !== 10
    ) {
      throw new Error("Batch splitting did not enforce 50-policy boundaries");
    }

    await service.request("batch.retry", { taskId: batchTaskId }, 15_000);
    const completed = await waitForBatchTask(
      service,
      batchTaskId,
      (details) => details.task.status === "completed"
    );
    const batchSearchIdentity = completed.task.checkpoint.searchIdentity as
      | Record<string, unknown>
      | undefined;
    const catalogIntentId = String(
      completed.task.checkpoint.catalogIntentId || ""
    );
    const zipIntentId = String(completed.task.checkpoint.zipIntentId || "");
    const retrievalContextCount = Array.isArray(
      completed.task.checkpoint.retrievalContexts
    )
      ? completed.task.checkpoint.retrievalContexts.length
      : 0;
    const batchIdentityVerification =
      completed.task.checkpoint.batchIdentityVerification;
    const completedBatchProcessingSearchCount = Number(
      completed.task.checkpoint.batchProcessingSearchCount || 0
    );
    const completedBatchReuseCount = Number(
      completed.task.checkpoint.batchReuseCount || 0
    );
    if (
      completed.batches.length !== 2 ||
      completed.batches.some((batch) => batch.status !== "completed") ||
      !batchSearchIdentity?.searchId ||
      batchSearchIdentity.query !== "批量测试" ||
      batchSearchIdentity.library !== "central" ||
      Number(batchSearchIdentity.actualPageSize || 0) !== 50 ||
      batchSearchIdentity.actualYear !== "全部年份" ||
      batchSearchIdentity.actualProvince !== "全部省份" ||
      batchSearchIdentity.actualTimeliness !==
        "全部（仅记录元数据，不筛选）" ||
      batchSearchIdentity.groupingMode !== "ungrouped" ||
      retrievalContextCount < 1 ||
      !batchIdentityVerification ||
      !catalogIntentId ||
      !zipIntentId ||
      catalogIntentId === zipIntentId
    ) {
      throw new Error(
        `Batch retry or SearchSession checkpoint failed: ${JSON.stringify(
          batchSearchIdentity
        )}`
      );
    }
    for (const batch of completed.batches) {
      if (!fs.existsSync(batch.catalogPath) || !fs.existsSync(batch.zipPath)) {
        throw new Error(`Batch output is missing: ${batch.batchKey}`);
      }
      const report = JSON.parse(await fsp.readFile(batch.reportPath, "utf8"));
      if (
        !report.crcOk ||
        !report.identityMappingOk ||
        report.catalog?.format !== "xlsx" ||
        report.catalog?.native !== false ||
        report.catalog?.sourceFormat !== "xls" ||
        report.catalog?.finalFormat !== "xlsx" ||
        report.catalog?.converted !== true
      ) {
        throw new Error(`Batch report verification failed: ${batch.batchKey}`);
      }
    }

    const htmlTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "阶段 4 HTML-XLS 目录验证",
      {
        queries: ["批量测试政策01"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 1,
        catalogFormat: "html-xls",
        fullFormat: "docx"
      },
      path.join(dataDirectory, "batch-files", "control-html-xls")
    );
    const htmlResult = await waitForBatchTask(
      service,
      htmlTaskId,
      (details) => details.task.status === "completed"
    );
    const htmlReport = JSON.parse(
      await fsp.readFile(htmlResult.batches[0].reportPath, "utf8")
    );
    if (
      htmlReport.catalog?.format !== "html-xls" ||
      htmlReport.catalog?.native !== false ||
      htmlReport.catalog?.sourceFormat !== "xls" ||
      htmlReport.catalog?.finalFormat !== "html-xls" ||
      htmlReport.catalog?.converted !== true ||
      !String(htmlResult.batches[0].catalogPath).toLowerCase().endsWith(".xls")
    ) {
      throw new Error("HTML-XLS compatibility catalog contract failed");
    }

    const pauseTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "阶段 4 批量暂停安全边界验证",
      {
        queries: ["暂停边界测试政策"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 1,
        catalogFormat: "xlsx",
        fullFormat: "docx"
      },
      path.join(dataDirectory, "batch-files", "control-pause")
    );
    await waitForBatchAction(service, pauseTaskId, "catalog-download");
    const catalogPauseRequest = (await service.request(
      "batch.pause",
      { taskId: pauseTaskId },
      15_000
    )) as unknown as BatchTaskDetails;
    if (!["pausing", "paused"].includes(catalogPauseRequest.task.status)) {
      throw new Error(
        `Batch pause request was not accepted: ${catalogPauseRequest.task.status}`
      );
    }
    const pausedAfterCatalog = await waitForBatchTask(
      service,
      pauseTaskId,
      (details) => details.task.status === "paused",
      30_000
    );
    if (
      pausedAfterCatalog.task.checkpoint.currentAction !==
      "after-catalog-validation"
    ) {
      throw new Error(
        `Batch paused before catalog validation: ${JSON.stringify(
          pausedAfterCatalog.task.checkpoint
        )}`
      );
    }
    if (
      pausedAfterCatalog.batches.some(
        (batch) => batch.status === "completed" || batch.catalogPath || batch.zipPath
      )
    ) {
      throw new Error("Batch catalog-only pause unexpectedly finalized a batch");
    }
    const duplicateBatchPause = (await service.request(
      "batch.pause",
      { taskId: pauseTaskId }
    )) as unknown as BatchTaskDetails;
    if (
      duplicateBatchPause.task.status !== "paused" ||
      duplicateBatchPause.task.updatedAt !== pausedAfterCatalog.task.updatedAt
    ) {
      throw new Error("Repeated batch pause was not idempotent");
    }
    await new Promise((resolve) => setTimeout(resolve, 900));
    const stillPausedAfterCatalog = (await service.request(
      "batch.details",
      { taskId: pauseTaskId }
    )) as unknown as BatchTaskDetails;
    if (
      stillPausedAfterCatalog.task.status !== "paused" ||
      stillPausedAfterCatalog.task.updatedAt !== pausedAfterCatalog.task.updatedAt ||
      stillPausedAfterCatalog.batches.some(
        (batch) => batch.status === "completed" || batch.catalogPath || batch.zipPath
      )
    ) {
      throw new Error("Paused batch continued working before resume");
    }

    await service.request("batch.resume", { taskId: pauseTaskId }, 15_000);
    await waitForBatchAction(service, pauseTaskId, "zip-download");
    const zipPauseRequest = (await service.request(
      "batch.pause",
      { taskId: pauseTaskId },
      15_000
    )) as unknown as BatchTaskDetails;
    if (!["pausing", "paused"].includes(zipPauseRequest.task.status)) {
      throw new Error(
        `Batch ZIP pause request was not accepted: ${zipPauseRequest.task.status}`
      );
    }
    const pausedAfterZip = await waitForBatchTask(
      service,
      pauseTaskId,
      (details) => details.task.status === "paused",
      30_000
    );
    const finalizedAtPause = pausedAfterZip.batches.find(
      (batch) => batch.status === "completed"
    );
    if (
      !finalizedAtPause ||
      !finalizedAtPause.catalogPath ||
      !finalizedAtPause.zipPath ||
      !fs.existsSync(finalizedAtPause.catalogPath) ||
      !fs.existsSync(finalizedAtPause.zipPath) ||
      pausedAfterZip.task.checkpoint.currentAction !== "after-batch"
    ) {
      throw new Error(
        "ZIP pause occurred before download, CRC validation, and batch commit completed"
      );
    }
    const finalizedReport = JSON.parse(
      await fsp.readFile(finalizedAtPause.reportPath, "utf8")
    );
    if (!finalizedReport.crcOk || !finalizedReport.identityMappingOk) {
      throw new Error("Paused batch ZIP did not pass CRC and identity validation");
    }
    await new Promise((resolve) => setTimeout(resolve, 700));
    const batchPauseScreenshotPath = path.join(
      dataDirectory,
      "stage4-batch-paused.png"
    );
    await captureApplicationWindow(window, batchPauseScreenshotPath);

    await service.request("batch.stop", { taskId: pauseTaskId }, 15_000);
    const stoppedPauseTask = await waitForBatchTask(
      service,
      pauseTaskId,
      (details) => details.task.status === "stopped",
      30_000
    );
    if (
      stoppedPauseTask.batches.filter((batch) => batch.status === "completed").length !==
        1 ||
      !stoppedPauseTask.task.checkpoint.lastCompletedBatch
    ) {
      throw new Error("Batch stop did not preserve the accepted batch checkpoint");
    }
    await service.request("batch.resume", { taskId: pauseTaskId }, 15_000);
    const resumedPauseTask = await waitForBatchTask(
      service,
      pauseTaskId,
      (details) => details.task.status === "completed",
      60_000
    );
    if (
      resumedPauseTask.batches.length !== 1 ||
      resumedPauseTask.batches.filter((batch) => batch.status === "completed").length !==
        1 ||
      resumedPauseTask.items.length !== 1
    ) {
      throw new Error("Batch resume duplicated or lost an accepted batch");
    }

    const rangeBatchTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "阶段 4 年份区间与双库组合验证",
      {
        queries: ["分页测试"],
        matchMode: "keyword",
        library: "both",
        yearSelection: { mode: "range", startYear: 2020, endYear: 2021 },
        yearMode: "range",
        yearStart: 2020,
        yearEnd: 2021,
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 3,
        catalogFormat: "xlsx",
        fullFormat: "docx"
      },
      path.join(dataDirectory, "batch-files", "control-year-range")
    );
    const rangeBatch = await waitForBatchTask(
      service,
      rangeBatchTaskId,
      (details) => details.task.status === "completed",
      120_000
    );
    const rangeBatchYears = rangeBatch.batches
      .flatMap((batch) => batch.selected.map((row) => String(row.year || "")))
      .filter(Boolean);
    const rangeBatchStats = Array.isArray(rangeBatch.task.checkpoint.yearStats)
      ? (rangeBatch.task.checkpoint.yearStats as Array<Record<string, unknown>>)
      : [];
    if (
      rangeBatch.items.length !== 4 ||
      rangeBatchYears.length !== 4 ||
      rangeBatchStats.find(
        (item) => item.library === "central" && item.year === "2020"
      )?.status !== "completed" ||
      rangeBatchStats.find(
        (item) => item.library === "central" && item.year === "2021"
      )?.status !== "completed" ||
      rangeBatchStats.find(
        (item) => item.library === "local" && item.year === "2020"
      )?.status !== "no_result"
    ) {
      throw new Error(
        `批量年份区间或双库统计不正确：${JSON.stringify({
          years: rangeBatchYears,
          stats: rangeBatchStats
        })}`
      );
    }

    const multiYearPageLimitTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "任务 8 跨年度独立分页上限",
      {
        queries: ["测试"],
        matchMode: "keyword",
        library: "central",
        yearSelection: { mode: "range", startYear: 2020, endYear: 2021 },
        yearMode: "range",
        yearStart: 2020,
        yearEnd: 2021,
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        pageSelection: { mode: "limit", maxPages: 1 },
        pageMode: "limit",
        maxPages: 1,
        catalogFormat: "xlsx",
        fullFormat: "docx"
      },
      path.join(dataDirectory, "batch-files", "task8-page-limit")
    );
    const multiYearPageLimitWaiting = await waitForBatchTask(
      service,
      multiYearPageLimitTaskId,
      (details) =>
        details.task.status === "waiting_manual" &&
        details.task.error?.code === "PAGE_LIMIT_REACHED",
      45_000
    );
    if (
      multiYearPageLimitWaiting.task.checkpoint.currentYear !== "2020" ||
      Number(multiYearPageLimitWaiting.task.checkpoint.scannedPages || 0) !== 1 ||
      Number(multiYearPageLimitWaiting.task.checkpoint.totalPages || 0) !== 2 ||
      Number(multiYearPageLimitWaiting.task.checkpoint.unscannedPages || 0) !== 1
    ) {
      throw new Error(
        `批量分页上限断点不正确：${JSON.stringify(
          multiYearPageLimitWaiting.task.checkpoint
        )}`
      );
    }
    await service.request("batch.continue-to-last", {
      taskId: multiYearPageLimitTaskId
    });
    const multiYearPageLimitResult = await waitForBatchTask(
      service,
      multiYearPageLimitTaskId,
      (details) => details.task.status === "completed",
      240_000
    );
    const multiYearCombinationTotals = Array.isArray(
      multiYearPageLimitResult.task.checkpoint.combinationTotals
    )
      ? (multiYearPageLimitResult.task.checkpoint.combinationTotals as Array<
          Record<string, unknown>
        >)
      : [];
    if (
      multiYearCombinationTotals.length !== 2 ||
      multiYearCombinationTotals.some(
        (item) => Number(item.totalPages || 0) !== 2
      ) ||
      multiYearPageLimitResult.task.checkpoint.pageMode !== "last"
    ) {
      throw new Error(
        `跨年度组合未独立记录总页数：${JSON.stringify(
          multiYearCombinationTotals
        )}`
      );
    }

    const scaleTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "任务 8 批量规模二次确认",
      {
        queries: ["规模确认"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 50,
        pageSelection: { mode: "last" },
        pageMode: "last",
        catalogFormat: "xlsx",
        fullFormat: "docx"
      },
      path.join(dataDirectory, "batch-files", "task8-scale-confirmation")
    );
    const scaleWaiting = await waitForBatchTask(
      service,
      scaleTaskId,
      (details) =>
        details.task.status === "waiting_manual" &&
        details.task.error?.code === "BATCH_SCALE_CONFIRMATION_REQUIRED",
      45_000
    );
    const projectedScale = scaleWaiting.task.checkpoint.scaleEstimate as
      | Record<string, unknown>
      | undefined;
    if (
      projectedScale?.confidence !== "projected" ||
      Number(projectedScale?.estimatedPages || 0) !== 11 ||
      Number(projectedScale?.estimatedRows || 0) < 500 ||
      !projectedScale?.requiresConfirmation ||
      scaleWaiting.batches.length !== 0
    ) {
      throw new Error(
        `批量规模投影或确认门禁不正确：${JSON.stringify(projectedScale)}`
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
    const scaleScreenshotPath = path.join(
      dataDirectory,
      "task8-scale-confirmation.png"
    );
    await captureApplicationWindow(window, scaleScreenshotPath);
    await service.request("batch.confirm-scale", { taskId: scaleTaskId });
    const scaleConfirmedResult = await waitForBatchTask(
      service,
      scaleTaskId,
      (details) => details.task.status === "completed",
      900_000
    );
    const actualScale = scaleConfirmedResult.task.checkpoint.scaleEstimate as
      | Record<string, unknown>
      | undefined;
    const actualScaleRows = Number(actualScale?.estimatedRows || 0);
    const actualScaleBatches = Number(actualScale?.estimatedBatches || 0);
    if (
      scaleConfirmedResult.task.checkpoint.scaleConfirmed !== true ||
      actualScale?.confidence !== "actual" ||
      actualScale?.stage !== "after_scan_complete" ||
      Number(actualScale?.estimatedPages || 0) !== 11 ||
      actualScaleRows < 500 ||
      actualScaleBatches !== Math.ceil(actualScaleRows / 50)
    ) {
      throw new Error(
        `批量规模确认后实际统计不正确：${JSON.stringify(actualScale)}`
      );
    }

    const taskRuntimeUi = await runTaskRuntimeUiSmoke(window, {
      taskName: "阶段 4 分批与失败隔离验证",
      screenshotName: "stageI-batch-download-intent.png",
      expectDownloadKind: "Word ZIP"
    });
    await new Promise((resolve) => setTimeout(resolve, 900));
    const screenshotPath =
      process.env.PKULAW_SMOKE_SCREENSHOT || path.join(dataDirectory, "smoke.png");
    await captureApplicationWindow(window, screenshotPath);
    await finishSmoke(true, {
      backendState: service.getState(),
      browserState: browserHost.getState(),
      batchTaskId,
      batchCount: completed.batches.length,
      batchSizes: completed.batches.map((batch) => batch.selected.length),
      batchSearchIdentity,
      loginBatchProcessingSearchCount,
      loginBatchReuseCount,
      catalogIntentId,
      zipIntentId,
      retrievalContextCount,
      batchIdentityVerification,
      completedBatchProcessingSearchCount,
      completedBatchReuseCount,
      batchSearchSessionCheckpointValidated: true,
      failureIsolatedBeforeRetry: true,
      completedBatchesBeforeRetry: completedBeforeRetry.length,
      failedBatchesBeforeRetry: failedBeforeRetry.length,
      retryCompletedAll: true,
      xlsxValidated: true,
      wordZipCrcValidated: true,
      zipIdentityMappingValidated: true,
      htmlXlsTaskId: htmlTaskId,
      htmlXlsValidated: true,
      batchAutomaticLoginPauseObserved: true,
      batchBrowserLeaseObserved: batchLease,
      differentBatchTaskRejected: batchLeaseBusyCode,
      batchAutomaticCaptchaPauseObserved: true,
      batchManualPauseObserved: true,
      batchCatalogBoundaryValidated: true,
      batchZipBoundaryValidated: true,
      batchPauseStayedIdle: true,
      batchPauseRequestsIdempotent: true,
      batchStopResumeRecovered: true,
      batchResumeDidNotDuplicate: true,
      batchYearRangeId: rangeBatchTaskId,
      batchYearRangeValidated: true,
      batchYearRangeDedupedItems: rangeBatch.items.length,
      multiYearPageLimitValidated: true,
      multiYearCombinationTotals,
      scaleConfirmationValidated: true,
      scaleProjection: projectedScale,
      scaleActual: actualScale,
      scaleScreenshotPath,
      batchPauseScreenshotPath,
      taskRuntimeUi,
      screenshotPath
    });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    await finishSmoke(false, { error: message });
  }
}

async function waitForDataProcessing(
  service: PythonService,
  processingId: string,
  predicate: (details: DataProcessingDetails) => boolean,
  timeoutMs = 180_000,
  intervalMs = 300
): Promise<DataProcessingDetails> {
  const deadline = Date.now() + timeoutMs;
  let last: DataProcessingDetails | null = null;
  while (Date.now() < deadline) {
    const details = (await service.request(
      "data.details",
      { processingId }
    )) as unknown as DataProcessingDetails;
    last = details;
    if (predicate(details)) return details;
    if (["failed", "stopped"].includes(details.run.status)) {
      throw new Error(
        `Data processing stopped: ${details.run.error?.message || details.run.status}`
      );
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Data processing timed out: ${JSON.stringify(last?.run || null)}`);
}

async function snapshotFileTree(root: string): Promise<string[]> {
  if (!fs.existsSync(root)) return [];
  const snapshot: string[] = [];
  const visit = async (directory: string, relative: string): Promise<void> => {
    const entries = await fsp.readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      const childRelative = path.join(relative, entry.name);
      const childPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(childPath, childRelative);
      } else if (entry.isFile()) {
        const stats = await fsp.stat(childPath);
        snapshot.push(`${childRelative}:${stats.size}`);
      }
    }
  };
  await visit(root, "");
  return snapshot.sort();
}

async function sha256File(filePath: string): Promise<string> {
  const hash = createHash("sha256");
  for await (const chunk of fs.createReadStream(filePath)) {
    hash.update(chunk as Buffer);
  }
  return hash.digest("hex");
}

async function runStage5Smoke(window: BrowserWindow, service: PythonService): Promise<void> {
  try {
    await waitForRenderer(window);
    if (!browserHost || !fixtureProfile) {
      throw new Error("Stage 5 smoke requires fixture browser mode");
    }
    await browserHost.selectInstitution(fixtureProfile.id);
    const browser = await browserHost.fixtureAction("login");
    if (!browser.authenticated) {
      throw new Error("Fixture login failed before data governance");
    }
    const dataDirectory = resolveDataDirectory();
    const singleTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 5 来源治理验证",
      {
        queries: [
          "阶段三测试政策",
          "正文提取异常政策",
          "政府官网补全政策",
          "政府官网正文整理政策",
          "政府官网来源异常政策"
        ],
        matchMode: "title",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 20,
        maxPages: 3,
        governmentSearchTemplate: "/government/search?title={title}"
      }
    );
    const sourceTask = await waitForTask(
      service,
      singleTaskId,
      (details) => details.task.status === "partial",
      60_000
    );
    if (
      !sourceTask.items.some(
        (item) => item.error?.code === "NO_EXACT_MATCH"
      ) ||
      !sourceTask.items.some(
        (item) => item.error?.code === "INVALID_WORD_FILE"
      )
    ) {
      throw new Error("Stage 5 source fixtures did not create missing and invalid items");
    }

    const batchTaskId = await createAndStartBatchTask(
      service,
      dataDirectory,
      "阶段 5 批量正文来源验证",
      {
        queries: ["批量测试政策01"],
        matchMode: "title",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 1,
        catalogFormat: "xlsx",
        fullFormat: "docx"
      }
    );
    await waitForBatchTask(
      service,
      batchTaskId,
      (details) => details.task.status === "completed"
    );

    browserHost.setBounds({
      x: 40,
      y: 64,
      width: 1060,
      height: 760,
      visible: true
    });
    const started = (await service.request("data.start", {
      taskId: singleTaskId,
      options: {
        governmentFallback: true,
        governmentSearchTemplate: "/government/search?title={title}"
      }
    })) as unknown as DataProcessingDetails;
    const singleData = await waitForDataProcessing(
      service,
      started.run.id,
      (details) => details.run.status === "partial"
    );
    if (singleData.documents.length !== 4) {
      throw new Error(`Expected 4 governed documents, received ${singleData.documents.length}`);
    }
    const processed = singleData.documents.filter(
      (document) => document.status === "processed"
    );
    const pending = singleData.documents.filter(
      (document) => document.status === "pending_review"
    );
    if (processed.length !== 3 || pending.length !== 1) {
      throw new Error(
        `Unexpected governance counts: processed=${processed.length}, pending=${pending.length}`
      );
    }
    const sourceTypes = new Set(processed.map((document) => document.sourceType));
    for (const expected of ["北大法宝原件", "政府官网原生附件", "政府官网正文整理"]) {
      if (!sourceTypes.has(expected as never)) {
        throw new Error(`Missing source type: ${expected}`);
      }
    }
    for (const document of processed) {
      if (
        !document.wordPath ||
        !document.txtPath ||
        !document.evidencePath ||
        !fs.existsSync(document.wordPath) ||
        !fs.existsSync(document.txtPath) ||
        !fs.existsSync(document.evidencePath)
      ) {
        throw new Error(`Processed document output is incomplete: ${document.title}`);
      }
      const txt = await fsp.readFile(document.txtPath, "utf8");
      if (txt.trim().length < 20) {
        throw new Error(`TXT is too short: ${document.title}`);
      }
      const wordSha256 = await sha256File(document.wordPath);
      const txtSha256 = await sha256File(document.txtPath);
      if (wordSha256 !== document.sha256) {
        throw new Error(`Word SHA-256 mismatch: ${document.title}`);
      }
      const expectedTxtSha = String(document.payload.txtSha256 || "");
      if (expectedTxtSha && txtSha256 !== expectedTxtSha) {
        throw new Error(`TXT SHA-256 mismatch: ${document.title}`);
      }
      const evidence = JSON.parse(
        await fsp.readFile(document.evidencePath, "utf8")
      ) as Record<string, unknown>;
      if (
        evidence.sourceType !== document.sourceType ||
        evidence.wordSha256 !== wordSha256 ||
        (expectedTxtSha && evidence.txtSha256 !== txtSha256)
      ) {
        throw new Error(`Evidence SHA/source mismatch: ${document.title}`);
      }
      if (document.sourceType.startsWith("政府官网")) {
        if (!evidence.htmlPath || !fs.existsSync(String(evidence.htmlPath))) {
          throw new Error(`Official HTML evidence missing: ${document.title}`);
        }
        if (
          !evidence.screenshotPath ||
          !fs.existsSync(String(evidence.screenshotPath))
        ) {
          throw new Error(`Official screenshot evidence missing: ${document.title}`);
        }
        if (
          (await sha256File(String(evidence.htmlPath))) !==
          evidence.htmlSha256 ||
          (await sha256File(String(evidence.screenshotPath))) !==
          evidence.screenshotSha256
        ) {
          throw new Error(`Official evidence SHA-256 mismatch: ${document.title}`);
        }
      }
    }
    if (singleData.artifacts.length !== 3) {
      throw new Error("Master CSV, XLSX, or report artifact is missing");
    }
    for (const artifact of singleData.artifacts) {
      if (!fs.existsSync(artifact.path) || !artifact.sha256) {
        throw new Error(`Artifact is missing or unhashed: ${artifact.kind}`);
      }
      if ((await sha256File(artifact.path)) !== artifact.sha256) {
        throw new Error(`Artifact SHA-256 mismatch: ${artifact.kind}`);
      }
    }
    browserHost.setBounds({ x: 0, y: 0, width: 1, height: 1, visible: false });

    const batchStarted = (await service.request("data.start", {
      taskId: batchTaskId,
      options: { governmentFallback: false }
    })) as unknown as DataProcessingDetails;
    const batchData = await waitForDataProcessing(
      service,
      batchStarted.run.id,
      (details) => details.run.status === "completed"
    );
    if (
      batchData.documents.length !== 1 ||
      batchData.documents[0].status !== "processed" ||
      batchData.documents[0].sourceType !== "北大法宝ZIP" ||
      batchData.documents[0].textSourceType !== "word-text" ||
      batchData.documents[0].extractionMethod !== "python-docx" ||
      batchData.documents[0].converterName !== "python-docx" ||
      !batchData.documents[0].converterVersion ||
      batchData.documents[0].bodyCompleteness !== "complete" ||
      batchData.documents[0].reviewStatus !== "not_required"
    ) {
      throw new Error("Batch ZIP source was not transformed into a governed document");
    }
    const batchText = await fsp.readFile(batchData.documents[0].txtPath, "utf8");
    if (!batchText.includes("批量测试政策第 1 条正文")) {
      throw new Error("Batch TXT does not contain the expected policy body");
    }
    const restoredBrowser = await browserHost.navigate("home");
    if (!restoredBrowser.authenticated) {
      throw new Error("Browser session was not restored after data processing");
    }

    const pauseSourceTaskId = await createAndStartSingleTask(
      service,
      dataDirectory,
      "阶段 5 数据处理暂停安全边界验证",
      {
        queries: ["分页测试"],
        matchMode: "keyword",
        library: "central",
        year: "全部年份",
        province: "全部省份",
        timeliness: "全部（仅记录元数据，不筛选）",
        pageSize: 10,
        maxPages: 3
      }
    );
    const pauseSourceTask = await waitForTask(
      service,
      pauseSourceTaskId,
      (details) => details.task.status === "completed",
      60_000
    );
    if (pauseSourceTask.items.length !== 12) {
      throw new Error(
        `Expected 12 source items for processing pause, received ${pauseSourceTask.items.length}`
      );
    }

    const processingPauseStarted = (await service.request(
      "data.start",
      {
        taskId: pauseSourceTaskId,
        options: { governmentFallback: false }
      }
    )) as unknown as DataProcessingDetails;
    await waitForDataProcessing(
      service,
      processingPauseStarted.run.id,
      (details) =>
        details.run.status === "running" &&
        Number(details.run.summary.completedDocuments || 0) >= 1,
      60_000,
      50
    );
    const processingPauseRequest = (await service.request(
      "data.pause",
      { processingId: processingPauseStarted.run.id },
      15_000
    )) as unknown as DataProcessingDetails;
    if (
      !["pausing", "paused"].includes(processingPauseRequest.run.status)
    ) {
      throw new Error(
        `Data processing pause was not accepted: ${processingPauseRequest.run.status}`
      );
    }
    const processingPaused = await waitForDataProcessing(
      service,
      processingPauseStarted.run.id,
      (details) => details.run.status === "paused",
      30_000,
      50
    );
    const pausedCompletedDocuments = Number(
      processingPaused.run.summary.completedDocuments || 0
    );
    if (
      pausedCompletedDocuments < 1 ||
      pausedCompletedDocuments !== processingPaused.documents.length ||
      processingPaused.documents.length < 1
    ) {
      throw new Error(
        `Data processing paused without a complete policy boundary: ${JSON.stringify(
          processingPaused.run
        )}`
      );
    }
    const processedRoot = path.join(dataDirectory, "single-word", "processed");
    const pausedFileSnapshot = await snapshotFileTree(processedRoot);
    const duplicateProcessingPause = (await service.request(
      "data.pause",
      { processingId: processingPauseStarted.run.id }
    )) as unknown as DataProcessingDetails;
    if (
      duplicateProcessingPause.run.status !== "paused" ||
      duplicateProcessingPause.run.updatedAt !== processingPaused.run.updatedAt
    ) {
      throw new Error("Repeated data-processing pause was not idempotent");
    }
    await new Promise((resolve) => setTimeout(resolve, 900));
    const stillPausedProcessing = (await service.request(
      "data.details",
      { processingId: processingPauseStarted.run.id }
    )) as unknown as DataProcessingDetails;
    const stillPausedFileSnapshot = await snapshotFileTree(processedRoot);
    if (
      stillPausedProcessing.run.status !== "paused" ||
      stillPausedProcessing.run.updatedAt !== processingPaused.run.updatedAt ||
      processingPaused.documents.length !== stillPausedProcessing.documents.length ||
      pausedFileSnapshot.join("|") !== stillPausedFileSnapshot.join("|")
    ) {
      throw new Error("Paused data processing wrote new files before resume");
    }
    await new Promise((resolve) => setTimeout(resolve, 700));
    const dataPauseScreenshotPath = path.join(
      dataDirectory,
      "stage5-data-paused.png"
    );
    await captureApplicationWindow(window, dataPauseScreenshotPath);

    await service.request(
      "data.resume",
      { processingId: processingPauseStarted.run.id },
      15_000
    );
    const processingCompleted = await waitForDataProcessing(
      service,
      processingPauseStarted.run.id,
      (details) => details.run.status === "completed",
      120_000,
      100
    );
    if (
      processingCompleted.documents.length !== 12 ||
      processingCompleted.documents.some(
        (document) => document.status !== "processed"
      )
    ) {
      throw new Error("Data processing did not resume from the policy checkpoint");
    }

    const processingStopStarted = (await service.request(
      "data.start",
      {
        taskId: pauseSourceTaskId,
        options: { governmentFallback: false }
      }
    )) as unknown as DataProcessingDetails;
    await waitForDataProcessing(
      service,
      processingStopStarted.run.id,
      (details) =>
        details.run.status === "running" &&
        Number(details.run.summary.completedDocuments || 0) >= 1,
      60_000,
      50
    );
    await service.request(
      "data.stop",
      { processingId: processingStopStarted.run.id },
      15_000
    );
    const processingStopped = await waitForDataProcessing(
      service,
      processingStopStarted.run.id,
      (details) => details.run.status === "stopped",
      30_000,
      50
    );
    const stoppedCompletedDocuments = Number(
      processingStopped.run.summary.completedDocuments || 0
    );
    if (
      stoppedCompletedDocuments < 1 ||
      processingStopped.documents.length !== stoppedCompletedDocuments ||
      processingStopped.documents.some(
        (document) =>
          document.status === "processed" &&
          (!document.wordPath ||
            !document.txtPath ||
            !document.evidencePath ||
            !fs.existsSync(document.wordPath) ||
            !fs.existsSync(document.txtPath) ||
            !fs.existsSync(document.evidencePath))
      ) ||
      (await snapshotFileTree(processedRoot)).some((pathValue) =>
        pathValue.endsWith(".tmp")
      )
    ) {
      throw new Error("Data processing stop did not preserve a complete checkpoint");
    }

    const dataRuntimeDeadline = Date.now() + 15_000;
    let dataRuntimeUi: Record<string, unknown> = {};
    while (Date.now() < dataRuntimeDeadline) {
      dataRuntimeUi = (await window.webContents.executeJavaScript(
        `(() => {
          const filter = document.querySelector('select[aria-label="SHA-256 状态筛选"]');
          return {
            panel: document.querySelector('[data-testid="data-runtime-panel"]')?.textContent || '',
            hashOptions: filter
              ? [...filter.options].map(option => option.value)
              : [],
            hashRows: document.querySelectorAll('.hash-state').length,
            artifactRows: document.querySelectorAll('.artifact-record').length
          };
        })()`
      )) as Record<string, unknown>;
      if (
        String(dataRuntimeUi.panel) &&
        Number(dataRuntimeUi.hashRows || 0) > 0
      ) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    const dataRuntimeScreenshotPath = path.join(
      dataDirectory,
      "stageI-data-hash-audit.png"
    );
    if (
      !String(dataRuntimeUi.panel) ||
      !Array.isArray(dataRuntimeUi.hashOptions) ||
      !dataRuntimeUi.hashOptions.includes("mismatch") ||
      Number(dataRuntimeUi.hashRows || 0) < 1
    ) {
      throw new Error(
        `阶段 I 数据处理与 SHA-256 界面未完整渲染：${JSON.stringify(
          dataRuntimeUi
        )}`
      );
    }
    await captureApplicationWindow(window, dataRuntimeScreenshotPath);

    await new Promise((resolve) => setTimeout(resolve, 900));
    const screenshotPath =
      process.env.PKULAW_SMOKE_SCREENSHOT || path.join(dataDirectory, "smoke.png");
    await captureApplicationWindow(window, screenshotPath);
    await finishSmoke(true, {
      backendState: service.getState(),
      browserState: browserHost.getState(),
      singleSourceTaskId: singleTaskId,
      batchSourceTaskId: batchTaskId,
      singleProcessingId: started.run.id,
      governedDocuments: singleData.documents.length,
      processedDocuments: processed.length,
      pendingDocuments: pending.length,
      sourceTypes: [...sourceTypes],
      originalWordValidated: true,
      txtValidated: true,
      officialHtmlEvidenceValidated: true,
      officialScreenshotEvidenceValidated: true,
      masterCsvXlsxReportValidated: true,
      sha256ConsistencyValidated: true,
      batchProcessingId: batchStarted.run.id,
      batchTxtValidated: true,
      processingPauseRunId: processingPauseStarted.run.id,
      dataManualPauseObserved: true,
      dataPauseOnlyAtPolicyBoundary: true,
      dataPauseStayedIdle: true,
      dataPauseRequestsIdempotent: true,
      dataResumeContinuedFromCheckpoint: true,
      dataProcessedDocumentsAfterResume: processingCompleted.documents.length,
      dataStopRunId: processingStopStarted.run.id,
      dataStopPreservedCheckpoint: true,
      dataStopCompletedDocuments: stoppedCompletedDocuments,
      dataPauseScreenshotPath,
      dataRuntimeUi,
      dataRuntimeScreenshotPath,
      screenshotPath
    });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    await finishSmoke(false, { error: message });
  }
}

async function waitForBackendReady(
  service: PythonService,
  timeoutMs = 30_000,
  previousPid: number | null = null
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = service.getState();
    if (
      state.status === "ready" &&
      (!previousPid || (state.pid && state.pid !== previousPid))
    ) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`Backend did not recover within ${timeoutMs} ms`);
}

async function runStage6Smoke(
  window: BrowserWindow,
  service: PythonService,
  appInfo: AppInfo
): Promise<void> {
  try {
    await waitForRenderer(window);
    if (!browserHost || !fixtureProfile) {
      throw new Error("Stage 6 smoke requires fixture browser mode");
    }
    await browserHost.selectInstitution(fixtureProfile.id);
    await browserHost.fixtureAction("login");
    const dataDirectory = resolveDataDirectory();

    const longPathRoot = path.join(
      dataDirectory,
      "中文 空格 长路径",
      "政策 采集".repeat(8),
      "processed"
    );
    const longPathFile = await ensureWritableDirectory(longPathRoot).then(
      async (resolved) => {
        const target = path.join(resolved, "长路径写入测试.txt");
        await fsp.writeFile(target, "STAGE6_LONG_PATH_OK", "utf8");
        return target;
      }
    );
    if (!fs.existsSync(longPathFile)) {
      throw new Error("Long, Chinese and spaced path write failed");
    }

    const occupiedFile = path.join(dataDirectory, "not-a-directory");
    await fsp.writeFile(occupiedFile, "occupied", "utf8");
    let rejectedFileAsDirectory = false;
    try {
      await ensureWritableDirectory(occupiedFile);
    } catch {
      rejectedFileAsDirectory = true;
    }
    if (!rejectedFileAsDirectory) {
      throw new Error("Invalid output path was not rejected");
    }

    const diagnostics = await runDiagnostics(appInfo, service, browserHost);
    if (
      appInfo.version !== "0.2.6" ||
      service.getState().engineVersion !== "0.2.6"
    ) {
      throw new Error(
        `Application/backend version mismatch: ${appInfo.version}/${service.getState().engineVersion}`
      );
    }
    if (
      !diagnostics.checks.some(
        (check) => check.id === "sqlite-integrity" && check.status === "ok"
      )
    ) {
      throw new Error("SQLite integrity diagnostic did not pass");
    }
    if (Number(diagnostics.database.schemaVersion) !== 6) {
      throw new Error(
        `Expected SQLite schema v6, received ${String(
          diagnostics.database.schemaVersion
        )}`
      );
    }
    if (
      !diagnostics.dependencies.DrissionPage ||
      diagnostics.desktop.network.status !== "ok"
    ) {
      throw new Error("Runtime dependencies or fixture network were not detected");
    }
    for (const checkId of [
      "desktop-data-directory",
      "desktop-logs-directory",
      "desktop-download-directory",
      "desktop-system-temp"
    ]) {
      if (
        !diagnostics.checks.some(
          (check) => check.id === checkId && check.status === "ok"
        )
      ) {
        throw new Error(`Stage I directory diagnostic did not pass: ${checkId}`);
      }
    }
    if (!diagnostics.paths.downloadDirectory) {
      throw new Error("Stage I download staging directory was not reported");
    }
    const settingsDeadline = Date.now() + 12_000;
    let settingsRuntimeUi: Record<string, unknown> = {};
    while (Date.now() < settingsDeadline) {
      settingsRuntimeUi = (await window.webContents.executeJavaScript(
        `(() => ({
          runtime: document.querySelector('[data-testid="diagnostics-runtime"]')?.textContent || '',
          paths: document.querySelector('[data-testid="diagnostics-paths"]')?.textContent || '',
          browser: document.querySelector('[data-testid="diagnostics-browser"]')?.textContent || '',
          checks: document.querySelectorAll('.diagnostic-check').length
        }))()`
      )) as Record<string, unknown>;
      if (
        String(settingsRuntimeUi.runtime).includes("协议版本") &&
        String(settingsRuntimeUi.paths).includes("下载临时目录") &&
        String(settingsRuntimeUi.browser).includes("Python 绑定")
      ) {
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (
      !String(settingsRuntimeUi.runtime).includes("完整性") ||
      !String(settingsRuntimeUi.paths).includes("下载临时目录") ||
      !String(settingsRuntimeUi.browser).includes("Session") ||
      Number(settingsRuntimeUi.checks || 0) < 10
    ) {
      throw new Error(
        `阶段 I 设置诊断界面信息不完整：${JSON.stringify(settingsRuntimeUi)}`
      );
    }
    const created = (await service.request("task.create", {
      name: "中文管道验证",
      mode: "single",
      input: { matchMode: "title", note: "空格 与 中文" },
      outputDir: dataDirectory
    })) as { task?: TaskRecord };
    const stored = (await service.request("task.get", {
      taskId: created.task?.id
    })) as { task?: TaskRecord };
    if (
      stored.task?.name !== "中文管道验证" ||
      stored.task?.input?.note !== "空格 与 中文"
    ) {
      throw new Error("Chinese text did not survive the Electron/Python protocol");
    }
    const pathEntrypointsOpened = Boolean(
      await window.webContents.executeJavaScript(
        `Promise.all([
          window.pkulaw.openDataDirectory(),
          window.pkulaw.openLogsDirectory()
        ]).then(() => true)`
      )
    );
    if (!pathEntrypointsOpened) {
      throw new Error("Data and log directory entry points failed");
    }
    const copyProbe = "阶段 I 长文本复制验证";
    const copySucceeded = Boolean(
      await window.webContents.executeJavaScript(
        `window.pkulaw.copyText(${JSON.stringify(copyProbe)}).then(() => true)`
      )
    );
    if (!copySucceeded || (await clipboard.readText()) !== copyProbe) {
      throw new Error("Stage I copy-text entry point failed");
    }

    const unifiedWorkspace = await runUnifiedWorkspaceSmoke(
      window,
      service,
      dataDirectory
    );

    await new Promise((resolve) => setTimeout(resolve, 1_400));
    const screenshotPath =
      process.env.PKULAW_SMOKE_SCREENSHOT || path.join(dataDirectory, "smoke.png");
    await captureApplicationWindow(window, screenshotPath);
    const settingsNavigationClicked = Boolean(
      await window.webContents.executeJavaScript(
        `(() => {
          const button = [...document.querySelectorAll('.nav-item')]
            .find(item => (item.textContent || '').includes('设置与诊断'));
          if (!button) return false;
          button.click();
          return true;
        })()`
      )
    );
    const settingsPageDeadline = Date.now() + 8_000;
    let settingsPageReady = false;
    while (Date.now() < settingsPageDeadline) {
      settingsPageReady = Boolean(
        await window.webContents.executeJavaScript(
          "Boolean(document.querySelector('.settings-grid'))"
        )
      );
      if (settingsPageReady) break;
      await new Promise((resolve) => setTimeout(resolve, 200));
    }
    if (!settingsNavigationClicked || !settingsPageReady) {
      throw new Error("阶段 I 设置诊断页面未渲染");
    }
    setLogicalContentSize(window, 880, 760);
    await new Promise((resolve) => setTimeout(resolve, 700));
    const narrowLayoutUi = (await window.webContents.executeJavaScript(
      `(() => {
        const sidebar = document.querySelector('.sidebar')?.getBoundingClientRect();
        const grid = document.querySelector('.settings-grid');
        const buttons = [...document.querySelectorAll('button')].map(button =>
          button.getBoundingClientRect()
        );
        return {
          innerWidth: window.innerWidth,
          sidebarWidth: sidebar?.width || 0,
          gridColumns: grid ? getComputedStyle(grid).gridTemplateColumns : '',
          scrollWidth: document.documentElement.scrollWidth,
          buttonsInside:
            buttons.length > 0 &&
            buttons.every(rect => rect.left >= -1 && rect.right <= window.innerWidth + 1)
        };
      })()`
    )) as Record<string, unknown>;
    const narrowScreenshotPath = path.join(
      dataDirectory,
      "stageI-narrow-highdpi.png"
    );
    await captureApplicationWindow(window, narrowScreenshotPath);
    if (
      Number(narrowLayoutUi.innerWidth || 0) > 900 ||
      Number(narrowLayoutUi.sidebarWidth || 0) > 90 ||
      Number(narrowLayoutUi.scrollWidth || 0) >
        Number(narrowLayoutUi.innerWidth || 0) + 1 ||
      narrowLayoutUi.buttonsInside !== true ||
      !String(narrowLayoutUi.gridColumns).includes("px")
    ) {
      throw new Error(
        `阶段 I 窄窗口高 DPI 布局不通过：${JSON.stringify(narrowLayoutUi)}`
      );
    }
    const collectorNarrowLayouts: Record<string, Record<string, unknown>> = {};
    for (const collectorCase of [
      {
        navigation: "逐篇采集",
        rootClass: ".task-workspace-frame",
        screenshot: "stageG-single-narrow-highdpi.png"
      },
      {
        navigation: "批量采集",
        rootClass: ".task-workspace-frame",
        screenshot: "stageG-batch-narrow-highdpi.png"
      }
    ]) {
      const clicked = Boolean(
        await window.webContents.executeJavaScript(
          `(() => {
            const button = [...document.querySelectorAll('.nav-item')]
              .find(item => (item.textContent || '').includes(${JSON.stringify(
                collectorCase.navigation
              )}));
            if (!button) return false;
            button.click();
            return true;
          })()`
        )
      );
      if (!clicked) {
        throw new Error(
          `Collector navigation was unavailable: ${collectorCase.navigation}`
        );
      }
      const collectorDeadline = Date.now() + 8_000;
      let collectorLayout: Record<string, unknown> = {};
      while (Date.now() < collectorDeadline) {
        collectorLayout = (await window.webContents.executeJavaScript(
          `(() => {
            const root = document.querySelector(${JSON.stringify(
              collectorCase.rootClass
            )});
            const buttons = [...document.querySelectorAll('button')].map(button =>
              button.getBoundingClientRect()
            );
            return {
              innerWidth: window.innerWidth,
              scrollWidth: document.documentElement.scrollWidth,
              rootPresent: Boolean(root),
              workspacePresent: Boolean(
                document.querySelector('.task-workspace-frame')
              ),
              rootColumns: root ? getComputedStyle(root).gridTemplateColumns : "",
              buttonsInside:
                buttons.length > 0 &&
                buttons.every(rect =>
                  rect.left >= -1 && rect.right <= window.innerWidth + 1
                )
            };
          })()`
        )) as Record<string, unknown>;
        if (
          collectorLayout.rootPresent === true &&
          collectorLayout.workspacePresent === true
        ) {
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      const screenshot = path.join(dataDirectory, collectorCase.screenshot);
      await captureApplicationWindow(window, screenshot);
      collectorNarrowLayouts[collectorCase.navigation] = {
        ...collectorLayout,
        screenshot
      };
      if (
        collectorLayout.rootPresent !== true ||
        collectorLayout.workspacePresent !== true ||
        Number(collectorLayout.scrollWidth || 0) >
          Number(collectorLayout.innerWidth || 0) + 1
      ) {
        throw new Error(
          `Collector narrow layout failed (${collectorCase.navigation}): ${JSON.stringify(
            collectorLayout
          )}`
        );
      }
    }
    setLogicalContentSize(window, 1440, 920);
    await new Promise((resolve) => setTimeout(resolve, 500));
    const taskCenterClicked = Boolean(
      await window.webContents.executeJavaScript(
        `(() => {
          const button = [...document.querySelectorAll('.nav-item')]
            .find(item => (item.textContent || '').includes('任务中心'));
          if (!button) return false;
          button.click();
          return true;
        })()`
      )
    );
    if (!taskCenterClicked) {
      throw new Error("Task center navigation was not available");
    }
    const taskCenterDeadline = Date.now() + 8_000;
    let taskCenterRows = 0;
    while (Date.now() < taskCenterDeadline) {
      taskCenterRows = Number(
        await window.webContents.executeJavaScript(
          "document.querySelectorAll('.task-browser-row').length"
        )
      );
      if (taskCenterRows > 0) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (taskCenterRows < 1) {
      throw new Error("Task center did not render task rows");
    }
    const taskCenterScreenshotPath = path.join(dataDirectory, "stage6-task-center.png");
    await captureApplicationWindow(window, taskCenterScreenshotPath);

    const previousPid = service.pid;
    if (!previousPid) {
      throw new Error("Python backend PID was unavailable before crash recovery test");
    }
    process.kill(previousPid);
    await waitForBackendReady(service, 30_000, previousPid);
    const recoveredBrowser = await browserHost.reconnectCurrent();
    if (!recoveredBrowser.authenticated) {
      throw new Error("Browser session did not recover after Python crash");
    }

    await fixtureServer?.stop();
    const offlineDiagnostics = await runDiagnostics(appInfo, service, browserHost);
    const offlineNetworkDetected =
      offlineDiagnostics.desktop.network.status === "error";
    if (!offlineNetworkDetected) {
      throw new Error("Offline network condition was not reported by diagnostics");
    }

    await finishSmoke(true, {
      backendState: service.getState(),
      browserState: browserHost.getState(),
      packaged: appInfo.packaged,
      backendMode: appInfo.backendMode,
      diagnostics: {
        checks: diagnostics.checks.length,
        databaseIntegrity: diagnostics.database.integrity,
        schemaVersion: diagnostics.database.schemaVersion,
        dependencies: diagnostics.dependencies,
        scaleFactor: diagnostics.desktop.scaleFactor,
        networkStatus: diagnostics.desktop.network.status
      },
      settingsRuntimeUi,
      narrowLayoutUi,
      collectorNarrowLayouts,
      narrowScreenshotPath,
      longPathValidated: true,
      nonDirectoryOutputRejected: true,
      pathEntrypointsOpened,
      chineseProtocolRoundTrip: true,
      taskCenterRows,
      taskCenterScreenshotPath,
      pythonCrashRecovered: true,
      browserRecoveredAfterCrash: true,
      offlineNetworkDetected,
      unifiedWorkspace,
      screenshotPath
    });
  } catch (error) {
    const message = error instanceof Error ? error.stack || error.message : String(error);
    await finishSmoke(false, { error: message });
  }
}

function configureApplicationEvents(): void {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) {
        mainWindow.restore();
      }
      mainWindow.focus();
    }
  });

  app.on("window-all-closed", () => {
    app.quit();
  });

  app.on("before-quit", (event) => {
    if (allowQuit) {
      return;
    }
    event.preventDefault();
    if (backendRecoveryTimer) {
      clearTimeout(backendRecoveryTimer);
      backendRecoveryTimer = null;
    }
    if (shutdownStarted) {
      return;
    }
    shutdownStarted = true;
    void (async () => {
      try {
        await browserHost?.dispose();
        await fixtureServer?.stop();
        await pythonService?.stop();
      } finally {
        allowQuit = true;
        app.quit();
      }
    })();
  });
}

async function bootstrap(): Promise<void> {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return;
  }
  configureApplicationEvents();
  await app.whenReady();

  const dataDirectory = resolveDataDirectory();
  const backendLaunch = await resolveBackendLaunch();
  const engineDirectory = backendLaunch.engineDirectory;
  const appVersion = resolveAppVersion();
  const pythonPath = backendLaunch.pythonPath;
  debugPort = Number(process.env.PKULAW_DEBUG_PORT || 0);
  if (!Number.isInteger(debugPort) || debugPort <= 0 || debugPort > 65535) {
    throw new Error("PKULAW_DEBUG_PORT must be injected before the Electron process starts");
  }
  const allowPackagedFixture =
    !app.isPackaged ||
    process.env.PKULAW_ALLOW_PACKAGED_FIXTURE === "1";
  const stage2Fixture =
    allowPackagedFixture && process.env.PKULAW_STAGE2_FIXTURE === "1";
  const stage3Fixture =
    allowPackagedFixture && process.env.PKULAW_STAGE3_FIXTURE === "1";
  const stage4Fixture =
    allowPackagedFixture && process.env.PKULAW_STAGE4_FIXTURE === "1";
  const stage5Fixture =
    allowPackagedFixture && process.env.PKULAW_STAGE5_FIXTURE === "1";
  const stage6Fixture =
    allowPackagedFixture && process.env.PKULAW_STAGE6_FIXTURE === "1";
  const fixtureMode =
    stage2Fixture || stage3Fixture || stage4Fixture || stage5Fixture || stage6Fixture;
  const logsDirectory = path.join(dataDirectory, "logs");
  await fsp.mkdir(logsDirectory, { recursive: true });
  logger = new JsonLineLogger(path.join(logsDirectory, "desktop.jsonl"));
  logger.write("info", "app.starting", "Desktop application starting", {
    version: appVersion,
    dataDirectory,
    engineDirectory,
    pythonPath
  });

  const appInfo: AppInfo = {
    name: "法宝工作台",
    version: appVersion,
    dataDirectory,
    logsDirectory,
    resourcesPath: process.resourcesPath,
    defaultSingleOutputDir: path.join(dataDirectory, "single-word"),
    defaultBatchOutputDir: path.join(dataDirectory, "batch-files"),
    engineDirectory,
    pythonPath,
    backendMode: backendLaunch.backendExecutable ? "bundled" : "source",
    packaged: app.isPackaged,
    electronVersion: process.versions.electron,
    chromeVersion: process.versions.chrome,
    nodeVersion: process.versions.node,
    fixtureMode,
    stage3FixtureMode: stage3Fixture,
    stage4FixtureMode: stage4Fixture,
    stage5FixtureMode: stage5Fixture,
    stage6FixtureMode: stage6Fixture
  };

  pythonService = new PythonService({
    engineDirectory,
    pythonPath,
    backendExecutable: backendLaunch.backendExecutable,
    dataDirectory,
    appVersion,
    logger
  });
  institutionStore = new InstitutionStore(dataDirectory);

  if (fixtureMode) {
    fixtureServer = new FixtureServer();
    const fixtureUrl = await fixtureServer.start();
    fixtureProfile = await institutionStore.saveFixture(fixtureUrl);
    logger.write("info", "fixture.ready", "Local test fixture is ready", {
      url: fixtureUrl,
      institutionId: fixtureProfile.id
    });
  }

  pythonService.on("state", (state: BackendState) => {
    sendToRenderer("backend:state", state);
    if (state.status === "ready") {
      backendReadyObserved = true;
      backendRecoveryAttempts = 0;
      return;
    }
    if (
      state.status === "failed" &&
      backendReadyObserved &&
      !shutdownStarted &&
      backendRecoveryAttempts < 1 &&
      !backendRecoveryTimer
    ) {
      backendRecoveryAttempts += 1;
      backendRecoveryTimer = setTimeout(() => {
        backendRecoveryTimer = null;
        void (async () => {
          const service = pythonService;
          if (!service || shutdownStarted) return;
          logger?.write(
            "warn",
            "backend.auto_recovery",
            "Attempting one automatic Python backend recovery"
          );
          try {
            await service.stop();
            await service.start();
            if (browserHost?.getCurrentProfile()) {
              await browserHost.reconnectCurrent();
            }
          } catch (error) {
            logger?.write(
              "error",
              "backend.auto_recovery_failed",
              String(error)
            );
          }
        })();
      }, 1_500);
    }
  });
  pythonService.on("engineEvent", (engineEvent: EngineEvent) => {
    sendToRenderer("engine:event", engineEvent);
    if (
      engineEvent.event === "browser.lease-acquired" ||
      engineEvent.event === "browser.lease-released"
    ) {
      browserHost?.syncBrowserLease(engineEvent.data.lease);
    }
    if (
      [
        "data.completed",
        "data.stopped",
        "data.failed",
        "data.safety-stopped"
      ].includes(engineEvent.event)
    ) {
      browserHost?.hideDataProcessingOverlay();
    }
  });
  logger.on("record", (record: LogRecord) => {
    sendToRenderer("log:record", record);
  });

  const defaultSession = session.defaultSession;
  defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });

  mainWindow = createMainWindow();
  browserHost = new BrowserHost({
    window: mainWindow,
    pythonService,
    institutions: institutionStore,
    dataDirectory,
    debugPort,
    logger,
    fixtureMode
  });
  browserHost.on("state", (state: BrowserHostState) => {
    sendToRenderer("browser:state", state);
  });
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  registerIpcHandlers({
    pythonService,
    logger,
    appInfo,
    dataDirectory,
    browserHost,
    institutions: institutionStore
  });
  loadRenderer(mainWindow);

  const backendStart = startBackend(pythonService, appInfo);
  if (process.env.PKULAW_SMOKE === "1") {
    void backendStart.then(async () => {
      if (pythonService?.getState().status === "ready" && mainWindow) {
        if (process.env.PKULAW_SMOKE_MODE === "stage5") {
          await runStage5Smoke(mainWindow, pythonService);
        } else if (process.env.PKULAW_SMOKE_MODE === "stage6") {
          await runStage6Smoke(mainWindow, pythonService, appInfo);
        } else if (process.env.PKULAW_SMOKE_MODE === "stage4") {
          await runStage4Smoke(mainWindow, pythonService);
        } else if (process.env.PKULAW_SMOKE_MODE === "stage3") {
          await runStage3Smoke(mainWindow, pythonService);
        } else {
          await runStage2Smoke(mainWindow, pythonService);
        }
      }
    });
  }
}

ipcMain.on("app:quit", () => app.quit());

bootstrap().catch(async (error) => {
  const message = error instanceof Error ? error.stack || error.message : String(error);
  logger?.write("error", "app.bootstrap_failed", message);
  if (process.env.PKULAW_SMOKE === "1") {
    await finishSmoke(false, { error: message });
  } else {
    console.error(message);
    app.quit();
  }
});
