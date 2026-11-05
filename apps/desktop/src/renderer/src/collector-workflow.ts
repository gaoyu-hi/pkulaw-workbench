import type {
  CollectorMatchMode,
  TaskMode,
  TaskPreviewSummary
} from "../../shared/protocol";

export function defaultCollectorMatchMode(
  _mode: TaskMode
): CollectorMatchMode {
  return "keyword";
}

function normalizeValue(value: unknown): unknown {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) return value.map(normalizeValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, item]) => item !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, normalizeValue(item)])
    );
  }
  return value;
}

export function collectorFormKey(
  mode: TaskMode,
  input: Record<string, unknown>
): string {
  return JSON.stringify(normalizeValue({ mode, input }));
}

export function canStartCollector(input: {
  preview: TaskPreviewSummary | null;
  previewFormKey: string;
  currentFormKey: string;
  busy: boolean;
  backendReady: boolean;
  browserLeaseBlocked: boolean;
}): boolean {
  return Boolean(
    input.preview &&
      input.previewFormKey === input.currentFormKey &&
      input.preview.downloadsStarted === 0 &&
      input.preview.downloadIntentsCreated === 0 &&
      input.preview.tasksCreated === 0 &&
      !input.busy &&
      input.backendReady &&
      !input.browserLeaseBlocked
  );
}

export interface CollectorPreviewCardView {
  combinations: string;
  results: string;
  pages: string;
  batches: string;
  downloads: string;
  windowNotice: string;
  stale: boolean;
}

export function collectorPreviewCardView(
  preview: TaskPreviewSummary,
  fresh: boolean
): CollectorPreviewCardView {
  const truncated = preview.combinations.find(
    (item) => item.windowStatus === "truncated"
  );
  const windowNotice = truncated
    ? `检测到结果窗口限制：站点报告 ${truncated.reportedTotalPages} 页，但按每页 ${truncated.actualPageSize} 条与 ${truncated.resultCount} 条结果应有 ${truncated.expectedTotalPages} 页。启动后先进行 0 下载探测，只有直接翻页仍无法覆盖时才按真实年份拆分。`
    : preview.windowStatus === "complete"
      ? "结果窗口与结果数和页大小一致，未发现截断。"
      : "结果窗口状态未确认；启动后必须先完成 0 下载探测。";
  return {
    combinations: String(preview.combinationCount),
    results: String(preview.totalResults),
    pages: String(preview.totalPages),
    batches: String(preview.estimatedBatches50),
    downloads: String(preview.downloadsStarted),
    windowNotice,
    stale: !fresh
  };
}
