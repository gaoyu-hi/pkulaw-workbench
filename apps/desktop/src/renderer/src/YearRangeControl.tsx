import type { YearSelection } from "../../shared/protocol";

interface YearRangeControlProps {
  value: YearSelection;
  onChange: (value: YearSelection) => void;
}

const MIN_YEAR = 2000;

export function yearSelectionPayload(
  value: YearSelection
): Record<string, unknown> {
  return {
    yearSelection: value,
    yearMode: value.mode,
    year:
      value.mode === "single"
        ? String(value.year)
        : value.mode === "all"
          ? "全部年份"
          : undefined,
    yearStart: value.mode === "range" ? value.startYear : undefined,
    yearEnd: value.mode === "range" ? value.endYear : undefined
  };
}

export default function YearRangeControl({
  value,
  onChange
}: YearRangeControlProps): React.JSX.Element {
  const currentYear = new Date().getFullYear();
  const years = Array.from(
    { length: currentYear - MIN_YEAR + 1 },
    (_, index) => currentYear - index
  );

  function selectMode(mode: YearSelection["mode"]): void {
    if (mode === "all") {
      onChange({ mode: "all" });
      return;
    }
    if (mode === "single") {
      onChange({
        mode: "single",
        year: value.mode === "single" ? value.year : currentYear
      });
      return;
    }
    onChange({
      mode: "range",
      startYear: value.mode === "range" ? value.startYear : MIN_YEAR,
      endYear: value.mode === "range" ? value.endYear : currentYear
    });
  }

  const rangeStart = value.mode === "range" ? value.startYear : MIN_YEAR;
  const rangeEnd = value.mode === "range" ? value.endYear : currentYear;
  return (
    <div className="year-range-control">
      <div className="year-mode-row">
        <label>
          年份模式
          <select
            value={value.mode}
            onChange={(event) =>
              selectMode(event.target.value as YearSelection["mode"])
            }
          >
            <option value="all">全部年份</option>
            <option value="single">指定年份</option>
            <option value="range">年份区间</option>
          </select>
        </label>
        {value.mode === "single" && (
          <label>
            指定年份
            <select
              value={value.year}
              onChange={(event) =>
                onChange({ mode: "single", year: Number(event.target.value) })
              }
            >
              {years.map((year) => (
                <option key={year} value={year}>{year}</option>
              ))}
            </select>
          </label>
        )}
        {value.mode === "range" && (
          <>
            <label>
              起始年份
              <select
                value={rangeStart}
                onChange={(event) =>
                  onChange({
                    mode: "range",
                    startYear: Number(event.target.value),
                    endYear: rangeEnd
                  })
                }
              >
                {years.map((year) => (
                  <option key={year} value={year}>{year}</option>
                ))}
              </select>
            </label>
            <label>
              结束年份
              <select
                value={rangeEnd}
                onChange={(event) =>
                  onChange({
                    mode: "range",
                    startYear: rangeStart,
                    endYear: Number(event.target.value)
                  })
                }
              >
                {years.map((year) => (
                  <option key={year} value={year}>{year}</option>
                ))}
              </select>
            </label>
          </>
        )}
      </div>
      {value.mode === "range" && rangeStart > rangeEnd && (
        <small className="year-range-error">起始年份不能晚于结束年份</small>
      )}
      <small className="year-range-hint">
        {value.mode === "all"
          ? "不分年份，一次检索全部年份"
          : value.mode === "single"
            ? `检索 ${value.year} 年`
            : `${rangeStart} 至 ${rangeEnd}，按升序逐年份执行`}
      </small>
    </div>
  );
}
