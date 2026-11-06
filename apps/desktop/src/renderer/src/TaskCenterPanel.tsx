import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AppInfo,
  BackendState,
  BatchTaskDetails,
  BrowserHostState,
  CleanupPreview,
  DataProcessingDetails,
  EngineEvent,
  TaskDetails,
  TaskItemCandidate,
  TaskItemRecord,
  TaskRecord,
  TaskStatus
} from "../../shared/protocol";
import {
  PageLimitDecision,
  ScaleDecisionPanel
} from "./TaskDecisionPanel";
import {
  holdingTaskLabel,
  leaseBlocksNewBrowserTask,
  newOperationId
} from "../../shared/browser-lease";
import {
  BrowserLeaseCard,
  ExpandableValue,
  TaskRuntimePanel,
  TaskTimelinePanel
} from "./TaskRuntimePanel";
import { useTaskRuntimeEvents } from "./useTaskRuntimeEvents";
import {
  TASK_STATUS_LABELS,
  collectionActions,
  progressSnapshotView,
  retryActionLabel,
  secondaryStopLabel,
  stageLabel
} from "./task-presentation";

interface TaskCenterPanelProps {
  appInfo: AppInfo | null;
  backend: BackendState;
}

const activeStatuses = new Set<TaskStatus>([
  "preflight",
  "waiting_login",
  "running",
  "waiting_manual",
  "verifying",
  "pausing",
  "paused",
  "stopping"
]);

function candidateKey(candidate: TaskItemCandidate): string {
  return String(candidate.policyId || candidate.url || "");
}

function formatTime(value: string): string {
  if (!value) return "—";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    }).format(new Date(value));
  } catch {
    return value;
  }
}

function itemStatusText(status: TaskItemRecord["status"]): string {
  return {
    pending: "待处理",
    running: "处理中",
    verifying: "校验中",
    waiting_manual: "等待人工",
    pending_review: "待核验",
    completed: "完成",
    failed: "失败",
    skipped: "跳过"
  }[status];
}

function formatItemStatus(item: TaskItemRecord): string {
  return itemStatusText(item.status);
}

export default function TaskCenterPanel({
  appInfo,
  backend
}: TaskCenterPanelProps): React.JSX.Element {
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [details, setDetails] = useState<TaskDetails | BatchTaskDetails | null>(null);
  const [dataDetails, setDataDetails] = useState<DataProcessingDetails | null>(null);
  const [browser, setBrowser] = useState<BrowserHostState | null>(null);
  const [modeFilter, setModeFilter] = useState<"all" | "single" | "batch">("all");
  const [statusFilter, setStatusFilter] = useState<"all" | TaskStatus>("all");
  const [query, setQuery] = useState("");
  const [itemFilter, setItemFilter] = useState<"all" | "attention">("all");
  const [scope, setScope] = useState<"active" | "archive">("active");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [cleanupPreview, setCleanupPreview] = useState<CleanupPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const taskActionInFlightRef = useRef(false);
  const [notice, setNotice] = useState("选择任务查看逐条状态、失败原因和可恢复操作。");

  async function refreshTasks(): Promise<TaskRecord[]> {
    const records = await window.pkulaw.listTasks(200, scope === "archive");
    const scoped = records.filter((task) =>
      scope === "archive" ? Boolean(task.archivedAt) : !task.archivedAt
    );
    setTasks(scoped);
    return scoped;
  }

  async function loadTask(taskId: string): Promise<void> {
    if (!taskId) {
      setDetails(null);
      setDataDetails(null);
      return;
    }
    const task = tasks.find((item) => item.id === taskId) ||
      (await window.pkulaw.getTask(taskId));
    const next =
      task.mode === "batch"
        ? await window.pkulaw.getBatchTaskDetails(taskId)
        : await window.pkulaw.getTaskDetails(taskId);
    setDetails(next);
    try {
      setDataDetails(await window.pkulaw.getLatestDataProcessing(taskId));
    } catch {
      setDataDetails(null);
    }
  }

  useEffect(() => {
    setSelectedIds(new Set());
    setCleanupPreview(null);
    setSelectedId("");
    setDetails(null);
    setDataDetails(null);
  }, [scope]);

  useEffect(() => {
    if (backend.status !== "ready") return;
    void refreshTasks()
      .then((records) => {
        if (!selectedId && records[0]) {
          setSelectedId(records[0].id);
        }
      })
      .catch((error) => setNotice(`读取任务失败：${String(error)}`));
  }, [backend.status, scope]);

  useEffect(() => {
    if (!selectedId) {
      setDetails(null);
      setDataDetails(null);
      return;
    }
    if (backend.status !== "ready") return;
    void loadTask(selectedId).catch((error) =>
      setNotice(`读取任务详情失败：${String(error)}`)
    );
  }, [selectedId, backend.status]);

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
      if (
        !event.event.startsWith("task.") &&
        !event.event.startsWith("single.") &&
        !event.event.startsWith("batch.") &&
        !event.event.startsWith("data.")
      ) {
        return;
      }
      void refreshTasks().catch(() => undefined);
      if (selectedId) {
        void loadTask(selectedId).catch(() => undefined);
      }
    });
    return unsubscribe;
  }, [selectedId, scope]);

  useEffect(() => {
    const task = details?.task;
    if (!task || !activeStatuses.has(task.status)) return;
    const timer = window.setInterval(() => {
      void refreshTasks().catch(() => undefined);
      void loadTask(task.id).catch(() => undefined);
    }, 1_200);
    return () => window.clearInterval(timer);
  }, [details?.task.id, details?.task.status]);

  const visibleTasks = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return tasks.filter((task) => {
      if (modeFilter !== "all" && task.mode !== modeFilter) return false;
      if (statusFilter !== "all" && task.status !== statusFilter) return false;
      if (
        keyword &&
        !`${task.name} ${task.currentStage} ${task.error?.message || ""}`
          .toLowerCase()
          .includes(keyword)
      ) {
        return false;
      }
      return true;
    });
  }, [modeFilter, query, statusFilter, tasks]);

  useEffect(() => {
    const visibleIds = new Set(visibleTasks.map((task) => task.id));
    setSelectedIds((current) => {
      const next = new Set([...current].filter((id) => visibleIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [visibleTasks]);

  const allVisibleSelected =
    visibleTasks.length > 0 && visibleTasks.every((task) => selectedIds.has(task.id));
  const selectedRecords = tasks.filter((task) => selectedIds.has(task.id));
  const selectedHasActive = selectedRecords.some((task) =>
    activeStatuses.has(task.status)
  );

  function toggleTaskSelection(taskId: string): void {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }

  function toggleSelectAllVisible(): void {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (allVisibleSelected) {
        visibleTasks.forEach((task) => next.delete(task.id));
      } else {
        visibleTasks.forEach((task) => next.add(task.id));
      }
      return next;
    });
  }

  const selectedTask = details?.task || tasks.find((task) => task.id === selectedId) || null;
  const items = useMemo(() => {
    const records = details?.items || [];
    return itemFilter === "attention"
      ? records.filter(
          (item) => item.status === "failed" || item.status === "waiting_manual"
        )
      : records;
  }, [details?.items, itemFilter]);
  const batches = details && "batches" in details ? details.batches : [];
  const candidateWaiting = Boolean(
    details?.items.some(
      (item) =>
        item.status === "waiting_manual" &&
        Array.isArray(item.payload.candidates) &&
        item.payload.candidates.length > 1 &&
        !item.payload.selectedCandidateKey
    )
  );
  const pageLimitWaiting =
    selectedTask?.status === "waiting_manual" &&
    selectedTask.error?.code === "PAGE_LIMIT_REACHED";
  const scaleWaiting =
    selectedTask?.mode === "batch" &&
    selectedTask?.status === "waiting_manual" &&
    selectedTask.error?.code === "BATCH_SCALE_CONFIRMATION_REQUIRED";
  const browserLeaseBlocked = leaseBlocksNewBrowserTask(
    browser,
    selectedTask?.id || ""
  );
  const failedItems = items.filter((item) => item.status === "failed").length;
  const actionPolicy = collectionActions(
    selectedTask,
    details?.capabilities,
    {
      candidateWaiting,
      failedItems
    }
  );
  const selectedProgress =
    selectedTask === null
      ? null
      : progressSnapshotView({
          status: selectedTask.status,
          progress: selectedTask.progress,
          currentStage: selectedTask.currentStage,
          progressSnapshot: selectedTask.progressSnapshot,
          completed:
            selectedTask.mode === "batch"
              ? batches.filter((batch) => batch.status === "completed").length
              : (details?.items || []).filter(
                  (item) => item.status === "completed"
                ).length,
          total:
            selectedTask.mode === "batch"
              ? batches.length
              : details?.items.length || 0
        });
  const runtimeAlerts = useTaskRuntimeEvents(selectedTask?.id || selectedId);

  async function runTaskAction(
    action:
      | "start"
      | "pause"
      | "resume"
      | "stop"
      | "continue"
      | "continueToLast"
      | "continueBoundary"
      | "confirmScale"
      | "retry"
  ): Promise<void> {
    if (!selectedTask) return;
    if (taskActionInFlightRef.current) return;
    taskActionInFlightRef.current = true;
    setBusy(true);
    try {
      if (action === "start" || action === "retry") {
        const browserState = await window.pkulaw.getBrowserState();
        setBrowser(browserState);
        if (
          !browserState.targetBound ||
          !browserState.authenticated ||
          !browserState.selectorReady
        ) {
          throw new Error(
            `任务启动门禁未通过：${
              browserState.selectorMessage || "请先打开已登录的北大法宝检索页"
            }`
          );
        }
        if (leaseBlocksNewBrowserTask(browserState, selectedTask.id)) {
          throw new Error(holdingTaskLabel(browserState, tasks));
        }
      }
      const operationId =
        action === "start" || action === "resume" || action === "retry"
          ? newOperationId()
          : undefined;
      if (selectedTask.mode === "single") {
        if (action === "start") await window.pkulaw.startSingleTask(selectedTask.id, operationId);
        if (action === "pause") await window.pkulaw.pauseSingleTask(selectedTask.id);
        if (action === "resume") await window.pkulaw.resumeSingleTask(selectedTask.id, operationId);
        if (action === "stop") await window.pkulaw.stopSingleTask(selectedTask.id);
        if (action === "continue") await window.pkulaw.continueSingleTask(selectedTask.id);
        if (action === "continueToLast") {
          await window.pkulaw.continueSingleToLast(selectedTask.id);
        }
        if (action === "continueBoundary") {
          await window.pkulaw.continueCaptureBoundary(
            selectedTask.id,
            newOperationId()
          );
        }
        if (action === "retry") await window.pkulaw.retrySingleTask(selectedTask.id, operationId);
      } else {
        if (action === "start") await window.pkulaw.startBatchTask(selectedTask.id, operationId);
        if (action === "pause") await window.pkulaw.pauseBatchTask(selectedTask.id);
        if (action === "resume") await window.pkulaw.resumeBatchTask(selectedTask.id, operationId);
        if (action === "stop") await window.pkulaw.stopBatchTask(selectedTask.id);
        if (action === "continue") await window.pkulaw.continueBatchTask(selectedTask.id);
        if (action === "continueToLast") {
          await window.pkulaw.continueBatchToLast(selectedTask.id);
        }
        if (action === "continueBoundary") {
          await window.pkulaw.continueCaptureBoundary(
            selectedTask.id,
            newOperationId()
          );
        }
        if (action === "confirmScale") {
          await window.pkulaw.confirmBatchScale(selectedTask.id);
        }
        if (action === "retry") await window.pkulaw.retryBatchTask(selectedTask.id, operationId);
      }
      await refreshTasks();
      await loadTask(selectedTask.id);
      setNotice(`任务操作已提交：${action}`);
    } catch (error) {
      setNotice(`任务操作失败：${String(error)}`);
    } finally {
      taskActionInFlightRef.current = false;
      setBusy(false);
    }
  }

  async function chooseItemCandidate(
    item: TaskItemRecord,
    candidate: TaskItemCandidate
  ): Promise<void> {
    if (!selectedTask) return;
    setBusy(true);
    try {
      await window.pkulaw.chooseCandidate(
        selectedTask.id,
        item.id,
        candidateKey(candidate)
      );
      await loadTask(selectedTask.id);
      setNotice(`已选择“${candidate.title}”的指定版本。`);
    } catch (error) {
      setNotice(`同名政策选择失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function openOutput(): Promise<void> {
    if (!selectedTask) return;
    try {
      await window.pkulaw.openTaskOutput(selectedTask.id);
    } catch (error) {
      setNotice(`打开输出目录失败：${String(error)}`);
    }
  }

  async function archiveSelected(): Promise<void> {
    const ids = [...selectedIds];
    if (!ids.length) return;
    if (selectedHasActive) {
      setNotice("活动任务必须先停止，不能归档。");
      return;
    }
    if (!window.confirm(`将 ${ids.length} 个任务移入回收站？任务数据和输出文件不会被删除。`)) {
      return;
    }
    setBusy(true);
    try {
      await window.pkulaw.archiveTasks(ids, "用户移入回收站");
      setSelectedIds(new Set());
      setSelectedId("");
      await refreshTasks();
      setNotice(`已将 ${ids.length} 个任务移入回收站，可从回收站恢复。`);
    } catch (error) {
      setNotice(`归档失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function archiveOne(taskId: string): Promise<void> {
    if (!window.confirm("将此任务移入回收站？任务数据和输出文件会保留。")) return;
    setBusy(true);
    try {
      await window.pkulaw.archiveTasks([taskId], "用户移入回收站");
      setSelectedIds((current) => {
        const next = new Set(current);
        next.delete(taskId);
        return next;
      });
      setSelectedId("");
      await refreshTasks();
      setNotice("任务已移入回收站，可随时恢复。");
    } catch (error) {
      setNotice(`归档失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function restoreSelected(): Promise<void> {
    const ids = [...selectedIds];
    if (!ids.length) return;
    setBusy(true);
    try {
      await window.pkulaw.restoreTasks(ids);
      setSelectedIds(new Set());
      setSelectedId("");
      await refreshTasks();
      setNotice(`已恢复 ${ids.length} 个任务。`);
    } catch (error) {
      setNotice(`恢复失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function restoreOne(taskId: string): Promise<void> {
    setBusy(true);
    try {
      await window.pkulaw.restoreTasks([taskId]);
      setSelectedId("");
      await refreshTasks();
      setNotice("任务已恢复到普通列表。");
    } catch (error) {
      setNotice(`恢复失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function previewPurge(taskIds?: string[]): Promise<void> {
    const ids = taskIds || [...selectedIds];
    if (!ids.length) return;
    if (selectedHasActive) {
      setNotice("活动任务必须先停止，不能彻底删除。");
      return;
    }
    setBusy(true);
    try {
      setCleanupPreview(await window.pkulaw.getCleanupPreview(ids));
    } catch (error) {
      setNotice(`读取清理预览失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function confirmPurge(fileMode: "keep" | "trash"): Promise<void> {
    if (!cleanupPreview) return;
    const ids = cleanupPreview.tasks.map((task) => task.id);
    setBusy(true);
    try {
      const result = await window.pkulaw.purgeTasks(ids, { fileMode });
      setCleanupPreview(null);
      setSelectedIds(new Set());
      setSelectedId("");
      await refreshTasks();
      setNotice(
        fileMode === "trash"
          ? `已删除 ${result.taskIds.length} 个任务记录，${result.trashedFiles} 个登记文件已移入 Windows 回收站。`
          : `已删除 ${result.taskIds.length} 个任务记录，输出文件保留。`
      );
    } catch (error) {
      setNotice(`彻底删除失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="task-center">
      <div className="task-center-header panel">
        <div>
          <span className="eyebrow">
            {appInfo?.version || "0.2.5"} · 统一任务中心
          </span>
          <h2>{scope === "archive" ? "回收站与任务恢复" : "任务状态、失败原因与恢复入口"}</h2>
          <p>逐篇和批量共享筛选、详情、回收站、恢复与产物治理。</p>
          <div className="segmented task-scope-tabs">
            <button
              type="button"
              className={scope === "active" ? "selected" : ""}
              onClick={() => setScope("active")}
            >
              任务
            </button>
            <button
              type="button"
              className={scope === "archive" ? "selected" : ""}
              onClick={() => setScope("archive")}
            >
              回收站
            </button>
          </div>
        </div>
        <div className="task-summary">
          <div><strong>{tasks.length}</strong><span>全部任务</span></div>
          <div><strong>{tasks.filter((task) => activeStatuses.has(task.status)).length}</strong><span>活跃任务</span></div>
          <div><strong>{tasks.filter((task) => task.status === "failed").length}</strong><span>失败任务</span></div>
        </div>
      </div>

      <div className="task-center-grid">
        <aside className="panel task-browser">
          <div className="task-filter-grid">
            <label>
              搜索
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="任务名、阶段或错误"
              />
            </label>
            <label>
              模式
              <select
                value={modeFilter}
                onChange={(event) =>
                  setModeFilter(event.target.value as typeof modeFilter)
                }
              >
                <option value="all">全部模式</option>
                <option value="single">逐篇采集</option>
                <option value="batch">批量采集</option>
              </select>
            </label>
            <label>
              状态
              <select
                value={statusFilter}
                onChange={(event) =>
                  setStatusFilter(event.target.value as typeof statusFilter)
                }
              >
                <option value="all">全部状态</option>
                {Object.entries(TASK_STATUS_LABELS).map(([value, label]) => (
                  <option value={value} key={value}>{label}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="task-selection-toolbar">
            <label>
              <input
                type="checkbox"
                checked={allVisibleSelected}
                onChange={toggleSelectAllVisible}
                disabled={visibleTasks.length === 0}
              />
              全选当前筛选结果
            </label>
            <strong>已选 {selectedIds.size}</strong>
            {scope === "active" ? (
              <button
                type="button"
                className="secondary-button compact"
                disabled={busy || selectedIds.size === 0}
                onClick={() => void archiveSelected()}
              >
                移入回收站
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="secondary-button compact"
                  disabled={busy || selectedIds.size === 0}
                  onClick={() => void restoreSelected()}
                >
                  恢复
                </button>
                <button
                  type="button"
                  className="danger-button compact"
                  disabled={busy || selectedIds.size === 0 || selectedHasActive}
                  onClick={() => void previewPurge()}
                >
                  彻底删除
                </button>
              </>
            )}
          </div>
          <div className="task-browser-list">
            {visibleTasks.map((task) => (
              <div
                className={`task-browser-row ${selectedId === task.id ? "selected" : ""}`}
                key={task.id}
                onClick={() => setSelectedId(task.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelectedId(task.id);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <input
                  type="checkbox"
                  className="task-row-checkbox"
                  checked={selectedIds.has(task.id)}
                  onClick={(event) => event.stopPropagation()}
                  onChange={() => toggleTaskSelection(task.id)}
                  aria-label={`选择 ${task.name}`}
                />
                <span className={`task-mode ${task.mode}`}>
                  {task.mode === "single" ? "逐篇" : "批量"}
                </span>
                <span className="task-browser-main">
                  <strong>{task.name}</strong>
                  <small>{task.currentStage || "等待启动"} · {formatTime(task.updatedAt)}</small>
                </span>
                <span className={`task-status ${task.status}`}>
                  {TASK_STATUS_LABELS[task.status]}
                </span>
              </div>
            ))}
            {visibleTasks.length === 0 && <div className="muted">没有符合条件的任务</div>}
          </div>
        </aside>

        <div className="task-detail-stack">
          {selectedTask ? (
            <>
              <article className="panel task-detail-head">
                <div>
                  <span className="eyebrow">{selectedTask.mode === "single" ? "逐篇任务" : "批量任务"}</span>
                  <h3>{selectedTask.name}</h3>
                  <p>{selectedTask.currentStage || "等待启动"} · 更新于 {formatTime(selectedTask.updatedAt)}</p>
                </div>
                <div className="task-detail-actions">
                  {actionPolicy.start && <button className="primary-button compact" type="button" disabled={busy || browserLeaseBlocked} onClick={() => void runTaskAction("start")}>开始</button>}
                  {actionPolicy.pause && <button className="secondary-button" type="button" disabled={busy} onClick={() => void runTaskAction("pause")}>暂停</button>}
                  {actionPolicy.resume && <button className="primary-button compact" type="button" disabled={busy} onClick={() => void runTaskAction("resume")}>继续</button>}
                  {actionPolicy.continue && <button className="primary-button compact" type="button" disabled={busy || Boolean(browser?.captchaVisible)} onClick={() => void runTaskAction("continue")}>已处理，继续</button>}
                  {actionPolicy.continueToLast && <button className="primary-button compact" type="button" disabled={busy} onClick={() => void runTaskAction("continueToLast")}>继续到尾页</button>}
                  {actionPolicy.confirmScale && <button className="primary-button compact" type="button" disabled={busy} onClick={() => void runTaskAction("confirmScale")}>确认规模并继续</button>}
                  {actionPolicy.continueCaptureBoundary && <button className="primary-button compact" type="button" disabled={busy} onClick={() => void runTaskAction("continueBoundary")}>继续完整补采</button>}
                  {actionPolicy.stop && <button className="secondary-button" type="button" disabled={busy} onClick={() => void runTaskAction("stop")}>{secondaryStopLabel(selectedTask)}</button>}
                  {actionPolicy.retry && <button className="secondary-button" type="button" disabled={busy || browserLeaseBlocked} onClick={() => void runTaskAction("retry")}>{retryActionLabel(selectedTask)}</button>}
                  <button className="secondary-button" type="button" onClick={() => void openOutput()}>打开输出目录</button>
                  {!selectedTask.archivedAt && !activeStatuses.has(selectedTask.status) && (
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={busy}
                      onClick={() => void archiveOne(selectedTask.id)}
                    >
                      移入回收站
                    </button>
                  )}
                  {selectedTask.archivedAt && (
                    <>
                      <button
                        type="button"
                        className="primary-button compact"
                        disabled={busy}
                        onClick={() => void restoreOne(selectedTask.id)}
                      >
                        恢复
                      </button>
                      <button
                        type="button"
                        className="danger-button"
                        disabled={busy}
                        onClick={() => {
                          setSelectedIds(new Set([selectedTask.id]));
                          void previewPurge([selectedTask.id]);
                        }}
                      >
                        彻底删除
                      </button>
                    </>
                  )}
                </div>
                {browser && (
                  <div data-testid="task-center-browser-lease">
                    <BrowserLeaseCard
                      browser={browser}
                      tasks={tasks}
                    />
                  </div>
                )}
                <div className="task-progress-track">
                  <span style={{ width: `${selectedProgress?.percent || 0}%` }} />
                </div>
                <TaskRuntimePanel
                  task={selectedTask}
                  capabilities={details?.capabilities}
                  completed={
                    selectedTask.mode === "batch"
                      ? batches.filter((batch) => batch.status === "completed").length
                      : (details?.items || []).filter((item) => item.status === "completed").length
                  }
                  total={
                    selectedTask.mode === "batch"
                      ? batches.length
                      : details?.items.length || 0
                  }
                  candidateWaiting={candidateWaiting}
                  browserCaptchaVisible={Boolean(browser?.captchaVisible)}
                  browserCaptchaCheckedAt={browser?.captchaCheckedAt}
                  browserCaptchaSelector={browser?.captchaSelector}
                  alerts={runtimeAlerts}
                />
                {pageLimitWaiting && (
                  <PageLimitDecision checkpoint={selectedTask.checkpoint} />
                )}
                {scaleWaiting && (
                  <ScaleDecisionPanel
                    checkpoint={selectedTask.checkpoint}
                    busy={busy}
                    onConfirm={() => void runTaskAction("confirmScale")}
                  />
                )}
                {selectedTask.archivedAt && (
                  <div className="task-archive-banner">
                    <strong>任务已在回收站</strong>
                    <span>
                      归档于 {formatTime(selectedTask.archivedAt)}
                      {selectedTask.archiveReason
                        ? ` · ${selectedTask.archiveReason}`
                        : ""}
                    </span>
                    <small>恢复不会重新采集；彻底删除前可选择保留文件或移入 Windows 回收站。</small>
                  </div>
                )}
              </article>

              <TaskTimelinePanel events={details?.timeline} />

              <article className="panel task-items-panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">逐条状态</span>
                    <h3>{details?.items.length || 0} 条任务项</h3>
                  </div>
                  <div className="segmented task-item-filter">
                    <button type="button" className={itemFilter === "all" ? "selected" : ""} onClick={() => setItemFilter("all")}>全部</button>
                    <button type="button" className={itemFilter === "attention" ? "selected" : ""} onClick={() => setItemFilter("attention")}>需处理</button>
                  </div>
                </div>
                <div className="task-item-head">
                  <span>政策 / 候选</span><span>来源</span><span>状态</span><span>当前阶段</span><span>说明</span>
                </div>
                <div className="task-item-list">
                  {items.map((item) => {
                    const hasAmbiguity =
                      item.payload.candidates &&
                      item.payload.candidates.length > 1 &&
                      !item.payload.selectedCandidateKey;
                    return (
                      <div className="task-item-block" key={item.id}>
                        <div className="task-item-row">
                          <div>
                            <strong>{item.title || "未命名政策"}</strong>
                            <small>{item.policyKey}</small>
                          </div>
                          <span>{item.sourceType || "待识别"}</span>
                          <span className={`item-state ${item.status}`}>{formatItemStatus(item)}</span>
                          <span>{item.currentStage || "—"}</span>
                          {item.error ? (
                            <ExpandableValue
                              label="错误"
                              value={`${item.error.code}: ${item.error.message}`}
                            />
                          ) : (
                            <span>—</span>
                          )}
                        </div>
                        {hasAmbiguity && (
                          <div className="candidate-picker compact">
                            <strong>需要选择同名版本</strong>
                            <div className="candidate-options">
                              {item.payload.candidates?.map((candidate) => (
                                <button
                                  type="button"
                                  className="candidate-card"
                                  key={candidateKey(candidate)}
                                  disabled={busy}
                                  onClick={() => void chooseItemCandidate(item, candidate)}
                                >
                                  <strong>{candidate.title}</strong>
                                  <span>{candidate.issuingAuthority || "机关未知"} · {candidate.publicationDate || "日期未知"}</span>
                                  <small>{candidate.documentNumber || "无文号"} · {candidate.validity || "时效未知"}</small>
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {items.length === 0 && <div className="muted">没有符合条件的任务项</div>}
                </div>
              </article>

              {batches.length > 0 && (
                <article className="panel task-batches-panel">
                  <div className="panel-heading">
                    <div><span className="eyebrow">批次</span><h3>下载与校验断点</h3></div>
                  </div>
                  <div className="batch-table">
                    {batches.map((batch) => (
                      <div className="batch-row" key={batch.id}>
                        <strong>{batch.query}</strong>
                        <span>{batch.library === "central" ? "中央" : "地方"} · 第 {batch.page} 页</span>
                        <span>{batch.rowStart}–{batch.rowEnd} · {batch.selected.length} 条</span>
                        <span className={`task-status ${batch.status}`}>{itemStatusText(batch.status)}</span>
                        {batch.error ? (
                          <ExpandableValue
                            label="批次错误"
                            value={`${batch.error.code}: ${batch.error.message}`}
                          />
                        ) : (
                          <ExpandableValue
                            label="批次键"
                            value={batch.batchKey}
                          />
                        )}
                      </div>
                    ))}
                  </div>
                </article>
              )}

              {dataDetails && (
                <article className="panel task-artifacts-panel">
                  <div className="panel-heading">
                    <div><span className="eyebrow">最新数据运行</span><h3>{dataDetails.run.status} · {dataDetails.documents.length} 条政策</h3></div>
                  </div>
                  <div className="artifact-actions">
                    {dataDetails.artifacts.map((artifact) => (
                      <button
                        type="button"
                        className="secondary-button"
                        key={artifact.id}
                        onClick={() =>
                          void window.pkulaw.openLocalFile(artifact.path).catch((error) =>
                            setNotice(`打开产物失败：${String(error)}`)
                          )
                        }
                      >
                        {artifact.kind}
                      </button>
                    ))}
                    {dataDetails.artifacts.length === 0 && <span className="muted">尚未生成总目录或验收报告</span>}
                  </div>
                </article>
              )}
            </>
          ) : (
            <div className="single-empty">
              <div className="single-empty-mark">选</div>
              <h3>选择一个任务</h3>
              <p>可查看逐条详情、错误原因、批次断点并执行恢复。</p>
            </div>
          )}
        </div>
      </div>

      {cleanupPreview && (
        <div className="cleanup-confirm-backdrop">
          <section className="cleanup-confirm-panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">永久删除确认</span>
                <h3>清理 {cleanupPreview.tasks.length} 个任务</h3>
                <p className="muted">
                  已登记文件 {cleanupPreview.summary.registeredFileCount} 个，可移入回收站
                  {" "}
                  {cleanupPreview.summary.existingEligibleFileCount} 个；受保护或共享路径
                  {" "}
                  {cleanupPreview.summary.protectedFileCount} 个。
                </p>
              </div>
              <button
                type="button"
                className="text-button"
                onClick={() => setCleanupPreview(null)}
              >
                关闭
              </button>
            </div>
            <div className="cleanup-task-list">
              {cleanupPreview.tasks.map((task) => (
                <span key={task.id}>{task.name}</span>
              ))}
            </div>
            <div className="cleanup-path-list">
              {cleanupPreview.eligibleFiles.slice(0, 12).map((file) => (
                <div key={file.path}>
                  <span className={file.exists ? "cleanup-exists" : "cleanup-missing"}>
                    {file.exists ? "文件" : "不存在"}
                  </span>
                  <code title={file.path}>{file.path}</code>
                </div>
              ))}
              {cleanupPreview.eligibleFiles.length > 12 && (
                <small>另有 {cleanupPreview.eligibleFiles.length - 12} 个登记文件未展开。</small>
              )}
              {cleanupPreview.protectedFiles.map((file) => (
                <div key={`protected-${file.path}`}>
                  <span className="cleanup-protected">保留</span>
                  <code title={file.reason}>{file.reason} · {file.path}</code>
                </div>
              ))}
            </div>
            <div className="cleanup-confirm-note">
              <strong>不会递归删除输出根目录。</strong>
              <span>共享目录、未登记文件、磁盘根和用户主目录均保留。</span>
            </div>
            <div className="cleanup-confirm-actions">
              <button
                type="button"
                className="secondary-button"
                disabled={busy}
                onClick={() => setCleanupPreview(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="secondary-button"
                disabled={busy}
                onClick={() => void confirmPurge("keep")}
              >
                仅删除记录，保留文件
              </button>
              <button
                type="button"
                className="danger-button"
                disabled={busy}
                onClick={() => void confirmPurge("trash")}
              >
                移入 Windows 回收站并删除记录
              </button>
            </div>
          </section>
        </div>
      )}

      <div className="notice browser-notice">
        <span className="notice-icon">i</span>
        <span>{notice}</span>
        {appInfo?.dataDirectory && (
          <button type="button" className="text-button" onClick={() => void refreshTasks()}>
            刷新
          </button>
        )}
      </div>
    </section>
  );
}
