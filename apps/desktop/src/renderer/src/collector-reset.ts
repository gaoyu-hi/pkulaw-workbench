import type {
  CollectorMatchMode,
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
}

export function useCollectorAutoLoad(): CollectorAutoLoadController {
  const previousStatusByTaskRef = useRef<
    Record<string, string | undefined>
  >({});
  const suppressedCompletedTaskIdRef = useRef("");

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
      shouldAutoSelectTask(taskId, suppressedCompletedTaskIdRef.current),
    []
  );

  const canLoadTaskDetails = useCallback(
    (taskId: string | null | undefined): boolean =>
      shouldLoadTaskDetails(taskId, suppressedCompletedTaskIdRef.current),
    []
  );

  const clearForExplicitSelection = useCallback(() => {
    suppressedCompletedTaskIdRef.current = "";
  }, []);

  const clearForNewTask = useCallback(() => {
    suppressedCompletedTaskIdRef.current = "";
  }, []);

  return {
    observeStatus,
    canAutoSelectTask,
    canLoadTaskDetails,
    clearForExplicitSelection,
    clearForNewTask
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
