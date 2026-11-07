import { useEffect, useMemo, useState } from "react";
import type {
  AppInfo,
  BackendState,
  BodyCompleteness,
  BrowserHostState,
  DataProcessingDetails,
  EngineEvent,
  PolicyDocumentRecord,
  ReviewStatus,
  TaskRecord
} from "../../shared/protocol";
import {
  leaseBlocksNewBrowserTask,
  newOperationId
} from "../../shared/browser-lease";
import {
  BrowserLeaseCard,
  ExpandableValue,
  ProcessingRuntimePanel
} from "./TaskRuntimePanel";
import { useTaskRuntimeEvents } from "./useTaskRuntimeEvents";
import {
  RUN_STATUS_LABELS,
  hashState,
  processingActions,
  stageLabel,
  type HashState
} from "./task-presentation";
import {
  bodyCompletenessLabel,
  matchesDataDocument,
  reviewStatusLabel
} from "./data-presentation";

interface DataPanelProps {
  appInfo: AppInfo | null;
  backend: BackendState;
}

const sourceClass: Record<string, string> = {
  "北大法宝原件": "pkulaw",
  "北大法宝ZIP": "pkulaw",
  "政府官网原生附件": "gov-native",
  "政府官网正文整理": "gov-generated",
  "待核验": "pending"
};

function fileName(path: string): string {
  return path ? String(path).split(/[\\/]/).pop() || path : "—";
}

function documentHashState(document: PolicyDocumentRecord): HashState {
  const txtHash = String(document.payload.txtSha256 || "");
  const mismatch = hashState("", document.error?.code || "");
  if (mismatch === "mismatch") return "mismatch";
  if (hashState(document.sha256) !== "complete") return "missing";
  if (
    document.status === "processed" &&
    hashState(txtHash) !== "complete"
  ) {
    return "missing";
  }
  return "complete";
}

function hashLabel(status: HashState): string {
  return {
    complete: "SHA-256 完整",
    missing: "SHA-256 缺失",
    mismatch: "SHA-256 不一致"
  }[status];
}

export default function DataPanel({
  appInfo,
  backend
}: DataPanelProps): React.JSX.Element {
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [details, setDetails] = useState<DataProcessingDetails | null>(null);
  const [browser, setBrowser] = useState<BrowserHostState | null>(null);
  const [documentQuery, setDocumentQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [documentStatusFilter, setDocumentStatusFilter] = useState("all");
  const [bodyFilter, setBodyFilter] = useState<"all" | BodyCompleteness>("all");
  const [reviewFilter, setReviewFilter] = useState<"all" | ReviewStatus>("all");
  const [hashFilter, setHashFilter] = useState<"all" | HashState>("all");
  const [governmentFallback, setGovernmentFallback] = useState(false);
  const [searchTemplate, setSearchTemplate] = useState(
    appInfo?.stage5FixtureMode
      ? "/government/search?title={title}"
      : 'https://cn.bing.com/search?q=site%3Agov.cn+%22{title}%22'
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(
    "从已完成的逐篇或批量任务生成 Word、TXT、总目录、来源证据和验收报告。"
  );

  useEffect(() => {
    setSearchTemplate(
      appInfo?.stage5FixtureMode
        ? "/government/search?title={title}"
        : 'https://cn.bing.com/search?q=site%3Agov.cn+%22{title}%22'
    );
  }, [appInfo?.stage5FixtureMode]);

  async function refreshTasks(): Promise<TaskRecord[]> {
    const records = (await window.pkulaw.listTasks(150)).filter(
      (task) => ["completed", "partial", "stopped"].includes(task.status)
    );
    setTasks(records);
    return records;
  }

  async function loadLatest(taskId: string): Promise<void> {
    if (!taskId) {
      setDetails(null);
      return;
    }
    try {
      setDetails(await window.pkulaw.getLatestDataProcessing(taskId));
    } catch {
      setDetails(null);
    }
  }

  useEffect(() => {
    if (backend.status !== "ready") return;
    let cancelled = false;
    void refreshTasks()
      .then(async (records) => {
        if (cancelled || !records.length) return;
        const preferred = records.find((task) => task.mode === "single") || records[0];
        setSelectedTaskId(preferred.id);
        await loadLatest(preferred.id);
      })
      .catch((error) => setNotice(`读取任务失败：${String(error)}`));
    return () => {
      cancelled = true;
    };
  }, [backend.status]);

  useEffect(() => {
    if (backend.status !== "ready") return;
    let cancelled = false;
    void window.pkulaw
      .getBrowserState()
      .then((state) => {
        if (!cancelled) setBrowser(state);
      })
      .catch(() => undefined);
    const unsubscribe = window.pkulaw.onBrowserState((state) => {
      if (!cancelled) setBrowser(state);
    });
    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [backend.status]);

  useEffect(() => {
    const unsubscribe = window.pkulaw.onEngineEvent((event: EngineEvent) => {
      if (backend.status !== "ready") return;
      if (
        event.event.startsWith("task.") ||
        event.event.startsWith("single.") ||
        event.event.startsWith("batch.")
      ) {
        void refreshTasks().catch(() => undefined);
      }
      if (!event.event.startsWith("data.")) return;
      const data = event.data as Record<string, unknown>;
      const processingId = String(data.processingId || "");
      if (processingId) {
        void window.pkulaw
          .getDataProcessing(processingId)
          .then(setDetails)
          .catch(() => undefined);
      }
    });
    return unsubscribe;
  }, [backend.status]);

  useEffect(() => {
    if (
      !details ||
      ![
        "running",
        "waiting_manual",
        "waiting_login",
        "pausing",
        "paused",
        "stopping"
      ].includes(details.run.status)
    ) {
      return;
    }
    const timer = window.setInterval(() => {
      void window.pkulaw
        .getDataProcessing(details.run.id)
        .then(setDetails)
        .catch(() => undefined);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [details?.run.id, details?.run.status]);

  const selectedTask = tasks.find((task) => task.id === selectedTaskId) || null;
  const exactTitleTask =
    selectedTask?.mode === "single" &&
    String(selectedTask.input.matchMode || "") === "title";

  useEffect(() => {
    if (!exactTitleTask) {
      setGovernmentFallback(false);
    }
  }, [exactTitleTask]);
  const browserLeaseBlocked =
    exactTitleTask &&
    governmentFallback &&
    leaseBlocksNewBrowserTask(browser);
  const runtimeAlerts = useTaskRuntimeEvents(
    details?.run.taskId || selectedTaskId
  );
  const runActions = processingActions(
    details?.run.status || "pending",
    String(details?.run.error?.code || "")
  );

  const counts = useMemo(
    () => ({
      processed:
        details?.documents.filter((document) => document.status === "processed").length || 0,
      pending:
        details?.documents.filter(
          (document) => document.reviewStatus === "pending_review"
        ).length || 0,
      gov:
        details?.documents.filter((document) =>
          document.sourceType.startsWith("政府官网")
        ).length || 0
    }),
    [details]
  );
  const filteredDocuments = useMemo(() => {
    const documents = details?.documents || [];
    return documents.filter((document) => {
      if (
        hashFilter !== "all" &&
        documentHashState(document) !== hashFilter
      ) {
        return false;
      }
      return matchesDataDocument(document, {
        query: documentQuery,
        source: sourceFilter,
        documentStatus: documentStatusFilter,
        bodyCompleteness: bodyFilter,
        reviewStatus: reviewFilter
      });
    });
  }, [
    bodyFilter,
    details?.documents,
    documentQuery,
    documentStatusFilter,
    hashFilter,
    reviewFilter,
    sourceFilter
  ]);

  async function startProcessing(): Promise<void> {
    if (!selectedTaskId) return;
    setBusy(true);
    try {
      const next = await window.pkulaw.startDataProcessing(
        selectedTaskId,
        {
          governmentFallback: exactTitleTask && governmentFallback,
          governmentSearchTemplate: searchTemplate
        },
        newOperationId()
      );
      setDetails(next);
      setNotice("数据处理已启动。");
    } catch (error) {
      setNotice(`启动数据处理失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  async function openManagedFile(pathValue: string): Promise<void> {
    try {
      await window.pkulaw.openLocalFile(pathValue);
    } catch (error) {
      setNotice(`打开产物失败：${String(error)}`);
    }
  }

  async function control(
    action: "pause" | "resume" | "stop" | "continue"
  ): Promise<void> {
    if (!details) return;
    setBusy(true);
    try {
      if (action === "pause") {
        setDetails(await window.pkulaw.pauseDataProcessing(details.run.id));
        setNotice("已请求暂停，将在当前政策完整写入后生效。");
      } else if (action === "resume") {
        setDetails(await window.pkulaw.resumeDataProcessing(details.run.id));
        setNotice("浏览器、登录和选择器已重新核验，数据处理从原断点继续。");
      } else if (action === "stop") {
        setDetails(await window.pkulaw.stopDataProcessing(details.run.id));
        setNotice("已请求停止，已完成的政策、文件和断点会保留。");
      } else {
        setDetails(await window.pkulaw.continueDataProcessing(details.run.id));
        setNotice("人工处理已确认，数据处理继续。");
      }
    } catch (error) {
      setNotice(`数据处理操作失败：${String(error)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="data-workspace">
      <div className="data-header panel">
        <div>
          <span className="eyebrow">
            {appInfo?.version || "0.2.6"} · 数据治理
          </span>
          <h2>从来源文件到可分析数据</h2>
          <p>统一生成逐条 TXT、总目录 CSV/XLSX、来源证据与任务报告。</p>
        </div>
        <div className="data-controls">
          <label>
            源任务
            <select
              value={selectedTaskId}
              onChange={(event) => {
                const taskId = event.target.value;
                setSelectedTaskId(taskId);
                void loadLatest(taskId);
              }}
            >
              {tasks.map((task) => (
                <option key={task.id} value={task.id}>
                  {task.mode === "single" ? "逐篇" : "批量"} · {task.name}
                </option>
              ))}
              {tasks.length === 0 && <option value="">暂无已完成任务</option>}
            </select>
          </label>
          {exactTitleTask && (
            <label className="fallback-toggle">
              <input
                type="checkbox"
                checked={governmentFallback}
                onChange={(event) => setGovernmentFallback(event.target.checked)}
              />
              允许精确标题政府官网补全
            </label>
          )}
          {exactTitleTask && governmentFallback && (
            <label>
              官方检索模板
              <input value={searchTemplate} onChange={(event) => setSearchTemplate(event.target.value)} />
            </label>
          )}
          {exactTitleTask && governmentFallback && (
            <p className="data-browser-hint">
              官方补全期间会临时显示内置浏览器，完成、停止或失败后自动收起。
            </p>
          )}
          {exactTitleTask && governmentFallback && browser && (
            <div data-testid="data-browser-lease">
              <BrowserLeaseCard
                browser={browser}
                tasks={tasks}
                compact
              />
            </div>
          )}
          <button
            type="button"
            className="primary-button"
            disabled={
              busy ||
              !selectedTaskId ||
              backend.status !== "ready" ||
              browserLeaseBlocked
            }
            onClick={() => void startProcessing()}
          >
            生成数据包
          </button>
        </div>
      </div>

      {details ? (
        <>
          <div className="data-status-grid">
            <div className="data-card">
              <span>处理状态</span>
              <strong>{RUN_STATUS_LABELS[details.run.status] || details.run.status}</strong>
              <small>{stageLabel(details.run.currentStage, String(details.run.summary.currentAction || ""))}</small>
            </div>
            <div className="data-card">
              <span>已完成正文</span>
              <strong>{counts.processed}</strong>
              <small>共 {details.documents.length} 条</small>
            </div>
            <div className="data-card">
              <span>待核验</span>
              <strong>{counts.pending}</strong>
              <small>没有伪造成功状态</small>
            </div>
            <div className="data-card">
              <span>政府官网来源</span>
              <strong>{counts.gov}</strong>
              <small>原生附件与正文整理</small>
            </div>
          </div>

          {[
            "running",
            "waiting_manual",
            "waiting_login",
            "pausing",
            "paused",
            "stopping"
          ].includes(details.run.status) && (
            <div className="data-run-actions">
              {runActions.pause && (
                <button type="button" className="secondary-button" disabled={busy} onClick={() => void control("pause")}>
                  暂停
                </button>
              )}
              {runActions.resume && (
                <button type="button" className="primary-button compact" disabled={busy} onClick={() => void control("resume")}>
                  继续
                </button>
              )}
              {runActions.continue && (
                <button type="button" className="primary-button compact" disabled={busy} onClick={() => void control("continue")}>
                  已处理，继续
                </button>
              )}
              {runActions.stop && (
                <button type="button" className="secondary-button" disabled={busy} onClick={() => void control("stop")}>
                  停止
                </button>
              )}
              {details.run.status === "stopping" && (
                <span className="task-control-note">正在等待当前安全步骤结束</span>
              )}
            </div>
          )}

          <ProcessingRuntimePanel
            run={details.run}
            completed={counts.processed}
            total={details.documents.length}
            alerts={runtimeAlerts}
          />

          <div className="data-content-grid">
            <div className="panel data-documents">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">政策数据库</span>
                  <h3>逐条结果</h3>
                </div>
                <span className="data-count">{filteredDocuments.length} / {details.documents.length} 条</span>
              </div>
              <div className="data-doc-filters">
                <input
                  value={documentQuery}
                  onChange={(event) => setDocumentQuery(event.target.value)}
                  placeholder="搜索标题、政策 ID、机关或文号"
                />
                <select
                  value={sourceFilter}
                  onChange={(event) => setSourceFilter(event.target.value)}
                >
                  <option value="all">全部来源</option>
                  <option value="北大法宝原件">北大法宝原件</option>
                  <option value="北大法宝ZIP">北大法宝 ZIP</option>
                  <option value="政府官网原生附件">政府官网原生附件</option>
                  <option value="政府官网正文整理">政府官网正文整理</option>
                  <option value="待核验">待核验</option>
                </select>
                <select
                  value={documentStatusFilter}
                  onChange={(event) => setDocumentStatusFilter(event.target.value)}
                >
                  <option value="all">全部状态</option>
                  <option value="processed">已处理</option>
                  <option value="pending_review">待核验</option>
                  <option value="failed">失败</option>
                </select>
                <select
                  value={bodyFilter}
                  onChange={(event) =>
                    setBodyFilter(
                      event.target.value as "all" | BodyCompleteness
                    )
                  }
                  aria-label="正文完整性筛选"
                >
                  <option value="all">全部正文完整性</option>
                  <option value="complete">完整</option>
                  <option value="suspicious">疑似缺条/截断</option>
                  <option value="failed">提取失败</option>
                  <option value="unknown">历史未检测</option>
                </select>
                <select
                  value={reviewFilter}
                  onChange={(event) =>
                    setReviewFilter(event.target.value as "all" | ReviewStatus)
                  }
                  aria-label="核验状态筛选"
                >
                  <option value="all">全部核验状态</option>
                  <option value="pending_review">待核验</option>
                  <option value="not_required">无需人工核验</option>
                  <option value="verified">已核验</option>
                  <option value="rejected">已驳回</option>
                </select>
                <select
                  value={hashFilter}
                  onChange={(event) =>
                    setHashFilter(
                      event.target.value as "all" | HashState
                    )
                  }
                  aria-label="SHA-256 状态筛选"
                >
                  <option value="all">全部 SHA-256 状态</option>
                  <option value="complete">SHA-256 完整</option>
                  <option value="missing">SHA-256 缺失</option>
                  <option value="mismatch">SHA-256 不一致</option>
                </select>
              </div>
              <div className="data-doc-head">
                <span>政策</span><span>来源</span><span>状态</span><span>SHA-256</span><span>TXT</span><span>打开</span>
              </div>
              {filteredDocuments.map((document) => (
                <div className="data-doc-record" key={document.id}>
                  <div className="data-doc-row">
                    <div>
                      <strong>{document.title || "未命名政策"}</strong>
                      <small>{document.policyId || document.policyKey}</small>
                    </div>
                    <span className={`source-chip ${sourceClass[document.sourceType] || "pending"}`}>
                      {document.sourceType || "待识别"}
                    </span>
                    <span className={`item-state ${document.status === "processed" ? "completed" : "failed"}`}>
                      {document.status === "processed" ? "已处理" : "待核验"}
                    </span>
                    <span className={`hash-state ${documentHashState(document)}`}>
                      {hashLabel(documentHashState(document))}
                    </span>
                    <span title={document.txtPath}>{fileName(document.txtPath)}</span>
                    <div className="data-file-actions">
                      {document.wordPath && (
                        <button type="button" onClick={() => void openManagedFile(document.wordPath)}>Word</button>
                      )}
                      {document.txtPath && (
                        <button type="button" onClick={() => void openManagedFile(document.txtPath)}>TXT</button>
                      )}
                      {document.evidencePath && (
                        <button type="button" onClick={() => void openManagedFile(document.evidencePath)}>证据</button>
                      )}
                    </div>
                  </div>
                  <div className="data-doc-meta">
                    <span>正文完整性：{bodyCompletenessLabel(document.bodyCompleteness)}</span>
                    <span>核验状态：{reviewStatusLabel(document.reviewStatus)}</span>
                    <span>
                      {document.converterName || "转换器未知"}
                      {document.converterVersion
                        ? ` ${document.converterVersion}`
                        : ""}
                    </span>
                  </div>
                  <ExpandableValue
                    label="正文来源、转换器与核验详情"
                    value={[
                      `原件来源：${document.sourceType || "未识别"}`,
                      `正文来源：${document.textSourceType || "不可用/空"}`,
                      `提取方法：${document.extractionMethod || "不可用/空"}`,
                      `转换器：${document.converterName || "不可用/空"} ${document.converterVersion || ""}`.trim(),
                      `正文完整性：${bodyCompletenessLabel(document.bodyCompleteness)}`,
                      `核验状态：${reviewStatusLabel(document.reviewStatus)}`,
                      document.error
                        ? `失败原因：${document.error.code}: ${document.error.message}`
                        : ""
                    ].filter(Boolean).join("\n")}
                  />
                </div>
              ))}
              {filteredDocuments.length === 0 && <div className="muted">没有符合筛选条件的政策记录</div>}
            </div>

            <aside className="panel data-artifacts">
              <span className="eyebrow">任务产物</span>
              <h3>下载与核验</h3>
              <div className="artifact-list">
                {details.artifacts.map((artifact) => {
                  const status = hashState(artifact.sha256);
                  return (
                    <div className="artifact-record" key={artifact.id}>
                      <button
                        type="button"
                        className="artifact-open-row"
                        onClick={() => void openManagedFile(artifact.path)}
                      >
                        <strong>{artifact.kind}</strong>
                        <span title={artifact.path}>{fileName(artifact.path)}</span>
                        <span className={`hash-state ${status}`}>
                          {hashLabel(status)}
                        </span>
                        <code>{artifact.sha256.slice(0, 16)}…</code>
                      </button>
                      <ExpandableValue
                        label={`${artifact.kind} 路径与 SHA-256`}
                        value={`${artifact.path}\n${artifact.sha256 || "未登记 SHA-256"}`}
                      />
                    </div>
                  );
                })}
                {details.artifacts.length === 0 && <div className="muted">报告尚未生成</div>}
              </div>
            </aside>
          </div>
        </>
      ) : (
        <div className="single-empty data-empty">
          <div className="single-empty-mark">库</div>
          <h3>选择一个已完成任务</h3>
          <p>系统将从 Word 和批量 ZIP 中生成逐条正文与统一目录。</p>
        </div>
      )}

      <div className="notice browser-notice">
        <span className="notice-icon">i</span>
        <span>{notice}</span>
      </div>
    </section>
  );
}
