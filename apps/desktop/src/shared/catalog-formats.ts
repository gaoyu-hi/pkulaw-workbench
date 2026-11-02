export type CatalogFormat = "xlsx" | "xls" | "html-xls";

export const CATALOG_FORMAT_LABELS: Record<CatalogFormat, string> = {
  xlsx: "本地转换 XLSX（源自网站 XLS）",
  xls: "网站原生 XLS",
  "html-xls": "兼容 HTML-XLS（本地生成）"
};

export function catalogFormatLabel(value: string): string {
  return CATALOG_FORMAT_LABELS[value as CatalogFormat] || value;
}
