import type {
  CollectorMatchMode,
  SearchModeOption,
  TaskMode
} from "../../shared/protocol";

const SEARCH_MODE_OPTIONS: Record<CollectorMatchMode, SearchModeOption> = {
  keyword: {
    value: "keyword",
    label: "关键词",
    detail: "标题 + 精确"
  },
  fulltext: {
    value: "fulltext",
    label: "全文",
    detail: "全文 + 精确"
  },
  title: {
    value: "title",
    label: "精确完整标题",
    detail: "标题 + 精确；同名版本人工选择"
  }
};

export function collectorSearchModeOptions(
  mode: TaskMode
): SearchModeOption[] {
  return mode === "single"
    ? [
        SEARCH_MODE_OPTIONS.keyword,
        SEARCH_MODE_OPTIONS.fulltext,
        SEARCH_MODE_OPTIONS.title
      ]
    : [SEARCH_MODE_OPTIONS.keyword, SEARCH_MODE_OPTIONS.fulltext];
}

export function migrateCollectorQueryDraft(stored: unknown): string {
  if (stored === "人工智能") return "";
  return typeof stored === "string" ? stored : "";
}

export function normalizeCollectorMatchMode(
  mode: TaskMode,
  stored: unknown
): CollectorMatchMode {
  if (stored === "fulltext") return "fulltext";
  if (mode === "single" && stored === "title") return "title";
  return "keyword";
}

export function searchModeDisplayLabel(mode: string): string {
  const normalized =
    mode === "fulltext" || mode === "title" ? mode : "keyword";
  const option = SEARCH_MODE_OPTIONS[normalized];
  return `${option.label}（${option.detail}）`;
}
