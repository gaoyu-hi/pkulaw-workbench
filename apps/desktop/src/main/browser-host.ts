import { BrowserWindow, session as electronSession, WebContentsView } from "electron";
import { EventEmitter } from "node:events";
import { randomUUID } from "node:crypto";
import path from "node:path";
import type {
  BrowserBounds,
  BrowserHostState,
  BrowserLeaseState,
  InstitutionProfile
} from "../shared/protocol";
import { DownloadManager, type DownloadEvent } from "./download-manager";
import type { InstitutionStore } from "./institution-store";
import type { JsonLineLogger } from "./logger";
import type { PythonService } from "./python-service";
import {
  controlledChildLoadOptions,
  shouldCloseSettledBatchChild,
  type ChildNavigationRequest
} from "./child-navigation";
import {
  browserZoomShortcut,
  DEFAULT_BROWSER_ZOOM_FACTOR,
  normalizeBrowserZoomFactor,
  reapplyBrowserZoom,
  zoomFactorForShortcut
} from "../shared/browser-zoom";

interface BrowserHostOptions {
  window: BrowserWindow;
  pythonService: PythonService;
  institutions: InstitutionStore;
  dataDirectory: string;
  debugPort: number;
  logger: JsonLineLogger;
  fixtureMode: boolean;
}

const EMPTY_LEASE: BrowserLeaseState = {
  available: true,
  leaseId: "",
  taskId: "",
  mode: "",
  createdAt: "",
  updatedAt: ""
};

const EMPTY_STATE: BrowserHostState = {
  status: "idle",
  institutionId: "",
  sessionKey: "",
  url: "",
  title: "",
  viewLoaded: false,
  targetBound: false,
  targetUrl: "",
  selectorProfile: "",
  selectorVersion: "",
  selectorReady: false,
  selectorMessage: "尚未进行选择器诊断",
  selectorDiagnostics: null,
  authenticated: false,
  loginVisible: false,
  captchaVisible: false,
  captchaSelector: "",
  captchaBox: {},
  captchaCheckedAt: "",
  zoomFactor: DEFAULT_BROWSER_ZOOM_FACTOR,
  canGoBack: false,
  childViews: 0,
  tabs: [],
  message: "尚未选择机构",
  lastDownload: null,
  browserLease: EMPTY_LEASE
};

function normalizeBrowserLease(value: unknown): BrowserLeaseState {
  if (!value || typeof value !== "object") {
    return { ...EMPTY_LEASE };
  }
  const lease = value as Record<string, unknown>;
  const rawMode = String(lease.mode || "");
  return {
    available:
      lease.available === undefined ? true : Boolean(lease.available),
    leaseId: String(lease.leaseId || ""),
    taskId: String(lease.taskId || ""),
    mode:
      rawMode === "single" || rawMode === "batch" || rawMode === "data"
        ? rawMode
        : "",
    createdAt: String(lease.createdAt || ""),
    updatedAt: String(lease.updatedAt || "")
  };
}

export class BrowserHost extends EventEmitter {
  private view: WebContentsView | null = null;
  private readonly childViews: WebContentsView[] = [];
  private readonly childOpenedUrls = new WeakMap<WebContentsView, string>();
  private readonly downloadManagers = new Map<string, DownloadManager>();
  private currentProfile: InstitutionProfile | null = null;
  private currentBounds: BrowserBounds = {
    x: 0,
    y: 0,
    width: 1,
    height: 1,
    visible: false
  };
  private state: BrowserHostState = { ...EMPTY_STATE };
  private recoveryAttempts = 0;
  private recovering = false;
  private fixtureStorageReset = false;
  private selectionInFlight: Promise<BrowserHostState> | null = null;
  private reconnectInFlight: Promise<BrowserHostState> | null = null;
  private connectedBackendPid: number | null = null;
  private viewId = "";
  private targetKey = "";
  private pendingInstitutionId = "";
  private zoomFactor = DEFAULT_BROWSER_ZOOM_FACTOR;

  constructor(private readonly options: BrowserHostOptions) {
    super();
    this.options.window.webContents.on(
      "before-input-event",
      (event, input) => {
        this.handleZoomShortcut(event, input);
      }
    );
  }

  getState(): BrowserHostState {
    return {
      ...this.state,
      tabs: [...this.state.tabs],
      lastDownload: this.state.lastDownload ? { ...this.state.lastDownload } : null,
      browserLease: { ...this.state.browserLease }
    };
  }

  getCurrentProfile(): InstitutionProfile | null {
    return this.currentProfile;
  }

  async selectInstitution(institutionId: string): Promise<BrowserHostState> {
    const profile = await this.options.institutions.get(institutionId);
    if (!profile) {
      throw new Error(`机构 ${institutionId} 不存在`);
    }
    if (this.selectionInFlight && this.pendingInstitutionId === institutionId) {
      return this.selectionInFlight;
    }
    if (this.selectionInFlight) {
      try {
        await this.selectionInFlight;
      } catch {
        // A new explicit selection supersedes a failed previous selection.
      }
    }
    if (
      this.currentProfile?.id === institutionId &&
      this.currentProfile.entryUrl === profile.entryUrl &&
      this.getUsableViewWebContents()
    ) {
      this.currentProfile = profile;
      if (this.state.targetBound) {
        await this.refreshFromEngine();
      } else {
        await this.reconnectCurrent();
      }
      return this.getState();
    }

    this.pendingInstitutionId = institutionId;
    const operation = this.performSelection(profile);
    this.selectionInFlight = operation;
    try {
      return await operation;
    } finally {
      if (this.selectionInFlight === operation) {
        this.selectionInFlight = null;
        this.pendingInstitutionId = "";
      }
    }
  }

  private async performSelection(profile: InstitutionProfile): Promise<BrowserHostState> {
    if (this.options.pythonService.getState().status !== "ready") {
      throw new Error("Python 后端尚未就绪，不能连接内置浏览器");
    }

    this.currentProfile = profile;
    this.recoveryAttempts = 0;
    await this.createView(profile);
    await this.connectPython(profile);
    await this.refreshFromEngine();
    return this.getState();
  }

  setBounds(bounds: BrowserBounds): void {
    this.currentBounds = {
      x: Math.max(0, Math.round(Number(bounds.x) || 0)),
      y: Math.max(0, Math.round(Number(bounds.y) || 0)),
      width: Math.max(1, Math.round(Number(bounds.width) || 1)),
      height: Math.max(1, Math.round(Number(bounds.height) || 1)),
      visible: Boolean(bounds.visible)
    };
    this.applyBounds();
  }

  showDataProcessingOverlay(): void {
    const { width, height } = this.options.window.getContentBounds();
    this.setBounds({
      x: 0,
      y: 0,
      width: Math.max(640, width),
      height: Math.max(480, height),
      visible: true
    });
  }

  hideDataProcessingOverlay(): void {
    this.setBounds({ x: 0, y: 0, width: 1, height: 1, visible: false });
  }

  async navigate(action: "back" | "reload" | "home"): Promise<BrowserHostState> {
    const webContents = this.getUsableViewWebContents();
    if (!webContents || !this.currentProfile) {
      throw new Error("内置浏览器尚未启动");
    }
    this.patchState({ status: "loading", message: "页面加载中" });
    if (action === "reload") {
      webContents.reload();
    } else if (action === "back" && webContents.navigationHistory.canGoBack()) {
      webContents.navigationHistory.goBack();
    } else {
      await webContents.loadURL(this.currentProfile.entryUrl);
    }
    await new Promise((resolve) => setTimeout(resolve, 450));
    await this.refreshFromEngine();
    return this.getState();
  }

  async setBrowserZoom(factor: number): Promise<number> {
    const normalized = normalizeBrowserZoomFactor(factor);
    const webContents = this.getUsableViewWebContents();
    if (!webContents || !this.currentProfile || !this.state.targetBound) {
      throw new Error("BROWSER_NOT_BOUND：仅可缩放已绑定的内置网页");
    }
    this.applyZoomToWebContents(webContents, normalized);
    for (const child of this.childViews) {
      if (!child.webContents.isDestroyed()) {
        this.applyZoomToWebContents(child.webContents, normalized);
      }
    }
    this.zoomFactor = normalized;
    this.patchState({ zoomFactor: normalized });
    const updatedProfile = await this.options.institutions.setBrowserZoom(
      this.currentProfile.id,
      normalized
    );
    this.currentProfile = updatedProfile;
    this.options.logger.write(
      "info",
      "browser.zoom_changed",
      "Browser view zoom changed",
      {
        institutionId: updatedProfile.id,
        sessionKey: updatedProfile.sessionKey,
        zoomFactor: normalized
      }
    );
    return normalized;
  }

  async userContinue(): Promise<BrowserHostState> {
    await this.options.pythonService.request("browser.user-continue");
    await this.refreshFromEngine();
    return this.getState();
  }

  syncBrowserLease(value: unknown): void {
    this.patchState({ browserLease: normalizeBrowserLease(value) });
  }

  async fixtureAction(
    action: "login" | "captcha" | "download" | "child"
  ): Promise<BrowserHostState> {
    const method = {
      login: "browser.fixture-login",
      captcha: "browser.fixture-captcha",
      download: "browser.fixture-download",
      child: "browser.fixture-child"
    }[action];
    await this.options.pythonService.request(method, {}, 10_000);
    if (action === "download") {
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    await this.refreshFromEngine();
    return this.getState();
  }

  async reconnectCurrent(): Promise<BrowserHostState> {
    if (!this.currentProfile) {
      return this.getState();
    }
    const backendPid = this.options.pythonService.pid;
    if (
      backendPid &&
      this.connectedBackendPid === backendPid &&
      this.state.targetBound &&
      this.getUsableViewWebContents() &&
      ["ready", "waiting_login", "waiting_manual"].includes(this.state.status)
    ) {
      return this.getState();
    }
    if (this.reconnectInFlight) {
      return this.reconnectInFlight;
    }
    const profile = this.currentProfile;
    const operation = (async () => {
      this.patchState({
        status: "loading",
        targetBound: false,
        message: "正在重新连接 Python 后端"
      });
      if (!this.getUsableViewWebContents()) {
        await this.createView(profile);
      }
      await this.connectPython(profile);
      await this.refreshFromEngine();
      return this.getState();
    })();
    this.reconnectInFlight = operation;
    try {
      return await operation;
    } finally {
      if (this.reconnectInFlight === operation) {
        this.reconnectInFlight = null;
      }
    }
  }

  async recreateCurrent(): Promise<BrowserHostState> {
    if (!this.currentProfile) {
      return this.getState();
    }
    await this.createView(this.currentProfile);
    await this.connectPython(this.currentProfile);
    await this.refreshFromEngine();
    return this.getState();
  }

  async exerciseRendererRecoveryForSmoke(): Promise<BrowserHostState> {
    const webContents = this.getUsableViewWebContents();
    if (!webContents || !this.currentProfile) {
      throw new Error("内置浏览器尚未启动，无法执行恢复测试");
    }
    webContents.forcefullyCrashRenderer();
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      if (
        this.view &&
        this.state.status === "ready" &&
        this.state.targetBound &&
        this.state.authenticated &&
        this.currentProfile
      ) {
        return this.getState();
      }
    }
    throw new Error("内置网页崩溃后未能在限定时间内恢复");
  }

  async dispose(): Promise<void> {
    this.destroyChildViews();
    this.destroyView();
    for (const manager of this.downloadManagers.values()) {
      manager.dispose();
    }
    this.downloadManagers.clear();
  }

  private async createView(profile: InstitutionProfile): Promise<void> {
    this.destroyChildViews();
    this.destroyView();

    const partition = `persist:${profile.sessionKey}`;
    const browserSession = electronSession.fromPartition(partition);
    if (
      this.options.fixtureMode &&
      profile.id === "local-fixture" &&
      !this.fixtureStorageReset
    ) {
      await browserSession.clearStorageData();
      this.fixtureStorageReset = true;
    }
    browserSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
      callback(false);
    });
    if (!this.downloadManagers.has(profile.sessionKey)) {
      const manager = new DownloadManager(
        browserSession,
        path.join(this.options.dataDirectory, "downloads", "staging"),
        (event) => this.handleDownloadEvent(event)
      );
      this.downloadManagers.set(profile.sessionKey, manager);
    }

    const view = new WebContentsView({
      webPreferences: {
        session: browserSession,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        webSecurity: true
      }
    });
    this.view = view;
    this.zoomFactor = normalizeBrowserZoomFactor(
      profile.browserZoomFactor ?? DEFAULT_BROWSER_ZOOM_FACTOR
    );
    this.viewId = String(view.webContents.id);
    this.targetKey = `pkulaw-view-${this.viewId}-${randomUUID()}`;
    this.options.window.contentView.addChildView(view);
    this.patchState({
      status: "loading",
      institutionId: profile.id,
      sessionKey: profile.sessionKey,
      viewLoaded: false,
      targetBound: false,
      targetUrl: "",
      selectorProfile: "",
      selectorVersion: "",
      selectorReady: false,
      selectorMessage: "等待页面完成选择器诊断",
      selectorDiagnostics: null,
      zoomFactor: this.zoomFactor,
      message: "正在创建内置网页"
    });
    this.applyZoomToWebContents(view.webContents, this.zoomFactor);
    this.installZoomShortcut(view.webContents);
    this.applyBounds();

    view.webContents.setWindowOpenHandler(({ url, postBody, referrer }) => {
      this.openControlledChild(url, { postBody, referrer });
      return { action: "deny" };
    });
    view.webContents.on("did-start-loading", () => {
      this.patchState({
        status: "loading",
        viewLoaded: false,
        message: "页面加载中"
      });
    });
    view.webContents.on("dom-ready", () => {
      reapplyBrowserZoom(view.webContents, this.zoomFactor);
      this.patchState({
        status: "loading",
        viewLoaded: true,
        url: view.webContents.getURL(),
        message: "网页 DOM 已就绪，正在建立 Python 绑定"
      });
    });
    view.webContents.on("did-finish-load", () => {
      reapplyBrowserZoom(view.webContents, this.zoomFactor);
      this.options.logger.write("info", "browser.page_loaded", "Browser page loaded", {
        url: view.webContents.getURL()
      });
      this.patchState({
        viewLoaded: true,
        url: view.webContents.getURL()
      });
      if (this.state.targetBound) {
        void this.refreshFromEngine().catch((error) => {
          this.patchState({
            status: "failed",
            message: `页面已加载，但 Python 状态同步失败：${String(error)}`
          });
        });
      }
    });
    view.webContents.on("did-fail-load", (_event, code, description, url, isMainFrame) => {
      if (!isMainFrame || code === -3) {
        return;
      }
      this.patchState({
        status: "failed",
        viewLoaded: false,
        targetBound: false,
        targetUrl: "",
        selectorReady: false,
        selectorMessage: "页面加载失败",
        selectorDiagnostics: null,
        message: `页面加载失败：${description} (${code})`,
        url
      });
    });
    view.webContents.on("page-title-updated", (_event, title) => {
      this.patchState({ title });
    });
    view.webContents.on("render-process-gone", (_event, details) => {
      this.options.logger.write("error", "browser.process_gone", "Browser renderer exited", {
        reason: details.reason,
        exitCode: details.exitCode
      });
      this.patchState({
        status: "failed",
        viewLoaded: false,
        targetBound: false,
        targetUrl: "",
        selectorReady: false,
        selectorMessage: "网页进程已退出",
        selectorDiagnostics: null,
        message: `内置网页进程退出：${details.reason}`
      });
      void this.recoverBrowser();
    });

    void view.webContents.loadURL(profile.entryUrl).catch((error) => {
      this.options.logger.write("warn", "browser.navigation_error", String(error), {
        url: profile.entryUrl
      });
    });
    await this.waitForHttpDomReady(view, 20_000);
    const targetUrl = view.webContents.getURL();
    this.patchState({
      viewLoaded: true,
      targetUrl,
      message: "网页已就绪，等待 Python 绑定"
    });
  }

  private async waitForHttpDomReady(
    view: WebContentsView,
    timeoutMs: number
  ): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const cleanup = (): void => {
        clearTimeout(timer);
        view.webContents.removeListener("dom-ready", onDomReady);
      };
      const onDomReady = (): void => {
        const currentUrl = view.webContents.getURL();
        if (!/^https?:\/\//i.test(currentUrl)) {
          return;
        }
        cleanup();
        resolve();
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error("BROWSER_VIEW_NOT_READY：等待网页 dom-ready 超时"));
      }, timeoutMs);
      view.webContents.on("dom-ready", onDomReady);
      onDomReady();
    });
  }

  private async connectPython(profile: InstitutionProfile): Promise<void> {
    const webContents = this.getUsableViewWebContents();
    if (!webContents) {
      throw new Error("BROWSER_VIEW_NOT_READY：内置网页不存在");
    }
    const targetUrl = webContents.getURL();
    if (!/^https?:\/\//i.test(targetUrl)) {
      throw new Error("BROWSER_VIEW_NOT_READY：内置网页尚未进入 HTTP/HTTPS 页面");
    }
    await webContents.executeJavaScript(
      `window.name = ${JSON.stringify(this.targetKey)};`
    );
    const result = await this.options.pythonService.request(
      "browser.connect",
      {
        debugPort: this.options.debugPort,
        entryUrl: profile.entryUrl,
        targetUrl,
        targetKey: this.targetKey,
        sessionKey: profile.sessionKey,
        viewId: this.viewId
      },
      20_000
    );
    const browser = result.browser as Record<string, unknown> | undefined;
    if (!browser?.targetBound) {
      throw new Error("PYTHON_BROWSER_NOT_BOUND：Python 未确认目标页面绑定");
    }
    this.connectedBackendPid = this.options.pythonService.pid;
    this.recoveryAttempts = 0;
    this.patchState({
      viewLoaded: true,
      targetBound: true,
      targetUrl: String(browser.targetUrl || targetUrl),
      url: String(browser.url || targetUrl),
      selectorReady: false,
      selectorMessage: "等待选择器诊断",
      browserLease: normalizeBrowserLease(browser.lease)
    });
  }

  private async refreshFromEngine(): Promise<void> {
    const webContents = this.getUsableViewWebContents();
    if (!webContents || !this.currentProfile) {
      return;
    }
    const result = await this.options.pythonService.request("browser.state", {}, 6_000);
    const browser = result.browser as Record<string, unknown>;
    const targetBound = Boolean(browser.targetBound);
    let selectorDiagnostics: Record<string, unknown> | null = null;
    let selectorError = "";
    if (targetBound) {
      try {
        selectorDiagnostics = (await this.options.pythonService.request(
          "browser.selector-diagnostics",
          {},
          8_000
        )) as Record<string, unknown>;
      } catch (error) {
        selectorError = error instanceof Error ? error.message : String(error);
      }
    }
    const captchaVisible = Boolean(browser.captchaVisible);
    const loginVisible = Boolean(browser.loginVisible);
    const authenticated = Boolean(browser.authenticated);
    const status = captchaVisible
      ? "waiting_manual"
      : loginVisible
        ? "waiting_login"
        : authenticated
          ? "ready"
          : "loading";
    this.state = {
      ...this.state,
      status,
      url: String(browser.url || ""),
      title: String(browser.title || ""),
      viewLoaded: this.state.viewLoaded || Boolean(browser.targetBound),
      targetBound,
      targetUrl: String(browser.targetUrl || browser.url || this.state.targetUrl),
      selectorProfile: String(selectorDiagnostics?.profileId || ""),
      selectorVersion: String(selectorDiagnostics?.version || ""),
      selectorReady: Boolean(selectorDiagnostics?.ready),
      selectorMessage: selectorError
        ? `选择器诊断失败：${selectorError}`
        : String(selectorDiagnostics?.message || "当前页面没有选择器诊断结果"),
      selectorDiagnostics,
      authenticated,
      loginVisible,
      captchaVisible,
      captchaSelector: String(browser.captchaSelector || ""),
      captchaBox:
        browser.captchaBox && typeof browser.captchaBox === "object"
          ? (browser.captchaBox as Record<string, number | string>)
          : {},
      captchaCheckedAt: String(browser.captchaCheckedAt || ""),
      canGoBack: webContents.navigationHistory.canGoBack(),
      childViews: this.childViews.length,
      tabs: Array.isArray(browser.tabs)
        ? (browser.tabs as Array<{ url: string; title: string }>)
        : [],
      lastDownload:
        (browser.lastDownload as Record<string, unknown> | null) ||
        this.state.lastDownload,
      browserLease: normalizeBrowserLease(browser.lease),
      message: captchaVisible
        ? "等待用户完成滑块验证"
        : loginVisible
          ? "请在网页中完成机构登录"
          : authenticated
            ? "内置网页和 Python 后端已连接"
            : "等待网页进入可操作状态"
    };
    this.emitState();
  }

  private handleDownloadEvent(event: DownloadEvent): void {
    this.state = { ...this.state, lastDownload: event as unknown as Record<string, unknown> };
    this.emitState();
    try {
      this.options.pythonService.notify(`browser.download.${event.event}`, {
        ...event
      });
    } catch (error) {
      this.options.logger.write("warn", "download.notify_failed", String(error));
    }
    this.options.logger.write("info", `download.${event.event}`, "Browser download event", {
      downloadId: event.downloadId,
      filename: event.filename,
      path: event.path,
      state: event.state,
      receivedBytes: event.receivedBytes
    });
    if (
      event.event === "completed" &&
      /\.zip$/i.test(event.filename)
    ) {
      setTimeout(() => this.closeSettledBatchDownloadChildren(), 500);
    }
  }

  private openControlledChild(
    url: string,
    navigation: ChildNavigationRequest = {}
  ): void {
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      this.options.logger.write("warn", "browser.child_invalid", "Rejected invalid child URL", { url });
      return;
    }
    if (!["http:", "https:"].includes(parsed.protocol)) {
      this.options.logger.write("warn", "browser.child_rejected", "Rejected child window", { url });
      return;
    }
    if (parsed.pathname.toLowerCase() === "/batchdownload.html") {
      const hasActiveDownloads = [...this.downloadManagers.values()].some(
        (manager) => manager.hasActiveDownloads()
      );
      if (hasActiveDownloads) {
        this.options.logger.write(
          "warn",
          "browser.child_rejected",
          "Rejected batch child while a download is active",
          { url }
        );
        return;
      }
      this.closeSettledBatchDownloadChildren();
    }
    if (this.childViews.length >= 2) {
      const hasActiveDownloads = [...this.downloadManagers.values()].some(
        (manager) => manager.hasActiveDownloads()
      );
      if (hasActiveDownloads) {
        this.options.logger.write(
          "warn",
          "browser.child_rejected",
          "Rejected child window while a download is active",
          { url }
        );
        return;
      }
      while (this.childViews.length >= 2) {
        this.closeOldestChildView();
      }
    }
    if (this.childViews.length >= 3) {
      this.options.logger.write("warn", "browser.child_rejected", "Rejected child window", { url });
      return;
    }

    const child = new WebContentsView({
      webPreferences: {
        session: this.getUsableViewWebContents()?.session,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        webSecurity: true
      }
    });
    this.childOpenedUrls.set(child, url);
    this.childViews.push(child);
    this.options.window.contentView.addChildView(child);
    child.setVisible(this.currentBounds.visible);
    child.setBounds(this.childBounds());
    this.applyZoomToWebContents(child.webContents, this.zoomFactor);
    this.installZoomShortcut(child.webContents);
    child.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    child.webContents.on("destroyed", () => {
      const index = this.childViews.indexOf(child);
      if (index >= 0) {
        this.childViews.splice(index, 1);
      }
      this.patchState({ childViews: this.childViews.length });
    });
    void child.webContents.loadURL(
      url,
      controlledChildLoadOptions(navigation)
    );
    this.options.logger.write("info", "browser.child_opened", "Controlled child view opened", {
      url,
      postPreserved: Boolean(navigation.postBody),
      contentType: navigation.postBody?.contentType || ""
    });
    try {
      this.options.pythonService.notify("browser.child-opened", { url });
    } catch (error) {
      this.options.logger.write("warn", "browser.child_notify_failed", String(error));
    }
    this.patchState({ childViews: this.childViews.length });
  }

  private async recoverBrowser(): Promise<void> {
    if (this.recovering || !this.currentProfile || this.recoveryAttempts >= 2) {
      return;
    }
    this.recovering = true;
    this.recoveryAttempts += 1;
    try {
      await new Promise((resolve) => setTimeout(resolve, 700));
      await this.createView(this.currentProfile);
      await this.connectPython(this.currentProfile);
      await this.refreshFromEngine();
    } catch (error) {
      this.patchState({
        status: "failed",
        message: `内置网页恢复失败：${String(error)}`
      });
    } finally {
      this.recovering = false;
    }
  }

  private applyBounds(): void {
    if (this.view && this.getUsableViewWebContents()) {
      this.view.setBounds({
        x: this.currentBounds.x,
        y: this.currentBounds.y,
        width: this.currentBounds.width,
        height: this.currentBounds.height
      });
      this.view.setVisible(this.currentBounds.visible);
    } else if (this.view) {
      this.destroyView();
    }
    for (const child of this.childViews) {
      child.setBounds(this.childBounds());
      child.setVisible(this.currentBounds.visible);
    }
  }

  private childBounds(): Electron.Rectangle {
    const width = Math.max(320, Math.min(760, Math.floor(this.currentBounds.width * 0.72)));
    const height = Math.max(240, Math.min(520, Math.floor(this.currentBounds.height * 0.7)));
    return {
      x: this.currentBounds.x + Math.max(16, Math.floor((this.currentBounds.width - width) / 2)),
      y: this.currentBounds.y + Math.max(16, Math.floor((this.currentBounds.height - height) / 2)),
      width,
      height
    };
  }

  private destroyView(): void {
    if (!this.view) {
      return;
    }
    const view = this.view;
    this.view = null;
    this.viewId = "";
    this.targetKey = "";
    this.connectedBackendPid = null;
    this.patchState({
      viewLoaded: false,
      targetBound: false,
      targetUrl: "",
      selectorProfile: "",
      selectorVersion: "",
      selectorReady: false,
      selectorMessage: "内置网页已销毁",
      selectorDiagnostics: null
    });
    try {
      this.options.window.contentView.removeChildView(view);
      view.webContents.close();
    } catch (error) {
      this.options.logger.write("warn", "browser.destroy_main_view_failed", String(error));
    }
  }

  private getUsableViewWebContents(): Electron.WebContents | null {
    const webContents = this.view?.webContents;
    if (!webContents) {
      return null;
    }
    try {
      return webContents.isDestroyed() ? null : webContents;
    } catch {
      return null;
    }
  }

  private destroyChildViews(): void {
    while (this.childViews.length) {
      const child = this.childViews.pop();
      if (!child) continue;
      try {
        this.options.window.contentView.removeChildView(child);
        child.webContents.close();
      } catch (error) {
        this.options.logger.write("warn", "browser.destroy_child_view_failed", String(error));
      }
    }
  }

  private closeOldestChildView(): void {
    const child = this.childViews.shift();
    if (!child) {
      return;
    }
    try {
      this.options.window.contentView.removeChildView(child);
      child.webContents.close();
    } catch (error) {
      this.options.logger.write(
        "warn",
        "browser.close_child_view_failed",
        String(error)
      );
    }
    this.patchState({ childViews: this.childViews.length });
  }

  private closeSettledBatchDownloadChildren(): void {
    for (let index = this.childViews.length - 1; index >= 0; index -= 1) {
      const child = this.childViews[index];
      let currentUrl = "";
      try {
        currentUrl = child.webContents.getURL();
      } catch {
        currentUrl = "";
      }
      const openedUrl = this.childOpenedUrls.get(child) || "";
      if (!shouldCloseSettledBatchChild(openedUrl, currentUrl)) {
        continue;
      }
      this.childViews.splice(index, 1);
      try {
        this.options.window.contentView.removeChildView(child);
        child.webContents.close();
      } catch (error) {
        this.options.logger.write(
          "warn",
          "browser.close_batch_child_failed",
          String(error)
        );
      }
    }
    this.patchState({ childViews: this.childViews.length });
  }

  private patchState(patch: Partial<BrowserHostState>): void {
    this.state = { ...this.state, ...patch, childViews: this.childViews.length };
    this.emitState();
  }

  private applyZoomToWebContents(
    webContents: Electron.WebContents,
    factor: number
  ): void {
    reapplyBrowserZoom(webContents, factor);
    void webContents
      .setVisualZoomLevelLimits(0.5, 2)
      .catch((error) => {
        this.options.logger.write(
          "warn",
          "browser.zoom_limits_failed",
          String(error)
        );
      });
  }

  private installZoomShortcut(webContents: Electron.WebContents): void {
    webContents.on("before-input-event", (event, input) => {
      this.handleZoomShortcut(event, input);
    });
  }

  private handleZoomShortcut(
    event: Electron.Event,
    input: Electron.Input
  ): void {
    const shortcut = browserZoomShortcut(input);
    if (!shortcut) return;
    event.preventDefault();
    if (!this.state.targetBound) return;
    const next = zoomFactorForShortcut(this.zoomFactor, shortcut);
    void this.setBrowserZoom(next).catch((error) => {
      this.options.logger.write(
        "warn",
        "browser.zoom_shortcut_failed",
        String(error)
      );
    });
  }

  private emitState(): void {
    this.emit("state", this.getState());
  }
}
