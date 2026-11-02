import type { JSX } from "react";

interface ControlDiagnostic {
  ok?: boolean;
  strategy?: string;
  matched?: string;
  visible?: boolean;
  enabled?: boolean;
  text?: string;
}

interface GroupDiagnostic {
  ready?: boolean;
  missingControls?: string[];
}

const controlLabels: Record<string, string> = {
  searchInput: "搜索输入框",
  searchSubmit: "普通检索按钮",
  exactMode: "精确检索",
  titleSearchType: "标题检索范围",
  libraryRoot: "中央/地方子库",
  pageSize: "每页条数",
  yearFilter: "公布年份筛选",
  provinceFilter: "省份筛选",
  timelinessFilter: "时效性筛选",
  groupUngroup: "不分组",
  resultsRoot: "结果容器",
  resultLink: "结果标题",
  resultNumber: "结果序号",
  batchCheckbox: "结果复选框",
  resultCount: "结果总数",
  pagination: "分页容器",
  nextPage: "下一页",
  detailTitle: "详情标题",
  detailFields: "详情元数据",
  detailBody: "详情正文",
  downloadWord: "Word 下载入口",
  login: "登录表单",
  loggedIn: "登录状态",
  captcha: "滑块/验证码"
};

const groupLabels: Record<string, string> = {
  search: "搜索",
  filters: "筛选",
  results: "结果",
  pagination: "分页",
  detail: "详情",
  access: "访问状态"
};

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export default function SelectorDiagnostics({
  diagnostics
}: {
  diagnostics: Record<string, unknown> | null;
}): JSX.Element {
  if (!diagnostics) {
    return <p className="selector-diagnostic-empty">等待页面选择器诊断。</p>;
  }
  const controls = (diagnostics.controls || {}) as Record<string, ControlDiagnostic>;
  const groups = (diagnostics.groups || {}) as Record<string, GroupDiagnostic>;
  const pageMissing = stringList(diagnostics.pageMissingControls);

  return (
    <div className="selector-diagnostics">
      <div className="selector-diagnostic-summary">
        <span>
          页面 <strong>{String(diagnostics.pageKind || "unknown")}</strong>
        </span>
        <span className={diagnostics.pageReady ? "binding-ok" : "binding-pending"}>
          {diagnostics.pageReady ? "当前页关键项通过" : "当前页有关键项缺失"}
        </span>
      </div>
      <div className="selector-group-list">
        {Object.entries(groups).map(([name, group]) => (
          <span
            className={group.ready ? "selector-group ok" : "selector-group missing"}
            key={name}
            title={stringList(group.missingControls).join(", ") || "全部解析成功"}
          >
            {groupLabels[name] || name}
          </span>
        ))}
      </div>
      {pageMissing.length > 0 && (
        <p className="selector-diagnostic-error">
          缺少：{pageMissing.map((key) => controlLabels[key] || key).join("、")}
        </p>
      )}
      <details className="selector-control-details">
        <summary>查看 {Object.keys(controls).length} 项控件的解析策略</summary>
        <div className="selector-control-list">
          {Object.entries(controls).map(([key, control]) => (
            <div className={control.ok ? "selector-control ok" : "selector-control missing"} key={key}>
              <span>{controlLabels[key] || key}</span>
              <strong>{control.ok ? control.strategy || "已解析" : "缺失"}</strong>
              <code title={control.matched || control.text || ""}>
                {control.matched || control.text || "—"}
              </code>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
