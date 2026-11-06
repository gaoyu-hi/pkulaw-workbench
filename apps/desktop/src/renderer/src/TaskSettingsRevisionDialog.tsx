import { useState } from "react";
import type {
  TaskSettingsRevisionPreview
} from "../../shared/protocol";

export type ArtifactDisposition = "preserve" | "delete";

export interface TaskSettingsRevisionDialogProps {
  preview: TaskSettingsRevisionPreview;
  busy: boolean;
  onCommit: (policy: ArtifactDisposition) => Promise<void>;
  onCancel: () => void;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${amount.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export default function TaskSettingsRevisionDialog({
  preview,
  busy,
  onCommit,
  onCancel
}: TaskSettingsRevisionDialogProps): React.JSX.Element {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const summary = preview.artifactSummary;
  const protectedCount = summary.protectedFileCount;

  return (
    <div className="task-dialog-backdrop">
      <section
        className="task-settings-revision-dialog task-pause-detail-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="task-settings-revision-title"
      >
        <div className="task-pause-detail-heading">
          <div>
            <span className="eyebrow">任务设置修订</span>
            <h3 id="task-settings-revision-title">
              {confirmingDelete ? "永久删除确认" : "如何处理已有成果"}
            </h3>
          </div>
          <button
            type="button"
            className="secondary-button compact"
            disabled={busy}
            onClick={onCancel}
          >
            取消
          </button>
        </div>

        <dl className="task-pause-detail-facts">
          <div>
            <dt>已完成政策</dt>
            <dd>{summary.completedPolicyCount}</dd>
          </div>
          <div>
            <dt>已完成批次</dt>
            <dd>{summary.completedBatchCount}</dd>
          </div>
          <div>
            <dt>登记文件</dt>
            <dd>{summary.registeredFileCount}</dd>
          </div>
          <div>
            <dt>任务专属目录</dt>
            <dd>{summary.directoryCount}</dd>
          </div>
          <div>
            <dt>预计可释放</dt>
            <dd>{formatBytes(summary.estimatedFreedBytes)}</dd>
          </div>
        </dl>

        {protectedCount > 0 && (
          <p className="task-settings-error">
            有 {protectedCount} 个文件受保护或与其他任务共享，永久删除可能被阻止。
          </p>
        )}

        {!confirmingDelete ? (
          <div className="collector-action-buttons">
            <button
              type="button"
              className="secondary-button"
              disabled={busy}
              onClick={onCancel}
            >
              取消
            </button>
            <button
              type="button"
              className="primary-button"
              disabled={busy}
              onClick={() => void onCommit("preserve")}
            >
              保留已完成文件并重新扫描
            </button>
            <button
              type="button"
              className="danger-button"
              disabled={busy}
              onClick={() => setConfirmingDelete(true)}
            >
              永久删除旧结果并重新扫描
            </button>
          </div>
        ) : (
          <div className="task-settings-revision-confirm">
            <p>
              将永久删除当前任务登记的下载文件、目录、批次和处理结果，立即释放磁盘空间。此操作不可恢复。
            </p>
            <div className="collector-action-buttons">
              <button
                type="button"
                className="secondary-button"
                disabled={busy}
                onClick={() => setConfirmingDelete(false)}
              >
                返回
              </button>
              <button
                type="button"
                className="danger-button"
                disabled={busy}
                onClick={() => void onCommit("delete")}
              >
                {busy ? "正在删除…" : "确认永久删除并重新扫描"}
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
