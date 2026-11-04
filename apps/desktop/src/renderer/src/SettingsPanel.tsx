import { useEffect, useMemo, useState } from "react";
import type {
  AppInfo,
  BackendState,
  BrowserHostState,
  DiagnosticsReport,
  InstitutionProfile,
  LogRecord,
  TaskRecord
} from "../../shared/protocol";
import SelectorDiagnostics from "./SelectorDiagnostics";
import {
  emptyBrowserLease,
} from "../../shared/browser-lease";
import {
  BrowserLeaseCard,
  ExpandableValue,
  TaskErrorCard
} from "./TaskRuntimePanel";

interface SettingsPanelProps {
  appInfo: AppInfo | null;
  backend: BackendState;
}

const emptyBrowser: BrowserHostState = {
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
  zoomFactor: 1,
  canGoBack: false,
  childViews: 0,
  tabs: [],
  message: "尚未选择机构",
  lastDownload: null,
  browserLease: emptyBrowserLease()
};

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
}

function checkLabel(report: DiagnosticsReport | null): string {
  if (!report) return "尚未运行";
  if (report.checks.length === 0) return "未检测";
  if (report.checks.some((check) => check.status === "error")) return "存在异常";
  if (report.checks.some((check) => check.status === "warning")) return "部分需关注";
  return "全部通过";
}

export default function SettingsPanel({
  appInfo,
  backend
}: SettingsPanelProps): React.JSX.Element {
  const [report, setReport] = useState<DiagnosticsReport | null>(null);
  const [browser, setBrowser] = useState<BrowserHostState>(emptyBrowser);
  const [institutions, setInstitutions] = useState<InstitutionProfile[]>([]);
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("启动预检会在后端就绪后自动运行，也可随时重新诊断。");

  async function refreshEnvironment(): Promise<void> {
    const [browserState, records, recentLogs, taskRecords] = await Promise.all([
      window.pkulaw.getBrowserState(),
      window.pkulaw.listInstitutions(),
      window.pkulaw.getRecentLogs(),
      window.pkulaw.listTasks(200)
    ]);
    setBrowser(browserState);
    setInstitutions(records);
    setLogs(recentLogs);
    setTasks(taskRecords);
  }

  async function runPreflight(manual = false): Promise<void> {
    if (backend.status !== "ready") {
      if (manual) setNotice("Python 后端尚未就绪，暂不能运行完整诊断。");
      return;
    }
    setBusy(true);
    try {
      const next = await window.pkulaw.getDiagnostics();
      setReport(next);
      await refreshEnvironment();
      setNotice(
        manual
          ? `诊断完成：${checkLabel(next)}。`
          : `启动预检完成：${checkLabel(next)}。`
      );
    } catch (error) {
      setNotice(`诊断失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (backend.status !== "ready" || report) return;
    void runPreflight(false);
  }, [backend.status]);

  useEffect(() => {
    const unsubscribe = window.pkulaw.onBrowserState(setBrowser);
    return unsubscribe;
  }, []);

  const currentInstitution = useMemo(
    () => institutions.find((item) => item.id === browser.institutionId) || null,
    [browser.institutionId, institutions]
  );
  const recentFailure = useMemo(
    () =>
      tasks.find(
        (task) =>
          ["failed", "partial", "stopped"].includes(task.status) &&
          Boolean(task.error)
      ) || null,
    [tasks]
  );

  return (
    <section className="settings-workspace">
      <div className="settings-hero panel">
        <div>
          <span className="eyebrow">
            {appInfo?.version || "0.2.2"} · 设置与诊断
          </span>
          <h2>运行预检与团队环境健康</h2>
          <p>检查 Python、SQLite、浏览器、网络、日志、数据目录和显示缩放。</p>
        </div>
        <div className={`diagnostics-score ${report?.checks.some((check) => check.status === "error") ? "error" : report ? "ok" : "pending"}`}>
          <span>{checkLabel(report)}</span>
          <strong>{report?.checks.filter((check) => check.status !== "warning" && check.status !== "skipped").length || 0}</strong>
          <small>已核验项目</small>
        </div>
      </div>

      <div className="diagnostics-actionbar panel">
        <div>
          <strong>启动预检与运行诊断</strong>
          <span>{report ? `最近诊断：${report.generatedAt}` : "等待首次诊断"}</span>
        </div>
        <div className="task-detail-actions">
          <button type="button" className="primary-button compact" disabled={busy || backend.status !== "ready"} onClick={() => void runPreflight(true)}>
            {busy ? "诊断中…" : "重新诊断"}
          </button>
          <button type="button" className="secondary-button" onClick={() => void window.pkulaw.openDataDirectory()}>
            打开数据目录
          </button>
          <button type="button" className="secondary-button" onClick={() => void window.pkulaw.openLogsDirectory()}>
            打开日志
          </button>
        </div>
      </div>

      <div className="settings-grid">
        <article className="panel diagnostics-panel" data-testid="diagnostics-checks">
          <div className="panel-heading">
            <div><span className="eyebrow">检查结果</span><h3>启动预检</h3></div>
            <span className="data-count">{report?.checks.length || 0} 项</span>
          </div>
          <div className="diagnostic-check-list">
            {report?.checks.map((check) => (
              <div className="diagnostic-check" key={check.id}>
                <span className={`diagnostic-dot ${check.status}`} />
                <div>
                  <strong>{check.label}</strong>
                  <ExpandableValue
                    label="详情"
                    value={check.detail}
                  />
                </div>
                <span className={`diagnostic-status ${check.status}`}>
                  {{ ok: "正常", warning: "注意", error: "异常", skipped: "跳过" }[check.status]}
                </span>
              </div>
            ))}
            {!report && <div className="muted">后端就绪后自动运行预检。</div>}
          </div>
        </article>

        <article className="panel runtime-panel" data-testid="diagnostics-runtime">
          <div className="panel-heading">
            <div><span className="eyebrow">运行时</span><h3>版本与路径</h3></div>
            <span className={`task-status ${backend.status === "ready" ? "completed" : "failed"}`}>
              {backend.status === "ready" ? "已连接" : backend.status}
            </span>
          </div>
          <dl className="runtime-list">
            <div><dt>应用版本</dt><dd>{appInfo?.version || "—"}</dd></div>
            <div><dt>Electron</dt><dd>{report?.desktop.electronVersion || appInfo?.electronVersion || "—"}</dd></div>
            <div><dt>Chromium</dt><dd>{report?.desktop.chromeVersion || appInfo?.chromeVersion || "—"}</dd></div>
            <div><dt>Node</dt><dd>{report?.desktop.nodeVersion || appInfo?.nodeVersion || "—"}</dd></div>
            <div><dt>Python</dt><dd>{report?.engine.pythonVersion || "等待诊断"}</dd></div>
            <div><dt>后端版本</dt><dd>{report?.engine.version || backend.engineVersion || "未知"}</dd></div>
            <div><dt>协议版本</dt><dd>{report?.engine.protocolVersion || backend.protocolVersion || "未知"}</dd></div>
            <div><dt>后端模式</dt><dd>{appInfo?.backendMode === "bundled" ? "应用内置" : "源码 Python"}</dd></div>
            <div><dt>显示缩放</dt><dd>{report ? `${report.desktop.scaleFactor * 100}% · ${report.desktop.displaySize.width}×${report.desktop.displaySize.height}` : "—"}</dd></div>
            <div><dt>数据库版本</dt><dd>{report ? `schema ${report.database.schemaVersion}/${report.database.expectedSchemaVersion}` : "未知"}</dd></div>
            <div><dt>完整性</dt><dd>{report?.database.integrity || "未知"}</dd></div>
            <div><dt>磁盘剩余</dt><dd>{report ? formatBytes(report.disk.free) : "—"}</dd></div>
          </dl>
        </article>

        <article className="panel network-panel" data-testid="diagnostics-browser">
          <div className="panel-heading">
            <div><span className="eyebrow">机构与网络</span><h3>{currentInstitution?.name || "尚未选择机构"}</h3></div>
            <span className={`diagnostic-status ${report?.desktop.network.status || "skipped"}`}>
              {report?.desktop.network.status || "未检查"}
            </span>
          </div>
          <p className="settings-copy">{browser.message}</p>
          <dl className="runtime-list compact-runtime">
            <div><dt>当前页面</dt><dd title={browser.url}>{browser.title || browser.url || "—"}</dd></div>
            <div><dt>网页 View</dt><dd>{browser.viewLoaded ? "已加载" : "未加载"}</dd></div>
            <div><dt>Python 绑定</dt><dd>{browser.targetBound ? "已绑定" : "未绑定"}</dd></div>
            <div><dt>Session</dt><dd>{browser.sessionKey || "未知"}</dd></div>
            <div><dt>认证状态</dt><dd>{browser.authenticated ? "已认证" : "未认证"}</dd></div>
            <div><dt>选择器配置</dt><dd title={browser.selectorMessage}>{browser.selectorProfile || "未识别"}</dd></div>
            <div><dt>适配状态</dt><dd>{browser.selectorReady ? "可用" : "未通过"}</dd></div>
            <div><dt>入口地址</dt><dd title={currentInstitution?.entryUrl}>{currentInstitution?.entryUrl || "—"}</dd></div>
            <div><dt>网络结果</dt><dd>{report?.desktop.network.detail || "选择并打开机构后检查"}</dd></div>
          </dl>
          <BrowserLeaseCard browser={browser} tasks={tasks} />
          <SelectorDiagnostics diagnostics={browser.selectorDiagnostics} />
          <p className="settings-hint">校外访问通常需要先连接学校 VPN。程序只检查可达性，不绕过 VPN、登录、滑块或额度。</p>
        </article>

        <article className="panel dependencies-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">依赖锁定</span><h3>采集与文档组件</h3></div>
          </div>
          <div className="dependency-grid">
            {Object.entries(report?.dependencies || {}).map(([name, version]) => (
              <div key={name}>
                <span>{name}</span>
                <strong>{version || "缺失"}</strong>
              </div>
            ))}
            {!report && <div className="muted">等待诊断结果</div>}
            {report && Object.keys(report.dependencies || {}).length === 0 && (
              <div className="muted">未检测到依赖清单</div>
            )}
          </div>
          <div className="settings-path-list" data-testid="diagnostics-paths">
            <ExpandableValue label="数据目录" value={report?.paths.dataDirectory || appInfo?.dataDirectory || "未知"} />
            <ExpandableValue label="数据库" value={report?.paths.database || "未知"} />
            <ExpandableValue label="日志目录" value={report?.paths.logsDirectory || appInfo?.logsDirectory || "未知"} />
            <ExpandableValue label="下载临时目录" value={report?.paths.downloadDirectory || "未检测"} />
            <ExpandableValue label="系统临时目录" value={report?.paths.tempDirectory || "未知"} />
            <ExpandableValue label="引擎目录" value={appInfo?.engineDirectory || "未知"} />
            <ExpandableValue label="Python 路径" value={report?.engine.executable || appInfo?.pythonPath || "未知"} />
          </div>
        </article>

        <article className="panel settings-failure-panel" data-testid="settings-last-failure">
          <div className="panel-heading">
            <div><span className="eyebrow">最近失败</span><h3>阶段与恢复建议</h3></div>
          </div>
          {recentFailure ? (
            <TaskErrorCard
              error={recentFailure.error}
              status={recentFailure.status}
              currentStage={recentFailure.currentStage}
              checkpoint={recentFailure.checkpoint}
            />
          ) : (
            <p className="muted">最近没有带错误的任务。诊断信息缺失时不会显示为成功。</p>
          )}
        </article>

        <article className="panel logs-diagnostics-panel">
          <div className="panel-heading">
            <div><span className="eyebrow">日志</span><h3>最近记录</h3></div>
            <span className="live-label"><i /> LIVE</span>
          </div>
          <div className="log-list compact-log-list">
            {logs.slice(-12).reverse().map((record, index) => (
              <div className="log-row" key={`${record.timestamp}-${index}`}>
                <span className={`log-level ${record.level}`}>{record.level.toUpperCase()}</span>
                <span className="log-time">{record.timestamp.slice(11, 19)}</span>
                <span className="log-message">{record.message}</span>
              </div>
            ))}
            {logs.length === 0 && <div className="muted">尚无日志</div>}
          </div>
        </article>
      </div>

      <div className="notice browser-notice">
        <span className="notice-icon">i</span>
        <span>{notice}</span>
      </div>
    </section>
  );
}
