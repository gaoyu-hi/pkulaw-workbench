import type { TaskPreviewSummary } from "../../shared/protocol";
import { collectorPreviewCardView } from "./collector-workflow";
import { ExpandableValue } from "./TaskRuntimePanel";

export function TaskPreviewCard({
  preview,
  fresh
}: {
  preview: TaskPreviewSummary | null;
  fresh: boolean;
}): React.JSX.Element {
  if (!preview) {
    return (
      <section className="collector-preview-card pending" data-testid="collector-preview">
        <div className="collector-preview-heading">
          <div>
            <span className="eyebrow">启动前预览</span>
            <strong>尚未执行 0 下载预览</strong>
          </div>
          <span>下载 0</span>
        </div>
        <p>结果窗口只做 0 下载探测：预览不创建下载意图，不打开详情或批次。</p>
      </section>
    );
  }
  const view = collectorPreviewCardView(preview, fresh);
  return (
    <section
      className={`collector-preview-card ${fresh ? "ready" : "stale"}`}
      data-testid="collector-preview"
    >
      <div className="collector-preview-heading">
        <div>
          <span className="eyebrow">启动前预览 · 下载 0</span>
          <strong>{fresh ? "预览结果可用于启动" : "配置已变化，请重新预览"}</strong>
        </div>
        <span>{preview.mode === "batch" ? "批量关键词" : "逐篇任务"}</span>
      </div>
      <div className="collector-preview-metrics">
        <span>组合<b>{view.combinations}</b></span>
        <span>结果<b>{view.results}</b></span>
        <span>总页数<b>{view.pages}</b></span>
        <span>预计 50 条批次<b>{view.batches}</b></span>
        <span>下载<b>{view.downloads}</b></span>
      </div>
      <p
        className={
          preview.windowStatus === "probe_required"
            ? "collector-window-warning"
            : "collector-window-note"
        }
        data-testid="collector-window-status"
      >
        {view.windowNotice}
      </p>
      <ExpandableValue
        label="逐组合预览"
        value={JSON.stringify(preview.combinations, null, 2)}
      />
    </section>
  );
}
