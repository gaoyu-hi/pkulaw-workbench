import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AppInfo,
  BackendState,
  BrowserHostState,
  EngineEvent,
  PageSelection,
  TaskDetails,
  TaskItemCandidate,
  TaskItemRecord,
  TaskPreviewSummary,
  TaskRecord,
  YearSelection
} from "../../shared/protocol";
import YearRangeControl, {
  yearSelectionPayload
} from "./YearRangeControl";
import YearStatsTable from "./YearStatsTable";
import PageLimitControl, {
  pageSelectionPayload
} from "./PageLimitControl";
import { PageLimitDecision } from "./TaskDecisionPanel";
import { TaskPreviewCard } from "./TaskPreviewCard";
import {
  holdingTaskLabel,
  leaseBlocksNewBrowserTask,
  newOperationId
} from "../../shared/browser-lease";
import {
  BrowserLeaseCard,
  ExpandableValue,
  ScanContinuitySummary,
  TaskRuntimePanel
} from "./TaskRuntimePanel";
import { useTaskRuntimeEvents } from "./useTaskRuntimeEvents";
import {
  TASK_STATUS_LABELS,
  collectionActions,
  progressSnapshotView,
  retryActionLabel,
  stageLabel
} from "./task-presentation";
import {
  canStartCollector,
  collectorFormKey,
  defaultCollectorMatchMode
} from "./collector-workflow";
import { usePersistentState } from "./usePersistentState";

interface SingleCollectorPanelProps {
  appInfo: AppInfo | null;
  backend: BackendState;
  onOpenBrowser: () => void;
}

const itemStatusText: Record<string, string> = {
  pending: "待处理",
  running: "处理中",
  verifying: "校验中",
  waiting_manual: "待选择",
  pending_review: "待核验",
  completed: "已完成",
  failed: "失败",
  skipped: "已跳过"
};

const PROVINCES = [
  "北京市", "天津市", "河北省", "山西省", "内蒙古自治区", "辽宁省", "吉林省",
  "黑龙江省", "上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省",
  "山东省", "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区", "海南省",
  "重庆市", "四川省", "贵州省", "云南省", "西藏自治区", "陕西省", "甘肃省",
  "青海省", "宁夏回族自治区", "新疆维吾尔自治区"
];

function formatTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function candidateKey(candidate: TaskItemCandidate): string {
  return String(candidate.policyId || candidate.url || "");
}

export default function SingleCollectorPanel({
  appInfo,
  backend,
  onOpenBrowser
}: SingleCollectorPanelProps): React.JSX.Element {
  const [queries, setQueries] = usePersistentState(
    "pkulaw.collector.single.v1.queries",
    ""
  );
  const [matchMode, setMatchMode] = usePersistentState<"keyword" | "title">(
    "pkulaw.collector.single.v1.matchMode",
    defaultCollectorMatchMode("single")
  );
  const [library, setLibrary] = usePersistentState<
    "central" | "local" | "both"
  >("pkulaw.collector.single.v1.library", "both");
  const [yearSelection, setYearSelection] =
    usePersistentState<YearSelection>(
      "pkulaw.collector.single.v1.yearSelection",
      { mode: "all" }
    );
  const [province, setProvince] = usePersistentState(
    "pkulaw.collector.single.v1.province",
    "全部省份"
  );
  const [timeliness, setTimeliness] = usePersistentState(
    "pkulaw.collector.single.v1.timeliness",
    "全部（仅记录元数据，不筛选）"
  );
  const [pageSize, setPageSize] = usePersistentState(
    "pkulaw.collector.single.v1.pageSize",
    20
  );
  const [pageSelection, setPageSelection] =
    usePersistentState<PageSelection>(
      "pkulaw.collector.single.v1.pageSelection",
      { mode: "last" }
    );
  const [outputDir, setOutputDir] = usePersistentState(
    "pkulaw.collector.single.v1.outputDir",
    ""
  );
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [details, setDetails] = useState<TaskDetails | null>(null);
  const [browser, setBrowser] = useState<BrowserHostState | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [preview, setPreview] = useState<TaskPreviewSummary | null>(null);
  const [previewFormKey, setPreviewFormKey] = useState("");
  const startInFlightRef = useRef(false);
  const [notice, setNotice] = useState(
    "任务会使用当前内置浏览器，不保存账号密码，不绕过登录、滑块或机构额度。"
  );

  useEffect(() => {
    if (appInfo?.defaultSingleOutputDir) {
      setOutputDir(
        (current) => current.trim() || appInfo.defaultSingleOutputDir
      );
    }
  }, [appInfo?.defaultSingleOutputDir]);

  async function refreshTaskList(): Promise<TaskRecord[]> {
    const records = (await window.pkulaw.listTasks(100)).filter(
      (task) => task.mode === "single"
    );
    setTasks(records);
    return records;
  }

  async function loadDetails(taskId: string): Promise<void> {
    if (!taskId) {
      setDetails(null);
      return;
    }
    const next = await window.pkulaw.getTaskDetails(taskId);
    setDetails(next);
    setSelectedTaskId(taskId);
  }

  useEffect(() => {
    if (backend.status !== "ready") {
      return;
    }
    let cancelled = false;
    void refreshTaskList()
      .then(async (records) => {
        if (cancelled) return;
        const preferred = records[0];
        if (preferred) {
          await loadDetails(preferred.id);
        }
      })
      .catch((error) => setNotice(`读取逐篇任务失败：${String(error)}`));
    return () => {
      cancelled = true;
    };
  }, [backend.status]);

  useEffect(() => {
    if (backend.status !== "ready") return;
    let cancelled = false;
    void window.pkulaw
      .getBrowserState()
      .then((state) => {
        if (!cancelled) setBrowser(state);
      })
      .catch(() => undefined);
    const unsubscribe = window.pkulaw.onBrowserState((state) => {
      if (!cancelled) setBrowser(state);
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [backend.status]);

  useEffect(() => {
    const unsubscribe = window.pkulaw.onEngineEvent((event: EngineEvent) => {
      if (backend.status !== "ready") {
        return;
      }
      if (
        event.event.startsWith("single.") ||
        event.event.startsWith("task.")
      ) {
        void refreshTaskList().catch(() => undefined);
        const eventData = event.data as Record<string, unknown>;
        const eventTaskId = String(
          eventData.taskId ||
          (eventData.task && typeof eventData.task === "object"
            ? (eventData.task as Record<string, unknown>).id
            : "") ||
          ""
        );
        if (eventTaskId) {
          void loadDetails(eventTaskId).catch(() => undefined);
        } else if (selectedTaskId) {
          void loadDetails(selectedTaskId).catch(() => undefined);
        }
      }
    });
    return unsubscribe;
  }, [selectedTaskId, backend.status]);

  useEffect(() => {
    if (
      !selectedTaskId ||
      !details ||
      backend.status !== "ready" ||
      ![
        "preflight",
        "running",
        "waiting_login",
        "waiting_manual",
        "verifying",
        "pausing",
        "paused",
        "stopping"
      ].includes(
        details.task.status
      )
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadDetails(selectedTaskId).catch(() => undefined);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [selectedTaskId, details?.task.status, backend.status]);

  useEffect(() => {
    if (!selectedTaskId && tasks[0]) {
      void loadDetails(tasks[0].id).catch(() => undefined);
    }
  }, [selectedTaskId, tasks]);

  const waitingItem = useMemo(
    () =>
      details?.items.find(
        (item) =>
          item.status === "waiting_manual" &&
          Array.isArray(item.payload.candidates) &&
          item.payload.candidates.length > 1
      ) || null,
    [details]
  );

  const completedCount =
    details?.items.filter((item) => item.status === "completed").length || 0;
  const totalItems = details?.items.length || 0;
  const progressView = details
    ? progressSnapshotView({
        status: details.task.status,
        progress: details.task.progress,
        currentStage: details.task.currentStage,
        progressSnapshot: details.task.progressSnapshot,
        completed: completedCount,
        total: totalItems
      })
    : null;
  const failedItems =
    details?.items.filter((item) => item.status === "failed").length || 0;
  const pageLimitWaiting =
    details?.task.status === "waiting_manual" &&
    details.task.error?.code === "PAGE_LIMIT_REACHED";
  const browserLeaseBlocked = leaseBlocksNewBrowserTask(browser);
  const actionPolicy = collectionActions(details?.task || null, {
    candidateWaiting: Boolean(waitingItem),
    failedItems
  });
  const retryLabel = retryActionLabel(details?.task || null);
  const runtimeAlerts = useTaskRuntimeEvents(
    details?.task.id || selectedTaskId
  );
  const previewInput = useMemo(() => {
    const lines = queries
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean);
    return {
      queries: lines,
      keywords: lines,
      matchMode,
      library,
      ...yearSelectionPayload(yearSelection),
      province,
      timeliness,
      pageSize,
      ...pageSelectionPayload(pageSelection)
    };
  }, [
    queries,
    matchMode,
    library,
    yearSelection,
    province,
    timeliness,
    pageSize,
    pageSelection
  ]);
  const currentPreviewFormKey = collectorFormKey("single", previewInput);
  const previewFresh =
    Boolean(preview) && previewFormKey === currentPreviewFormKey;
  const startAllowed = canStartCollector({
    preview,
    previewFormKey,
    currentFormKey: currentPreviewFormKey,
    busy: busy || previewBusy,
    backendReady: backend.status === "ready",
    browserLeaseBlocked
  });

  async function previewCollector(): Promise<void> {
    if (previewBusy) return;
    if (!previewInput.queries.length) {
      setNotice(
        matchMode === "title"
          ? "精确完整标题模式至少需要一条精确完整标题。"
          : "关键词模式至少需要一个关键词。"
      );
      return;
    }
    if (
      yearSelection.mode === "range" &&
      yearSelection.startYear > yearSelection.endYear
    ) {
      setNotice("起始年份不能晚于结束年份。");
      return;
    }
    setPreviewBusy(true);
    try {
      const browserState = await window.pkulaw.getBrowserState();
      setBrowser(browserState);
      if (
        !browserState.targetBound ||
        !browserState.authenticated ||
        !browserState.selectorReady
      ) {
        setPreview(null);
        setNotice(
          `预览门禁未通过：${browserState.selectorMessage || "请先打开已登录的北大法宝检索页"}`
        );
        return;
      }
      if (leaseBlocksNewBrowserTask(browserState)) {
        setPreview(null);
        setNotice(holdingTaskLabel(browserState, tasks));
        return;
      }
      const result = await window.pkulaw.previewTask(
        "single",
        previewInput
      );
      setPreview(result);
      setPreviewFormKey(currentPreviewFormKey);
      setNotice(
        `预览完成：${result.combinationCount} 个组合，${result.totalResults} 条结果，${result.totalPages} 页，下载 0。${
          result.windowStatus === "probe_required"
            ? "检测到结果窗口限制，启动后会先进行 0 下载探测。"
            : ""
        }`
      );
    } catch (error) {
      setPreview(null);
      setPreviewFormKey("");
      setNotice(`预览失败，未启动任务：${String(error)}`);
    } finally {
      setPreviewBusy(false);
    }
  }

  async function createAndStart(): Promise<void> {
    const lines = queries
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (!lines.length) {
      setNotice("请至少输入一个关键词或精确完整标题。");
      return;
    }
    if (!previewFresh || !preview) {
      setNotice("请先完成 0 下载预览；修改筛选或检索条件后需要重新预览。");
      return;
    }
    if (!outputDir.trim()) {
      setNotice("请指定输出目录。");
      return;
    }
    if (
      yearSelection.mode === "range" &&
      yearSelection.startYear > yearSelection.endYear
    ) {
      setNotice("起始年份不能晚于结束年份。");
      return;
    }
    if (startInFlightRef.current) return;
    startInFlightRef.current = true;
    setBusy(true);
    const operationId = newOperationId();
    try {
      const browserState = await window.pkulaw.getBrowserState();
      setBrowser(browserState);
      if (
        !browserState.targetBound ||
        !browserState.authenticated ||
        !browserState.selectorReady
      ) {
        setNotice(
          `任务启动门禁未通过：${browserState.selectorMessage || "请先打开已登录的北大法宝检索页"}`
        );
        onOpenBrowser();
        return;
      }
      if (leaseBlocksNewBrowserTask(browserState)) {
        setNotice(holdingTaskLabel(browserState, tasks));
        return;
      }
      const task = await window.pkulaw.createTask({
        name: `逐篇采集 ${new Date().toLocaleString("zh-CN")}`,
        mode: "single",
        outputDir: outputDir.trim(),
        input: {
          queries: lines,
          keywords: lines,
          matchMode,
          library,
          ...yearSelectionPayload(yearSelection),
          province,
          timeliness,
          pageSize,
          ...pageSelectionPayload(pageSelection),
          outputDir: outputDir.trim(),
          previewSnapshot: preview
        },
        operationId
      });
      const started = await window.pkulaw.startSingleTask(task.id, operationId);
      setSelectedTaskId(task.id);
      setDetails(started);
      await refreshTaskList();
      setNotice("逐篇任务已启动。任务暂停时可直接在下方处理。");
    } catch (error) {
      setNotice(`启动逐篇任务失败：${String(error)}`);
    } finally {
      startInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function control(
    action:
      | "pause"
      | "resume"
      | "stop"
      | "continue"
      | "continueToLast"
      | "retry"
  ): Promise<void> {
    if (!selectedTaskId) return;
    setBusy(true);
    try {
      if (action === "pause") {
        setDetails(await window.pkulaw.pauseSingleTask(selectedTaskId));
        setNotice("已请求暂停，将在当前检索、读取或下载步骤完成后生效。");
      } else if (action === "resume") {
        setDetails(
          await window.pkulaw.resumeSingleTask(
            selectedTaskId,
            newOperationId()
          )
        );
        setNotice("浏览器、登录和选择器已重新核验，任务从原断点继续。");
      } else if (action === "stop") {
        setDetails(await window.pkulaw.stopSingleTask(selectedTaskId));
        setNotice("已发送停止请求，已完成记录和断点会保留。");
      } else if (action === "retry") {
        setDetails(
          await window.pkulaw.retrySingleTask(
            selectedTaskId,
            newOperationId()
          )
        );
        setNotice(
          details?.task.checkpoint.scanCompleted === true
            ? "扫描已完成，只重试未完成或失败项，不会从第 1 页重新扫描。"
            : "已从扫描日志断点继续；只会重新建立一次检索会话并跳过已完成页。"
        );
      } else if (action === "continueToLast") {
        setDetails(await window.pkulaw.continueSingleToLast(selectedTaskId));
        setNotice("已确认扫描当前检索组合到尾页，分页模式将持久保存。");
      } else {
        if (
          details?.task.status === "waiting_manual" &&
          browser?.captchaVisible
        ) {
          try {
            setBrowser(await window.pkulaw.browserContinue());
          } catch (error) {
            setNotice(`验证码尚未清除：${String(error)}`);
            return;
          }
        }
        setDetails(await window.pkulaw.continueSingleTask(selectedTaskId));
        setNotice("人工处理已确认，任务继续。");
      }
      await refreshTaskList();
    } catch (error) {
      setNotice(`任务操作失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function choose(item: TaskItemRecord, candidate: TaskItemCandidate): Promise<void> {
    if (!selectedTaskId) return;
    setBusy(true);
    try {
      const next = await window.pkulaw.chooseCandidate(
        selectedTaskId,
        item.id,
        candidateKey(candidate)
      );
      setDetails(next);
      setNotice(`已选择“${candidate.title}”的 ${candidate.publicationDate || "未知日期"} 版本。`);
      await refreshTaskList();
    } catch (error) {
      setNotice(`选择同名版本失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="single-workspace">
      <div className="single-layout">
        <aside className="single-form-card">
          <span className="eyebrow">逐篇采集</span>
          <h2>创建采集任务</h2>
          <p className="single-intro">
            默认使用全文/关键词字段；“精确完整标题”只用于逐篇任务并核对同名版本。
          </p>
          <p className="collector-start-hint">
            关键词模式下，同名但稳定政策 ID 不同的政策会分别保留，不会要求二选一；只有精确完整标题模式才进行同名版本选择。
          </p>
          <label>
            {matchMode === "title"
              ? "精确完整标题（每行一个）"
              : "检索关键词（全文/关键词字段，每行一个）"}
            <textarea
              value={queries}
              onChange={(event) => setQueries(event.target.value)}
              rows={4}
            />
          </label>
          <div className="form-columns">
            <label>
              匹配方式
              <select
                value={matchMode}
                onChange={(event) => setMatchMode(event.target.value as "keyword" | "title")}
              >
                <option value="keyword">关键词</option>
                <option value="title">精确完整标题（仅逐篇）</option>
              </select>
            </label>
            <label>
              法规子库
              <select
                value={library}
                onChange={(event) =>
                  setLibrary(event.target.value as "central" | "local" | "both")
                }
              >
                <option value="both">中央 + 地方</option>
                <option value="central">中央法规</option>
                <option value="local">地方法规</option>
              </select>
            </label>
          </div>
          <div className="form-columns year-and-province">
            <YearRangeControl
              value={yearSelection}
              onChange={setYearSelection}
            />
            <label>
              省份
              <select value={province} onChange={(event) => setProvince(event.target.value)}>
                <option>全部省份</option>
                {PROVINCES.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
          </div>
          <label>
            时效性
            <select
              value={timeliness}
              onChange={(event) => setTimeliness(event.target.value)}
            >
              <option>全部（仅记录元数据，不筛选）</option>
              <option>现行有效</option>
              <option>已被修改</option>
              <option>已废止</option>
            </select>
          </label>
          <div className="form-columns">
            <label>
              每页条数
              <select
                value={pageSize}
                onChange={(event) => setPageSize(Number(event.target.value))}
              >
                {[10, 20, 50, 100].map((value) => (
                  <option key={value} value={value}>{value}</option>
                ))}
              </select>
            </label>
          </div>
          <PageLimitControl
            value={pageSelection}
            onChange={setPageSelection}
          />
          <button
            type="button"
            className="secondary-button collector-preview-button"
            disabled={
              busy ||
              previewBusy ||
              backend.status !== "ready" ||
              browserLeaseBlocked
            }
            onClick={() => void previewCollector()}
            data-testid="collector-preview-button"
          >
            {previewBusy ? "正在预览…" : "预览（0 下载）"}
          </button>
          <TaskPreviewCard preview={preview} fresh={previewFresh} />
          {!previewFresh && (
            <p className="collector-start-hint">
              请先完成 0 下载预览；修改检索、筛选、页大小或分页后需重新预览。
            </p>
          )}
          <label>
            输出目录
            <div className="path-input-row">
              <input value={outputDir} onChange={(event) => setOutputDir(event.target.value)} />
              <button
                type="button"
                className="secondary-button compact"
                onClick={() =>
                  void window.pkulaw
                    .selectOutputDirectory(outputDir)
                    .then((selected) => selected && setOutputDir(selected))
                }
              >
                浏览
              </button>
            </div>
          </label>
          <button
            type="button"
            className="primary-button single-start"
            disabled={busy || previewBusy || !startAllowed}
            onClick={() => void createAndStart()}
          >
            创建并开始逐篇采集
          </button>
          {browser && (
            <div data-testid="browser-lease-note">
              <BrowserLeaseCard
                browser={browser}
                tasks={tasks}
                compact
              />
            </div>
          )}
          <button type="button" className="secondary-button single-browser" onClick={onOpenBrowser}>
            打开内置浏览器处理登录
          </button>
          <div className="single-fixture-note">
            <strong>{appInfo?.stage3FixtureMode ? "阶段 3 本地夹具" : "正式机构入口"}</strong>
            <span>不保存账号密码；遇到滑块和同名版本会暂停。</span>
          </div>
        </aside>

        <div className="single-main">
          <div className="single-task-list">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">任务记录</span>
                <h3>逐篇任务</h3>
              </div>
              <button type="button" className="text-button" onClick={() => void refreshTaskList()}>
                刷新
              </button>
            </div>
            <div className="single-task-tabs">
              {tasks.slice(0, 8).map((task) => (
                <button
                  type="button"
                  key={task.id}
                  className={selectedTaskId === task.id ? "selected" : ""}
                  onClick={() => void loadDetails(task.id)}
                >
                  <span>{task.name}</span>
                  <small>{TASK_STATUS_LABELS[task.status]}</small>
                </button>
              ))}
              {tasks.length === 0 && <div className="muted">暂无逐篇任务</div>}
            </div>
          </div>

          {details ? (
            <div className="single-run-card">
              <div className="single-run-heading">
                <div>
                  <span className={`task-status ${details.task.status}`}>
                    {TASK_STATUS_LABELS[details.task.status]}
                  </span>
                  <h2>{details.task.name}</h2>
                  <p>
                    {stageLabel(
                      details.task.currentStage,
                      String(details.task.checkpoint.currentAction || "")
                    )} ·{" "}
                    {formatTime(details.task.updatedAt)}
                  </p>
                </div>
                <div className="single-run-actions">
                  {actionPolicy.pause && (
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={busy}
                      onClick={() => void control("pause")}
                    >
                      暂停
                    </button>
                  )}
                  {["pausing", "paused"].includes(details.task.status) && (
                    <span className="task-control-note">
                      {details.task.status === "pausing" ? "正在等待安全步骤结束" : "已暂停"}
                    </span>
                  )}
                  {actionPolicy.resume && (
                    <button
                      type="button"
                      className="primary-button compact"
                      disabled={busy}
                      onClick={() => void control("resume")}
                    >
                      继续
                    </button>
                  )}
                  {actionPolicy.stop && (
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={busy}
                      onClick={() => void control("stop")}
                    >
                      {details.task.status === "stopping" ? "正在停止" : "停止"}
                    </button>
                  )}
                  {actionPolicy.continueToLast && (
                    <button
                      type="button"
                      className="primary-button compact"
                      disabled={busy}
                      onClick={() => void control("continueToLast")}
                    >
                      继续到尾页
                    </button>
                  )}
                  {actionPolicy.continue && (
                    <button
                      type="button"
                      className="primary-button compact"
                      disabled={busy || Boolean(browser?.captchaVisible)}
                      onClick={() => void control("continue")}
                    >
                      已处理，继续
                    </button>
                  )}
                  {actionPolicy.retry && (
                    <button
                      type="button"
                      className="primary-button compact"
                      disabled={busy}
                      onClick={() => void control("retry")}
                    >
                      {retryLabel}
                    </button>
                  )}
                  {details.task.status === "stopping" && (
                    <span className="task-control-note">正在等待当前安全步骤结束</span>
                  )}
                </div>
              </div>

              <div className="single-progress">
                <div>
                  <span style={{ width: `${progressView?.percent || 0}%` }} />
                </div>
                <strong>{progressView?.percent || 0}%</strong>
                <small>{completedCount}/{totalItems} 条</small>
              </div>

              <YearStatsTable stats={details.task.checkpoint.yearStats} />
              <ScanContinuitySummary checkpoint={details.task.checkpoint} />

              {pageLimitWaiting && (
                <PageLimitDecision checkpoint={details.task.checkpoint} />
              )}

              <TaskRuntimePanel
                task={details.task}
                completed={completedCount}
                total={totalItems}
                candidateWaiting={Boolean(waitingItem)}
                browserCaptchaVisible={Boolean(browser?.captchaVisible)}
                browserCaptchaCheckedAt={browser?.captchaCheckedAt}
                browserCaptchaSelector={browser?.captchaSelector}
                alerts={runtimeAlerts}
              />

              {waitingItem && (
                <CandidateSelection
                  item={waitingItem}
                  busy={busy}
                  onChoose={(candidate) => void choose(waitingItem, candidate)}
                />
              )}

              <div className="single-items">
                <div className="single-items-head">
                  <span>政策</span>
                  <span>候选</span>
                  <span>状态</span>
                  <span>文件</span>
                </div>
                {details.items.map((item) => (
                  <div className="single-item-row" key={item.id}>
                    <div>
                      <strong>{item.title || "待识别政策"}</strong>
                      <small>{stageLabel(item.currentStage)}</small>
                      {item.error && (
                        <ExpandableValue
                          label="条目错误"
                          value={`${item.error.code}: ${item.error.message}`}
                        />
                      )}
                      {item.payload.downloadedFile && (
                        <ExpandableValue
                          label="Word 文件"
                          value={String(item.payload.downloadedFile)}
                        />
                      )}
                    </div>
                    <span>{item.payload.candidates?.length || 0}</span>
                    <span className={`item-state ${item.status}`}>
                      {itemStatusText[item.status] || item.status}
                    </span>
                    <span className="single-file" title={item.payload.downloadedFile || ""}>
                      {item.payload.downloadedFile
                        ? String(item.payload.downloadedFile).split(/[\\/]/).pop()
                        : "—"}
                    </span>
                  </div>
                ))}
                {details.items.length === 0 && (
                  <div className="muted">正在检索，尚未生成政策条目…</div>
                )}
              </div>
            </div>
          ) : (
            <div className="single-empty">
              <div className="single-empty-mark">稿</div>
              <h3>准备第一条逐篇任务</h3>
              <p>任务会在这里显示检索页码、候选版本、Word 文件和断点状态。</p>
            </div>
          )}

          <div className="notice browser-notice">
            <span className="notice-icon">i</span>
            <span>{notice}</span>
          </div>
        </div>
      </div>
    </section>
  );
}

function CandidateSelection({
  item,
  busy,
  onChoose
}: {
  item: TaskItemRecord;
  busy: boolean;
  onChoose: (candidate: TaskItemCandidate) => void;
}): React.JSX.Element {
  const candidates = item.payload.candidates || [];
  const [selected, setSelected] = useState(candidateKey(candidates[0]));
  return (
    <div className="candidate-panel">
      <div>
        <span className="eyebrow">需要人工消歧</span>
        <h3>“{item.title}”存在 {candidates.length} 个同名版本</h3>
        <p>系统不会自动取第一条，请根据公布日期、文号和制定机关选择。</p>
      </div>
      <div className="candidate-list">
        {candidates.map((candidate) => (
          <label key={candidateKey(candidate)} className="candidate-option">
            <input
              type="radio"
              checked={selected === candidateKey(candidate)}
              onChange={() => setSelected(candidateKey(candidate))}
            />
            <span>
              <strong>{candidate.issuingAuthority || "未识别机关"}</strong>
              <small>
                {candidate.publicationDate || "日期未识别"} ·{" "}
                {candidate.documentNumber || "文号未识别"} ·{" "}
                {candidate.validity || "时效性未识别"}
              </small>
            </span>
          </label>
        ))}
      </div>
      <button
        type="button"
        className="primary-button compact"
        disabled={busy}
        onClick={() => {
          const chosen = candidates.find((candidate) => candidateKey(candidate) === selected);
          if (chosen) onChoose(chosen);
        }}
      >
        采用所选版本
      </button>
    </div>
  );
}
