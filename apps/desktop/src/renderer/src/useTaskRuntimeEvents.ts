import { useEffect, useState } from "react";
import type { EngineEvent } from "../../shared/protocol";
import { newOperationId } from "../../shared/browser-lease";
import { asRecord } from "./task-presentation";
import type { RuntimeAlert } from "./TaskRuntimePanel";

function eventTaskId(event: EngineEvent): string {
  const data = asRecord(event.data);
  const intent = asRecord(data.intent);
  const browserEvent = asRecord(data.event);
  return String(
    data.taskId ||
      intent.taskId ||
      browserEvent.taskId ||
      ""
  );
}

export function useTaskRuntimeEvents(
  taskId: string
): RuntimeAlert[] {
  const [alerts, setAlerts] = useState<RuntimeAlert[]>([]);
  useEffect(() => {
    setAlerts([]);
    if (!taskId) return;
    const unsubscribe = window.pkulaw.onEngineEvent((event) => {
      if (eventTaskId(event) !== taskId) return;
      const data = asRecord(event.data);
      let alert: RuntimeAlert | null = null;
      if (event.event === "DOWNLOAD_EVENT_MISMATCH") {
        alert = {
          id: newOperationId(),
          title: "下载事件与当前意图不匹配",
          detail: String(
            data.reason ||
              "不匹配文件不会交给当前任务。"
          ),
          severity: "warning",
          createdAt: event.timestamp
        };
      } else if (event.event === "batch.artifact-invalid") {
        const error = asRecord(data.error);
        alert = {
          id: newOperationId(),
          title: "批次文件完整性失败关闭",
          detail: String(
            error.message ||
              "已登记文件缺失、被篡改或 SQLite 状态不一致。"
          ),
          severity: "error",
          createdAt: event.timestamp
        };
      }
      if (!alert) return;
      setAlerts((current) => [
        ...current.filter((item) => item.id !== alert?.id).slice(-19),
        alert as RuntimeAlert
      ]);
    });
    return unsubscribe;
  }, [taskId]);
  return alerts;
}
