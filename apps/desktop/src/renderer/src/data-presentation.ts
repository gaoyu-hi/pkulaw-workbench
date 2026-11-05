import type {
  BodyCompleteness,
  PolicyDocumentRecord,
  ReviewStatus
} from "../../shared/protocol";

export const BODY_COMPLETENESS_LABELS: Record<BodyCompleteness, string> = {
  unknown: "历史未检测",
  complete: "完整",
  suspicious: "疑似缺条/截断",
  failed: "提取失败"
};

export const REVIEW_STATUS_LABELS: Record<ReviewStatus, string> = {
  pending_review: "待核验",
  not_required: "无需人工核验",
  verified: "已核验",
  rejected: "已驳回"
};

export interface DataDocumentFilters {
  query: string;
  source: string;
  documentStatus: string;
  bodyCompleteness: string;
  reviewStatus: string;
}

export function bodyCompletenessLabel(value: string): string {
  return (
    BODY_COMPLETENESS_LABELS[value as BodyCompleteness] ||
    value ||
    "未知"
  );
}

export function reviewStatusLabel(value: string): string {
  return (
    REVIEW_STATUS_LABELS[value as ReviewStatus] ||
    value ||
    "未知"
  );
}

export function matchesDataDocument(
  document: PolicyDocumentRecord,
  filters: DataDocumentFilters
): boolean {
  if (filters.source !== "all" && document.sourceType !== filters.source) {
    return false;
  }
  if (
    filters.documentStatus !== "all" &&
    document.status !== filters.documentStatus
  ) {
    return false;
  }
  if (
    filters.bodyCompleteness !== "all" &&
    document.bodyCompleteness !== filters.bodyCompleteness
  ) {
    return false;
  }
  if (
    filters.reviewStatus !== "all" &&
    document.reviewStatus !== filters.reviewStatus
  ) {
    return false;
  }
  const keyword = filters.query.trim().toLowerCase();
  if (!keyword) return true;
  return `${document.title} ${document.policyId} ${document.issuingAuthority} ${
    document.documentNumber
  } ${document.textSourceType} ${document.extractionMethod} ${
    document.converterName
  }`
    .toLowerCase()
    .includes(keyword);
}
