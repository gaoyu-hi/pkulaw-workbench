import type { CatalogFormat } from "./catalog-formats";

export const PROTOCOL_VERSION = "1.0.0";

export type BackendStatus =
  | "stopped"
  | "starting"
  | "ready"
  | "stopping"
  | "failed";

export type TaskMode = "single" | "batch";
export type BrowserLeaseMode = "single" | "batch" | "data";
export type CollectorMatchMode = "keyword" | "fulltext" | "title";

export interface SearchModeOption {
  value: CollectorMatchMode;
  label: string;
  detail: string;
}

export type TaskStatus =
  | "pending"
  | "preflight"
  | "waiting_login"
  | "running"
  | "waiting_manual"
  | "verifying"
  | "pausing"
  | "paused"
  | "stopping"
  | "completed"
  | "partial"
  | "stopped"
  | "failed";

export type YearSelection =
  | { mode: "all" }
  | { mode: "single"; year: number }
  | { mode: "range"; startYear: number; endYear: number };

export type PageSelection =
  | { mode: "last" }
  | { mode: "limit"; maxPages: number };

export type BodyCompleteness =
  | "unknown"
  | "complete"
  | "suspicious"
  | "failed";

export type ReviewStatus =
  | "pending_review"
  | "not_required"
  | "verified"
  | "rejected";

export interface ProtocolError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ProgressSnapshot {
  overall: number;
  stage: string;
  completedUnits: number;
  totalUnits: number;
  succeeded: number;
  failed: number;
  pendingReview: number;
}

export interface EngineReadyEvent {
  type: "event";
  event: "engine.ready";
  protocolVersion: string;
  timestamp: string;
  data: {
    engine: string;
    engineVersion: string;
    protocolVersion: string;
    capabilities: string[];
  };
}

export interface EngineEvent {
  type: "event";
  event: string;
  protocolVersion: string;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface EngineResponse {
  type: "response";
  requestId: string;
  protocolVersion: string;
  timestamp: string;
  ok: boolean;
  result?: Record<string, unknown>;
  error?: ProtocolError;
}

export interface TaskRecord {
  id: string;
  name: string;
  mode: TaskMode;
  status: TaskStatus;
  input: Record<string, unknown>;
  outputDir: string;
  progress: number;
  progressSnapshot?: ProgressSnapshot | null;
  currentStage: string;
  error: ProtocolError | null;
  checkpoint: Record<string, unknown>;
  archivedAt: string | null;
  archiveReason: string;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TaskItemCandidate {
  policyId?: string;
  title: string;
  url: string;
  validity?: string;
  issuingAuthority?: string;
  documentNumber?: string;
  publicationDate?: string;
  effectiveDate?: string;
  province?: string;
  library?: string;
  [key: string]: unknown;
}

export interface TaskItemRecord {
  id: string;
  taskId: string;
  policyKey: string;
  title: string;
  status:
    | "pending"
    | "running"
    | "verifying"
    | "waiting_manual"
    | "pending_review"
    | "completed"
    | "failed"
    | "skipped";
  sourceType: string;
  progress: number;
  currentStage: string;
  payload: {
    candidates?: TaskItemCandidate[];
    selectedCandidate?: TaskItemCandidate;
    selectedCandidateKey?: string;
    downloadedFile?: string;
    sha256?: string;
    [key: string]: unknown;
  };
  error: ProtocolError | null;
  createdAt: string;
  updatedAt: string;
}

export interface TaskTimelineEvent {
  id: number;
  taskId: string;
  event: string;
  data: Record<string, unknown>;
  createdAt: string;
}

export interface TaskDetails {
  task: TaskRecord;
  items: TaskItemRecord[];
  timeline?: TaskTimelineEvent[];
}

export interface BatchRecord {
  id: string;
  taskId: string;
  batchKey: string;
  query: string;
  library: "central" | "local";
  page: number;
  rowStart: number;
  rowEnd: number;
  status: TaskItemRecord["status"];
  progress: number;
  catalogFormat: CatalogFormat;
  fullFormat: "docx";
  selected: TaskItemCandidate[];
  catalogPath: string;
  zipPath: string;
  reportPath: string;
  error: ProtocolError | null;
  createdAt: string;
  updatedAt: string;
}

export interface BatchTaskDetails {
  task: TaskRecord;
  batches: BatchRecord[];
  items: TaskItemRecord[];
  timeline?: TaskTimelineEvent[];
}

export interface PolicyDocumentRecord {
  id: string;
  taskId: string;
  policyKey: string;
  policyId: string;
  title: string;
  documentNumber: string;
  issuingAuthority: string;
  publicationDate: string;
  effectiveDate: string;
  validity: string;
  effectLevel: string;
  sourceType:
    | "北大法宝原件"
    | "北大法宝ZIP"
    | "政府官网原生附件"
    | "政府官网正文整理"
    | "待核验"
    | "";
  sourceUrl: string;
  retrievedAt: string;
  retrievalMethod: string;
  wordPath: string;
  txtPath: string;
  evidencePath: string;
  sha256: string;
  textSourceType: string;
  extractionMethod: string;
  converterName: string;
  converterVersion: string;
  bodyCompleteness: BodyCompleteness;
  reviewStatus: ReviewStatus;
  status: "pending" | "processed" | "pending_review" | "failed";
  error: ProtocolError | null;
  payload: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface ProcessingRunRecord {
  id: string;
  taskId: string;
  status:
    | "pending"
    | "running"
    | "waiting_manual"
    | "waiting_login"
    | "pausing"
    | "paused"
    | "stopping"
    | "completed"
    | "partial"
    | "stopped"
    | "failed";
  progress: number;
  progressSnapshot?: ProgressSnapshot | null;
  currentStage: string;
  options: Record<string, unknown>;
  summary: Record<string, unknown>;
  error: ProtocolError | null;
  createdAt: string;
  updatedAt: string;
}

export interface TaskArtifactRecord {
  id: string;
  taskId: string;
  processingId: string;
  kind: string;
  path: string;
  sha256: string;
  createdAt: string;
}

export interface DataProcessingDetails {
  run: ProcessingRunRecord;
  documents: PolicyDocumentRecord[];
  artifacts: TaskArtifactRecord[];
  timeline?: TaskTimelineEvent[];
}

export interface CleanupFileRecord {
  path: string;
  rawPath: string;
  taskIds: string[];
  sources: string[];
  exists: boolean;
  canTrash: boolean;
  reason: string;
}

export interface CleanupDirectoryRecord {
  path: string;
  taskIds: string[];
  reason: string;
  canTrash: false;
  shared: boolean;
}

export interface CleanupPreview {
  tasks: TaskRecord[];
  files: CleanupFileRecord[];
  eligibleFiles: CleanupFileRecord[];
  protectedFiles: CleanupFileRecord[];
  directories: CleanupDirectoryRecord[];
  summary: {
    taskCount: number;
    registeredFileCount: number;
    eligibleFileCount: number;
    existingEligibleFileCount: number;
    protectedFileCount: number;
    directoryCount: number;
  };
}

export interface PurgeResult {
  taskIds: string[];
  deleted: Record<string, number>;
  fileMode: "keep" | "trash";
  trashedFiles: number;
  missingFiles: number;
}

export interface TaskCreateInput {
  name: string;
  mode: TaskMode;
  input: Record<string, unknown>;
  outputDir?: string;
  operationId?: string;
}

export interface TaskPreviewCombination {
  query: string;
  matchMode: CollectorMatchMode;
  library: "central" | "local";
  year: string;
  status: "ready" | "no_result" | "skipped";
  resultCount: number;
  totalPages: number;
  reportedTotalPages: number;
  expectedTotalPages: number;
  accessibleCapacity: number;
  actualPageSize: number;
  groupingMode: string;
  searchId: string;
  resultSignature: string;
  actualYear: string;
  actualProvince: string;
  actualTimeliness: string;
  windowStatus: "complete" | "truncated" | "no_result";
  windowCode: "RESULT_WINDOW_TRUNCATED" | "";
  skipReason?: string;
}

export interface YearWindowSelectionSnapshot {
  mode: string;
  year?: number | null;
  startYear?: number | null;
  endYear?: number | null;
  [key: string]: unknown;
}

export interface TaskPreviewSummary {
  formFingerprint: string;
  previewFingerprint: string;
  generatedAt: string;
  mode: TaskMode;
  queryCount: number;
  libraryCount: number;
  yearCount: number;
  combinationCount: number;
  totalResults: number;
  totalPages: number;
  estimatedBatches50: number;
  windowStatus: "complete" | "probe_required" | "unknown";
  downloadsStarted: 0;
  downloadIntentsCreated: 0;
  tasksCreated: 0;
  combinations: TaskPreviewCombination[];
  originalYearSelection?: YearWindowSelectionSnapshot;
  effectiveYearSelection?: YearWindowSelectionSnapshot;
  yearWindowAdjustment?: Record<string, unknown>;
  yearWindows?: Record<string, unknown>[];
}

export interface CollectorStartResult {
  task: TaskRecord;
  preview: TaskPreviewSummary | null;
  details: TaskDetails | BatchTaskDetails;
  started: boolean;
  preparing: boolean;
  error: ProtocolError | null;
  idempotent: boolean;
}

export type ManualRecoveryAction = "continue" | "restart" | "none";

export interface ManualRecoveryResult {
  action: ManualRecoveryAction;
  task: TaskRecord;
  details: TaskDetails | BatchTaskDetails;
}

export interface TaskSettingsUpdateResult {
  changed: boolean;
  generation: number;
  task: TaskRecord;
  details: TaskDetails | BatchTaskDetails;
}

export interface BackendState {
  status: BackendStatus;
  pid: number | null;
  protocolVersion: string;
  engineVersion: string;
  message: string;
}

export interface AppInfo {
  name: string;
  version: string;
  dataDirectory: string;
  logsDirectory: string;
  resourcesPath: string;
  defaultSingleOutputDir: string;
  defaultBatchOutputDir: string;
  engineDirectory: string;
  pythonPath: string;
  backendMode: "bundled" | "source";
  packaged: boolean;
  electronVersion: string;
  chromeVersion: string;
  nodeVersion: string;
  fixtureMode: boolean;
  stage3FixtureMode: boolean;
  stage4FixtureMode: boolean;
  stage5FixtureMode: boolean;
  stage6FixtureMode: boolean;
}

export interface InstitutionProfile {
  id: string;
  name: string;
  entryUrl: string;
  sessionKey: string;
  browserZoomFactor: number;
  createdAt: string;
  updatedAt: string;
}

export interface BrowserRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BrowserTabInfo {
  url: string;
  title: string;
}

export interface BrowserLeaseState {
  available: boolean;
  leaseId: string;
  taskId: string;
  mode: BrowserLeaseMode | "";
  createdAt: string;
  updatedAt: string;
}

export interface BrowserHostState {
  status: "idle" | "loading" | "ready" | "waiting_login" | "waiting_manual" | "failed";
  institutionId: string;
  sessionKey: string;
  url: string;
  title: string;
  viewLoaded: boolean;
  targetBound: boolean;
  targetUrl: string;
  selectorProfile: string;
  selectorVersion: string;
  selectorReady: boolean;
  selectorMessage: string;
  selectorDiagnostics: Record<string, unknown> | null;
  authenticated: boolean;
  loginVisible: boolean;
  captchaVisible: boolean;
  captchaSelector?: string;
  captchaBox?: Record<string, number | string>;
  captchaCheckedAt?: string;
  zoomFactor: number;
  canGoBack: boolean;
  childViews: number;
  tabs: BrowserTabInfo[];
  message: string;
  lastDownload: Record<string, unknown> | null;
  browserLease: BrowserLeaseState;
}

export interface BrowserBounds extends BrowserRect {
  visible: boolean;
}

export interface LogRecord {
  timestamp: string;
  level: "debug" | "info" | "warn" | "error";
  source: "desktop" | "engine" | "renderer";
  event: string;
  message: string;
  data?: Record<string, unknown>;
}

export interface DiagnosticsCheck {
  id: string;
  label: string;
  status: "ok" | "warning" | "error" | "skipped";
  detail: string;
}

export interface DiagnosticsReport {
  generatedAt: string;
  checks: DiagnosticsCheck[];
  engine: {
    name: string;
    version: string;
    appVersion: string;
    protocolVersion: string;
    pythonVersion: string;
    executable: string;
    frozen: boolean;
  };
  paths: {
    dataDirectory: string;
    database: string;
    logsDirectory: string;
    tempDirectory: string;
    downloadDirectory?: string;
  };
  database: {
    integrity: string;
    schemaVersion: number;
    expectedSchemaVersion: number;
    taskCounts: Record<string, number>;
    sizeBytes: number;
  };
  dependencies: Record<string, string>;
  legacyConverters: Record<string, string>;
  disk: {
    total: number;
    used: number;
    free: number;
  };
  platform: {
    system: string;
    release: string;
    machine: string;
    processId: number;
  };
  desktop: {
    electronVersion: string;
    chromeVersion: string;
    nodeVersion: string;
    packaged: boolean;
    platform: string;
    arch: string;
    scaleFactor: number;
    displaySize: { width: number; height: number };
    dataDirectoryWritable: boolean;
    network: {
      status: "ok" | "warning" | "error" | "skipped";
      target: string;
      detail: string;
      latencyMs: number;
    };
  };
}

export interface DesktopApi {
  getAppInfo(): Promise<AppInfo>;
  getBackendState(): Promise<BackendState>;
  pingBackend(): Promise<Record<string, unknown>>;
  createTask(input: TaskCreateInput): Promise<TaskRecord>;
  startCollectorTask(input: TaskCreateInput): Promise<CollectorStartResult>;
  previewTask(
    mode: TaskMode,
    input: Record<string, unknown>
  ): Promise<TaskPreviewSummary>;
  listTasks(limit?: number, includeArchived?: boolean): Promise<TaskRecord[]>;
  getTask(taskId: string): Promise<TaskRecord>;
  getTaskDetails(taskId: string): Promise<TaskDetails>;
  resumeTaskAfterManual(
    taskId: string,
    operationId: string
  ): Promise<ManualRecoveryResult>;
  applyTaskSettings(
    taskId: string,
    input: Record<string, unknown>,
    operationId: string
  ): Promise<TaskSettingsUpdateResult>;
  startSingleTask(taskId: string, operationId?: string): Promise<TaskDetails>;
  pauseSingleTask(taskId: string): Promise<TaskDetails>;
  resumeSingleTask(taskId: string, operationId?: string): Promise<TaskDetails>;
  stopSingleTask(taskId: string): Promise<TaskDetails>;
  continueSingleTask(taskId: string): Promise<TaskDetails>;
  continueSingleToLast(taskId: string): Promise<TaskDetails>;
  retrySingleTask(taskId: string, operationId?: string): Promise<TaskDetails>;
  chooseCandidate(
    taskId: string,
    itemId: string,
    candidateKey: string
  ): Promise<TaskDetails>;
  getBatchTaskDetails(taskId: string): Promise<BatchTaskDetails>;
  startBatchTask(taskId: string, operationId?: string): Promise<BatchTaskDetails>;
  pauseBatchTask(taskId: string): Promise<BatchTaskDetails>;
  resumeBatchTask(taskId: string, operationId?: string): Promise<BatchTaskDetails>;
  stopBatchTask(taskId: string): Promise<BatchTaskDetails>;
  continueBatchTask(taskId: string): Promise<BatchTaskDetails>;
  continueBatchToLast(taskId: string): Promise<BatchTaskDetails>;
  confirmBatchScale(taskId: string): Promise<BatchTaskDetails>;
  continueCaptureBoundary(
    taskId: string
  ): Promise<TaskDetails | BatchTaskDetails>;
  retryBatchTask(taskId: string, operationId?: string): Promise<BatchTaskDetails>;
  startDataProcessing(
    taskId: string,
    options: {
      governmentFallback?: boolean;
      governmentSearchTemplate?: string;
    },
    operationId?: string
  ): Promise<DataProcessingDetails>;
  stopDataProcessing(processingId: string): Promise<DataProcessingDetails>;
  pauseDataProcessing(processingId: string): Promise<DataProcessingDetails>;
  resumeDataProcessing(processingId: string): Promise<DataProcessingDetails>;
  continueDataProcessing(processingId: string): Promise<DataProcessingDetails>;
  getDataProcessing(processingId: string): Promise<DataProcessingDetails>;
  getLatestDataProcessing(taskId: string): Promise<DataProcessingDetails | null>;
  archiveTasks(taskIds: string[], reason?: string): Promise<TaskRecord[]>;
  restoreTasks(taskIds: string[]): Promise<TaskRecord[]>;
  getCleanupPreview(taskIds: string[]): Promise<CleanupPreview>;
  purgeTasks(
    taskIds: string[],
    options: { fileMode: "keep" | "trash" }
  ): Promise<PurgeResult>;
  getRecentLogs(): Promise<LogRecord[]>;
  openDataDirectory(): Promise<void>;
  openLogsDirectory(): Promise<void>;
  copyText(text: string): Promise<void>;
  openLocalFile(path: string): Promise<void>;
  openTaskOutput(taskId: string): Promise<void>;
  selectOutputDirectory(defaultPath?: string): Promise<string | null>;
  getDiagnostics(): Promise<DiagnosticsReport>;
  listInstitutions(): Promise<InstitutionProfile[]>;
  saveInstitution(input: {
    id?: string;
    name: string;
    entryUrl: string;
  }): Promise<InstitutionProfile>;
  selectInstitution(institutionId: string): Promise<BrowserHostState>;
  getBrowserState(): Promise<BrowserHostState>;
  setBrowserBounds(bounds: BrowserBounds): Promise<void>;
  setBrowserZoom(factor: number): Promise<number>;
  browserNavigate(action: "back" | "reload" | "home"): Promise<BrowserHostState>;
  browserContinue(): Promise<BrowserHostState>;
  browserFixtureAction(action: "login" | "captcha" | "download" | "child"): Promise<BrowserHostState>;
  restartBackend(): Promise<BackendState>;
  onBackendState(listener: (state: BackendState) => void): () => void;
  onBrowserState(listener: (state: BrowserHostState) => void): () => void;
  onEngineEvent(listener: (event: EngineEvent) => void): () => void;
  onLog(listener: (record: LogRecord) => void): () => void;
}
