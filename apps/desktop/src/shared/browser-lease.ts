import type {
  BrowserHostState,
  BrowserLeaseMode,
  TaskRecord
} from "./protocol";

export function emptyBrowserLease(): BrowserHostState["browserLease"] {
  return {
    available: true,
    leaseId: "",
    taskId: "",
    mode: "",
    createdAt: "",
    updatedAt: ""
  };
}

export function leaseModeLabel(mode: BrowserLeaseMode | ""): string {
  const labels: Record<BrowserLeaseMode, string> = {
    single: "逐篇采集",
    batch: "批量采集",
    data: "数据处理"
  };
  return mode ? labels[mode] : "未知任务";
}

export function holdingTaskLabel(
  state: BrowserHostState,
  tasks: TaskRecord[]
): string {
  const lease = state.browserLease;
  if (lease.available) return "内置浏览器当前空闲";
  const task = tasks.find((item) => item.id === lease.taskId);
  const taskLabel = task?.name || `任务 ${lease.taskId.slice(0, 8)}`;
  return `内置浏览器正由“${taskLabel}”使用（${leaseModeLabel(lease.mode)}）`;
}

export function leaseBlocksNewBrowserTask(
  state: BrowserHostState | null,
  allowedTaskId = ""
): boolean {
  const lease = state?.browserLease;
  if (!lease || lease.available) return false;
  return !allowedTaskId || lease.taskId !== allowedTaskId;
}

export function newOperationId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
