import { useEffect, useState } from "react";
import type {
  BrowserHostState,
  ProgressSnapshot,
  ProtocolError,
  ProcessingRunRecord,
  TaskRecord,
  TaskTimelineEvent
} from "../../shared/protocol";
import type { ManualRecoveryView } from "../../shared/task-workspace";
import { holdingTaskLabel, leaseModeLabel } from "../../shared/browser-lease";
import {
  asRecord,
  buildTaskTimeline,
  checkpointSummary,
  downloadIntentView,
  explainError,
  libraryLabel,
  pauseView,
  RUN_STATUS_LABELS,
  progressSnapshotView,
  searchSessionView,
  stageLabel,
  TASK_STATUS_LABELS,
  taskFailureView,
  type TaskFailureView,
  type TaskPrimaryAction,
  valueString,
  workflowSteps,
  yearWindowView
} from "./task-presentation";
import { searchModeDisplayLabel } from "./collector-search-mode";
import TaskPauseDetailDialog, {
  type TaskPauseDetail
} from "./TaskPauseDetailDialog";

export interface RuntimeAlert {
  id: string;
  title: string;
  detail: string;
  severity: "warning" | "error";
  createdAt: string;
}

export interface TaskActionFeedback {
  status: "processing" | "success" | "error";
  message: string;
}

export function ManualRecoveryStatus({
  recovery
}: {
  recovery: ManualRecoveryView;
}): React.JSX.Element | null {
  if (recovery.phase === "idle") return null;
  return (
    <div className="manual-recovery-status-row">
      <p
        className={`manual-recovery-status ${recovery.phase}`}
        aria-live="polite"
      >
        {recovery.detail}
      </p>
    </div>
  );
}

export function TaskPrimaryActionBar({
  action,
  label,
  busy,
  feedback,
  onAction
}: {
  action: TaskPrimaryAction;
  label: string;
  busy: boolean;
  feedback: TaskActionFeedback | null;
  onAction: () => void;
}): React.JSX.Element | null {
  const actionable = [
    "continue",
    "resume-from-checkpoint",
    "redetect"
  ].includes(action);
  if (!actionable && !feedback) return null;
  return (
    <section className="task-primary-action-bar">
      {feedback && (
        <p
          className={`task-action-feedback ${feedback.status}`}
          role={feedback.status === "error" ? "alert" : "status"}
        >
          {feedback.message}
        </p>
      )}
      {actionable && (
        <button
          type="button"
          className="primary-button compact"
          disabled={busy}
          onClick={onAction}
        >
          {label}
        </button>
      )}
    </section>
  );
}

export function ExpandableValue({
  label,
  value,
  testId
}: {
  label: string;
  value: string;
  testId?: string;
}): React.JSX.Element {
  const [copied, setCopied] = useState(false);
  const display = value || "未检测";
  async function copy(): Promise<void> {
    try {
      await window.pkulaw.copyText(display);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_200);
    } catch {
      setCopied(false);
    }
  }
  return (
    <details className="expandable-value" data-testid={testId}>
      <summary>
        <span>{label}</span>
        <code>{display}</code>
      </summary>
      <div>
        <code>{display}</code>
        <button type="button" onClick={() => void copy()}>
          {copied ? "已复制" : "复制"}
        </button>
      </div>
    </details>
  );
}

export function ScanContinuitySummary({
  checkpoint
}: {
  checkpoint: Record<string, unknown>;
}): React.JSX.Element | null {
  const journal =
    checkpoint.scanJournal &&
    typeof checkpoint.scanJournal === "object"
      ? checkpoint.scanJournal as Record<string, unknown>
      : null;
  const partition =
    checkpoint.yearPartition &&
    typeof checkpoint.yearPartition === "object"
      ? checkpoint.yearPartition as Record<string, unknown>
      : null;
  const resultWindow =
    checkpoint.resultWindow &&
    typeof checkpoint.resultWindow === "object"
      ? checkpoint.resultWindow as Record<string, unknown>
      : null;
  const probe =
    checkpoint.resultWindowProbe &&
    typeof checkpoint.resultWindowProbe === "object"
      ? checkpoint.resultWindowProbe as Record<string, unknown>
      : null;
  if (!journal && !partition) return null;
  const lastSession =
    journal?.lastStableSession &&
    typeof journal.lastStableSession === "object"
      ? journal.lastStableSession as Record<string, unknown>
      : null;
  const combinations = Array.isArray(journal?.combinations)
    ? journal.combinations.length
    : 0;
  const completedPages = Array.isArray(journal?.completedPages)
    ? journal.completedPages.join(", ")
    : "—";
  const lastPage = Number(lastSession?.page || 0);
  const partitionStatus = String(partition?.status || "");
  return (
    <section className="runtime-subcard scan-continuity-card">
      <div className="runtime-subcard-heading">
        <strong>扫描断点与结果窗口</strong>
        <span>{journal?.sealed ? "已封存" : "可续扫"}</span>
      </div>
      <p>
        已完成组合 {combinations} 个；已完成页 {completedPages || "—"}
        {lastPage > 0 ? `；下一页 ${lastPage + 1}` : ""}
      </p>
      {resultWindow && (
        <p>
          结果窗口：站点报告 {Number(resultWindow.reportedTotalPages || 0)} 页，
          按结果数与页大小应为 {Number(resultWindow.expectedTotalPages || 0)} 页；
          探测状态 {String(probe?.status || "未知")}
          {probe?.code ? `；${String(probe.code)}` : ""}
        </p>
      )}
      {partitionStatus && (
        <p>
          {partitionStatus === "complete"
            ? "检测到结果窗口限制，已按年份拆分："
            : "年份拆分："}
          {partitionStatus === "complete"
            ? `已完成，读取 ${Number(partition?.actualRowsRead || 0)} 条`
            : `待执行，共 ${Array.isArray(partition?.years) ? partition.years.length : 0} 个真实年份`}
          {partition?.expectedResultCount
            ? `；原始结果 ${Number(partition.expectedResultCount)} 条`
            : ""}
        </p>
      )}
      {journal && (
        <ExpandableValue
          label="扫描日志详情"
          value={JSON.stringify(journal, null, 2)}
        />
      )}
    </section>
  );
}

export function BrowserLeaseCard({
  browser,
  tasks,
  compact = false
}: {
  browser: BrowserHostState | null;
  tasks: TaskRecord[];
  compact?: boolean;
}): React.JSX.Element {
  if (!browser) {
    return (
      <section className="runtime-card browser-lease-card pending" data-testid="browser-lease-card">
        <strong>浏览器租约：未知</strong>
        <span>尚未读取 BrowserHost 状态。</span>
      </section>
    );
  }
  const lease = browser.browserLease;
  const task = tasks.find((item) => item.id === lease.taskId);
  return (
    <section
      className={`runtime-card browser-lease-card ${lease.available ? "available" : "busy"}`}
      data-testid="browser-lease-card"
    >
      <div className="runtime-card-heading">
        <strong>{holdingTaskLabel(browser, tasks)}</strong>
        <span>{lease.available ? "可启动" : "已占用"}</span>
      </div>
      {!compact && !lease.available && (
        <div className="runtime-facts">
          <span>任务 <b>{task?.name || lease.taskId || "未知"}</b></span>
          <span>模式 <b>{leaseModeLabel(lease.mode)}</b></span>
          <span>leaseId <b>{lease.leaseId || "未知"}</b></span>
          <span>开始 <b>{lease.createdAt || "未知"}</b></span>
          <span>更新 <b>{lease.updatedAt || "未知"}</b></span>
        </div>
      )}
      {!lease.available && (
        <ExpandableValue
          label="浏览器租约持有信息"
          value={`${task?.name || lease.taskId || "未知任务"} · ${leaseModeLabel(lease.mode)} · leaseId=${lease.leaseId || "unknown"} · createdAt=${lease.createdAt || "unknown"} · updatedAt=${lease.updatedAt || "unknown"}`}
        />
      )}
    </section>
  );
}

export function SearchContextCard({
  task
}: {
  task: TaskRecord;
}): React.JSX.Element | null {
  const session = searchSessionView(task);
  const checkpoint = task.checkpoint || {};
  const yearWindow = yearWindowView(checkpoint);
  if (
    !session.query &&
    !checkpoint.searchId &&
    !checkpoint.currentAction
  ) {
    return null;
  }
  return (
    <section className="runtime-card search-context-card" data-testid="task-search-context">
      <div className="runtime-card-heading">
        <strong>当前检索身份</strong>
        <span>{stageLabel(task.currentStage, String(checkpoint.currentAction || ""))}</span>
      </div>
      <div className="workflow-step-strip" data-testid="workflow-stage-strip">
        {workflowSteps(task).map((step) => (
          <span className={`workflow-step ${step.state}`} key={step.id}>
            {step.label}
          </span>
        ))}
      </div>
      <div className="session-grid">
        <span>查询<b>{session.query || "未记录"}</b></span>
        <span>
          匹配
          <b>
            {session.matchMode
              ? searchModeDisplayLabel(session.matchMode)
              : "未记录"}
          </b>
        </span>
        <span>子库<b>{libraryLabel(session.library)}</b></span>
        <span>年份<b>{session.year || "未记录"}</b></span>
        <span>省份<b>{session.province || "未记录"}</b></span>
        <span>时效性<b>{session.timeliness || "未记录"}</b></span>
        <span>页码<b>{session.page ? `${session.page}${session.totalPages ? ` / ${session.totalPages}` : ""}` : "未记录"}</b></span>
        <span>每页条数<b>{session.pageSize || "未记录"}</b></span>
        <span>分组<b>{session.groupingMode === "ungrouped" ? "不分组" : session.groupingMode || "未记录"}</b></span>
        <span>结果数<b>{session.resultCount || "未记录"}</b></span>
        <span>当前页行数<b>{session.rowCount || "未记录"}</b></span>
        <span>searchId<b>{String(checkpoint.searchId || "未记录").slice(0, 18)}</b></span>
      </div>
      {yearWindow && (
        <div className="year-window-adjustment">
          <strong>年份范围已自动调整</strong>
          <span>请求年份 {yearWindow.original}</span>
          <span>实际年份 {yearWindow.effective}</span>
          <small>{yearWindow.detail}</small>
        </div>
      )}
      {session.signature && (
        <ExpandableValue
          label="结果签名"
          value={session.signature}
          testId="result-signature"
        />
      )}
      <ExpandableValue
        label="当前断点"
        value={checkpointSummary(checkpoint)}
      />
      <ExpandableValue label="输出目录" value={task.outputDir || "未记录"} />
    </section>
  );
}

export function DownloadIntentCard({
  source
}: {
  source: Record<string, unknown>;
}): React.JSX.Element | null {
  const intent = downloadIntentView(source);
  if (!intent) return null;
  return (
    <section
      className={`runtime-card download-intent-card ${intent.status}`}
      data-testid="download-intent-card"
    >
      <div className="runtime-card-heading">
        <strong>当前下载意图：{intent.kindLabel}</strong>
        <span>{intent.statusLabel}</span>
      </div>
      <div className="runtime-facts">
        <span>已消费 <b>{intent.matchedDownloads}/{intent.maxDownloads}</b></span>
        <span>预期后缀 <b>{intent.expectedSuffixes.join(" / ") || "未记录"}</b></span>
        <span>允许来源 <b>{intent.sourceHosts.join(" / ") || "未记录"}</b></span>
      </div>
      <ExpandableValue label="intentId" value={intent.intentId} />
      {intent.downloadId && (
        <ExpandableValue label="downloadId" value={intent.downloadId} />
      )}
      {intent.path && <ExpandableValue label="临时文件" value={intent.path} />}
    </section>
  );
}

export function ProgressSnapshotCard({
  status,
  progress,
  currentStage,
  progressSnapshot,
  completed,
  total
}: {
  status: string;
  progress: number;
  currentStage: string;
  progressSnapshot?: ProgressSnapshot | null;
  completed?: number;
  total?: number;
}): React.JSX.Element {
  const view = progressSnapshotView({
    status,
    progress,
    currentStage,
    progressSnapshot,
    completed,
    total
  });
  return (
    <section
      className={`runtime-card progress-snapshot-card ${status} ${view.legacy ? "legacy" : ""}`}
      data-testid="progress-snapshot-card"
    >
      <div className="runtime-card-heading">
        <strong>{view.stageText}</strong>
        <span>
          {TASK_STATUS_LABELS[status as keyof typeof TASK_STATUS_LABELS] ||
            RUN_STATUS_LABELS[status] ||
            status}
          {" · "}
          {view.percent}%
        </span>
      </div>
      <div className="task-progress-track">
        <span style={{ width: `${view.percent}%` }} />
      </div>
      <div className="runtime-facts">
        <span>已处理 {view.completedUnits}/{view.totalUnits}</span>
        <span>成功 {view.succeeded}</span>
        <span>失败 {view.failed}</span>
        <span>待核验 {view.pendingReview}</span>
        <span>共 {view.totalUnits}</span>
      </div>
      {view.partialSuccessWarning && (
        <p className="partial-progress-warning">
          {view.partialSuccessWarning}；这不是 100% 成功。
        </p>
      )}
      {view.legacy && (
        <p className="muted">
          旧任务未记录统一进度快照，界面按现有进度和任务项安全回退。
        </p>
      )}
    </section>
  );
}

function MissingDownloadIntentCard(): React.JSX.Element {
  return (
    <section
      className="runtime-card download-intent-card pending"
      data-testid="download-intent-card"
    >
      <div className="runtime-card-heading">
        <strong>当前下载意图：未创建</strong>
        <span>未到下载阶段</span>
      </div>
      <p>
        只有进入 Word、批量目录、批量 ZIP 或政府官网附件步骤时才会创建独立 intent。
      </p>
    </section>
  );
}

export function PauseReasonCard({
  status,
  checkpoint,
  error,
  candidateWaiting = false,
  currentStage = ""
}: {
  status: string;
  checkpoint: Record<string, unknown>;
  error: ProtocolError | null;
  candidateWaiting?: boolean;
  currentStage?: string;
}): React.JSX.Element | null {
  const [detailOpen, setDetailOpen] = useState(false);
  const pause = pauseView(status, checkpoint, error, candidateWaiting);
  if (!pause) return null;
  const detail: TaskPauseDetail = {
    code: String(error?.code || pause.kind.toUpperCase()),
    title: pause.title,
    message: String(error?.message || pause.detail),
    stage: currentStage || "未记录",
    checkpoint: checkpointSummary(checkpoint),
    recommendation:
      pause.kind === "page-limit"
        ? "选择继续到尾页，或停止并保留分页断点。"
        : pause.kind === "scale"
          ? "确认批量规模后继续。"
          : pause.kind === "candidate"
            ? "在候选列表中选择明确版本。"
            : pause.detail,
    raw: {
      error: error || null,
      checkpoint
    }
  };
  return (
    <section
      className={`runtime-card pause-reason-card ${pause.kind}`}
      data-testid="pause-reason-card"
    >
      <span className="eyebrow">{pause.automatic ? "自动暂停" : "任务控制"}</span>
      <strong>{pause.title}</strong>
      <p>{pause.detail}</p>
      <small>{checkpointSummary(checkpoint)}</small>
      <button
        type="button"
        className="secondary-button compact"
        onClick={() => setDetailOpen(true)}
      >
        查看完整原因
      </button>
      {detailOpen && (
        <TaskPauseDetailDialog
          detail={detail}
          onClose={() => setDetailOpen(false)}
        />
      )}
    </section>
  );
}

export function TaskErrorCard({
  error,
  status,
  currentStage,
  checkpoint,
  completed,
  total
}: {
  error: ProtocolError | null;
  status: string;
  currentStage: string;
  checkpoint: Record<string, unknown>;
  completed?: number;
  total?: number;
}): React.JSX.Element | null {
  const explanation = explainError(error, status, currentStage, checkpoint);
  if (!explanation || !error) return null;
  const details = asRecord(error.details);
  const bodyQuality = asRecord(checkpoint.bodyQuality);
  const actualFile =
    valueString(details, "actualFile", "path", "filePath") ||
    valueString(checkpoint, "currentCatalogFile", "catalogPath", "currentZipFile", "zipPath");
  const expectedFormat = valueString(
    details,
    "expectedFormat",
    "expectedSuffix",
    "expectedSuffixes"
  );
  const actualFormat = valueString(details, "actualFormat", "actualSuffix");
  const intentId =
    valueString(details, "intentId") ||
    valueString(
      asRecord(checkpoint.currentDownloadIntent),
      "intentId"
    );
  const downloadId =
    valueString(details, "downloadId") ||
    valueString(
      asRecord(checkpoint.currentDownloadIntent),
      "downloadId"
    );
  const bodyCompleteness =
    valueString(details, "bodyCompleteness") ||
    valueString(bodyQuality, "status");
  return (
    <section
      className={`runtime-card task-error-card ${explanation.severity}`}
      data-testid="task-error-card"
    >
      <div className="runtime-card-heading">
        <strong>{explanation.title}</strong>
        <span>{explanation.severity === "fatal" ? "必须停止或诊断" : explanation.severity === "manual" ? "等待人工" : "可恢复错误"}</span>
      </div>
      <div className="error-code-line">
        <code>{String(error.code || "UNKNOWN_ERROR")}</code>
        {explanation.hardStop && <b>硬停止</b>}
      </div>
      <p><b>原因：</b>{explanation.reason || error.message}</p>
      <p><b>阶段：</b>{explanation.stage}</p>
      <p><b>动作策略：</b>{explanation.actionPolicy}</p>
      <p>
        <b>断点：</b>
        {completed !== undefined && total !== undefined
          ? `已完成 ${completed}/${total} 条 · `
          : ""}
        {checkpointSummary(checkpoint)}
      </p>
      {actualFile && <p><b>实际文件：</b>{actualFile}</p>}
      {(expectedFormat || actualFormat) && (
        <p>
          <b>格式契约：</b>
          预期 {expectedFormat || "未知"} · 实际 {actualFormat || "未知"}
        </p>
      )}
      {intentId && <p><b>intentId：</b>{intentId}</p>}
      {downloadId && <p><b>downloadId：</b>{downloadId}</p>}
      {bodyCompleteness && (
        <p><b>正文完整性：</b>{bodyCompleteness}</p>
      )}
      <p><b>恢复建议：</b>{explanation.recommendation}</p>
      <div className="recovery-actions">
        {explanation.allowedActions.map((action) => (
          <span key={action}>{action}</span>
        ))}
      </div>
      <ExpandableValue
        label="完整错误"
        value={JSON.stringify(
          {
            code: error.code,
            message: error.message,
            details: error.details || {},
            checkpoint
          },
          null,
          2
        )}
      />
    </section>
  );
}

export function RuntimeAlertsCard({
  alerts
}: {
  alerts: RuntimeAlert[];
}): React.JSX.Element | null {
  if (!alerts.length) return null;
  return (
    <section className="runtime-card runtime-alert-card" data-testid="runtime-alert-card">
      <div className="runtime-card-heading">
        <strong>运行时告警</strong>
        <span>{alerts.length} 条</span>
      </div>
      {alerts.slice(-5).reverse().map((alert) => (
        <div className={`runtime-alert ${alert.severity}`} key={alert.id}>
          <strong>{alert.title}</strong>
          <span>{alert.detail}</span>
          <small>{alert.createdAt}</small>
        </div>
      ))}
    </section>
  );
}

export function BrowserAccessStateCard({
  captchaVisible,
  captchaCheckedAt = "",
  captchaSelector = ""
}: {
  captchaVisible: boolean;
  captchaCheckedAt?: string;
  captchaSelector?: string;
}): React.JSX.Element | null {
  if (!captchaVisible) return null;
  return (
    <section
      className="runtime-card pause-reason-card captcha"
      data-testid="task-captcha-state"
    >
      <span className="eyebrow">浏览器与任务共用状态</span>
      <strong>等待人工完成滑块或验证码</strong>
      <p>任务卡显示浏览器当前实时状态；验证码仍可见时，继续按钮已同步锁定。</p>
      <div className="runtime-facts">
        <span>检测结果 <b>验证码可见</b></span>
        <span>检测时间 <b>{captchaCheckedAt || "未记录"}</b></span>
      </div>
      <ExpandableValue
        label="验证码控件"
        value={captchaSelector || "未记录"}
      />
    </section>
  );
}

export function TaskFailureSummaryCard({
  failure,
  onOpen
}: {
  failure: TaskFailureView;
  onOpen: () => void;
}): React.JSX.Element {
  return (
    <section
      className={`runtime-card task-failure-summary ${failure.severity}`}
      data-testid="task-failure-summary"
    >
      <span className="eyebrow">任务已停止</span>
      <strong>{failure.title}</strong>
      <p>{failure.summary}</p>
      <small>{failure.nextStep}</small>
      <button
        type="button"
        className="secondary-button compact"
        onClick={onOpen}
      >
        查看停止原因
      </button>
    </section>
  );
}

export function TaskRuntimePanel({
  task,
  completed,
  total,
  candidateWaiting,
  browserCaptchaVisible = false,
  browserCaptchaCheckedAt,
  browserCaptchaSelector,
  alerts = [],
  primaryAction = "none",
  primaryActionLabel = "",
  actionBusy = false,
  actionFeedback = null,
  onPrimaryAction,
  onPauseDetailOpenChange
}: {
  task: TaskRecord;
  completed?: number;
  total?: number;
  candidateWaiting?: boolean;
  browserCaptchaVisible?: boolean;
  browserCaptchaCheckedAt?: string;
  browserCaptchaSelector?: string;
  alerts?: RuntimeAlert[];
  primaryAction?: TaskPrimaryAction;
  primaryActionLabel?: string;
  actionBusy?: boolean;
  actionFeedback?: TaskActionFeedback | null;
  onPrimaryAction?: () => void;
  onPauseDetailOpenChange?: (open: boolean) => void;
}): React.JSX.Element {
  const [detailOpen, setDetailOpen] = useState(false);
  const failure = taskFailureView(task);

  useEffect(() => {
    onPauseDetailOpenChange?.(detailOpen);
    return () => {
      if (detailOpen) {
        onPauseDetailOpenChange?.(false);
      }
    };
  }, [detailOpen, onPauseDetailOpenChange]);

  const detail: TaskPauseDetail = {
    code: String(task.error?.code || ""),
    title: failure?.title || "任务已停止",
    message: String(task.error?.message || failure?.summary || ""),
    summary: failure?.summary,
    stage: stageLabel(
      task.currentStage,
      String(task.checkpoint.currentAction || "")
    ),
    checkpoint: checkpointSummary(task.checkpoint),
    recommendation: failure?.nextStep || "",
    nextStep: failure?.nextStep,
    raw: {
      error: task.error,
      checkpoint: task.checkpoint
    }
  };

  return (
    <div className="task-runtime-stack" data-testid="task-runtime-panel">
      <TaskPrimaryActionBar
        action={primaryAction}
        label={primaryActionLabel}
        busy={actionBusy}
        feedback={actionFeedback}
        onAction={() => onPrimaryAction?.()}
      />
      <ProgressSnapshotCard
        status={task.status}
        progress={task.progress}
        currentStage={task.currentStage}
        progressSnapshot={task.progressSnapshot}
        completed={completed}
        total={total}
      />
      {failure && (
        <TaskFailureSummaryCard
          failure={failure}
          onOpen={() => setDetailOpen(true)}
        />
      )}
      <div className="task-runtime-scroll">
        <SearchContextCard task={task} />
        <BrowserAccessStateCard
          captchaVisible={browserCaptchaVisible}
          captchaCheckedAt={browserCaptchaCheckedAt}
          captchaSelector={browserCaptchaSelector}
        />
        {downloadIntentView(task.checkpoint) ? (
          <DownloadIntentCard source={task.checkpoint} />
        ) : (
          <MissingDownloadIntentCard />
        )}
        {!failure && (
          <PauseReasonCard
            status={task.status}
            checkpoint={task.checkpoint}
            error={task.error}
            candidateWaiting={candidateWaiting}
            currentStage={task.currentStage}
          />
        )}
        <RuntimeAlertsCard alerts={alerts} />
      </div>
      {detailOpen && (
        <TaskPauseDetailDialog
          detail={detail}
          onClose={() => setDetailOpen(false)}
        />
      )}
    </div>
  );
}

export function ProcessingRuntimePanel({
  run,
  completed,
  total,
  alerts = []
}: {
  run: ProcessingRunRecord;
  completed: number;
  total: number;
  alerts?: RuntimeAlert[];
}): React.JSX.Element {
  const summary = run.summary || {};
  return (
    <div className="task-runtime-stack" data-testid="data-runtime-panel">
      <ProgressSnapshotCard
        status={run.status}
        progress={run.progress}
        currentStage={run.currentStage}
        progressSnapshot={run.progressSnapshot}
        completed={completed}
        total={total}
      />
      {downloadIntentView(summary) ? (
        <DownloadIntentCard source={summary} />
      ) : (
        <MissingDownloadIntentCard />
      )}
      <PauseReasonCard
        status={run.status}
        checkpoint={summary}
        error={run.error}
        currentStage={run.currentStage}
      />
      <TaskErrorCard
        error={run.error}
        status={run.status}
        currentStage={run.currentStage}
        checkpoint={summary}
        completed={completed}
        total={total}
      />
      <RuntimeAlertsCard alerts={alerts} />
    </div>
  );
}

export function TaskTimelinePanel({
  events
}: {
  events: TaskTimelineEvent[] | undefined;
}): React.JSX.Element {
  const timeline = buildTaskTimeline(events);
  return (
    <section className="panel task-timeline-panel" data-testid="task-timeline">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">阶段时间线</span>
          <h3>已去重的关键状态</h3>
        </div>
        <span className="data-count">{timeline.length} 个阶段</span>
      </div>
      <div className="task-timeline">
        {timeline.length ? (
          timeline
            .slice()
            .reverse()
            .map((item) => (
              <div className={`timeline-row ${item.status}`} key={item.id}>
                <span className="timeline-dot" />
                <div>
                  <strong>{item.label}</strong>
                  {item.detail && <small>{item.detail}</small>}
                </div>
                <time>{item.createdAt}</time>
              </div>
            ))
        ) : (
          <div className="muted">尚未记录任务阶段。</div>
        )}
      </div>
    </section>
  );
}
