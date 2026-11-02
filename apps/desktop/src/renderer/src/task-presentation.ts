import type {
  ProgressSnapshot,
  ProtocolError,
  TaskRecord,
  TaskStatus,
  TaskTimelineEvent
} from "../../shared/protocol";

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "待启动",
  preflight: "启动预检",
  waiting_login: "等待机构登录",
  running: "运行中",
  waiting_manual: "等待人工处理",
  verifying: "校验中",
  pausing: "正在等待安全暂停",
  paused: "已暂停",
  stopping: "正在安全停止",
  completed: "已完成",
  partial: "部分完成",
  stopped: "已停止",
  failed: "失败"
};

export const RUN_STATUS_LABELS: Record<string, string> = {
  pending: "等待开始",
  running: "处理中",
  waiting_manual: "等待人工处理",
  waiting_login: "等待机构登录",
  pausing: "正在等待安全暂停",
  paused: "已暂停",
  stopping: "正在安全停止",
  completed: "已完成",
  partial: "部分完成",
  stopped: "已停止",
  failed: "失败"
};

const ACTION_LABELS: Record<string, string> = {
  preflight: "浏览器与运行环境预检",
  "preflight-complete": "预检完成",
  search: "验证新检索",
  "after-search": "检索身份已确认",
  "read-results": "读取并核验结果",
  "next-page": "翻到下一页",
  "after-next-page": "分页身份已确认",
  "results-complete": "结果读取完成",
  "select-batch": "选择批次",
  "after-select-batch": "批次勾选已核验",
  "catalog-download": "下载并校验目录",
  "after-catalog-validation": "目录已校验",
  "zip-download": "下载并校验 ZIP",
  "after-zip-validation": "ZIP 已校验",
  "prepare-batches": "生成批量任务",
  "next-batch": "准备下一批次",
  "after-batch": "批次原子发布与登记完成",
  "word-download": "下载 Word",
  "after-word-download": "Word 下载与校验完成",
  "open-detail": "读取政策详情",
  "after-detail": "详情身份已核验",
  "candidate-selection": "等待同名版本选择",
  "return-to-results": "返回结果页",
  "prepare-item": "准备政策条目",
  "next-item": "处理下一条政策",
  "page-limit-decision": "等待分页上限决策",
  "scale-confirmation": "等待批量规模确认",
  "finalize-artifacts": "生成并校验数据产物",
  "after-artifacts": "数据产物登记完成",
  "process-policy": "处理政策正文",
  "after-policy": "当前政策写入完成",
  "government-search": "检索政府官网原文",
  "government-attachment-download": "下载政府官网 Word 附件",
  started: "开始处理"
};

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function stageLabel(
  stage: string,
  action = ""
): string {
  const direct = ACTION_LABELS[stage] || ACTION_LABELS[action];
  if (direct) return direct;
  const normalized = stage.replaceAll("_", "-");
  const indexed =
    normalized.match(/^batch-(\d+)-(\d+)$/) ||
    normalized.match(/^batch-completed-(\d+)-(\d+)$/);
  if (indexed) {
    return normalized.startsWith("batch-completed")
      ? `批次 ${indexed[1]}/${indexed[2]} 已完成`
      : `正在处理批次 ${indexed[1]}/${indexed[2]}`;
  }
  const detail = normalized.match(/^detail-(\d+)-(\d+)$/);
  if (detail) return `读取详情 ${detail[1]}/${detail[2]}`;
  const downloaded = normalized.match(/^downloaded-(\d+)-(\d+)$/);
  if (downloaded) return `Word 已完成 ${downloaded[1]}/${downloaded[2]}`;
  const document = normalized.match(/^document-(\d+)-(\d+)$/);
  if (document) return `处理正文 ${document[1]}/${document[2]}`;
  const known: Record<string, string> = {
    pending: "等待启动",
    preflight: "启动预检",
    scan: "扫描搜索结果",
    searching: "验证新检索",
    collecting: "读取政策结果",
    "results-collected": "结果已收集",
    detail: "读取政策详情",
    download: "逐条下载与核验",
    documents: "处理正文与证据",
    select: "选择批次",
    catalog: "校验目录",
    zip: "校验 Word ZIP",
    publish: "原子发布并登记 SQLite",
    "batches-prepared": "批次已生成",
    "preparing-batches": "正在生成批次",
    "waiting-login": "等待机构登录",
    "waiting-manual": "等待人工处理",
    "waiting-candidate": "等待同名版本选择",
    "candidate-selection": "等待同名版本选择",
    "candidate-selected": "同名版本已选择",
    "waiting-page-limit": "分页上限已到达",
    "page-limit-continued": "已确认扫描到尾页",
    "waiting-scale-confirmation": "等待批量规模确认",
    "scale-confirmed": "批量规模已确认",
    "pause-requested": "等待安全暂停",
    pausing: "正在等待安全暂停",
    paused: "已暂停",
    resumed: "已继续",
    stopping: "正在安全停止",
    stopped: "已停止",
    "manual-continued": "人工处理已确认",
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
    "application-interrupted": "应用异常中断后已安全停止",
    "exact-title-missing": "未找到精确标题"
  };
  return known[normalized] || stage || "等待状态";
}

export interface ProgressSnapshotView {
  overall: number;
  percent: number;
  stage: string;
  stageText: string;
  completedUnits: number;
  totalUnits: number;
  succeeded: number;
  failed: number;
  pendingReview: number;
  legacy: boolean;
  partialSuccessWarning: string;
}

export function progressSnapshotView(input: {
  status: string;
  progress: number;
  currentStage?: string;
  progressSnapshot?: ProgressSnapshot | null;
  completed?: number;
  total?: number;
}): ProgressSnapshotView {
  const snapshot = input.progressSnapshot;
  const legacy =
    !snapshot ||
    typeof snapshot.overall !== "number" ||
    typeof snapshot.stage !== "string";
  const fallbackTotal = Math.max(0, Number(input.total || 0));
  const fallbackCompleted = Math.max(0, Number(input.completed || 0));
  const snapshotTotal = Math.max(0, Number(snapshot?.totalUnits || 0));
  const totalUnits = snapshotTotal || fallbackTotal;
  const completedUnits = Math.max(
    0,
    Number(snapshot?.completedUnits ?? fallbackCompleted)
  );
  const succeeded = Math.max(
    0,
    Number(snapshot?.succeeded ?? fallbackCompleted)
  );
  const failed = Math.max(0, Number(snapshot?.failed || 0));
  const pendingReview = Math.max(
    0,
    Number(
      snapshot?.pendingReview ??
        Math.max(0, totalUnits - succeeded - failed)
    )
  );
  const rawOverall = Math.max(
    0,
    Math.min(1, Number(snapshot?.overall ?? input.progress ?? 0))
  );
  const overall =
    input.status === "partial" ? Math.min(rawOverall, 0.99) : rawOverall;
  const stage = snapshot?.stage || input.currentStage || input.status;
  const partialSuccessWarning =
    input.status === "partial"
      ? `工作步骤完成，但不是 100% 成功：成功 ${succeeded}/${totalUnits || Math.max(1, completedUnits)}`
      : "";
  return {
    overall,
    percent: Math.round(overall * 100),
    stage,
    stageText: stageLabel(stage),
    completedUnits,
    totalUnits,
    succeeded,
    failed,
    pendingReview,
    legacy,
    partialSuccessWarning
  };
}

export function libraryLabel(value: unknown): string {
  if (value === "central") return "中央法规";
  if (value === "local") return "地方法规";
  if (value === "both") return "中央 + 地方";
  return String(value || "未记录");
}

export interface SearchSessionView {
  query: string;
  matchMode: string;
  library: string;
  year: string;
  province: string;
  timeliness: string;
  page: string;
  totalPages: string;
  pageSize: string;
  groupingMode: string;
  resultCount: string;
  rowCount: string;
  signature: string;
}

export interface WorkflowStep {
  id: string;
  label: string;
  state: "done" | "current" | "pending" | "error";
}

export function valueString(
  record: Record<string, unknown>,
  ...keys: string[]
): string {
  for (const key of keys) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== "") {
      return String(value);
    }
  }
  return "";
}

export function searchSessionView(task: TaskRecord): SearchSessionView {
  const checkpoint = asRecord(task.checkpoint);
  const identity = asRecord(checkpoint.searchIdentity);
  const input = asRecord(task.input);
  const rawQueries = input.queries || input.keywords;
  const inputQuery = Array.isArray(rawQueries) ? String(rawQueries[0] || "") : "";
  const signature = valueString(identity, "resultSignature");
  return {
    query:
      valueString(identity, "query") ||
      valueString(checkpoint, "currentQuery", "query") ||
      inputQuery,
    matchMode:
      valueString(identity, "matchMode") ||
      valueString(input, "matchMode"),
    library:
      valueString(identity, "library") ||
      valueString(checkpoint, "currentLibrary", "library") ||
      valueString(input, "library"),
    year:
      valueString(identity, "actualYear", "year") ||
      valueString(checkpoint, "currentYear", "year"),
    province:
      valueString(identity, "actualProvince", "province") ||
      valueString(input, "province"),
    timeliness:
      valueString(identity, "actualTimeliness", "timeliness") ||
      valueString(input, "timeliness"),
    page:
      valueString(identity, "page") ||
      valueString(checkpoint, "currentPage", "page"),
    totalPages: valueString(identity, "totalPages") ||
      valueString(checkpoint, "totalPages"),
    pageSize:
      valueString(identity, "actualPageSize", "requestedPageSize") ||
      valueString(input, "pageSize"),
    groupingMode: valueString(identity, "groupingMode"),
    resultCount: valueString(identity, "resultCount"),
    rowCount: valueString(identity, "rowCount"),
    signature
  };
}

export function workflowSteps(task: TaskRecord): WorkflowStep[] {
  const checkpoint = asRecord(task.checkpoint);
  const session = searchSessionView(task);
  const status = task.status;
  const action = valueString(checkpoint, "currentAction");
  const errorCode = String(task.error?.code || "");
  const definitions =
    task.mode === "batch"
      ? [
          ["preflight", "绑定与登录"],
          ["search", "验证新检索"],
          ["filters", "应用筛选"],
          ["page-size", "验证页大小"],
          ["grouping", "切换不分组"],
          ["select", "选择批次"],
          ["catalog", "校验目录"],
          ["zip", "校验 ZIP"],
          ["publish", "原子发布"],
          ["register", "登记 SQLite"]
        ]
      : [
          ["preflight", "绑定与登录"],
          ["search", "验证新检索"],
          ["filters", "应用筛选"],
          ["page-size", "验证页大小"],
          ["grouping", "切换不分组"],
          ["detail", "读取详情"],
          ["download", "下载与校验 Word"],
          ["complete", "登记完成"]
        ];
  const evidence: Record<string, boolean> = {
    preflight: !["pending"].includes(status),
    search: Boolean(
      checkpoint.searchId ||
        checkpoint.searchIdentity ||
        checkpoint.retrievalContexts
    ),
    filters: Boolean(session.year || session.province || session.timeliness),
    "page-size": Boolean(session.pageSize),
    grouping: session.groupingMode === "ungrouped",
    select: Boolean(
      action.includes("batch") ||
        action.includes("catalog") ||
        action.includes("zip") ||
        checkpoint.lastCompletedBatch
    ),
    catalog: Boolean(
      checkpoint.catalogIntentId ||
        action.includes("catalog") ||
        action.includes("zip") ||
        checkpoint.lastCompletedBatch
    ),
    zip: Boolean(
      checkpoint.zipIntentId ||
        action.includes("zip") ||
        checkpoint.lastCompletedBatch
    ),
    publish: Boolean(checkpoint.publishManifest || checkpoint.lastCompletedBatch),
    register: Boolean(checkpoint.lastCompletedBatch || status === "completed"),
    detail: Boolean(
      action.includes("detail") ||
        action.includes("word") ||
        action.includes("candidate") ||
        task.progress > 0.15
    ),
    download: Boolean(
      action.includes("word") ||
        action === "results-complete" ||
        status === "completed"
    ),
    complete: ["completed", "partial"].includes(status)
  };
  const errorIndex =
    ["preflight", "waiting_login"].includes(status)
      ? 0
      : /DOWNLOAD_|INVALID_WORD/.test(errorCode)
      ? task.mode === "batch"
        ? 6
        : 6
      : /FILTER_|YEAR_FILTER_|PAGE_SIZE_|RESULT_GROUPING_/.test(errorCode)
        ? /PAGE_SIZE_/.test(errorCode)
          ? 3
          : /RESULT_GROUPING_/.test(errorCode)
            ? 4
            : 2
        : /PAGE_SELECTOR_|PAGE_STRUCTURE_/.test(errorCode)
          ? 1
          : /SEARCH_|RESULTS_|STALE/.test(errorCode)
            ? 1
            : /BATCH_ARTIFACT_/.test(errorCode)
              ? task.mode === "batch"
                ? 8
                : 7
              : -1;
  let currentAssigned = false;
  return definitions.map(([id, label], index) => {
    if (errorIndex === index) {
      currentAssigned = true;
      return { id, label, state: "error" };
    }
    if (errorIndex >= 0 && index > errorIndex) {
      return { id, label, state: "pending" };
    }
    if (evidence[id]) {
      return { id, label, state: "done" };
    }
    if (!currentAssigned && !["failed", "stopped"].includes(status)) {
      currentAssigned = true;
      return { id, label, state: "current" };
    }
    return { id, label, state: "pending" };
  });
}

export interface DownloadIntentView {
  intentId: string;
  kind: string;
  kindLabel: string;
  status: string;
  statusLabel: string;
  downloadId: string;
  path: string;
  maxDownloads: number;
  matchedDownloads: number;
  expectedSuffixes: string[];
  sourceHosts: string[];
}

const DOWNLOAD_KIND_LABELS: Record<string, string> = {
  word: "Word",
  "batch-catalog": "批量目录",
  "batch-zip": "Word ZIP",
  "government-word": "政府官网 Word"
};

const DOWNLOAD_STATUS_LABELS: Record<string, string> = {
  pending: "已创建，等待 started",
  started: "下载已开始",
  progressing: "下载进行中",
  completed: "下载完成，等待校验",
  validated: "文件校验通过",
  validation_failed: "文件校验失败",
  failed: "下载失败"
};

export function downloadIntentView(
  source: Record<string, unknown>
): DownloadIntentView | null {
  const raw =
    asRecord(source.currentDownloadIntent).intentId
      ? asRecord(source.currentDownloadIntent)
      : asRecord(source.downloadIntent).intentId
        ? asRecord(source.downloadIntent)
        : asRecord(source.lastDownloadIntent);
  const action = valueString(source, "currentAction");
  let fallback: Record<string, unknown> = {};
  if (action === "catalog-download" || action === "after-catalog-validation") {
    fallback = {
      intentId: valueString(source, "catalogIntentId"),
      kind: "batch-catalog",
      downloadId: valueString(source, "catalogDownloadId")
    };
  } else if (action === "zip-download" || action === "after-zip-validation") {
    fallback = {
      intentId: valueString(source, "zipIntentId"),
      kind: "batch-zip",
      downloadId: valueString(source, "zipDownloadId")
    };
  }
  const intent = Object.keys(raw).length ? raw : fallback;
  const intentId = valueString(intent, "intentId");
  if (!intentId) return null;
  const kind = valueString(intent, "kind");
  const status =
    valueString(source, "currentDownloadStatus") ||
    valueString(intent, "status") ||
    "pending";
  const suffixes = Array.isArray(intent.expectedSuffixes)
    ? intent.expectedSuffixes.map(String)
    : [];
  const hosts = Array.isArray(intent.expectedSourceHosts)
    ? intent.expectedSourceHosts.map(String)
    : [];
  return {
    intentId,
    kind,
    kindLabel: DOWNLOAD_KIND_LABELS[kind] || kind || "未知下载",
    status,
    statusLabel: DOWNLOAD_STATUS_LABELS[status] || status,
    downloadId: valueString(intent, "downloadId"),
    path: valueString(intent, "path"),
    maxDownloads: Math.max(1, Number(intent.maxDownloads || 1)),
    matchedDownloads: Math.max(0, Number(intent.matchedDownloads || 0)),
    expectedSuffixes: suffixes,
    sourceHosts: hosts
  };
}

export type RecoverySeverity = "manual" | "error" | "fatal";

export interface ErrorExplanation {
  title: string;
  stage: string;
  reason: string;
  recommendation: string;
  severity: RecoverySeverity;
  hardStop: boolean;
  actionPolicy: string;
  allowedActions: string[];
}

const HARD_STOP_CODES = new Set([
  "INSTITUTION_QUOTA_REACHED",
  "PAGE_SELECTOR_MISMATCH",
  "PAGE_SELECTOR_UNSTABLE",
  "PAGE_STRUCTURE_CHANGED",
  "APPLICATION_INTERRUPTED"
]);

export function isHardStopCode(code: string): boolean {
  return (
    HARD_STOP_CODES.has(code) ||
    code === "DOWNLOAD_INTENT_EXHAUSTED" ||
    code.startsWith("BATCH_ARTIFACT_")
  );
}

function matchesCode(code: string, ...prefixes: string[]): boolean {
  return prefixes.some((prefix) => code.startsWith(prefix));
}

export function explainError(
  error: ProtocolError | null,
  status: TaskStatus | string,
  currentStage = "",
  checkpoint: Record<string, unknown> = {}
): ErrorExplanation | null {
  if (!error) return null;
  const code = String(error.code || "UNKNOWN_ERROR");
  if (["PROCESSING_STOPPED", "TASK_STOPPED", "STOPPED"].includes(code)) {
    return null;
  }
  const action = valueString(checkpoint, "currentAction");
  const stage = stageLabel(currentStage, action);
  const hardStopControl = isHardStopCode(code);
  const actionPolicy = hardStopControl
    ? "普通继续不会放行；完成诊断和重新核验后可显式重试，或停止任务"
    : code === "PAGE_LIMIT_REACHED"
      ? "人工选择继续到尾页，或停止并保留分页断点"
      : code === "BATCH_SCALE_CONFIRMATION_REQUIRED"
        ? "人工确认批量规模后继续，或停止任务"
        : ["waiting_login", "waiting_manual"].includes(status)
          ? "人工处理后可显式继续；继续前重新核验登录、页面和断点"
          : ["failed", "partial", "stopped"].includes(status)
            ? "可重试未完成项；继续前重新核验登录、页面和断点"
            : ["running", "pausing", "paused", "stopping"].includes(status)
              ? "可请求安全暂停或停止，等待当前原子步骤结束"
              : "无需任务控制";
  if (code === "PAGE_LIMIT_REACHED") {
    return {
      title: "分页上限需要决策",
      stage,
      reason: error.message,
      recommendation: "继续当前组合到尾页，或停止并保留年度、组合和页码断点。",
      severity: "manual",
      hardStop: false,
      actionPolicy,
      allowedActions: ["继续到尾页", "停止任务"]
    };
  }
  if (code === "BATCH_SCALE_CONFIRMATION_REQUIRED") {
    return {
      title: "批量规模需要确认",
      stage,
      reason: error.message,
      recommendation: "核对预计页数、政策数和批次数后再确认；确认前不会下载新批次。",
      severity: "manual",
      hardStop: false,
      actionPolicy,
      allowedActions: ["确认规模并继续", "停止任务"]
    };
  }
  if (code === "LOGIN_REQUIRED" || status === "waiting_login") {
    return {
      title: "需要完成机构登录",
      stage,
      reason: error.message || "当前内置网页尚未认证。",
      recommendation: "在内置浏览器完成登录或连接校园 VPN，然后重新核验页面。",
      severity: "manual",
      hardStop: false,
      actionPolicy,
      allowedActions: ["完成登录", "已处理，继续", "停止任务"]
    };
  }
  if (code === "MANUAL_ACTION_REQUIRED" || code === "SEARCH_TRANSITION_UNCONFIRMED") {
    return {
      title: "检索或页面状态需要人工确认",
      stage,
      reason: error.message,
      recommendation: "确认页面已离开加载状态且搜索条件正确，再从原断点继续。",
      severity: "manual",
      hardStop: false,
      actionPolicy,
      allowedActions: ["检查页面", "已处理，继续", "停止任务"]
    };
  }
  if (matchesCode(code, "DOWNLOAD_")) {
    const exhausted = code === "DOWNLOAD_INTENT_EXHAUSTED";
    return {
      title: exhausted ? "下载意图次数已用尽" : "下载与当前意图不匹配",
      stage,
      reason: error.message,
      recommendation:
        "核对 intentId、downloadId、文件类型和来源；不匹配文件不会交给任务。暂停后运行诊断，再按断点重试。",
      severity: "error",
      hardStop: exhausted,
      actionPolicy,
      allowedActions: ["暂停任务", "运行诊断", "检查断点"]
    };
  }
  if (code === "INVALID_WORD_FILE") {
    return {
      title: "Word 文件校验失败",
      stage,
      reason: error.message,
      recommendation:
        "失败文件已隔离或保留在临时目录。检查真实格式、来源和下载断言后再重试。",
      severity: "error",
      hardStop: false,
      actionPolicy,
      allowedActions: ["打开输出目录", "检查失败样本", "按状态重试"]
    };
  }
  if (matchesCode(code, "BATCH_ARTIFACT_")) {
    return {
      title: "已登记批次文件完整性失败",
      stage,
      reason: error.message,
      recommendation:
        "保留现有文件并运行诊断。该批次已失败关闭，不会自动覆盖或重复下载。",
      severity: "fatal",
      hardStop: true,
      actionPolicy,
      allowedActions: ["运行诊断", "打开输出目录", "停止任务"]
    };
  }
  if (
    matchesCode(code, "SEARCH_", "RESULTS_", "BATCH_RESULTS_", "FILTER_", "YEAR_FILTER_", "PAGE_SIZE_", "RESULT_GROUPING_")
  ) {
    return {
      title: "检索身份或筛选条件未通过核验",
      stage,
      reason: error.message,
      recommendation:
        "不要沿用旧 DOM。确认当前检索条件、页码、筛选和分组状态后，运行诊断并从断点恢复。",
      severity: HARD_STOP_CODES.has(code) ? "fatal" : "error",
      hardStop: HARD_STOP_CODES.has(code),
      actionPolicy,
      allowedActions: ["暂停任务", "运行诊断", "检查断点"]
    };
  }
  if (
    matchesCode(code, "PAGE_SELECTOR_", "PAGE_STRUCTURE_")
  ) {
    return {
      title: "网页结构未通过稳定核验",
      stage,
      reason: error.message,
      recommendation:
        "确认已登录且停留在正确页面。程序会失败关闭，不会猜测控件；运行诊断后再决定是否重试。",
      severity: "fatal",
      hardStop: true,
      actionPolicy,
      allowedActions: ["运行诊断", "检查页面", "停止任务"]
    };
  }
  if (code === "INSTITUTION_QUOTA_REACHED") {
    return {
      title: "机构下载额度已触发硬停止",
      stage,
      reason: error.message,
      recommendation:
        "立即停止扩大下载。确认机构额度恢复后，再重新核验并显式重试。",
      severity: "fatal",
      hardStop: true,
      actionPolicy,
      allowedActions: ["停止任务", "检查额度", "运行诊断"]
    };
  }
  if (code === "APPLICATION_INTERRUPTED") {
    return {
      title: "应用异常中断，任务已安全停止",
      stage,
      reason: error.message,
      recommendation:
        "先检查浏览器绑定、登录、选择器和 SQLite，再从保存的断点恢复。",
      severity: "fatal",
      hardStop: true,
      actionPolicy,
      allowedActions: ["运行诊断", "检查断点", "重新核验后重试"]
    };
  }
  return {
    title: "任务执行失败",
    stage,
    reason: error.message,
    recommendation:
      "查看当前断点和最近事件；先修复错误原因，再按状态选择继续、重试或停止。",
    severity: "error",
    hardStop: false,
    actionPolicy,
    allowedActions: ["运行诊断", "检查断点", "按状态恢复"]
  };
}

export interface PauseView {
  title: string;
  detail: string;
  kind:
    | "manual"
    | "login"
    | "captcha"
    | "selector"
    | "candidate"
    | "page-limit"
    | "scale"
    | "quota"
    | "stopped";
  automatic: boolean;
}

export function pauseView(
  status: string,
  checkpoint: Record<string, unknown>,
  error: ProtocolError | null,
  candidateWaiting = false
): PauseView | null {
  const reason = valueString(checkpoint, "pauseReason") ||
    String(error?.message || "");
  if (status === "paused" || checkpoint.pauseKind === "manual") {
    return {
      title: "手动暂停已生效",
      detail: reason || "当前完整步骤已结束，断点已保存。",
      kind: "manual",
      automatic: false
    };
  }
  if (status === "stopped") {
    const quota = error?.code === "INSTITUTION_QUOTA_REACHED";
    return {
      title: quota ? "机构额度异常，任务已硬停止" : "任务已停止",
      detail: reason || "已完成项和断点已保留。",
      kind: quota ? "quota" : "stopped",
      automatic: false
    };
  }
  if (status === "waiting_login") {
    return {
      title: "自动暂停：等待机构登录",
      detail: reason || "请在专用内置浏览器中完成登录。",
      kind: "login",
      automatic: true
    };
  }
  if (error?.code === "PAGE_LIMIT_REACHED") {
    return {
      title: "自动暂停：分页上限已到达",
      detail: "请选择继续到尾页或停止并保留分页断点。",
      kind: "page-limit",
      automatic: true
    };
  }
  if (error?.code === "BATCH_SCALE_CONFIRMATION_REQUIRED") {
    return {
      title: "自动暂停：批量规模需要确认",
      detail: "确认前不会创建或下载新的批次。",
      kind: "scale",
      automatic: true
    };
  }
  if (candidateWaiting) {
    return {
      title: "自动暂停：等待同名版本选择",
      detail: "程序不会自动取第一条候选。",
      kind: "candidate",
      automatic: true
    };
  }
  if (error?.code === "INSTITUTION_QUOTA_REACHED") {
    return {
      title: "机构额度异常，任务已硬停止",
      detail: reason || "不得通过普通继续绕过额度限制。",
      kind: "quota",
      automatic: false
    };
  }
  if (status === "waiting_manual") {
    const selector = matchesCode(
      String(error?.code || ""),
      "PAGE_SELECTOR_",
      "PAGE_STRUCTURE_"
    );
    const captcha = /滑块|验证码|captcha/i.test(reason);
    return {
      title: selector
        ? "自动暂停：页面结构需要核验"
        : captcha
          ? "自动暂停：等待滑块或验证码"
          : "自动暂停：等待人工处理",
      detail: reason || "人工处理完成后需要显式确认继续。",
      kind: selector ? "selector" : captcha ? "captcha" : "selector",
      automatic: true
    };
  }
  return null;
}

export function checkpointSummary(
  checkpoint: Record<string, unknown>
): string {
  const view = searchSessionView({
    checkpoint,
    input: {},
    name: "",
    id: "",
    mode: "single",
    status: "pending",
    outputDir: "",
    progress: 0,
    currentStage: "",
    error: null,
    archivedAt: null,
    archiveReason: "",
    startedAt: null,
    finishedAt: null,
    createdAt: "",
    updatedAt: ""
  });
  const parts: string[] = [];
  if (view.query) parts.push(`查询 ${view.query}`);
  if (view.library) parts.push(libraryLabel(view.library));
  if (view.year) parts.push(`年份 ${view.year}`);
  if (view.page) parts.push(`第 ${view.page} 页`);
  if (view.totalPages) parts.push(`共 ${view.totalPages} 页`);
  if (checkpoint.currentBatchKey) {
    parts.push(`批次 ${String(checkpoint.currentBatchKey)}`);
  }
  if (checkpoint.currentRowStart && checkpoint.currentRowEnd) {
    parts.push(
      `行 ${String(checkpoint.currentRowStart)}–${String(checkpoint.currentRowEnd)}`
    );
  }
  if (checkpoint.currentPolicyId) {
    parts.push(`政策 ${String(checkpoint.currentPolicyId)}`);
  }
  if (checkpoint.completedDocuments !== undefined) {
    parts.push(`已完成 ${String(checkpoint.completedDocuments)} 条`);
  }
  const action = valueString(checkpoint, "currentAction");
  if (action) parts.push(stageLabel(action));
  return parts.join(" · ") || "断点保存在本地 SQLite";
}

export interface CollectionActions {
  start: boolean;
  pause: boolean;
  resume: boolean;
  stop: boolean;
  continue: boolean;
  continueToLast: boolean;
  confirmScale: boolean;
  retry: boolean;
  reason: string;
}

export interface ProcessingActions {
  pause: boolean;
  resume: boolean;
  stop: boolean;
  continue: boolean;
  hardStop: boolean;
}

export function processingActions(
  status: string,
  errorCode = ""
): ProcessingActions {
  const hardStop = isHardStopCode(errorCode);
  return {
    pause: status === "running",
    resume: status === "paused" && !hardStop,
    stop: [
      "running",
      "pausing",
      "paused",
      "waiting_manual",
      "waiting_login"
    ].includes(status),
    continue:
      ["waiting_manual", "waiting_login"].includes(status) && !hardStop,
    hardStop
  };
}

export function collectionActions(
  task: TaskRecord | null,
  options: {
    candidateWaiting?: boolean;
    failedItems?: number;
  } = {}
): CollectionActions {
  if (!task || task.archivedAt) {
    return {
      start: false,
      pause: false,
      resume: false,
      stop: false,
      continue: false,
      continueToLast: false,
      confirmScale: false,
      retry: false,
      reason: task?.archivedAt ? "任务在回收站中" : "尚未选择任务"
    };
  }
  const code = String(task.error?.code || "");
  const hardStop = isHardStopCode(code);
  const pageLimit = code === "PAGE_LIMIT_REACHED";
  const scale = code === "BATCH_SCALE_CONFIRMATION_REQUIRED";
  const journal = asRecord(task.checkpoint.scanJournal);
  const scanIncomplete = Boolean(
    task.checkpoint.scanCompleted !== true &&
      (Object.keys(journal).length > 0 ||
        Number(task.checkpoint.currentPage || 0) > 0)
  );
  const active = new Set<TaskStatus>([
    "preflight",
    "running",
    "waiting_login",
    "waiting_manual",
    "verifying",
    "pausing",
    "paused",
    "stopping"
  ]).has(task.status);
  let reason = "";
  if (task.status === "running") reason = "当前步骤完成后进入安全暂停点";
  if (hardStop) reason = "当前错误属于硬停止，不能通过普通继续放行";
  if (options.candidateWaiting) reason = "请先选择同名版本";
  if (pageLimit) reason = "请先确认继续到尾页或停止";
  if (scale) reason = "请先确认批量规模";
  return {
    start: task.status === "pending",
    pause: ["preflight", "running", "verifying"].includes(task.status),
    resume: task.status === "paused" && !hardStop,
    stop: active && task.status !== "stopping",
    continue:
      ["waiting_login", "waiting_manual"].includes(task.status) &&
      !pageLimit &&
      !scale &&
      !options.candidateWaiting &&
      !hardStop,
    continueToLast: task.status === "waiting_manual" && pageLimit,
    confirmScale: task.status === "waiting_manual" && scale,
    retry:
      ["failed", "partial", "stopped"].includes(task.status) &&
      (
        options.failedItems === undefined ||
        options.failedItems > 0 ||
        scanIncomplete ||
        task.status === "stopped"
      ),
    reason
  };
}

export function retryActionLabel(task: TaskRecord | null): string {
  if (!task) return "重试失败项";
  if (task.checkpoint.scanCompleted === true) {
    return "重试失败项";
  }
  const journal = asRecord(task.checkpoint.scanJournal);
  if (
    Object.keys(journal).length > 0 ||
    Number(task.checkpoint.currentPage || 0) > 0
  ) {
    return "从断点继续扫描";
  }
  return "重试失败项";
}

export interface TimelineItem {
  id: string;
  label: string;
  detail: string;
  status: string;
  createdAt: string;
}

const LOW_LEVEL_EVENTS = new Set([
  "task.item.updated",
  "task.item.completed",
  "task.batch.updated"
]);

export function buildTaskTimeline(
  events: TaskTimelineEvent[] | undefined,
  limit = 16
): TimelineItem[] {
  const result: TimelineItem[] = [];
  let previousKey = "";
  for (const event of events || []) {
    if (LOW_LEVEL_EVENTS.has(event.event)) continue;
    const data = asRecord(event.data);
    const status = valueString(data, "status");
    const stage = valueString(data, "currentStage");
    const action = valueString(data, "currentAction");
    const error = asRecord(data.error);
    const errorCode = valueString(error, "code");
    const pauseKind = valueString(data, "pauseKind");
    const snapshot = asRecord(data.progressSnapshot);
    const snapshotKey = [
      valueString(snapshot, "stage"),
      valueString(snapshot, "completedUnits"),
      valueString(snapshot, "totalUnits"),
      valueString(snapshot, "succeeded"),
      valueString(snapshot, "failed"),
      valueString(snapshot, "pendingReview")
    ].join(":");
    const transitionKey = [
      event.event,
      status,
      stage,
      action,
      errorCode,
      pauseKind,
      snapshotKey
    ].join("|");
    if (previousKey === transitionKey) continue;
    previousKey = transitionKey;
    const label =
      event.event === "task.created"
        ? "任务已创建"
        : event.event === "task.updated"
          ? stageLabel(stage || status, action)
          : event.event === "data.run.updated"
            ? stageLabel(stage || status, action)
          : event.event === "task.archived"
            ? "任务已移入回收站"
            : event.event === "task.restored"
              ? "任务已从回收站恢复"
              : event.event === "task.items.retry"
                ? "失败项已重置等待重试"
                : event.event === "task.batch.completed"
                  ? "批次已原子发布并登记"
                  : event.event;
    const detailParts = [
      errorCode ? `错误 ${errorCode}` : "",
      valueString(data, "pauseReason"),
      valueString(data, "currentQuery"),
      valueString(data, "currentYear"),
      valueString(data, "currentPage")
        ? `第 ${valueString(data, "currentPage")} 页`
        : "",
      Object.keys(snapshot).length
        ? `完成 ${valueString(snapshot, "completedUnits") || "0"}/${valueString(snapshot, "totalUnits") || "0"} · 成功 ${valueString(snapshot, "succeeded") || "0"} · 失败 ${valueString(snapshot, "failed") || "0"} · 待核验 ${valueString(snapshot, "pendingReview") || "0"}`
        : ""
    ].filter(Boolean);
    const detail = detailParts.join(" · ");
    const previous = result[result.length - 1];
    const beforePrevious = result[result.length - 2];
    const previousTime = previous ? Date.parse(previous.createdAt) : Number.NaN;
    const currentTime = Date.parse(event.createdAt);
    if (
      beforePrevious &&
      previous &&
      beforePrevious.label === label &&
      beforePrevious.detail === detail &&
      ["running", "preflight", "waiting_login", "waiting_manual"].includes(
        previous.status
      ) &&
      !previous.detail.includes("错误") &&
      Number.isFinite(currentTime) &&
      Math.abs(currentTime - Date.parse(beforePrevious.createdAt)) <= 2_000
    ) {
      result.pop();
      continue;
    }
    if (
      previous &&
      previous.label === label &&
      previous.detail === detail &&
      Number.isFinite(previousTime) &&
      Number.isFinite(currentTime) &&
      Math.abs(currentTime - previousTime) <= 2_000
    ) {
      continue;
    }
    result.push({
      id: `${transitionKey}#${event.id}`,
      label,
      detail,
      status,
      createdAt: event.createdAt
    });
  }
  return result.slice(-limit);
}

export type HashState = "complete" | "missing" | "mismatch";

export function hashState(
  value: string,
  errorCode = ""
): HashState {
  if (/HASH_MISMATCH|INTEGRITY_MISMATCH/.test(errorCode)) return "mismatch";
  return /^[a-fA-F0-9]{64}$/.test(value || "") ? "complete" : "missing";
}
