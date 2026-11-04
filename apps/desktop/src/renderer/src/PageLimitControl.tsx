import type { PageSelection } from "../../shared/protocol";

interface PageLimitControlProps {
  value: PageSelection;
  onChange: (value: PageSelection) => void;
}

export function pageSelectionPayload(
  value: PageSelection
): Record<string, unknown> {
  return {
    pageSelection: value,
    pageMode: value.mode,
    maxPages: value.mode === "limit" ? value.maxPages : undefined
  };
}

export default function PageLimitControl({
  value,
  onChange
}: PageLimitControlProps): React.JSX.Element {
  return (
    <div className="page-limit-control">
      <label>
        分页模式
        <select
          value={value.mode}
          onChange={(event) => {
            const mode = event.target.value as PageSelection["mode"];
            onChange(
              mode === "last"
                ? { mode: "last" }
                : {
                    mode: "limit",
                    maxPages: value.mode === "limit" ? value.maxPages : 1
                  }
            );
          }}
        >
          <option value="last">扫描到尾页</option>
          <option value="limit">指定页数上限</option>
        </select>
      </label>
      {value.mode === "limit" && (
        <label>
          最多页数
          <input
            type="number"
            min={1}
            max={1000}
            value={value.maxPages}
            onChange={(event) =>
              onChange({
                mode: "limit",
                maxPages: Math.max(1, Number(event.target.value) || 1)
              })
            }
          />
        </label>
      )}
      <small className="page-limit-hint">
        {value.mode === "last"
          ? "每个关键词 × 子库 × 年份独立扫描到尾页"
          : `超过 ${value.maxPages} 页时暂停，由你决定是否继续到尾页`}
      </small>
    </div>
  );
}
