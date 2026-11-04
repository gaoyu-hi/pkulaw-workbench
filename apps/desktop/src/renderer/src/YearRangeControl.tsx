import { useEffect, useState } from "react";
import type { YearSelection } from "../../shared/protocol";

interface YearRangeControlProps {
  value: YearSelection;
  onChange: (value: YearSelection) => void;
}

const MIN_YEAR = 1949;

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
          <YearInput
            label="指定年份"
            value={value.year}
            min={MIN_YEAR}
            max={currentYear}
            onChange={(year) => onChange({ mode: "single", year })}
          />
        )}
        {value.mode === "range" && (
          <>
            <YearInput
              label="起始年份"
              value={rangeStart}
              min={MIN_YEAR}
              max={currentYear}
              onChange={(year) =>
                onChange({
                  mode: "range",
                  startYear: year,
                  endYear: rangeEnd
                })
              }
            />
            <YearInput
              label="结束年份"
              value={rangeEnd}
              min={MIN_YEAR}
              max={currentYear}
              onChange={(year) =>
                onChange({
                  mode: "range",
                  startYear: rangeStart,
                  endYear: year
                })
              }
            />
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
            ? `检索 ${value.year} 年；可手动输入 ${MIN_YEAR}–${currentYear} 的四位年份`
            : `${rangeStart} 至 ${rangeEnd}，按升序逐年份执行；年份可手动输入`}
      </small>
    </div>
  );
}

function YearInput({
  label,
  value,
  min,
  max,
  onChange
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}): React.JSX.Element {
  const [text, setText] = useState(String(value));

  useEffect(() => {
    setText(String(value));
  }, [value]);

  function isValidYear(candidate: string): boolean {
    if (!/^\d{4}$/.test(candidate)) return false;
    const parsed = Number(candidate);
    return parsed >= min && parsed <= max;
  }

  function commit(candidate = text): void {
    if (!isValidYear(candidate)) {
      setText(String(value));
      return;
    }
    const parsed = Number(candidate);
    if (parsed !== value) onChange(parsed);
  }

  const invalid =
    text.length === 4 && /^\d{4}$/.test(text) && !isValidYear(text);
  return (
    <label>
      {label}
      <input
        type="text"
        inputMode="numeric"
        pattern="[0-9]{4}"
        maxLength={4}
        autoComplete="off"
        spellCheck={false}
        title={`请输入 ${min}–${max} 的四位年份`}
        aria-invalid={invalid}
        value={text}
        onFocus={(event) => event.currentTarget.select()}
        onChange={(event) => {
          const candidate = event.target.value.replace(/\D/g, "").slice(0, 4);
          setText(candidate);
          if (isValidYear(candidate)) onChange(Number(candidate));
        }}
        onBlur={() => commit()}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            commit();
            event.currentTarget.blur();
          }
        }}
      />
    </label>
  );
}
