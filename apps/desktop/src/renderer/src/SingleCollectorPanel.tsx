import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AppInfo,
  BackendState,
  BrowserHostState,
  CollectorMatchMode,
  EngineEvent,
  PageSelection,
  TaskDetails,
  TaskItemCandidate,
  TaskItemRecord,
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
import {
  holdingTaskLabel,
  leaseBlocksNewBrowserTask,
  newOperationId
} from "../../shared/browser-lease";
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
import {
  resetSingleCollectorForm,
  shouldAutoSelectTask,
  shouldLoadTaskDetails,
  shouldResetCollector
} from "./collector-reset";

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
    "",
    migrateCollectorQueryDraft
  );
  const [matchMode, setMatchMode] = usePersistentState<CollectorMatchMode>(
    "pkulaw.collector.single.v1.matchMode",
    defaultCollectorMatchMode("single"),
    (stored) => normalizeCollectorMatchMode("single", stored)
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
  const [actionFeedback, setActionFeedback] =
    useState<TaskActionFeedback | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pauseDetailOpen, setPauseDetailOpen] = useState(false);
  const startInFlightRef = useRef(false);
  const settingsInFlightRef = useRef(false);
  const previousStatusByTaskRef = useRef<
    Record<string, string | undefined>
  >({});
  const suppressedCompletedTaskIdRef = useRef("");
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

  function selectTask(taskId: string): void {
    suppressedCompletedTaskIdRef.current = "";
    void loadDetails(taskId);
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
        const automaticTaskId = eventTaskId || selectedTaskId;
        if (
          shouldLoadTaskDetails(
            automaticTaskId,
            suppressedCompletedTaskIdRef.current
          )
        ) {
          void loadDetails(automaticTaskId).catch(() => undefined);
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
    if (selectedTaskId || !tasks[0]) return;
    if (!shouldAutoSelectTask(tasks[0].id, suppressedCompletedTaskIdRef.current)) {
      return;
    }
    void loadDetails(tasks[0].id).catch(() => undefined);
  }, [selectedTaskId, tasks]);

  useEffect(() => {
    if (!details) return;
    const taskId = details.task.id;
    const previousStatus = previousStatusByTaskRef.current[taskId];
    const currentStatus = details.task.status;
    previousStatusByTaskRef.current[taskId] = currentStatus;
    if (!shouldResetCollector(previousStatus, currentStatus)) return;
    suppressedCompletedTaskIdRef.current = taskId;
    resetSingleCollectorForm(
      appInfo?.defaultSingleOutputDir || "",
      {
        setQueries,
        setMatchMode,
        setLibrary,
        setYearSelection,
        setProvince,
        setTimeliness,
        setPageSize,
        setPageSelection,
        setOutputDir
      }
    );
    setSelectedTaskId("");
    setDetails(null);
    setSettingsOpen(false);
    setPauseDetailOpen(false);
    setNotice("任务已完成，可创建新的逐篇采集任务。");
  }, [
    details?.task.id,
    details?.task.status,
    appInfo?.defaultSingleOutputDir
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
      if (result.task.mode !== "single") return;
      setDetails(result.details as TaskDetails);
      setNotice(
        result.action === "restart"
          ? "验证码已清除，原逐篇任务已从断点重新启动。"
          : "验证码已清除，原逐篇任务已继续运行。"
      );
      await refreshTaskList();
    }
  });

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
  const primaryAction = derivePrimaryTaskAction(
    details?.task || null,
    {
      recovery: manualRecovery,
      candidateWaiting: Boolean(waitingItem),
      failedItems
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
    if (!lines.length) {
      setNotice("请至少输入一个关键词或精确完整标题。");
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
      const result = await window.pkulaw.startCollectorTask({
        name: `逐篇采集 ${new Date().toLocaleString("zh-CN")}`,
        mode: "single",
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
          searchStrategyVersion: 2,
          outputDir: outputDir.trim()
        },
        operationId
      });
      suppressedCompletedTaskIdRef.current = "";
      setSelectedTaskId(result.task.id);
      setDetails(result.details as TaskDetails);
      await refreshTaskList();
      setNotice(
        result.preparing
          ? "逐篇任务已创建，正在核验检索结果窗口；任务进度会实时显示。"
          : result.started
          ? "逐篇任务已创建并开始运行。"
          : `任务已创建，启动前核验需要处理：${
              result.error?.message || "请检查当前页面"
            }`
      );
    } catch (error) {
      setNotice(`启动逐篇任务失败：${String(error)}`);
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
      setDetails(result.details as TaskDetails);
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

  async function control(
    action:
      | "pause"
      | "resume"
      | "stop"
      | "continue"
      | "continueToLast"
      | "continueBoundary"
      | "retry"
  ): Promise<void> {
    if (!selectedTaskId) return;
    setBusy(true);
    setActionFeedback({
      status: "processing",
      message: "正在处理：已提交任务操作。"
    });
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
      } else if (action === "continueBoundary") {
        setDetails(
          (await window.pkulaw.continueCaptureBoundary(
            selectedTaskId
          )) as TaskDetails
        );
        setNotice("已确认完整补采，任务只处理尚未完成的年份和省份。");
      } else {
        if (
          details?.task.status === "waiting_manual" &&
          browser?.captchaVisible
        ) {
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
        setDetails(await window.pkulaw.continueSingleTask(selectedTaskId));
        setNotice("人工处理已确认，任务继续。");
      }
      await refreshTaskList();
      setActionFeedback({
        status: "success",
        message: "任务操作已完成。"
      });
    } catch (error) {
      setNotice(`任务操作失败：${String(error)}`);
      setActionFeedback({
        status: "error",
        message: `任务操作失败：${String(error)}`
      });
    } finally {
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

  async function choose(item: TaskItemRecord, candidate: TaskItemCandidate): Promise<void> {
    if (!selectedTaskId) return;
    setBusy(true);
    setActionFeedback({
      status: "processing",
      message: "正在处理：正在提交同名版本选择。"
    });
    try {
      const next = await window.pkulaw.chooseCandidate(
        selectedTaskId,
        item.id,
        candidateKey(candidate)
      );
      setDetails(next);
      setNotice(`已选择“${candidate.title}”的 ${candidate.publicationDate || "未知日期"} 版本。`);
      setActionFeedback({
        status: "success",
        message: "同名版本选择已提交。"
      });
      await refreshTaskList();
    } catch (error) {
      setNotice(`选择同名版本失败：${String(error)}`);
      setActionFeedback({
        status: "error",
        message: `选择同名版本失败：${String(error)}`
      });
    } finally {
      setBusy(false);
    }
  }

  const settingsPanel = (
    <aside className="single-form-card collector-form-shell">
      <div className="collector-form-body">
        <div className="collector-form-heading">
          <div>
            <span className="eyebrow">逐篇采集</span>
            <h2>创建采集任务</h2>
          </div>
        </div>
        <p className="collector-form-guidance">
          启动前自动检查结果窗口、页数和筛选；核验期间下载为 0。
        </p>
        <label className="collector-query-field">
            {matchMode === "title"
              ? "精确完整标题（每行一个）"
              : matchMode === "fulltext"
                ? "全文检索词（每行一个）"
                : "标题关键词（每行一个）"}
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
              onChange={(event) =>
                setMatchMode(event.target.value as CollectorMatchMode)
              }
            >
              {collectorSearchModeOptions("single").map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <small>{searchModeDisplayLabel(matchMode)}</small>
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
                {PROVINCES.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
          </div>
        )}
        <div className="form-columns">
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
            className="primary-button single-start"
            disabled={
              busy ||
              backend.status !== "ready" ||
              browserLeaseBlocked
            }
            onClick={() => void createAndStart()}
          >
            创建并开始逐篇采集
          </button>
        </div>
      </div>
    </aside>
  );

  const taskSettingsPanel = details ? (
    <TaskSettingsPanel
      key={details.task.id}
      mode="single"
      taskId={details.task.id}
      input={{
        ...details.task.input,
        outputDir: details.task.outputDir
      }}
      busy={busy}
      onApply={applySettings}
      onClose={() => setSettingsOpen(false)}
    />
  ) : null;

  const taskPanel = (
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
                  onClick={() => selectTask(task.id)}
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
                  {primaryAction === "page-limit" && (
                    <button
                      type="button"
                      className="primary-button compact"
                      disabled={busy}
                      onClick={() => void control("continueToLast")}
                    >
                      继续到尾页
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
                primaryAction={primaryAction}
                primaryActionLabel={primaryActionLabel}
                actionBusy={busy}
                actionFeedback={actionFeedback}
                onPrimaryAction={() => void runPrimaryAction()}
                onPauseDetailOpenChange={setPauseDetailOpen}
              />

              <ScanContinuitySummary checkpoint={details.task.checkpoint} />
              <YearStatsTable stats={details.task.checkpoint.yearStats} />

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
  );

  if (browser && details) {
    return (
      <TaskWorkspaceFrame
        mode="single"
        browser={browser}
        settingsOpen={settingsOpen}
        pauseDetailOpen={pauseDetailOpen}
        manualRecovery={manualRecovery}
        onToggleSettings={() => {
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
      />
    );
  }

  return (
    <section className="single-workspace">
      <div className="single-layout">
        {settingsPanel}
        {taskPanel}
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
