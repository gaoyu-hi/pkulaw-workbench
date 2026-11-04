import { clipboard, dialog, ipcMain, shell } from "electron";
import fsp from "node:fs/promises";
import path from "node:path";
import type {
  AppInfo,
  BatchTaskDetails,
  BrowserBounds,
  BrowserHostState,
  CleanupPreview,
  CollectorStartResult,
  DataProcessingDetails,
  InstitutionProfile,
  ManualRecoveryResult,
  TaskCreateInput,
  TaskDetails,
  TaskPreviewSummary,
  TaskRecord,
  TaskSettingsUpdateResult,
  PurgeResult
} from "../shared/protocol";
import type { BrowserHost } from "./browser-host";
import { runDiagnostics } from "./diagnostics";
import type { InstitutionStore } from "./institution-store";
import type { JsonLineLogger } from "./logger";
import { ensureWritableDirectory } from "./path-safety";
import { EngineRequestError, type PythonService } from "./python-service";
import {
  TrashCleanupError,
  trashRegisteredFiles
} from "./task-file-cleanup";

interface RegisterIpcOptions {
  pythonService: PythonService;
  logger: JsonLineLogger;
  appInfo: AppInfo;
  dataDirectory: string;
  browserHost: BrowserHost;
  institutions: InstitutionStore;
}

function assertTaskId(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("taskId must be a non-empty string");
  }
  return value;
}

function assertTaskIds(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error("taskIds must contain at least one task");
  }
  const ids = [...new Set(value.map((item) => String(item || "").trim()))];
  if (ids.some((item) => !item)) {
    throw new Error("taskIds contains an empty task id");
  }
  return ids;
}

function optionalOperationId(value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value !== "string" || !value.trim()) {
    throw new Error("operationId must be a non-empty string when provided");
  }
  return value.trim();
}

function requiredOperationId(value: unknown): string {
  const operationId = optionalOperationId(value);
  if (!operationId) {
    throw new Error("operationId must be a non-empty string");
  }
  return operationId;
}

async function openLocalPath(targetPath: string): Promise<void> {
  if (process.env.PKULAW_SMOKE_FAKE_SHELL_OPEN === "1") {
    return;
  }
  const error = await shell.openPath(targetPath);
  if (error) {
    throw new Error(error);
  }
}

export function registerIpcHandlers(options: RegisterIpcOptions): void {
  const { pythonService, logger, appInfo, dataDirectory, browserHost, institutions } = options;

  ipcMain.handle("app:get-info", () => appInfo);
  ipcMain.handle("backend:get-state", () => pythonService.getState());
  ipcMain.handle("backend:ping", async () => {
    const result = await pythonService.request("system.ping");
    logger.write("info", "backend.ping", "Backend ping completed", result);
    return result;
  });
  ipcMain.handle("logs:get-recent", () => logger.getRecent());
  ipcMain.handle("app:open-data-directory", async () => {
    await fsp.mkdir(dataDirectory, { recursive: true });
    await openLocalPath(dataDirectory);
  });
  ipcMain.handle("app:open-logs-directory", async () => {
    const logsDirectory = appInfo.logsDirectory;
    await fsp.mkdir(logsDirectory, { recursive: true });
    await openLocalPath(logsDirectory);
  });
  ipcMain.handle("app:copy-text", (_event, value: unknown) => {
    if (typeof value !== "string") {
      throw new Error("copy text must be a string");
    }
    clipboard.writeText(value);
  });
  ipcMain.handle("app:select-output-directory", async (_event, defaultPath?: unknown) => {
    const result = await dialog.showOpenDialog({
      title: "选择任务输出目录",
      defaultPath:
        typeof defaultPath === "string" && defaultPath.trim()
          ? path.resolve(defaultPath)
          : dataDirectory,
      properties: ["openDirectory", "createDirectory"]
    });
    return result.canceled ? null : result.filePaths[0] || null;
  });
  ipcMain.handle("diagnostics:run", async () =>
    runDiagnostics(appInfo, pythonService, browserHost)
  );
  ipcMain.handle("artifacts:open", async (_event, pathValue: unknown) => {
    if (typeof pathValue !== "string" || !pathValue.trim()) {
      throw new Error("文件路径不能为空");
    }
    const result = await pythonService.request("system.resolve-managed-file", {
      path: pathValue
    });
    await openLocalPath(String(result.path || ""));
  });
  ipcMain.handle("tasks:open-output", async (_event, taskId: unknown) => {
    const result = await pythonService.request("system.resolve-task-output", {
      taskId: assertTaskId(taskId)
    });
    await openLocalPath(String(result.path || ""));
  });
  ipcMain.handle("institutions:list", () => institutions.list());
  ipcMain.handle(
    "institutions:save",
    async (
      _event,
      input: { id?: string; name: string; entryUrl: string }
    ): Promise<InstitutionProfile> => {
      if (!input || typeof input !== "object") {
        throw new Error("机构配置不能为空");
      }
      const profile = await institutions.save({
        id: input.id,
        name: String(input.name || ""),
        entryUrl: String(input.entryUrl || "")
      });
      logger.write("info", "institution.saved", "Institution profile saved", {
        institutionId: profile.id,
        entryUrl: profile.entryUrl
      });
      return profile;
    }
  );
  ipcMain.handle("browser:get-state", (): BrowserHostState => browserHost.getState());
  ipcMain.handle("browser:select", async (_event, institutionId: unknown) => {
    if (typeof institutionId !== "string" || !institutionId.trim()) {
      throw new Error("institutionId is required");
    }
    return browserHost.selectInstitution(institutionId.trim());
  });
  ipcMain.handle("browser:set-bounds", (_event, bounds: BrowserBounds) => {
    browserHost.setBounds(bounds);
  });
  ipcMain.handle("browser:set-zoom", (_event, factor: unknown) => {
    if (typeof factor !== "number") {
      throw new Error("网页缩放必须是数字");
    }
    return browserHost.setBrowserZoom(factor);
  });
  ipcMain.handle(
    "browser:navigate",
    async (_event, action: "back" | "reload" | "home") => {
      if (!["back", "reload", "home"].includes(action)) {
        throw new Error("unsupported browser navigation action");
      }
      return browserHost.navigate(action);
    }
  );
  ipcMain.handle("browser:continue", () => browserHost.userContinue());
  ipcMain.handle(
    "browser:fixture-action",
    async (_event, action: "login" | "captcha" | "download" | "child") => {
      if (!["login", "captcha", "download", "child"].includes(action)) {
        throw new Error("unsupported fixture action");
      }
      return browserHost.fixtureAction(action);
    }
  );
  ipcMain.handle("backend:restart", async () => {
    await pythonService.stop();
    await pythonService.start();
    if (browserHost.getCurrentProfile()) {
      await browserHost.reconnectCurrent();
    }
    return pythonService.getState();
  });

  ipcMain.handle("tasks:create", async (_event, input: TaskCreateInput) => {
    if (!input || typeof input !== "object") {
      throw new Error("task input is required");
    }
    if (input.mode !== "single" && input.mode !== "batch") {
      throw new Error("task mode must be single or batch");
    }
    const requestedOutput = String(input.outputDir || "").trim();
    const outputDir = await ensureWritableDirectory(
      requestedOutput ||
        (input.mode === "single"
          ? appInfo.defaultSingleOutputDir
          : appInfo.defaultBatchOutputDir)
    );
    const result = await pythonService.request("task.create", {
      name: String(input.name || ""),
      mode: input.mode,
      input: input.input || {},
      outputDir,
      operationId: optionalOperationId(input.operationId)
    });
    return result.task as TaskRecord;
  });

  ipcMain.handle(
    "tasks:start-collector",
    async (_event, input: TaskCreateInput): Promise<CollectorStartResult> => {
      if (!input || typeof input !== "object") {
        throw new Error("task input is required");
      }
      if (input.mode !== "single" && input.mode !== "batch") {
        throw new Error("task mode must be single or batch");
      }
      const requestedOutput = String(input.outputDir || "").trim();
      const outputDir = await ensureWritableDirectory(
        requestedOutput ||
          (input.mode === "single"
            ? appInfo.defaultSingleOutputDir
            : appInfo.defaultBatchOutputDir)
      );
      const result = await pythonService.request(
        "collector.start",
        {
          name: String(input.name || ""),
          mode: input.mode,
          input: input.input || {},
          outputDir,
          operationId: optionalOperationId(input.operationId)
        },
        180_000
      );
      return result as unknown as CollectorStartResult;
    }
  );

  ipcMain.handle(
    "tasks:preview",
    async (
      _event,
      mode: unknown,
      input: unknown
    ): Promise<TaskPreviewSummary> => {
      if (mode !== "single" && mode !== "batch") {
        throw new Error("预览模式必须是 single 或 batch");
      }
      if (!input || typeof input !== "object" || Array.isArray(input)) {
        throw new Error("预览配置不能为空");
      }
      const result = await pythonService.request(
        "task.preview",
        {
          mode,
          input
        },
        180_000
      );
      return result as unknown as TaskPreviewSummary;
    }
  );

  ipcMain.handle(
    "tasks:list",
    async (_event, limit?: number, includeArchived?: boolean) => {
    const result = await pythonService.request("task.list", {
      limit: Math.max(1, Math.min(Number(limit) || 50, 200)),
      includeArchived: Boolean(includeArchived)
    });
    return result.tasks as TaskRecord[];
    }
  );

  ipcMain.handle("tasks:get", async (_event, taskId: unknown) => {
    const result = await pythonService.request("task.get", {
      taskId: assertTaskId(taskId)
    });
    return result.task as TaskRecord;
  });
  ipcMain.handle("tasks:get-details", async (_event, taskId: unknown) => {
    const result = await pythonService.request("task.details", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as TaskDetails;
  });
  ipcMain.handle(
    "tasks:resume-after-manual",
    async (_event, taskId: unknown, operationId: unknown) => {
      try {
        const result = await pythonService.request(
          "task.resume-after-manual",
          {
            taskId: assertTaskId(taskId),
            operationId: requiredOperationId(operationId)
          }
        );
        return result as unknown as ManualRecoveryResult;
      } catch (error) {
        if (error instanceof EngineRequestError) {
          throw new Error(`${error.code}: ${error.message}`);
        }
        throw error;
      }
    }
  );
  ipcMain.handle(
    "tasks:apply-settings",
    async (
      _event,
      payload: {
        taskId?: unknown;
        input?: unknown;
        operationId?: unknown;
      }
    ) => {
      if (!payload || typeof payload !== "object") {
        throw new Error("task settings payload is required");
      }
      if (!payload.input || typeof payload.input !== "object") {
        throw new Error("task settings input must be an object");
      }
      const taskInput = payload.input as Record<string, unknown>;
      const requestedOutput = String(taskInput.outputDir || "").trim();
      if (!requestedOutput) {
        throw new Error("outputDir must be a non-empty string");
      }
      const outputDir = await ensureWritableDirectory(requestedOutput);
      const result = await pythonService.request(
        "task.apply-settings",
        {
          taskId: assertTaskId(payload.taskId),
          input: { ...taskInput, outputDir },
          outputDir,
          operationId: requiredOperationId(payload.operationId)
        },
        60_000
      );
      return result as unknown as TaskSettingsUpdateResult;
    }
  );
  ipcMain.handle(
    "tasks:archive",
    async (_event, input: { taskIds?: unknown; reason?: unknown }) => {
      const result = await pythonService.request("task.archive", {
        taskIds: assertTaskIds(input?.taskIds),
        reason: String(input?.reason || "")
      });
      return result.tasks as TaskRecord[];
    }
  );
  ipcMain.handle("tasks:restore", async (_event, taskIds: unknown) => {
    const result = await pythonService.request("task.restore", {
      taskIds: assertTaskIds(taskIds)
    });
    return result.tasks as TaskRecord[];
  });
  ipcMain.handle("tasks:cleanup-preview", async (_event, taskIds: unknown) => {
    const result = await pythonService.request("task.cleanup-preview", {
      taskIds: assertTaskIds(taskIds)
    });
    return result as unknown as CleanupPreview;
  });
  ipcMain.handle(
    "tasks:purge",
    async (
      _event,
      input: { taskIds?: unknown; fileMode?: unknown }
    ): Promise<PurgeResult> => {
      const taskIds = assertTaskIds(input?.taskIds);
      const fileMode = String(input?.fileMode || "");
      if (fileMode !== "keep" && fileMode !== "trash") {
        throw new Error("fileMode must be keep or trash");
      }

      let trashedFiles = 0;
      let missingFiles = 0;
      if (fileMode === "trash") {
        const preview = (await pythonService.request(
          "task.cleanup-preview",
          { taskIds }
        )) as unknown as CleanupPreview;
        try {
          const cleanup = await trashRegisteredFiles(preview, {
            protectedRoots: [dataDirectory],
            trashItem: (targetPath) => shell.trashItem(targetPath)
          });
          trashedFiles = cleanup.trashedFiles;
          missingFiles = cleanup.missingFiles;
          logger.write("info", "task.cleanup.trash", "Task files moved to recycle bin", {
            taskIds,
            trashedFiles,
            missingFiles
          });
        } catch (error) {
          if (error instanceof TrashCleanupError) {
            logger.write("error", "task.cleanup.trash_failed", error.message, {
              ...error.details,
              taskIds
            });
          }
          throw error;
        }
      }

      const result = (await pythonService.request("task.purge", {
        taskIds,
        fileMode: fileMode === "trash" ? "cleaned" : "keep"
      })) as unknown as PurgeResult;
      return {
        ...result,
        fileMode,
        trashedFiles,
        missingFiles
      };
    }
  );
  ipcMain.handle("single:start", async (_event, taskId: unknown, operationId?: unknown) => {
    const result = await pythonService.request("single.start", {
      taskId: assertTaskId(taskId),
      operationId: optionalOperationId(operationId)
    }, 15_000);
    return result as unknown as TaskDetails;
  });
  ipcMain.handle("single:pause", async (_event, taskId: unknown) => {
    const result = await pythonService.request("single.pause", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as TaskDetails;
  });
  ipcMain.handle("single:resume", async (_event, taskId: unknown, operationId?: unknown) => {
    const result = await pythonService.request("single.resume", {
      taskId: assertTaskId(taskId),
      operationId: optionalOperationId(operationId)
    }, 15_000);
    return result as unknown as TaskDetails;
  });
  ipcMain.handle("single:stop", async (_event, taskId: unknown) => {
    const result = await pythonService.request("single.stop", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as TaskDetails;
  });
  ipcMain.handle("single:continue", async (_event, taskId: unknown) => {
    const result = await pythonService.request("single.continue", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as TaskDetails;
  });
  ipcMain.handle("single:continue-to-last", async (_event, taskId: unknown) => {
    const result = await pythonService.request("single.continue-to-last", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as TaskDetails;
  });
  ipcMain.handle("single:retry", async (_event, taskId: unknown, operationId?: unknown) => {
    const result = await pythonService.request("single.retry", {
      taskId: assertTaskId(taskId),
      operationId: optionalOperationId(operationId)
    }, 15_000);
    return result as unknown as TaskDetails;
  });
  ipcMain.handle(
    "single:choose-candidate",
    async (
      _event,
      input: { taskId?: unknown; itemId?: unknown; candidateKey?: unknown }
    ) => {
      const result = await pythonService.request("single.choose-candidate", {
        taskId: assertTaskId(input?.taskId),
        itemId: assertTaskId(input?.itemId),
        candidateKey: assertTaskId(input?.candidateKey)
      });
      return result as unknown as TaskDetails;
    }
  );
  ipcMain.handle("batch:get-details", async (_event, taskId: unknown) => {
    const result = await pythonService.request("batch.details", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle("batch:start", async (_event, taskId: unknown, operationId?: unknown) => {
    const result = await pythonService.request("batch.start", {
      taskId: assertTaskId(taskId),
      operationId: optionalOperationId(operationId)
    }, 15_000);
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle("batch:pause", async (_event, taskId: unknown) => {
    const result = await pythonService.request("batch.pause", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle("batch:resume", async (_event, taskId: unknown, operationId?: unknown) => {
    const result = await pythonService.request("batch.resume", {
      taskId: assertTaskId(taskId),
      operationId: optionalOperationId(operationId)
    }, 15_000);
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle("batch:stop", async (_event, taskId: unknown) => {
    const result = await pythonService.request("batch.stop", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle("batch:continue", async (_event, taskId: unknown) => {
    const result = await pythonService.request("batch.continue", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle("batch:continue-to-last", async (_event, taskId: unknown) => {
    const result = await pythonService.request("batch.continue-to-last", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle("batch:confirm-scale", async (_event, taskId: unknown) => {
    const result = await pythonService.request("batch.confirm-scale", {
      taskId: assertTaskId(taskId)
    });
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle("batch:retry", async (_event, taskId: unknown, operationId?: unknown) => {
    const result = await pythonService.request("batch.retry", {
      taskId: assertTaskId(taskId),
      operationId: optionalOperationId(operationId)
    }, 15_000);
    return result as unknown as BatchTaskDetails;
  });
  ipcMain.handle(
    "data:start",
    async (
      _event,
      input: {
        taskId?: unknown;
        options?: {
          governmentFallback?: boolean;
          governmentSearchTemplate?: string;
        };
        operationId?: string;
      }
    ) => {
      const governmentFallback = Boolean(input?.options?.governmentFallback);
      if (governmentFallback) {
        browserHost.showDataProcessingOverlay();
      }
      try {
        const result = await pythonService.request(
          "data.start",
          {
            taskId: assertTaskId(input?.taskId),
            options: {
              governmentFallback,
              governmentSearchTemplate: String(
                input?.options?.governmentSearchTemplate || ""
              )
            },
            operationId: optionalOperationId(input?.operationId)
          },
          15_000
        );
        return result as unknown as DataProcessingDetails;
      } catch (error) {
        if (governmentFallback) {
          browserHost.hideDataProcessingOverlay();
        }
        throw error;
      }
    }
  );
  ipcMain.handle("data:stop", async (_event, processingId: unknown) => {
    const result = await pythonService.request("data.stop", {
      processingId: assertTaskId(processingId)
    });
    return result as unknown as DataProcessingDetails;
  });
  ipcMain.handle("data:pause", async (_event, processingId: unknown) => {
    const result = await pythonService.request("data.pause", {
      processingId: assertTaskId(processingId)
    });
    return result as unknown as DataProcessingDetails;
  });
  ipcMain.handle("data:resume", async (_event, processingId: unknown) => {
    const result = await pythonService.request("data.resume", {
      processingId: assertTaskId(processingId)
    }, 15_000);
    return result as unknown as DataProcessingDetails;
  });
  ipcMain.handle("data:continue", async (_event, processingId: unknown) => {
    const result = await pythonService.request("data.continue", {
      processingId: assertTaskId(processingId)
    });
    return result as unknown as DataProcessingDetails;
  });
  ipcMain.handle("data:get", async (_event, processingId: unknown) => {
    const result = await pythonService.request("data.details", {
      processingId: assertTaskId(processingId)
    });
    return result as unknown as DataProcessingDetails;
  });
  ipcMain.handle("data:latest", async (_event, taskId: unknown) => {
    try {
      const result = await pythonService.request("data.latest", {
        taskId: assertTaskId(taskId)
      });
      return result as unknown as DataProcessingDetails;
    } catch (error) {
      if (
        error instanceof EngineRequestError &&
        error.code === "PROCESSING_NOT_FOUND"
      ) {
        return null;
      }
      throw error;
    }
  });

  logger.write("info", "ipc.registered", "IPC handlers registered", {
    channels: [
      "app:get-info",
      "backend:get-state",
      "backend:ping",
      "tasks:create",
      "tasks:list",
      "tasks:get",
      "tasks:get-details",
      "tasks:resume-after-manual",
      "tasks:apply-settings",
      "tasks:archive",
      "tasks:restore",
      "tasks:cleanup-preview",
      "tasks:purge",
      "single:start",
      "single:pause",
      "single:resume",
      "single:stop",
      "single:continue",
      "single:continue-to-last",
      "single:retry",
      "single:choose-candidate",
      "batch:get-details",
      "batch:start",
      "batch:pause",
      "batch:resume",
      "batch:stop",
      "batch:continue",
      "batch:continue-to-last",
      "batch:confirm-scale",
      "batch:retry",
      "data:start",
      "data:stop",
      "data:pause",
      "data:resume",
      "data:continue",
      "data:get",
      "data:latest",
      "logs:get-recent",
      "app:open-data-directory",
      "app:open-logs-directory",
      "app:select-output-directory",
      "diagnostics:run",
      "artifacts:open",
      "tasks:open-output",
      "institutions:list",
      "institutions:save",
      "browser:get-state",
      "browser:select",
      "browser:set-bounds",
      "browser:navigate",
      "browser:continue",
      "browser:fixture-action",
      "backend:restart"
    ],
    appVersion: appInfo.version,
    databaseDirectoryName: path.basename(dataDirectory)
  });
}
