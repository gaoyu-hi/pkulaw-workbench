import { describe, expect, it } from "vitest";

import type { PolicyDocumentRecord } from "../src/shared/protocol";
import {
  BODY_COMPLETENESS_LABELS,
  REVIEW_STATUS_LABELS,
  matchesDataDocument
} from "../src/renderer/src/data-presentation";

function document(
  overrides: Partial<PolicyDocumentRecord> = {}
): PolicyDocumentRecord {
  return {
    id: "doc-1",
    taskId: "task-1",
    policyKey: "id:p1",
    policyId: "p1",
    title: "碳中和政策",
    documentNumber: "测试〔2026〕1号",
    issuingAuthority: "测试机关",
    publicationDate: "2026-01-01",
    effectiveDate: "2026-02-01",
    validity: "现行有效",
    effectLevel: "规范性文件",
    sourceType: "北大法宝原件",
    sourceUrl: "https://example.test/p1",
    retrievedAt: "2026-11-02T00:00:00Z",
    retrievalMethod: "单篇 Word 下载",
    wordPath: "D:/输出/word.docx",
    txtPath: "D:/输出/policy.txt",
    evidencePath: "D:/输出/evidence.json",
    sha256: "a".repeat(64),
    status: "processed",
    textSourceType: "word-text",
    extractionMethod: "bundled-antiword",
    converterName: "antiword",
    converterVersion: "0.37",
    bodyCompleteness: "complete",
    reviewStatus: "not_required",
    error: null,
    payload: { txtSha256: "b".repeat(64) },
    createdAt: "2026-11-02T00:00:00Z",
    updatedAt: "2026-11-02T00:00:01Z",
    ...overrides
  };
}

describe("data presentation", () => {
  it("labels source, completeness and review states explicitly", () => {
    expect(BODY_COMPLETENESS_LABELS.complete).toBe("完整");
    expect(BODY_COMPLETENESS_LABELS.suspicious).toBe("疑似缺条/截断");
    expect(BODY_COMPLETENESS_LABELS.failed).toBe("提取失败");
    expect(BODY_COMPLETENESS_LABELS.unknown).toBe("历史未检测");
    expect(REVIEW_STATUS_LABELS.pending_review).toBe("待核验");
    expect(REVIEW_STATUS_LABELS.not_required).toBe("无需人工核验");
  });

  it("filters original ZIP source and pending body failures independently", () => {
    const failedZip = document({
      id: "doc-failed",
      sourceType: "北大法宝ZIP",
      status: "pending_review",
      textSourceType: "",
      extractionMethod: "",
      converterName: "",
      converterVersion: "",
      bodyCompleteness: "failed",
      reviewStatus: "pending_review",
      error: {
        code: "DOC_TEXT_EXTRACTION_FAILED",
        message: "DOC 转换失败"
      }
    });

    expect(
      matchesDataDocument(failedZip, {
        query: "碳中和",
        source: "北大法宝ZIP",
        documentStatus: "pending_review",
        bodyCompleteness: "failed",
        reviewStatus: "pending_review"
      })
    ).toBe(true);
    expect(
      matchesDataDocument(failedZip, {
        query: "",
        source: "北大法宝原件",
        documentStatus: "all",
        bodyCompleteness: "all",
        reviewStatus: "all"
      })
    ).toBe(false);
  });

  it("does not infer complete or verified for historical records", () => {
    const historical = document({
      status: "pending_review",
      textSourceType: "",
      extractionMethod: "",
      converterName: "",
      converterVersion: "",
      bodyCompleteness: "unknown",
      reviewStatus: "pending_review"
    });

    expect(
      matchesDataDocument(historical, {
        query: "",
        source: "all",
        documentStatus: "all",
        bodyCompleteness: "complete",
        reviewStatus: "not_required"
      })
    ).toBe(false);
  });
});
