export interface TaskPauseDetail {
  code: string;
  title: string;
  message: string;
  stage: string;
  checkpoint: string;
  recommendation: string;
  raw: Record<string, unknown>;
}

export default function TaskPauseDetailDialog({
  detail,
  onClose
}: {
  detail: TaskPauseDetail;
  onClose: () => void;
}): React.JSX.Element {
  return (
    <div className="task-dialog-backdrop">
      <section
        className="task-pause-detail-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="task-pause-detail-title"
      >
        <div className="task-pause-detail-heading">
          <div>
            <span className="eyebrow">任务暂停详情</span>
            <h3 id="task-pause-detail-title">{detail.title}</h3>
          </div>
          <button
            type="button"
            className="secondary-button compact"
            onClick={onClose}
          >
            关闭详情
          </button>
        </div>
        <dl className="task-pause-detail-facts">
          <div>
            <dt>错误代码</dt>
            <dd><code>{detail.code || "未记录"}</code></dd>
          </div>
          <div>
            <dt>当前阶段</dt>
            <dd>{detail.stage || "未记录"}</dd>
          </div>
          <div>
            <dt>完整原因</dt>
            <dd>{detail.message || "未记录"}</dd>
          </div>
          <div>
            <dt>当前组合与断点</dt>
            <dd>{detail.checkpoint || "未记录"}</dd>
          </div>
          <div>
            <dt>建议动作</dt>
            <dd>{detail.recommendation || "未记录"}</dd>
          </div>
        </dl>
        <div className="task-pause-detail-raw">
          <strong>原始错误与断点</strong>
          <pre>{JSON.stringify(detail.raw, null, 2)}</pre>
        </div>
      </section>
    </div>
  );
}
