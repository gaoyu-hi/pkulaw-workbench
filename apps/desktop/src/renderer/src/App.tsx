import { useEffect, useMemo, useState } from "react";
import type {
  AppInfo,
  BackendState,
  DiagnosticsReport,
  EngineEvent,
  LogRecord,
  TaskRecord
} from "../../shared/protocol";
import BatchCollectorPanel from "./BatchCollectorPanel";
import SingleCollectorPanel from "./SingleCollectorPanel";
import DataPanel from "./DataPanel";
import TaskCenterPanel from "./TaskCenterPanel";
import SettingsPanel from "./SettingsPanel";
import InstitutionWorkspace from "./InstitutionWorkspace";
import {
  TASK_STATUS_LABELS,
  stageLabel
} from "./task-presentation";

const initialBackend: BackendState = {
  status: "starting",
  pid: null,
  protocolVersion: "1.0.0",
  engineVersion: "",
  message: "正在连接 Python 后端"
};

const navItems = [
  ["overview", "总览", "◫"],
  ["single", "逐篇采集", "▤"],
  ["batch", "批量采集", "▥"],
  ["tasks", "任务中心", "◷"],
  ["reports", "数据与报告", "▱"],
  ["settings", "设置与诊断", "⚙"]
] as const;

function formatTime(value: string): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function App(): React.JSX.Element {
  const [activeNav, setActiveNav] = useState<(typeof navItems)[number][0]>("overview");
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null);
  const [backend, setBackend] = useState<BackendState>(initialBackend);
  const [preflight, setPreflight] = useState<DiagnosticsReport | null>(null);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(
    "当前使用应用内网页和本地后端；任务、恢复与本机文件操作均不依赖 Agent。"
  );

  async function refreshTasks(): Promise<void> {
    const records = await window.pkulaw.listTasks(50);
    setTasks(records);
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      window.pkulaw.getAppInfo(),
      window.pkulaw.getBackendState(),
      window.pkulaw.getRecentLogs()
    ])
      .then(([info, state, recentLogs]) => {
        if (cancelled) return;
        setAppInfo(info);
        setBackend(state);
        setLogs(recentLogs);
      })
      .catch((error) => {
        if (!cancelled) setNotice(`初始化失败：${String(error)}`);
      });

    const unsubscribeState = window.pkulaw.onBackendState((state) => {
      setBackend(state);
    });
    const unsubscribeLog = window.pkulaw.onLog((record) => {
      setLogs((current) => [...current.slice(-199), record]);
    });
    const unsubscribeEvent = window.pkulaw.onEngineEvent((event: EngineEvent) => {
      if (event.event.startsWith("task.")) {
        void refreshTasks();
      }
    });

    return () => {
      cancelled = true;
      unsubscribeState();
      unsubscribeLog();
      unsubscribeEvent();
    };
  }, []);

  useEffect(() => {
    if (backend.status !== "ready") {
      return;
    }
    void refreshTasks().catch((error) => {
      setNotice(`读取任务失败：${String(error)}`);
    });
    if (!preflight) {
      void window.pkulaw
        .getDiagnostics()
        .then(setPreflight)
        .catch((error) => setNotice(`启动预检失败：${String(error)}`));
    }
  }, [backend.status]);

  useEffect(() => {
    if (appInfo?.stage6FixtureMode) {
      setActiveNav("settings");
    } else if (appInfo?.stage5FixtureMode) {
      setActiveNav("reports");
    } else if (appInfo?.stage4FixtureMode) {
      setActiveNav("batch");
    } else if (appInfo?.stage3FixtureMode) {
      setActiveNav("single");
    } else if (appInfo?.fixtureMode) {
      setActiveNav("overview");
    }
  }, [
    appInfo?.fixtureMode,
    appInfo?.stage3FixtureMode,
    appInfo?.stage4FixtureMode,
    appInfo?.stage5FixtureMode,
    appInfo?.stage6FixtureMode
  ]);

  const liveTasks = useMemo(
    () => tasks.filter((task) => !["completed", "failed", "stopped"].includes(task.status)),
    [tasks]
  );
  const activeLabel = navItems.find(([key]) => key === activeNav)?.[1] || "工作台总览";

  async function pingBackend(): Promise<void> {
    setBusy(true);
    try {
      const result = await window.pkulaw.pingBackend();
      setNotice(`协议连接正常，引擎版本 ${String(result.engineVersion || "未知")}。`);
    } catch (error) {
      setNotice(`连接检查失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">法</div>
          <div className="brand-copy">
            <strong>法宝工作台</strong>
            <span>Policy Collection Studio</span>
          </div>
        </div>
        <nav>
          {navItems.map(([key, label, icon]) => (
            <button
              type="button"
              className={activeNav === key ? "nav-item active" : "nav-item"}
              key={key}
              onClick={() => setActiveNav(key)}
            >
              <span className="nav-icon">{icon}</span>
              <span className="nav-label">{label}</span>
              {["single", "batch", "tasks"].includes(key) && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span>本地工作台</span>
          <strong>v{appInfo?.version || "0.2.6"}</strong>
        </div>
      </aside>

      <div className={`workspace workspace-${activeNav}`}>
        <header className="topbar">
          <div>
            <h1>{activeNav === "overview" ? "工作台总览" : activeLabel}</h1>
            <p>
              {activeNav === "single"
                  ? "检索、详情、逐条 Word 下载、同名消歧与断点"
                  : activeNav === "batch"
                    ? "分批目录、Word ZIP、完整性校验与失败隔离"
                  : activeNav === "tasks"
                    ? "统一筛选、逐条详情、失败原因、重试与恢复"
                  : activeNav === "reports"
                    ? "正文抽取、统一总目录、来源证据与任务报告"
                  : activeNav === "settings"
                    ? "启动预检、运行诊断、日志、路径与发布环境"
                : "运行在本机的政策采集与数据整理工作空间"}
            </p>
          </div>
          <div className="topbar-actions">
            <button type="button" className="secondary-button" onClick={() => void window.pkulaw.openDataDirectory()}>
              打开数据目录
            </button>
            <div className={`backend-pill ${backend.status}`}>
              <span className="status-light" />
              {backend.status === "ready" ? "引擎已连接" : backend.message}
            </div>
          </div>
        </header>

        <main>
          {activeNav === "single" ? (
            <SingleCollectorPanel
              appInfo={appInfo}
              backend={backend}
              onOpenBrowser={() => setActiveNav("overview")}
            />
          ) : activeNav === "batch" ? (
            <BatchCollectorPanel
              appInfo={appInfo}
              backend={backend}
              onOpenBrowser={() => setActiveNav("overview")}
            />
          ) : activeNav === "reports" ? (
            <DataPanel appInfo={appInfo} backend={backend} />
          ) : activeNav === "tasks" ? (
            <TaskCenterPanel appInfo={appInfo} backend={backend} />
          ) : activeNav === "settings" ? (
            <SettingsPanel appInfo={appInfo} backend={backend} />
          ) : (
            <>
          <InstitutionWorkspace
            appInfo={appInfo}
            backend={backend}
            onNavigate={setActiveNav}
          />

          <section className="status-grid">
            <article className="metric-card">
              <span className="metric-label">Python 引擎</span>
              <strong>{backend.status === "ready" ? "在线" : backend.status === "failed" ? "异常" : "连接中"}</strong>
              <small>{backend.engineVersion || "等待协议握手"}</small>
              <div className={`metric-bar ${backend.status}`} />
            </article>
            <article className="metric-card">
              <span className="metric-label">运行中任务</span>
              <strong>{liveTasks.length}</strong>
              <small>浏览器任务由人工显式启动</small>
              <div className="metric-bar idle" />
            </article>
            <article className="metric-card">
              <span className="metric-label">任务总数</span>
              <strong>{tasks.length}</strong>
              <small>保存在本地 SQLite</small>
              <div className="metric-bar tasks" />
            </article>
            <article className="metric-card">
              <span className="metric-label">协议版本</span>
              <strong>{backend.protocolVersion}</strong>
              <small>版本不兼容时拒绝握手</small>
              <div className="metric-bar protocol" />
            </article>
          </section>

          <div className="notice">
            <span className="notice-icon">i</span>
            <span>{notice}</span>
            <button type="button" className="text-button" onClick={() => void pingBackend()} disabled={busy}>
              检查连接
            </button>
          </div>

          <section className="content-grid overview-task-grid">
            <div className="panel task-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">任务中心</span>
                  <h3>最近任务</h3>
                </div>
                <button type="button" className="text-button" onClick={() => void refreshTasks()}>
                  刷新
                </button>
              </div>
              {tasks.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-mark">＋</div>
                  <strong>还没有任务</strong>
                  <p>创建一个演示任务，验证本地状态链路。</p>
                </div>
              ) : (
                <div className="task-list">
                  {tasks.slice(0, 6).map((task) => (
                    <article className="task-row" key={task.id}>
                      <div className={`task-mode ${task.mode}`}>
                        {task.mode === "single" ? "逐篇" : "批量"}
                      </div>
                      <div className="task-main">
                        <strong>{task.name}</strong>
                        <span>
                          {stageLabel(task.currentStage, String(task.checkpoint.currentAction || ""))} ·{" "}
                          {formatTime(task.createdAt)}
                        </span>
                      </div>
                      <span className={`task-status ${task.status}`}>
                        {TASK_STATUS_LABELS[task.status]}
                      </span>
                    </article>
                  ))}
                </div>
              )}
            </div>

          </section>

          <section className="content-grid bottom-grid">
            <div className="panel log-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">诊断</span>
                  <h3>最近事件</h3>
                </div>
                <span className="live-label"><i /> LIVE</span>
              </div>
              <div className="log-list">
                {logs.slice(-7).reverse().map((record, index) => (
                  <div className="log-row" key={`${record.timestamp}-${index}`}>
                    <span className={`log-level ${record.level}`}>{record.level.toUpperCase()}</span>
                    <span className="log-time">{formatTime(record.timestamp)}</span>
                    <span className="log-message">{record.message}</span>
                  </div>
                ))}
                {logs.length === 0 && <div className="muted">等待日志事件…</div>}
              </div>
            </div>

            <aside className="panel environment-panel">
              <span className="eyebrow">运行环境</span>
              <dl>
                <div><dt>Electron</dt><dd>{appInfo?.electronVersion || "—"}</dd></div>
                <div><dt>Chromium</dt><dd>{appInfo?.chromeVersion || "—"}</dd></div>
                <div><dt>Node</dt><dd>{appInfo?.nodeVersion || "—"}</dd></div>
                <div><dt>Python PID</dt><dd>{backend.pid || "—"}</dd></div>
                <div><dt>后端模式</dt><dd>{appInfo?.backendMode === "bundled" ? "内置运行时" : "源码 Python"}</dd></div>
                <div><dt>安装状态</dt><dd>{appInfo?.packaged ? "发布包" : "开发目录"}</dd></div>
                <div><dt>启动预检</dt><dd>{preflight ? (preflight.checks.some((check) => check.status === "error") ? "存在异常" : "通过") : "检查中"}</dd></div>
              </dl>
              <div className="data-path" title={appInfo?.dataDirectory}>
                {appInfo?.dataDirectory || "正在读取数据目录…"}
              </div>
            </aside>
          </section>
            </>
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
