import type {
  CollectorMatchMode,
  FreshTaskSeed,
  PageSelection,
  YearSelection
} from "../../shared/protocol";
import { useCallback, useRef } from "react";
import { defaultCollectorMatchMode } from "./collector-workflow";

export type CollectorLibrary = "central" | "local" | "both";
export type CollectorCatalogFormat = "xlsx" | "xls" | "html-xls";

export function shouldResetCollector(
  previousStatus: string | null | undefined,
  currentStatus: string | null | undefined
): boolean {
  return Boolean(
    previousStatus &&
      previousStatus !== "completed" &&
      currentStatus === "completed"
  );
}

export function canOpenTaskSettings(
  status: string | null | undefined
): boolean {
  return status === "paused";
}

export function shouldAutoSelectTask(
  taskId: string | null | undefined,
  suppressedTaskId: string | null | undefined
): boolean {
  return Boolean(taskId) && taskId !== suppressedTaskId;
}

export function shouldLoadTaskDetails(
  taskId: string | null | undefined,
  suppressedTaskId: string | null | undefined
): boolean {
  return shouldAutoSelectTask(taskId, suppressedTaskId);
}

export interface CollectorAutoLoadController {
  observeStatus(
    taskId: string,
    currentStatus: string | null | undefined
  ): boolean;
  canAutoSelectTask(taskId: string | null | undefined): boolean;
  canLoadTaskDetails(taskId: string | null | undefined): boolean;
  clearForExplicitSelection(): void;
  clearForNewTask(): void;
  holdFreshWorkspace(taskId?: string): void;
}

export function useCollectorAutoLoad(): CollectorAutoLoadController {
  const previousStatusByTaskRef = useRef<
    Record<string, string | undefined>
  >({});
  const suppressedCompletedTaskIdRef = useRef("");
  const freshWorkspaceHeldRef = useRef(false);

  const observeStatus = useCallback(
    (taskId: string, currentStatus: string | null | undefined): boolean => {
      const previousStatus = previousStatusByTaskRef.current[taskId];
      previousStatusByTaskRef.current[taskId] =
        currentStatus ?? undefined;
      if (!shouldResetCollector(previousStatus, currentStatus)) {
        return false;
      }
      suppressedCompletedTaskIdRef.current = taskId;
      return true;
    },
    []
  );

  const canAutoSelectTask = useCallback(
    (taskId: string | null | undefined): boolean =>
      !freshWorkspaceHeldRef.current &&
      shouldAutoSelectTask(taskId, suppressedCompletedTaskIdRef.current),
    []
  );

  const canLoadTaskDetails = useCallback(
    (taskId: string | null | undefined): boolean =>
      !freshWorkspaceHeldRef.current &&
      shouldLoadTaskDetails(taskId, suppressedCompletedTaskIdRef.current),
    []
  );

  const clearForExplicitSelection = useCallback(() => {
    suppressedCompletedTaskIdRef.current = "";
    freshWorkspaceHeldRef.current = false;
  }, []);

  const clearForNewTask = useCallback(() => {
    suppressedCompletedTaskIdRef.current = "";
    freshWorkspaceHeldRef.current = false;
  }, []);

  const holdFreshWorkspace = useCallback((taskId = "") => {
    freshWorkspaceHeldRef.current = true;
    suppressedCompletedTaskIdRef.current = taskId;
  }, []);

  return {
    observeStatus,
    canAutoSelectTask,
    canLoadTaskDetails,
    clearForExplicitSelection,
    clearForNewTask,
    holdFreshWorkspace
  };
}

export interface SingleCollectorResetSetters {
  setQueries: (value: string) => void;
  setMatchMode: (value: CollectorMatchMode) => void;
  setLibrary: (value: CollectorLibrary) => void;
  setYearSelection: (value: YearSelection) => void;
  setProvince: (value: string) => void;
  setTimeliness: (value: string) => void;
  setPageSize: (value: number) => void;
  setPageSelection: (value: PageSelection) => void;
  setOutputDir: (value: string) => void;
}

export interface BatchCollectorResetSetters
  extends SingleCollectorResetSetters {
  setCatalogFormat: (value: CollectorCatalogFormat) => void;
}

export function resetSingleCollectorForm(
  defaultOutputDir: string,
  setters: SingleCollectorResetSetters
): void {
  setters.setQueries("");
  setters.setMatchMode(defaultCollectorMatchMode("single"));
  setters.setLibrary("both");
  setters.setYearSelection({ mode: "all" });
  setters.setProvince("全部省份");
  setters.setTimeliness("全部（仅记录元数据，不筛选）");
  setters.setPageSize(20);
  setters.setPageSelection({ mode: "last" });
  setters.setOutputDir(defaultOutputDir);
}

export function resetBatchCollectorForm(
  defaultOutputDir: string,
  setters: BatchCollectorResetSetters
): void {
  setters.setQueries("");
  setters.setMatchMode(defaultCollectorMatchMode("batch"));
  setters.setLibrary("central");
  setters.setYearSelection({ mode: "all" });
  setters.setProvince("全部省份");
  setters.setTimeliness("全部（仅记录元数据，不筛选）");
  setters.setPageSize(50);
  setters.setPageSelection({ mode: "last" });
  setters.setCatalogFormat("xlsx");
  setters.setOutputDir(defaultOutputDir);
}

export function prefillSingleCollectorForm(
  seed: FreshTaskSeed,
  setters: SingleCollectorResetSetters
): void {
  const input = seed.input;
  setters.setQueries(seedQueries(input));
  setters.setMatchMode(
    normalizeMatchMode(input.matchMode, "single")
  );
  setters.setLibrary(normalizeLibrary(input.library, "both"));
  setters.setYearSelection(seedYearSelection(input));
  setters.setProvince(
    String(input.province || "全部省份")
  );
  setters.setTimeliness(
    String(
      input.timeliness ||
        "全部（仅记录元数据，不筛选）"
    )
  );
  setters.setPageSize(numberValue(input.pageSize, 20));
  setters.setPageSelection(seedPageSelection(input));
  setters.setOutputDir(seed.outputDir);
}

export function prefillBatchCollectorForm(
  seed: FreshTaskSeed,
  setters: BatchCollectorResetSetters
): void {
  prefillSingleCollectorForm(seed, setters);
  setters.setCatalogFormat(
    normalizeCatalogFormat(seed.input.catalogFormat)
  );
}

function seedQueries(input: Record<string, unknown>): string {
  const values = Array.isArray(input.queries)
    ? input.queries
    : Array.isArray(input.keywords)
      ? input.keywords
      : [];
  return values
    .map((value) => String(value).trim())
    .filter(Boolean)
    .join("\n");
}

function seedYearSelection(
  input: Record<string, unknown>
): YearSelection {
  const mode = String(input.yearMode || "all");
  if (mode === "single") {
    return {
      mode: "single",
      year: numberValue(input.year, new Date().getFullYear())
    };
  }
  if (mode === "range") {
    return {
      mode: "range",
      startYear: numberValue(
        input.startYear,
        new Date().getFullYear()
      ),
      endYear: numberValue(
        input.endYear,
        new Date().getFullYear()
      )
    };
  }
  return { mode: "all" };
}

function seedPageSelection(
  input: Record<string, unknown>
): PageSelection {
  const nested = input.pageSelection;
  const nestedMode =
    nested && typeof nested === "object"
      ? String((nested as Record<string, unknown>).mode || "")
      : "";
  const mode = String(input.pageMode || nestedMode || "last");
  if (mode === "limit") {
    const nestedMax =
      nested && typeof nested === "object"
        ? (nested as Record<string, unknown>).maxPages
        : undefined;
    return {
      mode: "limit",
      maxPages: Math.max(
        1,
        numberValue(input.maxPages ?? nestedMax, 1)
      )
    };
  }
  return { mode: "last" };
}

function normalizeMatchMode(
  value: unknown,
  mode: "single" | "batch"
): CollectorMatchMode {
  const candidate = String(value || "");
  if (
    candidate === "keyword" ||
    candidate === "title" ||
    candidate === "fulltext"
  ) {
    return candidate;
  }
  return defaultCollectorMatchMode(mode);
}

function normalizeLibrary(
  value: unknown,
  fallback: CollectorLibrary
): CollectorLibrary {
  const candidate = String(value || "");
  return candidate === "central" ||
    candidate === "local" ||
    candidate === "both"
    ? candidate
    : fallback;
}

function normalizeCatalogFormat(
  value: unknown
): CollectorCatalogFormat {
  const candidate = String(value || "");
  return candidate === "xls" ||
    candidate === "html-xls" ||
    candidate === "xlsx"
    ? candidate
    : "xlsx";
}

function numberValue(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}
