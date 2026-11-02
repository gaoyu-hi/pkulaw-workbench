import { useEffect, useRef, useState } from "react";
import type {
  AppInfo,
  BackendState,
  BrowserHostState,
  InstitutionProfile,
  TaskRecord
} from "../../shared/protocol";
import SelectorDiagnostics from "./SelectorDiagnostics";
import {
  emptyBrowserLease,
  holdingTaskLabel
} from "../../shared/browser-lease";
import {
  BrowserLeaseCard,
  ExpandableValue
} from "./TaskRuntimePanel";
import {
  DEFAULT_BROWSER_ZOOM_FACTOR,
  stepBrowserZoom
} from "../../shared/browser-zoom";
import { browserViewBoundsFromRect } from "../../shared/browser-layout";

const EMPTY_BROWSER: BrowserHostState = {
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
  browserLease: emptyBrowserLease()
};

const browserStatusLabels: Record<BrowserHostState["status"], string> = {
  idle: "未启动",
  loading: "加载中",
  ready: "已连接",
  waiting_login: "等待登录",
  waiting_manual: "等待人工",
  failed: "异常"
};

interface BrowserPanelProps {
  appInfo: AppInfo | null;
  backend: BackendState;
}

export function BrowserZoomControls({
  factor,
  disabled,
  onChange
}: {
  factor: number;
  disabled: boolean;
  onChange: (factor: number) => void;
}): React.JSX.Element {
  const percent = Math.round(factor * 100);
  return (
    <div className="browser-zoom-controls" title="Ctrl + / Ctrl - / Ctrl 0">
      <button
        type="button"
        className="icon-button"
        aria-label="缩小网页"
        disabled={disabled || factor <= 0.5}
        onClick={() => onChange(stepBrowserZoom(factor, "out"))}
      >
        −
      </button>
      <span data-testid="browser-zoom-value">{percent}%</span>
      <button
        type="button"
        className="icon-button"
        aria-label="放大网页"
        disabled={disabled || factor >= 2}
        onClick={() => onChange(stepBrowserZoom(factor, "in"))}
      >
        +
      </button>
      <button
        type="button"
        className="text-button"
        aria-label="重置网页缩放"
        disabled={disabled || factor === DEFAULT_BROWSER_ZOOM_FACTOR}
        onClick={() => onChange(DEFAULT_BROWSER_ZOOM_FACTOR)}
      >
        100%
      </button>
    </div>
  );
}

export default function BrowserPanel({
  appInfo,
  backend
}: BrowserPanelProps): React.JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  const [browser, setBrowser] = useState<BrowserHostState>(EMPTY_BROWSER);
  const [institutions, setInstitutions] = useState<InstitutionProfile[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("本地测试机构");
  const [entryUrl, setEntryUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [zoomBusy, setZoomBusy] = useState(false);
  const [notice, setNotice] = useState("本地夹具模式：不会访问真实北大法宝。");

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      window.pkulaw.getBrowserState(),
      window.pkulaw.listInstitutions()
    ])
      .then(([browserState, records]) => {
        if (cancelled) return;
        setBrowser(browserState);
        setInstitutions(records);
        const preferred =
          records.find((item) => item.id === browserState.institutionId) ||
          records.find((item) => item.id === "local-fixture") ||
          records[0];
        if (preferred) {
          setSelectedId(preferred.id);
          setName(preferred.name);
          setEntryUrl(preferred.entryUrl);
        }
      })
      .catch((error) => setNotice(`读取机构配置失败：${String(error)}`));

    const unsubscribe = window.pkulaw.onBrowserState((state) => {
      setBrowser(state);
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (backend.status !== "ready") return;
    let cancelled = false;
    void window.pkulaw
      .listTasks(50)
      .then((records) => {
        if (!cancelled) setTasks(records);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [backend.status]);

  useEffect(() => {
    if (!appInfo?.fixtureMode || browser.institutionId || backend.status !== "ready") {
      return;
    }
    const profile = institutions.find((item) => item.id === "local-fixture");
    if (!profile) {
      return;
    }
    setBusy(true);
    window.pkulaw
      .selectInstitution(profile.id)
      .then((state) => setBrowser(state))
      .catch((error) => setNotice(`打开测试机构失败：${String(error)}`))
      .finally(() => setBusy(false));
  }, [appInfo?.fixtureMode, backend.status, browser.institutionId, institutions]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) {
      return;
    }
    const update = (): void => {
      const bounds = browserViewBoundsFromRect(host.getBoundingClientRect());
      if (bounds) {
        void window.pkulaw.setBrowserBounds(bounds);
      }
    };
    const observer = new ResizeObserver(update);
    observer.observe(host);
    window.addEventListener("resize", update);
    const frame = requestAnimationFrame(() => {
      requestAnimationFrame(update);
    });
    const retryTimers = [80, 240, 600, 1200].map((delay) =>
      window.setTimeout(update, delay)
    );
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", update);
      for (const timer of retryTimers) window.clearTimeout(timer);
      void window.pkulaw.setBrowserBounds({ x: 0, y: 0, width: 1, height: 1, visible: false });
    };
  }, []);

  async function saveProfile(): Promise<void> {
    setBusy(true);
    try {
      const profile = await window.pkulaw.saveInstitution({
        id: selectedId.startsWith("institution-") ? selectedId : undefined,
        name,
        entryUrl
      });
      const records = await window.pkulaw.listInstitutions();
      setInstitutions(records);
      setSelectedId(profile.id);
      setNotice("机构配置已保存，可用于创建独立持久 Session。");
    } catch (error) {
      setNotice(`保存机构失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function openSelected(): Promise<void> {
    if (!selectedId) {
      setNotice("请先选择或保存一个机构配置。");
      return;
    }
    setBusy(true);
    try {
      const state = await window.pkulaw.selectInstitution(selectedId);
      setBrowser(state);
      setNotice("内置浏览器已启动，当前页面由 Python/DrissionPage 同步控制。");
    } catch (error) {
      setNotice(`打开机构失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function fixtureAction(action: "login" | "captcha" | "download" | "child"): Promise<void> {
    setBusy(true);
    try {
      setBrowser(await window.pkulaw.browserFixtureAction(action));
      setNotice("夹具操作已完成。");
    } catch (error) {
      setNotice(`夹具操作失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function continueBrowser(): Promise<void> {
    setBusy(true);
    try {
      setBrowser(await window.pkulaw.browserContinue());
      setNotice("已确认人工处理，Python 将重新核验页面状态。");
    } catch (error) {
      setNotice(`人工继续失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function refreshBrowserState(): Promise<void> {
    setBusy(true);
    try {
      setBrowser(await window.pkulaw.getBrowserState());
      setNotice("已重新检测内置网页状态。");
    } catch (error) {
      setNotice(`重新检测失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function changeBrowserZoom(factor: number): Promise<void> {
    setZoomBusy(true);
    try {
      const applied = await window.pkulaw.setBrowserZoom(factor);
      setBrowser((current) => ({ ...current, zoomFactor: applied }));
      setNotice(`内置网页缩放已设为 ${Math.round(applied * 100)}%。`);
    } catch (error) {
      setNotice(`网页缩放失败：${String(error)}`);
    } finally {
      setZoomBusy(false);
    }
  }

  const download = browser.lastDownload as
    | { event?: string; filename?: string; receivedBytes?: number }
    | null;

  return (
    <section className="browser-lab">
      <div className="browser-toolbar">
        <div className="browser-navigation">
          <button
            type="button"
            className="icon-button"
            disabled={!browser.canGoBack}
            onClick={() => void window.pkulaw.browserNavigate("back").then(setBrowser)}
            aria-label="后退"
          >
            ‹
          </button>
          <button
            type="button"
            className="icon-button"
            disabled={!browser.institutionId}
            onClick={() => void window.pkulaw.browserNavigate("reload").then(setBrowser)}
            aria-label="刷新"
          >
            ↻
          </button>
          <button
            type="button"
            className="icon-button"
            disabled={!browser.institutionId}
            onClick={() => void window.pkulaw.browserNavigate("home").then(setBrowser)}
            aria-label="主页"
          >
            ⌂
          </button>
          <div className="browser-address" title={browser.url}>
            <span className="lock-dot" />
            {browser.url || "等待打开机构入口"}
          </div>
        </div>
        <div className="browser-toolbar-right">
          <BrowserZoomControls
            factor={browser.zoomFactor}
            disabled={zoomBusy || !browser.targetBound}
            onChange={(factor) => void changeBrowserZoom(factor)}
          />
          <span className={`browser-status ${browser.status}`}>
            {browserStatusLabels[browser.status]}
          </span>
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              void window.pkulaw
                .restartBackend()
                .then((state) => {
                  setNotice(`后端已重启，协议 ${state.protocolVersion}`);
                })
                .catch((error) => setNotice(`重启失败：${String(error)}`))
                .finally(() => setBusy(false));
            }}
          >
            重启后端
          </button>
        </div>
      </div>

      <div className="browser-content-grid">
        <div className="browser-stage">
          <div className="browser-stage-heading">
            <div>
              <span className="eyebrow">内置浏览器</span>
              <h2>{browser.title || "等待页面加载"}</h2>
            </div>
            <div className="browser-badges">
              <span className={backend.status === "ready" ? "binding-ok" : "binding-pending"}>
                后端{backend.status === "ready" ? "已连接" : "未连接"}
              </span>
              <span className={browser.viewLoaded ? "binding-ok" : "binding-pending"}>
                网页{browser.viewLoaded ? "已加载" : "未加载"}
              </span>
              <span className={browser.targetBound ? "binding-ok" : "binding-error"}>
                Python{browser.targetBound ? "已绑定" : "未绑定"}
              </span>
              <span className={browser.selectorReady ? "binding-ok" : "binding-pending"}>
                选择器{browser.selectorReady ? "可用" : "待诊断"}
              </span>
              <span className={browser.authenticated ? "binding-ok" : "binding-pending"}>
                {browser.authenticated ? "机构已认证" : "尚未认证"}
              </span>
              <span>子窗口 {browser.childViews}</span>
              <span className={browser.browserLease.available ? "binding-ok" : "binding-pending"}>
                浏览器租约 {browser.browserLease.available ? "空闲" : "已占用"}
              </span>
            </div>
          </div>
          <div ref={hostRef} className="browser-view-host">
            {!browser.institutionId && (
              <div className="browser-placeholder">
                <div className="browser-placeholder-mark">◎</div>
                <strong>选择机构后在这里显示网页</strong>
                <p>网页由 Electron 自己承载，不会弹出外部 Edge。</p>
              </div>
            )}
          </div>
        </div>

        <aside className="browser-inspector">
          <div className="inspector-card">
            <span className="eyebrow">机构配置</span>
            <label>
              已保存机构
              <select
                value={selectedId}
                onChange={(event) => {
                  const nextId = event.target.value;
                  setSelectedId(nextId);
                  const profile = institutions.find((item) => item.id === nextId);
                  if (profile) {
                    setName(profile.name);
                    setEntryUrl(profile.entryUrl);
                  }
                }}
              >
                {institutions.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                  </option>
                ))}
                {institutions.length === 0 && <option value="">暂无配置</option>}
              </select>
            </label>
            <label>
              机构名称
              <input value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            <label>
              授权入口
              <input value={entryUrl} onChange={(event) => setEntryUrl(event.target.value)} />
            </label>
            <div className="inspector-actions">
              <button type="button" className="secondary-button" disabled={busy} onClick={() => void saveProfile()}>
                保存配置
              </button>
              <button type="button" className="primary-button compact" disabled={busy || backend.status !== "ready"} onClick={() => void openSelected()}>
                在应用内打开
              </button>
            </div>
            <p className="security-note">不保存账号密码，不导出 Cookie；不同机构使用独立持久 Session。</p>
          </div>

          {browser.captchaVisible && (
            <div className="manual-banner">
              <strong>等待人工完成滑块或验证码</strong>
              <p>请先在内置网页中完成验证，再重新检测。验证仍可见时不能继续。</p>
              <button type="button" onClick={() => void refreshBrowserState()} disabled={busy}>
                重新检测
              </button>
              <button
                type="button"
                onClick={() => void continueBrowser()}
                disabled={busy || browser.captchaVisible}
              >
                已处理，继续
              </button>
            </div>
          )}

          {appInfo?.fixtureMode && (
            <div className="inspector-card fixture-card">
              <span className="eyebrow">阶段 2 测试夹具</span>
              <div className="fixture-actions">
                <button type="button" onClick={() => void fixtureAction("login")}>模拟登录</button>
                <button type="button" onClick={() => void fixtureAction("captcha")}>显示滑块</button>
                <button type="button" onClick={() => void fixtureAction("download")}>测试下载</button>
                <button type="button" onClick={() => void fixtureAction("child")}>打开子视图</button>
              </div>
            </div>
          )}

          <div className="inspector-card">
            <span className="eyebrow">连接状态</span>
            <dl className="browser-details">
              <div><dt>Session</dt><dd>{browser.sessionKey || "—"}</dd></div>
              <div><dt>Python 绑定</dt><dd>{browser.targetBound ? "已绑定" : "未绑定"}</dd></div>
              <div><dt>页面目标</dt><dd title={browser.targetUrl}>{browser.targetBound ? `${browser.tabs.length} 个` : "0"}</dd></div>
              <div><dt>选择器配置</dt><dd title={browser.selectorMessage}>{browser.selectorProfile || "未识别"}</dd></div>
              <div><dt>配置版本</dt><dd>{browser.selectorVersion || "—"}</dd></div>
              <div><dt>适配状态</dt><dd title={browser.selectorMessage}>{browser.selectorReady ? "可用" : "未通过"}</dd></div>
              <div><dt>登录可见</dt><dd>{browser.loginVisible ? "是" : "否"}</dd></div>
              <div><dt>滑块可见</dt><dd>{browser.captchaVisible ? "是" : "否"}</dd></div>
              <div><dt>验证码控件</dt><dd title={browser.captchaSelector || ""}>{browser.captchaSelector || "—"}</dd></div>
              <div><dt>验证码尺寸</dt><dd>{browser.captchaBox?.width && browser.captchaBox?.height ? `${browser.captchaBox.width} × ${browser.captchaBox.height}` : "—"}</dd></div>
              <div><dt>验证码检测</dt><dd title={browser.captchaCheckedAt || ""}>{browser.captchaCheckedAt || "未检测"}</dd></div>
              <div><dt>下载</dt><dd>{download?.event || "—"}</dd></div>
              <div><dt>网页缩放</dt><dd>{Math.round(browser.zoomFactor * 100)}%</dd></div>
              <div>
                <dt>浏览器租约</dt>
                <dd data-testid="browser-lease-state">
                  {holdingTaskLabel(browser, tasks)}
                </dd>
              </div>
            </dl>
            <ExpandableValue label="当前页面 URL" value={browser.url || "等待页面加载"} />
            <BrowserLeaseCard browser={browser} tasks={tasks} compact />
            <SelectorDiagnostics diagnostics={browser.selectorDiagnostics} />
            {download?.filename && (
              <div className="download-line">
                <span>{download.filename}</span>
                <strong>{download.receivedBytes || 0} B</strong>
              </div>
            )}
          </div>

          <div className="notice browser-notice">
            <span className="notice-icon">i</span>
            <span>{notice || browser.message}</span>
          </div>
        </aside>
      </div>
    </section>
  );
}
