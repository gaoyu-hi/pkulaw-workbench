import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AppInfo,
  BackendState,
  BatchRecord,
  BatchTaskDetails,
  BrowserHostState,
  CollectorMatchMode,
  EngineEvent,
  PageSelection,
  TaskRecord,
  TaskSettingsRevisionPreview,
  YearSelection
} from "../../shared/protocol";
import YearRangeControl, {
  yearSelectionPayload
} from "./YearRangeControl";
import YearStatsTable from "./YearStatsTable";
import PageLimitControl, {
  pageSelectionPayload
} from "./PageLimitControl";
import {
  PageLimitDecision,
  ScaleDecisionPanel,
  ScaleEstimateSummary
} from "./TaskDecisionPanel";
import {
  holdingTaskLabel,
  leaseBlocksNewBrowserTask,
  newOperationId
} from "../../shared/browser-lease";
import {
  CATALOG_FORMAT_LABELS,
  catalogFormatLabel
} from "../../shared/catalog-formats";
import {
  BrowserLeaseCard,
  ExpandableValue,
  ScanContinuitySummary,
  TaskRuntimePanel,
  type TaskActionFeedback
} from "./TaskRuntimePanel";
import { useTaskRuntimeEvents } from "./useTaskRuntimeEvents";
import {
  TASK_STATUS_LABELS,
  collectionActions,
  derivePrimaryTaskAction,
  progressSnapshotView,
  retryActionLabel,
  secondaryStopLabel,
  stageLabel
} from "./task-presentation";
import {
  defaultCollectorMatchMode
} from "./collector-workflow";
import {
  effectiveProvince,
  showsProvinceControl
} from "./collector-library-policy";
import {
  collectorSearchModeOptions,
  migrateCollectorQueryDraft,
  normalizeCollectorMatchMode,
  searchModeDisplayLabel
} from "./collector-search-mode";
import { usePersistentState } from "./usePersistentState";
import TaskWorkspaceFrame from "./TaskWorkspaceFrame";
import { useManualRecovery } from "./useManualRecovery";
import TaskSettingsPanel from "./TaskSettingsPanel";
import TaskSettingsRevisionDialog, {
  type ArtifactDisposition
} from "./TaskSettingsRevisionDialog";
import {
  canOpenTaskSettings,
  prefillBatchCollectorForm,
  resetBatchCollectorForm,
  useCollectorAutoLoad
} from "./collector-reset";

interface BatchCollectorPanelProps {
  appInfo: AppInfo | null;
  backend: BackendState;
  onOpenBrowser: () => void;
}

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

export default function BatchCollectorPanel({
  appInfo,
  backend,
  onOpenBrowser
}: BatchCollectorPanelProps): React.JSX.Element {
  const [queries, setQueries] = usePersistentState(
    "pkulaw.collector.batch.v1.queries",
    "",
    migrateCollectorQueryDraft
  );
  const [matchMode, setMatchMode] = usePersistentState<CollectorMatchMode>(
    "pkulaw.collector.batch.v1.matchMode",
    defaultCollectorMatchMode("batch"),
    (stored) => normalizeCollectorMatchMode("batch", stored)
  );
  const [library, setLibrary] = usePersistentState<
    "central" | "local" | "both"
  >("pkulaw.collector.batch.v1.library", "central");
  const [yearSelection, setYearSelection] =
    usePersistentState<YearSelection>(
      "pkulaw.collector.batch.v1.yearSelection",
      { mode: "all" }
    );
  const [province, setProvince] = usePersistentState(
    "pkulaw.collector.batch.v1.province",
    "全部省份"
  );
  const [timeliness, setTimeliness] = usePersistentState(
    "pkulaw.collector.batch.v1.timeliness",
    "全部（仅记录元数据，不筛选）"
  );
  const [pageSize, setPageSize] = usePersistentState(
    "pkulaw.collector.batch.v1.pageSize",
    50
  );
  const [pageSelection, setPageSelection] =
    usePersistentState<PageSelection>(
      "pkulaw.collector.batch.v1.pageSelection",
      { mode: "last" }
    );
  const [catalogFormat, setCatalogFormat] = usePersistentState<
    "xlsx" | "xls" | "html-xls"
  >("pkulaw.collector.batch.v1.catalogFormat", "xlsx");
  const [outputDir, setOutputDir] = usePersistentState(
    "pkulaw.collector.batch.v1.outputDir",
    ""
  );
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [details, setDetails] = useState<BatchTaskDetails | null>(null);
  const [browser, setBrowser] = useState<BrowserHostState | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionFeedback, setActionFeedback] =
    useState<TaskActionFeedback | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsRevision, setSettingsRevision] =
    useState<TaskSettingsRevisionPreview | null>(null);
  const [settingsCommitError, setSettingsCommitError] =
    useState<string | null>(null);
  const [pauseDetailOpen, setPauseDetailOpen] = useState(false);
  const startInFlightRef = useRef(false);
  const settingsInFlightRef = useRef(false);
  const taskActionInFlightRef = useRef(false);
  const autoLoad = useCollectorAutoLoad();
  const [notice, setNotice] = useState(
    "批量任务会先确认页数和规模，再使用当前内置浏览器执行受控下载。"
  );

  useEffect(() => {
    if (appInfo?.defaultBatchOutputDir) {
      setOutputDir(
        (current) => current.trim() || appInfo.defaultBatchOutputDir
      );
    }
  }, [appInfo?.defaultBatchOutputDir]);

  async function refreshTaskList(): Promise<TaskRecord[]> {
    const records = (await window.pkulaw.listTasks(100)).filter(
      (task) => task.mode === "batch"
    );
    setTasks(records);
    return records;
  }

  async function loadDetails(taskId: string): Promise<void> {
    if (!taskId) {
      setDetails(null);
      return;
    }
    const next = await window.pkulaw.getBatchTaskDetails(taskId);
    setDetails(next);
    setSelectedTaskId(taskId);
  }

  function selectTask(taskId: string): void {
    autoLoad.clearForExplicitSelection();
    void loadDetails(taskId);
  }

  useEffect(() => {
    if (backend.status !== "ready") {
      return;
    }
    let cancelled = false;
    void refreshTaskList()
      .then(async (records) => {
        if (!cancelled && records[0]) {
          await loadDetails(records[0].id);
        }
      })
      .catch((error) => setNotice(`读取批量任务失败：${String(error)}`));
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
      if (backend.status !== "ready") return;
      if (event.event.startsWith("batch.") || event.event.startsWith("task.")) {
        void refreshTaskList().catch(() => undefined);
        const data = event.data as Record<string, unknown>;
        const taskId = String(
          data.taskId ||
          (data.task && typeof data.task === "object"
            ? (data.task as Record<string, unknown>).id
            : "") ||
          ""
        );
        const automaticTaskId = taskId || selectedTaskId;
        if (autoLoad.canLoadTaskDetails(automaticTaskId)) {
          void loadDetails(automaticTaskId).catch(() => undefined);
        }
      }
    });
    return unsubscribe;
  }, [backend.status, selectedTaskId]);

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
    if (selectedTaskId || !tasks[0]) return;
    if (!autoLoad.canAutoSelectTask(tasks[0].id)) {
      return;
    }
    void loadDetails(tasks[0].id).catch(() => undefined);
  }, [selectedTaskId, tasks]);

  useEffect(() => {
    if (!details) return;
    const taskId = details.task.id;
    const currentStatus = details.task.status;
    if (!autoLoad.observeStatus(taskId, currentStatus)) return;
    resetBatchCollectorForm(
      appInfo?.defaultBatchOutputDir || "",
      {
        setQueries,
        setMatchMode,
        setLibrary,
        setYearSelection,
        setProvince,
        setTimeliness,
        setPageSize,
        setPageSelection,
        setCatalogFormat,
        setOutputDir
      }
    );
    setSelectedTaskId("");
    setDetails(null);
    setSettingsOpen(false);
    setPauseDetailOpen(false);
    setNotice("任务已完成，可创建新的批量采集任务。");
  }, [
    details?.task.id,
    details?.task.status,
    appInfo?.defaultBatchOutputDir
  ]);

  const {
    view: manualRecovery,
    retry: retryManualRecovery
  } = useManualRecovery({
    task: details?.task,
    browser,
    enabled: backend.status === "ready",
    onChallengeVisible: () => setSettingsOpen(false),
    onRecovered: async (result) => {
      if (result.task.mode !== "batch") return;
      setDetails(result.details as BatchTaskDetails);
      setNotice(
        result.action === "restart"
          ? "验证码已清除，原批量任务已从断点重新启动。"
          : "验证码已清除，原批量任务已继续运行。"
      );
      await refreshTaskList();
    }
  });

  const completedBatches = useMemo(
    () => details?.batches.filter((batch) => batch.status === "completed").length || 0,
    [details]
  );
  const progressView = details
    ? progressSnapshotView({
        status: details.task.status,
        progress: details.task.progress,
        currentStage: details.task.currentStage,
        progressSnapshot: details.task.progressSnapshot,
        completed: completedBatches,
        total: details.batches.length
      })
    : null;
  const failedBatches = details?.batches.filter(
    (batch) => batch.status === "failed"
  ).length || 0;
  const pageLimitWaiting =
    details?.task.status === "waiting_manual" &&
    details.task.error?.code === "PAGE_LIMIT_REACHED";
  const scaleWaiting =
    details?.task.status === "waiting_manual" &&
    details.task.error?.code === "BATCH_SCALE_CONFIRMATION_REQUIRED";
  const browserLeaseBlocked = leaseBlocksNewBrowserTask(browser);
  const actionPolicy = collectionActions(
    details?.task || null,
    details?.capabilities,
    {
      failedItems: failedBatches
    }
  );
  const retryLabel = retryActionLabel(details?.task || null);
  const primaryAction = derivePrimaryTaskAction(
    details?.task || null,
    details?.capabilities,
    {
      recovery: manualRecovery,
      failedItems: failedBatches
    }
  );
  const primaryActionLabel =
    primaryAction === "continue"
      ? "已处理，继续"
      : primaryAction === "redetect"
        ? "重新检测并继续"
        : primaryAction === "capture-boundary"
          ? "继续完整补采"
        : primaryAction === "resume-from-checkpoint"
          ? retryLabel
          : "";
  const runtimeAlerts = useTaskRuntimeEvents(
    details?.task.id || selectedTaskId
  );

  useEffect(() => {
    if (primaryAction !== "redetect") return;
    if (manualRecovery.phase === "failed") {
      setActionFeedback({
        status: "error",
        message: manualRecovery.detail || "自动恢复失败，请重新检测。"
      });
    } else if (manualRecovery.phase === "succeeded") {
      setActionFeedback({
        status: "success",
        message: "重新检测成功，任务已继续。"
      });
    } else if (
      ["waiting", "observing", "verifying", "resuming"].includes(
        manualRecovery.phase
      )
    ) {
      setActionFeedback({
        status: "processing",
        message: manualRecovery.detail || "正在重新检测浏览器状态。"
      });
    }
  }, [
    manualRecovery.detail,
    manualRecovery.phase,
    primaryAction
  ]);
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
      province: effectiveProvince(library, province),
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
  async function createAndStart(): Promise<void> {
    const lines = queries
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (!lines.length || !outputDir.trim()) {
      setNotice("请填写检索关键词和输出目录。");
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
      const result = await window.pkulaw.startCollectorTask({
        name: `批量采集 ${new Date().toLocaleString("zh-CN")}`,
        mode: "batch",
        outputDir: outputDir.trim(),
        input: {
          queries: lines,
          keywords: lines,
          matchMode,
          library,
          ...yearSelectionPayload(yearSelection),
          province: effectiveProvince(library, province),
          timeliness,
          pageSize,
          ...pageSelectionPayload(pageSelection),
          catalogFormat,
          fullFormat: "docx",
          searchStrategyVersion: 2,
          outputDir: outputDir.trim()
        },
        operationId
      });
      setDetails(result.details as BatchTaskDetails);
      autoLoad.clearForNewTask();
      setSelectedTaskId(result.task.id);
      await refreshTaskList();
      setNotice(
        result.preparing
          ? "批量任务已创建，正在核验检索结果窗口；任务进度会实时显示。"
          : result.started
          ? "批量任务已创建并开始运行，单批最多 50 条。"
          : `任务已创建，启动前核验需要处理：${
              result.error?.message || "请检查当前页面"
            }`
      );
    } catch (error) {
      setNotice(`启动批量任务失败：${String(error)}`);
    } finally {
      startInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function applySettings(
    nextInput: Record<string, unknown>
  ): Promise<void> {
    if (!details || settingsInFlightRef.current) return;
    settingsInFlightRef.current = true;
    setBusy(true);
    try {
      const result = await window.pkulaw.applyTaskSettings(
        details.task.id,
        nextInput,
        newOperationId()
      );
      setDetails(result.details as BatchTaskDetails);
      setSelectedTaskId(result.task.id);
      setSettingsOpen(false);
      setNotice(
        result.changed
          ? `任务设置已应用，配置版本 ${result.generation} 已从新扫描起点运行。`
          : "任务设置没有变化，任务保持原运行状态。"
      );
      await refreshTaskList();
    } catch (error) {
      setNotice(`应用任务设置失败：${String(error)}`);
      throw error;
    } finally {
      settingsInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function prepareSettings(
    nextInput: Record<string, unknown>
  ): Promise<void> {
    if (!details || settingsInFlightRef.current) return;
    settingsInFlightRef.current = true;
    setBusy(true);
    try {
      const preview = await window.pkulaw.prepareTaskSettingsRevision(
        details.task.id,
        nextInput,
        newOperationId()
      );
      if (!preview.changed) {
        setSettingsOpen(false);
        setNotice("任务设置没有变化，任务保持原运行状态。");
        return;
      }
      setSettingsRevision(preview);
      setSettingsCommitError(null);
    } catch (error) {
      setNotice(`生成任务设置修订失败：${String(error)}`);
      throw error;
    } finally {
      settingsInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function commitSettings(policy: ArtifactDisposition): Promise<void> {
    if (!details || !settingsRevision || settingsInFlightRef.current) return;
    settingsInFlightRef.current = true;
    setBusy(true);
    try {
      const result = await window.pkulaw.commitTaskSettingsRevision(
        details.task.id,
        settingsRevision.token,
        policy,
        newOperationId()
      );
      setDetails(result.details as BatchTaskDetails);
      setSelectedTaskId(result.task.id);
      setSettingsRevision(null);
      setSettingsCommitError(null);
      setSettingsOpen(false);
      setNotice(
        `任务设置已保存，配置版本 ${result.generation} 已从新扫描起点运行。`
      );
      await refreshTaskList();
    } catch (error) {
      setSettingsCommitError(String(error));
      setNotice(`保存任务设置失败：${String(error)}`);
    } finally {
      settingsInFlightRef.current = false;
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
      | "continueBoundary"
      | "confirmScale"
      | "retry"
  ): Promise<void> {
    if (!selectedTaskId) return;
    if (taskActionInFlightRef.current) return;
    taskActionInFlightRef.current = true;
    setBusy(true);
    setActionFeedback({
      status: "processing",
      message: "正在处理：已提交任务操作。"
    });
    try {
      if (action === "pause") {
        setDetails(await window.pkulaw.pauseBatchTask(selectedTaskId));
        setNotice("已请求暂停，将在当前页读取或批次下载与校验完成后生效。");
      } else if (action === "resume") {
        setDetails(
          await window.pkulaw.resumeBatchTask(
            selectedTaskId,
            newOperationId()
          )
        );
        setNotice("浏览器、登录和选择器已重新核验，批量任务从原断点继续。");
      } else if (action === "stop") {
        setDetails(await window.pkulaw.stopBatchTask(selectedTaskId));
        setNotice("已请求停止，已验收批次和失败断点会保留。");
      } else if (action === "retry") {
        setDetails(
          await window.pkulaw.retryBatchTask(
            selectedTaskId,
            newOperationId()
          )
        );
        setNotice(
          details?.task.checkpoint.scanCompleted === true
            ? "扫描已完成，只重试失败批次；已完成批次不会重下。"
            : "已从批量扫描日志断点继续；已完成组合和页不会重复扫描。"
        );
      } else if (action === "continueToLast") {
        setDetails(await window.pkulaw.continueBatchToLast(selectedTaskId));
        setNotice("已确认后续检索组合扫描到尾页，分页模式将持久保存。");
      } else if (action === "confirmScale") {
        setDetails(await window.pkulaw.confirmBatchScale(selectedTaskId));
        setNotice("批量规模已确认，将仅在全部搜索完成后创建和下载批次。");
      } else if (action === "continueBoundary") {
        setDetails(
          (await window.pkulaw.continueCaptureBoundary(
            selectedTaskId,
            newOperationId()
          )) as BatchTaskDetails
        );
        setNotice("已确认完整补采，任务只处理尚未完成的年份和省份。");
      } else {
        if (browser?.captchaVisible) {
          try {
            setBrowser(await window.pkulaw.browserContinue());
          } catch (error) {
            setNotice(`验证码尚未清除：${String(error)}`);
            setActionFeedback({
              status: "error",
              message: `任务操作失败：${String(error)}`
            });
            return;
          }
        }
        setDetails(await window.pkulaw.continueBatchTask(selectedTaskId));
        setNotice("人工处理已确认，批量任务继续。");
      }
      await refreshTaskList();
      setActionFeedback({
        status: "success",
        message: "任务操作已完成。"
      });
    } catch (error) {
      setNotice(`批量任务操作失败：${String(error)}`);
      setActionFeedback({
        status: "error",
        message: `任务操作失败：${String(error)}`
      });
    } finally {
      taskActionInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function runPrimaryAction(): Promise<void> {
    if (primaryAction === "redetect") {
      setActionFeedback({
        status: "processing",
        message: "正在重新检测浏览器状态。"
      });
      retryManualRecovery();
      return;
    }
    if (primaryAction === "capture-boundary") {
      await control("continueBoundary");
      return;
    }
    if (primaryAction === "continue") {
      await control("continue");
    } else if (primaryAction === "resume-from-checkpoint") {
      await control("retry");
    }
  }

  async function createFreshTask(): Promise<void> {
    if (
      !details ||
      !details.capabilities.canCreateFreshTask ||
      taskActionInFlightRef.current
    ) {
      return;
    }
    taskActionInFlightRef.current = true;
    setBusy(true);
    const oldTaskId = details.task.id;
    try {
      const seed = await window.pkulaw.prepareFreshTask(
        oldTaskId,
        newOperationId()
      );
      prefillBatchCollectorForm(seed, {
        setQueries,
        setMatchMode,
        setLibrary,
        setYearSelection,
        setProvince,
        setTimeliness,
        setPageSize,
        setPageSelection,
        setCatalogFormat,
        setOutputDir
      });
      autoLoad.holdFreshWorkspace(oldTaskId);
      setSelectedTaskId("");
      setDetails(null);
      setSettingsOpen(false);
      setSettingsRevision(null);
      setSettingsCommitError(null);
      setPauseDetailOpen(false);
      setActionFeedback(null);
      setNotice(
        [
          "已打开新建批量任务表单；旧任务仍保留在任务中心。",
          seed.mayDuplicateDownloads
            ? "旧任务已有下载成果，新任务不会跨任务跳过同名政策。"
            : "",
          seed.leaseHeld
            ? "内置浏览器仍由其他任务占用，释放后才能启动新任务。"
            : ""
        ]
          .filter(Boolean)
          .join(" ")
      );
    } catch (error) {
      setNotice(`打开新建批量任务失败：${String(error)}`);
    } finally {
      taskActionInFlightRef.current = false;
      setBusy(false);
    }
  }

  const settingsPanel = (
    <aside className="batch-form-card collector-form-shell">
      <div className="collector-form-body">
        <div className="collector-form-heading">
          <div>
            <span className="eyebrow">批量采集</span>
            <h2>创建批量任务</h2>
          </div>
        </div>
        <p className="collector-form-guidance">
          每批最多 50 条；启动前检查结果窗口和批次规模，核验期间下载为 0。
        </p>
        <label className="collector-query-field">
            {matchMode === "fulltext"
              ? "批量全文检索词（每行一个）"
              : "批量标题关键词（每行一个）"}
            <textarea value={queries} onChange={(event) => setQueries(event.target.value)} rows={4} />
        </label>
        <div className="form-columns">
          <label>
            匹配方式
            <select
              value={matchMode}
              onChange={(event) =>
                setMatchMode(event.target.value as CollectorMatchMode)
              }
            >
              {collectorSearchModeOptions("batch").map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <small>{searchModeDisplayLabel(matchMode)}</small>
          </label>
          <label>
            法规子库
            <select value={library} onChange={(event) => setLibrary(event.target.value as "central" | "local" | "both")}>
              <option value="central">中央法规</option>
              <option value="local">地方法规</option>
              <option value="both">中央 + 地方</option>
            </select>
          </label>
        </div>
        <div className="collector-year-row">
            <YearRangeControl
              value={yearSelection}
              onChange={setYearSelection}
            />
        </div>
        {showsProvinceControl(library) && (
          <div
            className="collector-province-row"
            data-testid="collector-province-row"
          >
            <label>
              省份
              <select value={province} onChange={(event) => setProvince(event.target.value)}>
                <option>全部省份</option>
                {PROVINCES.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
          </div>
        )}
        <div className="form-columns">
          <label>
            时效性
            <select value={timeliness} onChange={(event) => setTimeliness(event.target.value)}>
              <option>全部（仅记录元数据，不筛选）</option>
              <option>现行有效</option>
              <option>已被修改</option>
              <option>已废止</option>
            </select>
          </label>
          <label>
            每页条数
            <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
              {[10, 20, 50, 100].map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        </div>
        <PageLimitControl
          value={pageSelection}
          onChange={setPageSelection}
        />
        <div className="form-columns">
          <label>
            目录格式
            <select value={catalogFormat} onChange={(event) => setCatalogFormat(event.target.value as "xlsx" | "xls" | "html-xls")}>
              <option value="xlsx">{CATALOG_FORMAT_LABELS.xlsx}</option>
              <option value="xls">{CATALOG_FORMAT_LABELS.xls}</option>
              <option value="html-xls">{CATALOG_FORMAT_LABELS["html-xls"]}</option>
            </select>
          </label>
          <label>
            全文格式
            <select value="docx" disabled><option value="docx">Word ZIP</option></select>
          </label>
        </div>
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
        {browser && (
          <div data-testid="browser-lease-note">
            <BrowserLeaseCard
              browser={browser}
              tasks={tasks}
              compact
            />
          </div>
        )}
      </div>
      <div className="collector-action-footer">
        <div className="collector-action-row">
          <button
            type="button"
            className="secondary-button collector-browser-action"
            onClick={onOpenBrowser}
          >
            浏览器登录
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={
              busy ||
              backend.status !== "ready" ||
              browserLeaseBlocked
            }
            onClick={() => void createAndStart()}
          >
            创建并开始批量采集
          </button>
        </div>
      </div>
    </aside>
  );

  const taskSettingsPanel = details ? (
    <TaskSettingsPanel
      key={details.task.id}
      mode="batch"
      taskId={details.task.id}
      input={{
        ...details.task.input,
        outputDir: details.task.outputDir
      }}
      busy={busy}
      onApply={applySettings}
      onPrepare={prepareSettings}
      onClose={() => setSettingsOpen(false)}
    />
  ) : null;

  const taskPanel = (
        <div className="batch-main">
          <div className="single-task-list batch-task-list">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">任务记录</span>
                <h3>批量任务</h3>
              </div>
              <button type="button" className="text-button" onClick={() => void refreshTaskList()}>刷新</button>
            </div>
            <div className="single-task-tabs">
              {tasks.slice(0, 8).map((task) => (
                <button
                  type="button"
                  key={task.id}
                  className={selectedTaskId === task.id ? "selected" : ""}
                  onClick={() => selectTask(task.id)}
                >
                  <span>{task.name}</span>
                  <small>{TASK_STATUS_LABELS[task.status]}</small>
                </button>
              ))}
              {tasks.length === 0 && <div className="muted">暂无批量任务</div>}
            </div>
          </div>

          {details ? (
            <div className="batch-run-card">
              <div className="single-run-heading">
                <div>
                  <span className={`task-status ${details.task.status}`}>
                    {TASK_STATUS_LABELS[details.task.status]}
                  </span>
                  <h2>{details.task.name}</h2>
                  <p>{stageLabel(details.task.currentStage, String(details.task.checkpoint.currentAction || ""))} · {formatTime(details.task.updatedAt)}</p>
                </div>
                <div className="single-run-actions">
                  {actionPolicy.pause && (
                    <button type="button" className="secondary-button" disabled={busy} onClick={() => void control("pause")}>暂停</button>
                  )}
                  {actionPolicy.resume && (
                    <button type="button" className="primary-button compact" disabled={busy} onClick={() => void control("resume")}>继续</button>
                  )}
                  {actionPolicy.stop && (
                    <button type="button" className="secondary-button" disabled={busy} onClick={() => void control("stop")}>
                      {details.task.status === "stopping"
                        ? "正在停止"
                        : secondaryStopLabel(details.task)}
                    </button>
                  )}
                  {primaryAction === "page-limit" && (
                    <button type="button" className="primary-button compact" disabled={busy} onClick={() => void control("continueToLast")}>继续到尾页</button>
                  )}
                  {details.task.status === "stopping" && (
                    <span className="task-control-note">正在等待当前安全步骤结束</span>
                  )}
                </div>
              </div>
              <div className="single-progress">
                <div><span style={{ width: `${progressView?.percent || 0}%` }} /></div>
                <strong>{progressView?.percent || 0}%</strong>
                <small>{completedBatches}/{details.batches.length} 批</small>
              </div>
              <YearStatsTable stats={details.task.checkpoint.yearStats} />
              <ScanContinuitySummary checkpoint={details.task.checkpoint} />
              {Boolean(details.task.checkpoint.scaleEstimate) &&
                typeof details.task.checkpoint.scaleEstimate === "object" && (
                  <ScaleEstimateSummary checkpoint={details.task.checkpoint} />
                )}
              {pageLimitWaiting && (
                <PageLimitDecision checkpoint={details.task.checkpoint} />
              )}
              {scaleWaiting && (
                <ScaleDecisionPanel
                  checkpoint={details.task.checkpoint}
                  busy={busy}
                  onConfirm={() => void control("confirmScale")}
                />
              )}
              <TaskRuntimePanel
                task={details.task}
                capabilities={details.capabilities}
                completed={completedBatches}
                total={details.batches.length}
                browserCaptchaVisible={Boolean(browser?.captchaVisible)}
                browserCaptchaCheckedAt={browser?.captchaCheckedAt}
                browserCaptchaSelector={browser?.captchaSelector}
                alerts={runtimeAlerts}
                onCreateFreshTask={() => void createFreshTask()}
                primaryAction={primaryAction}
                primaryActionLabel={primaryActionLabel}
                actionBusy={busy}
                actionFeedback={actionFeedback}
                onPrimaryAction={() => void runPrimaryAction()}
                onPauseDetailOpenChange={setPauseDetailOpen}
              />
              <div className="batch-table">
                <div className="batch-head">
                  <span>批次</span><span>页/序号</span><span>政策数</span><span>目录</span><span>ZIP</span><span>状态</span>
                </div>
                {details.batches.map((batch) => (
                  <BatchRow key={batch.id} batch={batch} />
                ))}
                {details.batches.length === 0 && <div className="muted">正在检索并准备批次…</div>}
              </div>
            </div>
          ) : (
            <div className="single-empty batch-empty">
              <div className="single-empty-mark">批</div>
              <h3>准备批量采集</h3>
              <p>目录与 ZIP 会在验证通过后进入正式目录。</p>
            </div>
          )}

          <div className="notice browser-notice">
            <span className="notice-icon">i</span>
            <span>{notice}</span>
          </div>
        </div>
  );

  if (browser && details) {
    return (
      <TaskWorkspaceFrame
        mode="batch"
        browser={browser}
        settingsOpen={settingsOpen}
        pauseDetailOpen={pauseDetailOpen}
        manualRecovery={manualRecovery}
        onToggleSettings={() => {
          if (
            !settingsOpen &&
            !canOpenTaskSettings(details?.task.status)
          ) {
            setNotice("请先暂停任务，再手动打开任务设置。");
            return;
          }
          if (
            !settingsOpen &&
            (browser?.captchaVisible ||
              browser?.loginVisible ||
              ["waiting", "observing", "verifying", "resuming"].includes(
                manualRecovery.phase
              ))
          ) {
            setNotice("请先在右侧浏览器完成登录或验证码。");
            return;
          }
          setSettingsOpen((current) => !current);
        }}
        settingsPanel={taskSettingsPanel || settingsPanel}
        taskPanel={taskPanel}
        decisionDialog={
          settingsRevision ? (
            <TaskSettingsRevisionDialog
              preview={settingsRevision}
              busy={busy}
              error={settingsCommitError}
              onCommit={commitSettings}
              onCancel={() => {
                setSettingsRevision(null);
                setSettingsCommitError(null);
              }}
            />
          ) : undefined
        }
      />
    );
  }

  return (
    <>
      <section className="batch-workspace">
        <div className="batch-layout">
          {settingsPanel}
          {taskPanel}
        </div>
      </section>
      {settingsRevision && (
        <TaskSettingsRevisionDialog
          preview={settingsRevision}
          busy={busy}
          error={settingsCommitError}
          onCommit={commitSettings}
          onCancel={() => {
            setSettingsRevision(null);
            setSettingsCommitError(null);
          }}
        />
      )}
    </>
  );
}

function BatchRow({ batch }: { batch: BatchRecord }): React.JSX.Element {
  const name = (path: string): string =>
    path ? String(path).split(/[\\/]/).pop() || "—" : "—";
  return (
    <div className="batch-record">
      <div className="batch-row">
        <div>
          <strong>{batch.library === "central" ? "中央" : "地方"} · {catalogFormatLabel(batch.catalogFormat)}</strong>
          <small>{batch.query}</small>
        </div>
        <span>{batch.page} / {batch.rowStart}-{batch.rowEnd}</span>
        <span>{batch.selected.length}</span>
        <span title={batch.catalogPath}>{name(batch.catalogPath)}</span>
        <span title={batch.zipPath}>{name(batch.zipPath)}</span>
        <span className={`item-state ${batch.status}`}>{stageLabel(batch.status)}</span>
      </div>
      {batch.error && (
        <ExpandableValue
          label="批次错误"
          value={[
            `${batch.error.code}: ${batch.error.message}`,
            batch.error.details
              ? `详细信息：${JSON.stringify(batch.error.details, null, 2)}`
              : ""
          ].filter(Boolean).join("\n")}
        />
      )}
      <ExpandableValue
        label="目录、ZIP 与报告"
        value={`目录：${batch.catalogPath || "未登记"}\nZIP：${batch.zipPath || "未登记"}\n报告：${batch.reportPath || "未登记"}`}
      />
    </div>
  );
}
