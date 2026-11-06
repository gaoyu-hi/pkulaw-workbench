import { useState } from "react";
import type {
  CollectorMatchMode,
  PageSelection,
  TaskMode,
  YearSelection
} from "../../shared/protocol";
import type { CatalogFormat } from "../../shared/catalog-formats";
import {
  CATALOG_FORMAT_LABELS
} from "../../shared/catalog-formats";
import PageLimitControl, {
  pageSelectionPayload
} from "./PageLimitControl";
import YearRangeControl, {
  yearSelectionPayload
} from "./YearRangeControl";
import {
  collectorSearchModeOptions,
  normalizeCollectorMatchMode,
  searchModeDisplayLabel
} from "./collector-search-mode";
import {
  effectiveProvince,
  showsProvinceControl
} from "./collector-library-policy";
import { usePersistentState } from "./usePersistentState";

const PROVINCES = [
  "北京市", "天津市", "河北省", "山西省", "内蒙古自治区", "辽宁省", "吉林省",
  "黑龙江省", "上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省",
  "山东省", "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区", "海南省",
  "重庆市", "四川省", "贵州省", "云南省", "西藏自治区", "陕西省", "甘肃省",
  "青海省", "宁夏回族自治区", "新疆维吾尔自治区"
];

export interface TaskSettingsDraft {
  queries: string;
  matchMode: CollectorMatchMode;
  library: "central" | "local" | "both";
  yearSelection: YearSelection;
  province: string;
  timeliness: string;
  pageSize: number;
  pageSelection: PageSelection;
  catalogFormat: CatalogFormat;
  outputDir: string;
}

function queryText(input: Record<string, unknown>): string {
  const value = input.queries ?? input.keywords;
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean).join("\n");
  }
  return typeof value === "string" ? value : "";
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? value as Record<string, unknown>
    : {};
}

function yearSelectionFromInput(input: Record<string, unknown>): YearSelection {
  const selection = recordValue(input.yearSelection);
  const mode = selection.mode ?? input.yearMode;
  const currentYear = new Date().getFullYear();
  const clamp = (value: unknown): number =>
    Math.max(1949, Math.min(currentYear, Number(value) || currentYear));
  if (mode === "single") {
    return {
      mode: "single",
      year: clamp(selection.year ?? input.year)
    };
  }
  if (mode === "range") {
    const startYear = clamp(selection.startYear ?? input.yearStart);
    const endYear = clamp(selection.endYear ?? input.yearEnd);
    return { mode: "range", startYear, endYear };
  }
  if (input.year && input.year !== "全部年份") {
    return { mode: "single", year: clamp(input.year) };
  }
  return { mode: "all" };
}

function pageSelectionFromInput(input: Record<string, unknown>): PageSelection {
  const selection = recordValue(input.pageSelection);
  const mode = selection.mode ?? input.pageMode;
  if (mode === "limit") {
    return {
      mode: "limit",
      maxPages: Math.max(
        1,
        Number(selection.maxPages ?? input.maxPages) || 1
      )
    };
  }
  return { mode: "last" };
}

export function taskSettingsDraftFromInput(
  mode: TaskMode,
  input: Record<string, unknown>
): TaskSettingsDraft {
  const library = String(input.library || "both");
  const pageSize = Number(input.pageSize || (mode === "batch" ? 50 : 20));
  const catalogFormat = String(input.catalogFormat || "xlsx");
  return {
    queries: queryText(input),
    matchMode: normalizeCollectorMatchMode(mode, input.matchMode),
    library:
      library === "central" || library === "local"
        ? library
        : "both",
    yearSelection: yearSelectionFromInput(input),
    province: String(input.province || "全部省份"),
    timeliness: String(
      input.timeliness || "全部（仅记录元数据，不筛选）"
    ),
    pageSize: [10, 20, 50, 100].includes(pageSize) ? pageSize : 20,
    pageSelection: pageSelectionFromInput(input),
    catalogFormat:
      catalogFormat === "xls" || catalogFormat === "html-xls"
        ? catalogFormat
        : "xlsx",
    outputDir: String(input.outputDir || "")
  };
}

export function buildTaskSettingsInput(
  mode: TaskMode,
  draft: TaskSettingsDraft
): Record<string, unknown> {
  const lines = draft.queries
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  const outputDir = draft.outputDir.trim();
  return {
    queries: lines,
    keywords: lines,
    matchMode: normalizeCollectorMatchMode(mode, draft.matchMode),
    library: draft.library,
    ...yearSelectionPayload(draft.yearSelection),
    province: effectiveProvince(draft.library, draft.province),
    timeliness: draft.timeliness,
    pageSize: draft.pageSize,
    ...pageSelectionPayload(draft.pageSelection),
    searchStrategyVersion: 2,
    ...(mode === "batch"
      ? {
          catalogFormat: draft.catalogFormat,
          fullFormat: "docx"
        }
      : {}),
    outputDir
  };
}

function draftError(draft: TaskSettingsDraft): string {
  if (!draft.queries.split(/\r?\n/).some((value) => value.trim())) {
    return "请至少保留一个检索关键词或完整标题。";
  }
  if (!draft.outputDir.trim()) {
    return "请指定输出目录。";
  }
  if (
    draft.yearSelection.mode === "range" &&
    draft.yearSelection.startYear > draft.yearSelection.endYear
  ) {
    return "起始年份不能晚于结束年份。";
  }
  return "";
}

export interface TaskSettingsPanelProps {
  mode: TaskMode;
  taskId: string;
  input: Record<string, unknown>;
  busy: boolean;
  onApply: (input: Record<string, unknown>) => Promise<void> | void;
  onPrepare?: (input: Record<string, unknown>) => Promise<void> | void;
  onClose: () => void;
}

export default function TaskSettingsPanel({
  mode,
  taskId,
  input,
  busy,
  onApply,
  onPrepare,
  onClose
}: TaskSettingsPanelProps): React.JSX.Element {
  const persistKey = `pkulaw.task-settings.${taskId}.v1`;
  const [draft, setDraft] = usePersistentState<TaskSettingsDraft>(
    persistKey,
    () => taskSettingsDraftFromInput(mode, input)
  );
  const [error, setError] = useState("");

  function update<K extends keyof TaskSettingsDraft>(
    key: K,
    value: TaskSettingsDraft[K]
  ): void {
    setDraft((current) => ({ ...current, [key]: value }));
    setError("");
  }

  async function apply(): Promise<void> {
    const validationError = draftError(draft);
    if (validationError) {
      setError(validationError);
      return;
    }
    try {
      const nextInput = buildTaskSettingsInput(mode, draft);
      if (onPrepare) {
        await onPrepare(nextInput);
      } else {
        await onApply(nextInput);
      }
      if (!onPrepare) {
        globalThis.localStorage?.removeItem?.(persistKey);
      }
    } catch (applyError) {
      setError(`应用设置失败：${String(applyError)}`);
    }
  }

  return (
    <section
      className="task-settings-panel collector-form-shell"
      data-testid="task-settings-panel"
    >
      <div className="collector-form-body">
        <div className="task-settings-heading">
          <div>
            <span className="eyebrow">
              {mode === "single" ? "逐篇采集" : "批量采集"}
            </span>
            <h2>任务设置</h2>
            <p className="collector-form-guidance">
              保存后选择处理旧成果，并从第 1 页按新设置重新扫描。
            </p>
          </div>
          <span className="task-settings-badge">同一任务</span>
        </div>

        <fieldset disabled={busy}>
        <label>
          {draft.matchMode === "title"
            ? "精确完整标题（每行一个）"
            : draft.matchMode === "fulltext"
              ? "全文检索词（每行一个）"
              : "标题关键词（每行一个）"}
          <textarea
            rows={4}
            value={draft.queries}
            onChange={(event) => update("queries", event.target.value)}
          />
        </label>

        <div className="form-columns">
          <label>
            匹配方式
            <select
              value={draft.matchMode}
              onChange={(event) =>
                update(
                  "matchMode",
                  event.target.value as CollectorMatchMode
                )
              }
            >
              {collectorSearchModeOptions(mode).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <small>{searchModeDisplayLabel(draft.matchMode)}</small>
          </label>
          <label>
            法规子库
            <select
              value={draft.library}
              onChange={(event) =>
                update(
                  "library",
                  event.target.value as "central" | "local" | "both"
                )
              }
            >
              <option value="central">中央法规</option>
              <option value="local">地方法规</option>
              <option value="both">中央 + 地方</option>
            </select>
          </label>
        </div>

        <div className="collector-year-row">
          <YearRangeControl
            value={draft.yearSelection}
            onChange={(value) => update("yearSelection", value)}
          />
        </div>

        {showsProvinceControl(draft.library) && (
          <div
            className="collector-province-row"
            data-testid="collector-province-row"
          >
            <label>
              省份
              <select
                value={draft.province}
                onChange={(event) => update("province", event.target.value)}
              >
                <option>全部省份</option>
                {PROVINCES.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
          </div>
        )}

        <label>
          时效性
          <select
            value={draft.timeliness}
            onChange={(event) => update("timeliness", event.target.value)}
          >
            <option>全部（仅记录元数据，不筛选）</option>
            <option>现行有效</option>
            <option>已被修改</option>
            <option>已废止</option>
          </select>
        </label>

        <div className="form-columns">
          <label>
            每页条数
            <select
              value={draft.pageSize}
              onChange={(event) => update("pageSize", Number(event.target.value))}
            >
              {[10, 20, 50, 100].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
        </div>

        <PageLimitControl
          value={draft.pageSelection}
          onChange={(value) => update("pageSelection", value)}
        />

        {mode === "batch" && (
          <div className="form-columns">
            <label>
              目录格式
              <select
                value={draft.catalogFormat}
                onChange={(event) =>
                  update(
                    "catalogFormat",
                    event.target.value as CatalogFormat
                  )
                }
              >
                {Object.entries(CATALOG_FORMAT_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </label>
            <label>
              全文格式
              <select value="docx" disabled>
                <option value="docx">Word ZIP</option>
              </select>
            </label>
          </div>
        )}

        <label>
          输出目录
          <div className="path-input-row">
            <input
              value={draft.outputDir}
              onChange={(event) => update("outputDir", event.target.value)}
            />
            <button
              type="button"
              className="secondary-button compact"
              onClick={() =>
                void window.pkulaw
                  .selectOutputDirectory(draft.outputDir)
                  .then((selected) => {
                    if (selected) update("outputDir", selected);
                  })
              }
            >
              浏览
            </button>
          </div>
        </label>
        </fieldset>
      </div>

      <div className="task-settings-actions collector-action-footer">
        {error && <p className="task-settings-error">{error}</p>}
        <div className="collector-action-buttons">
          <button
            type="button"
            className="secondary-button"
            disabled={busy}
            onClick={onClose}
          >
            关闭
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={busy}
            onClick={() => void apply()}
          >
            {busy ? "正在保存修改…" : "保存修改"}
          </button>
        </div>
      </div>
    </section>
  );
}
