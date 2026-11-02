interface YearStatsTableProps {
  stats: unknown;
}

const statusText: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  no_result: "无结果",
  completed: "已完成",
  paused: "已暂停",
  failed: "失败"
};

export default function YearStatsTable({
  stats
}: YearStatsTableProps): React.JSX.Element | null {
  if (!Array.isArray(stats) || stats.length === 0) return null;
  return (
    <div className="year-stats-panel">
      <div className="year-stats-heading">
        <strong>年度执行统计</strong>
        <span>按组合顺序记录无结果、完成和失败年份</span>
      </div>
      <div className="year-stats-grid">
        {stats.map((raw, index) => {
          const item = raw as Record<string, unknown>;
          const status = String(item.status || "pending");
          return (
            <div className="year-stat-row" key={`${String(item.year)}-${index}`}>
              <strong>{String(item.year || "全部年份")}</strong>
              <span>{item.library === "local" ? "地方" : "中央"}</span>
              <span>{String(item.query || "—")}</span>
              <span className={`year-stat-status ${status}`}>
                {statusText[status] || status}
              </span>
              <small>
                {item.resultCount !== undefined
                  ? `${String(item.resultCount)} 条`
                  : ""}
                {item.rowsAccepted !== undefined
                  ? ` · 接收 ${String(item.rowsAccepted)} 条`
                  : ""}
              </small>
            </div>
          );
        })}
      </div>
    </div>
  );
}
