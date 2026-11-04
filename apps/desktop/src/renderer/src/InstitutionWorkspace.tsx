import { useEffect, useRef, useState } from "react";
import type {
  AppInfo,
  BackendState,
  BrowserHostState,
  InstitutionProfile
} from "../../shared/protocol";
import { emptyBrowserLease } from "../../shared/browser-lease";
import { DEFAULT_BROWSER_ZOOM_FACTOR } from "../../shared/browser-zoom";
import BrowserSurface from "./BrowserSurface";

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

interface InstitutionWorkspaceProps {
  appInfo: AppInfo | null;
  backend: BackendState;
  onNavigate: (target: "single" | "batch" | "settings") => void;
}

export function InstitutionStatusCard({
  browser,
  institutionName,
  entryUrl,
  onOpen,
  onSwitch,
  onRefresh
}: {
  browser: BrowserHostState;
  institutionName: string;
  entryUrl: string;
  onOpen: () => void;
  onSwitch: () => void;
  onRefresh: () => void;
}): React.JSX.Element {
  return (
    <section className="institution-status-card" data-testid="institution-status">
      <div>
        <span className="eyebrow">机构入口</span>
        <h2>{institutionName || "尚未选择机构"}</h2>
        <p>{entryUrl || "请先填写学校或北大法宝授权入口"}</p>
      </div>
      <div className="institution-status-metrics">
        <span className={browser.authenticated ? "binding-ok" : "binding-pending"}>
          {browser.authenticated ? "机构已认证" : "尚未认证"}
        </span>
        <span className={browser.selectorReady ? "binding-ok" : "binding-pending"}>
          {browser.selectorMessage || "等待选择器诊断"}
        </span>
        <span>网页缩放 {Math.round(browser.zoomFactor * 100)}%</span>
      </div>
      <div className="institution-status-actions">
        <button type="button" className="secondary-button" onClick={onRefresh}>
          重新检测
        </button>
        <button type="button" className="secondary-button" onClick={onSwitch}>
          切换账号
        </button>
        <button type="button" className="primary-button compact" onClick={onOpen}>
          打开机构页面
        </button>
      </div>
    </section>
  );
}

export default function InstitutionWorkspace({
  appInfo,
  backend,
  onNavigate
}: InstitutionWorkspaceProps): React.JSX.Element {
  const [browser, setBrowser] = useState<BrowserHostState>(EMPTY_BROWSER);
  const [institutions, setInstitutions] = useState<InstitutionProfile[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [entryUrl, setEntryUrl] = useState("");
  const [browserOpen, setBrowserOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const awaitingLogin = useRef(false);

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
    const unsubscribe = window.pkulaw.onBrowserState(setBrowser);
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, []);

  useEffect(() => {
    if (browser.authenticated && awaitingLogin.current) {
      awaitingLogin.current = false;
      setBrowserOpen(false);
      setNotice("机构登录已完成，可以进入采集任务。");
    }
  }, [browser.authenticated]);

  useEffect(() => {
    if (
      !appInfo?.fixtureMode ||
      browser.institutionId ||
      backend.status !== "ready"
    ) {
      return;
    }
    const profile = institutions.find((item) => item.id === "local-fixture");
    if (!profile) return;
    setBusy(true);
    void window.pkulaw
      .selectInstitution(profile.id)
      .then(setBrowser)
      .catch((error) => setNotice(`打开测试机构失败：${String(error)}`))
      .finally(() => setBusy(false));
  }, [
    appInfo?.fixtureMode,
    backend.status,
    browser.institutionId,
    institutions
  ]);

  const selectedInstitution =
    institutions.find((item) => item.id === selectedId) || null;
  const showBrowser = !browser.authenticated || browserOpen;

  async function saveProfile(): Promise<void> {
    setBusy(true);
    try {
      const profile = await window.pkulaw.saveInstitution({
        id: selectedId.startsWith("institution-") ? selectedId : undefined,
        name,
        entryUrl
      });
      setInstitutions(await window.pkulaw.listInstitutions());
      setSelectedId(profile.id);
      setName(profile.name);
      setEntryUrl(profile.entryUrl);
      setNotice("机构配置已保存。");
    } catch (error) {
      setNotice(`保存机构失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function openSelected(switchAccount = false): Promise<void> {
    if (!selectedId) {
      setNotice("请先保存一个机构配置。");
      setBrowserOpen(true);
      return;
    }
    setBusy(true);
    setBrowserOpen(true);
    awaitingLogin.current = switchAccount || !browser.authenticated;
    try {
      setBrowser(await window.pkulaw.selectInstitution(selectedId));
      setNotice(
        switchAccount
          ? "请在网页中退出当前账号并登录需要使用的账号。"
          : "请在网页中完成机构登录或继续使用当前 Session。"
      );
    } catch (error) {
      setNotice(`打开机构失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function refreshStatus(): Promise<void> {
    setBrowser(await window.pkulaw.getBrowserState());
  }

  async function continueBrowser(): Promise<void> {
    setBusy(true);
    try {
      setBrowser(await window.pkulaw.browserContinue());
    } catch (error) {
      setNotice(`网页状态复核失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="overview-institution-workspace">
      {showBrowser ? (
        <div className="overview-institution-grid">
          <aside className="institution-config-card">
            <span className="eyebrow">机构配置</span>
            <h2>机构与登录</h2>
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
              <input
                value={entryUrl}
                onChange={(event) => setEntryUrl(event.target.value)}
              />
            </label>
            <div className="institution-config-actions">
              <button
                type="button"
                className="secondary-button"
                disabled={busy}
                onClick={() => void saveProfile()}
              >
                保存配置
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={busy}
                onClick={() => void openSelected(true)}
              >
                切换账号
              </button>
              <button
                type="button"
                className="primary-button compact"
                disabled={busy || backend.status !== "ready"}
                onClick={() => void openSelected(false)}
              >
                打开机构页面
              </button>
            </div>
            <p className="security-note">
              不保存账号密码，不导出 Cookie；IP 登录和个人账号切换均在右侧网页完成。
            </p>
          </aside>

          <BrowserSurface
            browser={browser}
            visible
            variant="overview"
            onRefresh={() =>
              void window.pkulaw.browserNavigate("reload").then(setBrowser)
            }
            onContinue={() => void continueBrowser()}
          />
        </div>
      ) : (
        <InstitutionStatusCard
          browser={browser}
          institutionName={name || selectedInstitution?.name || ""}
          entryUrl={entryUrl || selectedInstitution?.entryUrl || ""}
          onOpen={() => void openSelected(false)}
          onSwitch={() => void openSelected(true)}
          onRefresh={() => void refreshStatus()}
        />
      )}

      <div className="overview-quick-entry">
        <div>
          <span className="eyebrow">采集入口</span>
          <h3>机构配置完成后开始工作</h3>
          <p>任务设置、浏览器与断点将在同一个运行工作区中显示。</p>
        </div>
        <div className="overview-quick-actions">
          <button
            type="button"
            className="primary-button overview-quick-action"
            onClick={() => onNavigate("single")}
          >
            进入逐篇采集
          </button>
          <button
            type="button"
            className="primary-button overview-quick-action"
            onClick={() => onNavigate("batch")}
          >
            进入批量采集
          </button>
          <button
            type="button"
            className="primary-button overview-quick-action"
            onClick={() => onNavigate("settings")}
          >
            运行环境诊断
          </button>
        </div>
      </div>

      {notice && (
        <div className="notice">
          <span className="notice-icon">i</span>
          <span>{notice}</span>
        </div>
      )}
    </section>
  );
}
