import { contextBridge, ipcRenderer } from "electron";
import type {
  AppInfo,
  BatchTaskDetails,
  BackendState,
  BrowserBounds,
  BrowserHostState,
  CleanupPreview,
  CollectorStartResult,
  DataProcessingDetails,
  DesktopApi,
  DiagnosticsReport,
  EngineEvent,
  InstitutionProfile,
  LogRecord,
  ManualRecoveryResult,
  TaskCreateInput,
  TaskDetails,
  TaskPreviewSummary,
  TaskRecord,
  TaskSettingsRevisionPreview,
  TaskSettingsUpdateResult,
  PurgeResult
} from "../shared/protocol";

function subscribe<T>(channel: string, listener: (payload: T) => void): () => void {
  const wrapped = (_event: Electron.IpcRendererEvent, payload: T): void => listener(payload);
  ipcRenderer.on(channel, wrapped);
  return () => ipcRenderer.removeListener(channel, wrapped);
}

const api: DesktopApi = {
  getAppInfo: () => ipcRenderer.invoke("app:get-info") as Promise<AppInfo>,
  getBackendState: () =>
    ipcRenderer.invoke("backend:get-state") as Promise<BackendState>,
  pingBackend: () =>
    ipcRenderer.invoke("backend:ping") as Promise<Record<string, unknown>>,
  createTask: (input: TaskCreateInput) =>
    ipcRenderer.invoke("tasks:create", input) as Promise<TaskRecord>,
  startCollectorTask: (input: TaskCreateInput) =>
    ipcRenderer.invoke(
      "tasks:start-collector",
      input
    ) as Promise<CollectorStartResult>,
  previewTask: (mode, input) =>
    ipcRenderer.invoke(
      "tasks:preview",
      mode,
      input
    ) as Promise<TaskPreviewSummary>,
  listTasks: (limit?: number, includeArchived?: boolean) =>
    ipcRenderer.invoke("tasks:list", limit, includeArchived) as Promise<TaskRecord[]>,
  getTask: (taskId: string) =>
    ipcRenderer.invoke("tasks:get", taskId) as Promise<TaskRecord>,
  getTaskDetails: (taskId: string) =>
    ipcRenderer.invoke("tasks:get-details", taskId) as Promise<TaskDetails>,
  resumeTaskAfterManual: (taskId: string, operationId: string) =>
    ipcRenderer.invoke(
      "tasks:resume-after-manual",
      taskId,
      operationId
    ) as Promise<ManualRecoveryResult>,
  applyTaskSettings: (
    taskId: string,
    input: Record<string, unknown>,
    operationId: string
  ) =>
    ipcRenderer.invoke(
      "tasks:apply-settings",
      { taskId, input, operationId }
    ) as Promise<TaskSettingsUpdateResult>,
  prepareTaskSettingsRevision: (
    taskId: string,
    input: Record<string, unknown>,
    operationId: string
  ) =>
    ipcRenderer.invoke(
      "tasks:prepare-settings-revision",
      { taskId, input, operationId }
    ) as Promise<TaskSettingsRevisionPreview>,
  commitTaskSettingsRevision: (
    taskId: string,
    token: string,
    artifactPolicy: "preserve" | "delete",
    operationId: string
  ) =>
    ipcRenderer.invoke(
      "tasks:commit-settings-revision",
      { taskId, token, artifactPolicy, operationId }
    ) as Promise<TaskSettingsUpdateResult>,
  startSingleTask: (taskId: string, operationId?: string) =>
    ipcRenderer.invoke("single:start", taskId, operationId) as Promise<TaskDetails>,
  pauseSingleTask: (taskId: string) =>
    ipcRenderer.invoke("single:pause", taskId) as Promise<TaskDetails>,
  resumeSingleTask: (taskId: string, operationId?: string) =>
    ipcRenderer.invoke("single:resume", taskId, operationId) as Promise<TaskDetails>,
  stopSingleTask: (taskId: string) =>
    ipcRenderer.invoke("single:stop", taskId) as Promise<TaskDetails>,
  continueSingleTask: (taskId: string) =>
    ipcRenderer.invoke("single:continue", taskId) as Promise<TaskDetails>,
  continueSingleToLast: (taskId: string) =>
    ipcRenderer.invoke("single:continue-to-last", taskId) as Promise<TaskDetails>,
  retrySingleTask: (taskId: string, operationId?: string) =>
    ipcRenderer.invoke("single:retry", taskId, operationId) as Promise<TaskDetails>,
  chooseCandidate: (taskId: string, itemId: string, candidateKey: string) =>
    ipcRenderer.invoke("single:choose-candidate", {
      taskId,
      itemId,
      candidateKey
    }) as Promise<TaskDetails>,
  getBatchTaskDetails: (taskId: string) =>
    ipcRenderer.invoke("batch:get-details", taskId) as Promise<BatchTaskDetails>,
  startBatchTask: (taskId: string, operationId?: string) =>
    ipcRenderer.invoke("batch:start", taskId, operationId) as Promise<BatchTaskDetails>,
  pauseBatchTask: (taskId: string) =>
    ipcRenderer.invoke("batch:pause", taskId) as Promise<BatchTaskDetails>,
  resumeBatchTask: (taskId: string, operationId?: string) =>
    ipcRenderer.invoke("batch:resume", taskId, operationId) as Promise<BatchTaskDetails>,
  stopBatchTask: (taskId: string) =>
    ipcRenderer.invoke("batch:stop", taskId) as Promise<BatchTaskDetails>,
  continueBatchTask: (taskId: string) =>
    ipcRenderer.invoke("batch:continue", taskId) as Promise<BatchTaskDetails>,
  continueBatchToLast: (taskId: string) =>
    ipcRenderer.invoke("batch:continue-to-last", taskId) as Promise<BatchTaskDetails>,
  confirmBatchScale: (taskId: string) =>
    ipcRenderer.invoke("batch:confirm-scale", taskId) as Promise<BatchTaskDetails>,
  continueCaptureBoundary: (taskId: string, operationId: string) =>
    ipcRenderer.invoke(
      "tasks:continue-capture-boundary",
      taskId,
      operationId
    ) as Promise<TaskDetails | BatchTaskDetails>,
  retryBatchTask: (taskId: string, operationId?: string) =>
    ipcRenderer.invoke("batch:retry", taskId, operationId) as Promise<BatchTaskDetails>,
  startDataProcessing: (taskId, options, operationId) =>
    ipcRenderer.invoke("data:start", {
      taskId,
      options,
      operationId
    }) as Promise<DataProcessingDetails>,
  stopDataProcessing: (processingId: string) =>
    ipcRenderer.invoke("data:stop", processingId) as Promise<DataProcessingDetails>,
  pauseDataProcessing: (processingId: string) =>
    ipcRenderer.invoke("data:pause", processingId) as Promise<DataProcessingDetails>,
  resumeDataProcessing: (processingId: string) =>
    ipcRenderer.invoke("data:resume", processingId) as Promise<DataProcessingDetails>,
  continueDataProcessing: (processingId: string) =>
    ipcRenderer.invoke("data:continue", processingId) as Promise<DataProcessingDetails>,
  getDataProcessing: (processingId: string) =>
    ipcRenderer.invoke("data:get", processingId) as Promise<DataProcessingDetails>,
  getLatestDataProcessing: (taskId: string) =>
    ipcRenderer.invoke("data:latest", taskId) as Promise<DataProcessingDetails | null>,
  archiveTasks: (taskIds: string[], reason?: string) =>
    ipcRenderer.invoke("tasks:archive", { taskIds, reason }) as Promise<TaskRecord[]>,
  restoreTasks: (taskIds: string[]) =>
    ipcRenderer.invoke("tasks:restore", taskIds) as Promise<TaskRecord[]>,
  getCleanupPreview: (taskIds: string[]) =>
    ipcRenderer.invoke("tasks:cleanup-preview", taskIds) as Promise<CleanupPreview>,
  purgeTasks: (
    taskIds: string[],
    options: { fileMode: "keep" | "trash" }
  ) =>
    ipcRenderer.invoke("tasks:purge", {
      taskIds,
      fileMode: options.fileMode
    }) as Promise<PurgeResult>,
  getRecentLogs: () =>
    ipcRenderer.invoke("logs:get-recent") as Promise<LogRecord[]>,
  openDataDirectory: () => ipcRenderer.invoke("app:open-data-directory") as Promise<void>,
  openLogsDirectory: () =>
    ipcRenderer.invoke("app:open-logs-directory") as Promise<void>,
  copyText: (text: string) =>
    ipcRenderer.invoke("app:copy-text", text) as Promise<void>,
  openLocalFile: (path: string) =>
    ipcRenderer.invoke("artifacts:open", path) as Promise<void>,
  openTaskOutput: (taskId: string) =>
    ipcRenderer.invoke("tasks:open-output", taskId) as Promise<void>,
  selectOutputDirectory: (defaultPath?: string) =>
    ipcRenderer.invoke("app:select-output-directory", defaultPath) as Promise<string | null>,
  getDiagnostics: () =>
    ipcRenderer.invoke("diagnostics:run") as Promise<DiagnosticsReport>,
  listInstitutions: () =>
    ipcRenderer.invoke("institutions:list") as Promise<InstitutionProfile[]>,
  saveInstitution: (input) =>
    ipcRenderer.invoke("institutions:save", input) as Promise<InstitutionProfile>,
  selectInstitution: (institutionId: string) =>
    ipcRenderer.invoke("browser:select", institutionId) as Promise<BrowserHostState>,
  getBrowserState: () =>
    ipcRenderer.invoke("browser:get-state") as Promise<BrowserHostState>,
  setBrowserBounds: (bounds: BrowserBounds) =>
    ipcRenderer.invoke("browser:set-bounds", bounds) as Promise<void>,
  setBrowserZoom: (factor: number) =>
    ipcRenderer.invoke("browser:set-zoom", factor) as Promise<number>,
  browserNavigate: (action) =>
    ipcRenderer.invoke("browser:navigate", action) as Promise<BrowserHostState>,
  browserContinue: () =>
    ipcRenderer.invoke("browser:continue") as Promise<BrowserHostState>,
  browserFixtureAction: (action) =>
    ipcRenderer.invoke("browser:fixture-action", action) as Promise<BrowserHostState>,
  restartBackend: () =>
    ipcRenderer.invoke("backend:restart") as Promise<BackendState>,
  onBackendState: (listener) => subscribe<BackendState>("backend:state", listener),
  onBrowserState: (listener) => subscribe<BrowserHostState>("browser:state", listener),
  onEngineEvent: (listener) => subscribe<EngineEvent>("engine:event", listener),
  onLog: (listener) => subscribe<LogRecord>("log:record", listener)
};

contextBridge.exposeInMainWorld("pkulaw", api);
