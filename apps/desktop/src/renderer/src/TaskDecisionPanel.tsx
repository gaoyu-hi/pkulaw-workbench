interface TaskDecisionPanelProps {
  checkpoint: Record<string, unknown>;
}

interface ScaleDecisionPanelProps extends TaskDecisionPanelProps {
  busy: boolean;
  onConfirm: () => void;
}

function value(
  record: Record<string, unknown>,
  key: string,
  fallback = "—"
): string {
  const item = record[key];
  return item === undefined || item === null || item === ""
    ? fallback
    : String(item);
}

function libraryLabel(raw: unknown): string {
  if (raw === "central") return "中央法规";
  if (raw === "local") return "地方法规";
  return String(raw || "当前子库");
}

export function PageLimitDecision({
  checkpoint
}: TaskDecisionPanelProps): React.JSX.Element {
  return (
    <section className="task-decision-panel page-limit">
      <div>
        <span className="eyebrow">分页上限已到达</span>
        <h3>当前组合尚未扫描完</h3>
        <p>
          程序不会把已到达上限误标为完成。你可以继续扫描当前范围到尾页，
          或停止并保留当前年度、页码和组合断点。
        </p>
      </div>
      <div className="decision-metrics">
        <div>
          <span>年份</span>
          <strong>{value(checkpoint, "currentYear")}</strong>
        </div>
        <div>
          <span>已扫描</span>
          <strong>{value(checkpoint, "scannedPages")} 页</strong>
        </div>
        <div>
          <span>未扫描</span>
          <strong>{value(checkpoint, "unscannedPages")} 页</strong>
        </div>
        <div>
          <span>实际总页数</span>
          <strong>{value(checkpoint, "totalPages")} 页</strong>
        </div>
      </div>
      <small>
        {value(checkpoint, "currentQuery", "当前关键词")} ·{" "}
        {libraryLabel(checkpoint.currentLibrary)} · 当前第{" "}
        {value(checkpoint, "currentPage")} 页
      </small>
    </section>
  );
}

export function ScaleDecisionPanel({
  checkpoint,
  busy,
  onConfirm
}: ScaleDecisionPanelProps): React.JSX.Element {
  const scale =
    checkpoint.scaleEstimate &&
    typeof checkpoint.scaleEstimate === "object"
      ? (checkpoint.scaleEstimate as Record<string, unknown>)
      : {};
  const projected = value(scale, "confidence") === "projected";
  const actual = value(scale, "confidence") === "actual";
  return (
    <section className="task-decision-panel scale-limit">
      <div>
        <span className="eyebrow">批量规模确认</span>
        <h3>{actual ? "扫描规模已达到确认阈值" : "预计批量规模较大"}</h3>
        <p>
          {projected
            ? "首批结果仅用于保守投影；后续各关键词、子库和年度仍会独立读取真实页数。"
            : actual
              ? "以下为已完成检索后的实际规模。确认前不会创建或下载新的批次。"
              : "请核对关键词、子库、年度和分页范围后继续。"}
        </p>
      </div>
      <div className="decision-metrics">
        <div>
          <span>预计页数</span>
          <strong>{value(scale, "estimatedPages")}</strong>
        </div>
        <div>
          <span>预计政策</span>
          <strong>{value(scale, "estimatedRows")}</strong>
        </div>
        <div>
          <span>50 条批次</span>
          <strong>{value(scale, "estimatedBatches")}</strong>
        </div>
        <div>
          <span>年度组合</span>
          <strong>
            {value(scale, "yearCount")} / {value(scale, "combinationCount")}
          </strong>
        </div>
      </div>
      <div className="decision-caption">
        <span>
          {scale.dualLibrary === true ? "中央 + 地方双库" : "单一子库"} ·{" "}
          {value(scale, "pageMode") === "last" ? "逐组合扫描到尾页" : "指定页数上限"}
        </span>
        <button
          type="button"
          className="primary-button compact"
          disabled={busy}
          onClick={onConfirm}
        >
          确认规模并继续
        </button>
      </div>
    </section>
  );
}

export function ScaleEstimateSummary({
  checkpoint
}: TaskDecisionPanelProps): React.JSX.Element {
  const scale =
    checkpoint.scaleEstimate &&
    typeof checkpoint.scaleEstimate === "object"
      ? (checkpoint.scaleEstimate as Record<string, unknown>)
      : {};
  const confidence = value(scale, "confidence");
  const stageLabel =
    confidence === "actual"
      ? "实际扫描规模"
      : confidence === "projected"
        ? "首批结果投影"
        : "扫描前结构估算";
  return (
    <section className="batch-scale-summary">
      <div>
        <strong>{stageLabel}</strong>
        <small>
          {value(scale, "combinationCount")} 个关键词 × 子库 × 年度组合
        </small>
      </div>
      <span>{value(scale, "estimatedPages")} 页</span>
      <span>{value(scale, "estimatedRows")} 条政策</span>
      <span>{value(scale, "estimatedBatches")} 个批次</span>
    </section>
  );
}
