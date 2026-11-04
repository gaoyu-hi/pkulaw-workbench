import { useCallback, useEffect, useRef, useState } from "react";
import type {
  BrowserHostState,
  ManualRecoveryResult,
  TaskRecord
} from "../../shared/protocol";
import {
  AUTO_RECOVERY_FAILURE_GRACE_MS,
  CAPTCHA_SETTLE_WINDOW_MS,
  REQUIRED_STABLE_CLEAR_SAMPLES,
  idleManualRecovery,
  nextManualRecoveryPhase,
  shouldAutoResumeManual,
  type ManualRecoveryPhase,
  type ManualRecoveryView
} from "../../shared/task-workspace";

export {
  AUTO_RECOVERY_FAILURE_GRACE_MS,
  CAPTCHA_SETTLE_WINDOW_MS,
  REQUIRED_STABLE_CLEAR_SAMPLES
} from "../../shared/task-workspace";

const MANUAL_RECOVERY_POLL_MS = Math.ceil(
  CAPTCHA_SETTLE_WINDOW_MS /
    Math.max(1, REQUIRED_STABLE_CLEAR_SAMPLES - 1)
);

const ACCESS_WAIT_CODES = new Set([
  "MANUAL_ACTION_REQUIRED",
  "LOGIN_REQUIRED",
  "CAPTCHA_VISIBLE"
]);

export interface ManualRecoveryStep extends ManualRecoveryView {
  stableSamples: number;
  shouldResume: boolean;
  operationId?: string;
}

export interface ManualRecoveryObservationInput {
  task: TaskRecord | null | undefined;
  browser: BrowserHostState | null | undefined;
  previousStableSamples: number;
}

function accessWaitCode(task: TaskRecord | null | undefined): string {
  return String(task?.error?.code || "");
}

export function manualRecoveryErrorCode(error: unknown): string {
  const explicitCode = String(
    (error as { code?: unknown } | null)?.code || ""
  );
  if (explicitCode) return explicitCode;
  const message = String(error);
  return (
    [
      "CAPTCHA_VISIBLE",
      "MANUAL_ACTION_REQUIRED",
      "LOGIN_REQUIRED",
      "SOURCE_ACCESS_ERROR"
    ].find((code) => message.includes(code)) || ""
  );
}

function isAccessWait(task: TaskRecord | null | undefined): boolean {
  return Boolean(
    task &&
      ["waiting_login", "waiting_manual"].includes(task.status) &&
      ACCESS_WAIT_CODES.has(accessWaitCode(task))
  );
}

function browserBelongsToTask(
  task: TaskRecord,
  browser: BrowserHostState
): boolean {
  if (browser.browserLease.taskId === task.id) return true;
  if (!browser.browserLease.available) return false;
  const recovery = recordValue(task.checkpoint?.accessRecovery);
  return Boolean(
    String(recovery.taskId || "") === task.id &&
      String(recovery.browserLeaseTaskId || "") === task.id &&
      String(recovery.waitCode || "") === accessWaitCode(task)
  );
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? value as Record<string, unknown>
    : {};
}

function clearBrowserForTask(
  task: TaskRecord,
  browser: BrowserHostState
): boolean {
  return Boolean(
    browserBelongsToTask(task, browser) &&
      browser.targetBound &&
      !browser.captchaVisible &&
      !browser.loginVisible &&
      browser.authenticated &&
      browser.selectorReady
  );
}

export function observeManualRecovery({
  task,
  browser,
  previousStableSamples
}: ManualRecoveryObservationInput): Pick<
  ManualRecoveryStep,
  "stableSamples" | "phase" | "shouldResume"
> {
  if (!task || !isAccessWait(task)) {
    return {
      stableSamples: 0,
      phase: "idle",
      shouldResume: false
    };
  }
  const activeTask = task;
  if (
    !browser ||
    browser.captchaVisible ||
    browser.loginVisible ||
    !browserBelongsToTask(activeTask, browser)
  ) {
    return {
      stableSamples: 0,
      phase: "waiting",
      shouldResume: false
    };
  }
  if (!clearBrowserForTask(activeTask, browser)) {
    return {
      stableSamples: 0,
      phase: "waiting",
      shouldResume: false
    };
  }
  const stableSamples = Math.min(
    REQUIRED_STABLE_CLEAR_SAMPLES,
    Math.max(0, previousStableSamples) + 1
  );
  return {
    stableSamples,
    phase:
      stableSamples >= REQUIRED_STABLE_CLEAR_SAMPLES
        ? "verifying"
        : "observing",
    shouldResume: shouldAutoResumeManual({
      taskId: activeTask.id,
      leaseTaskId: browserBelongsToTask(activeTask, browser)
        ? activeTask.id
        : "",
      taskStatus: activeTask.status,
      errorCode: accessWaitCode(activeTask),
      captchaVisible: browser.captchaVisible,
      loginVisible: browser.loginVisible,
      authenticated: browser.authenticated,
      selectorReady: browser.selectorReady,
      stableSamples
    })
  };
}

const PHASE_DETAILS: Record<ManualRecoveryPhase, string> = {
  idle: "",
  waiting: "等待人工完成验证码或登录；浏览器保持可见。",
  observing: "验证码已清除，正在观察页面是否稳定。",
  verifying: "验证码已清除，正在复核浏览器门禁。",
  resuming: "浏览器门禁已通过，正在从原断点恢复任务。",
  succeeded: "已自动恢复原任务，正在刷新任务详情。",
  failed: "自动恢复失败，请重新检测浏览器状态。"
};

export function manualRecoveryViewForPhase(
  phase: ManualRecoveryPhase,
  detail = "",
  canRetry = phase === "failed"
): ManualRecoveryView {
  return {
    phase,
    detail: detail || PHASE_DETAILS[phase],
    canRetry: phase === "failed" && canRetry
  };
}

export interface ManualRecoveryController {
  observe(
    task: TaskRecord | null | undefined,
    browser: BrowserHostState | null | undefined
  ): ManualRecoveryStep;
  markResumeStarted(): void;
  markResumeSucceeded(): void;
  markResumeFailed(detail?: string, code?: string): void;
  retry(): void;
  reset(): void;
  isActive(operationId: string): boolean;
}

class Controller implements ManualRecoveryController {
  private taskId = "";
  private stableSamples = 0;
  private phase: ManualRecoveryPhase = "idle";
  private operationId = "";
  private failedMessage = "";
  private failureDetectedAt = 0;
  private suppressAutomaticRecovery = false;
  private succeeded = false;

  constructor(
    private readonly operationIdFactory: () => string,
    private readonly nowFactory: () => number
  ) {}

  private canRetry(): boolean {
    return Boolean(
      this.failureDetectedAt > 0 &&
        this.nowFactory() - this.failureDetectedAt >=
          AUTO_RECOVERY_FAILURE_GRACE_MS
    );
  }

  private currentStep(): ManualRecoveryStep {
    return {
      ...manualRecoveryViewForPhase(
        this.phase,
        this.phase === "failed" ? this.failedMessage : "",
        this.canRetry()
      ),
      stableSamples: this.stableSamples,
      shouldResume: false,
      ...(this.operationId ? { operationId: this.operationId } : {})
    };
  }

  private resetForTask(taskId: string): void {
    this.taskId = taskId;
    this.stableSamples = 0;
    this.phase = "idle";
    this.operationId = "";
    this.failedMessage = "";
    this.failureDetectedAt = 0;
    this.suppressAutomaticRecovery = false;
    this.succeeded = false;
  }

  observe(
    task: TaskRecord | null | undefined,
    browser: BrowserHostState | null | undefined
  ): ManualRecoveryStep {
    if (!task) {
      this.reset();
      return this.currentStep();
    }
    if (task.id !== this.taskId) {
      this.resetForTask(task.id);
    }
    if (!isAccessWait(task)) {
      if (!this.succeeded) {
        this.stableSamples = 0;
        this.phase = "idle";
        this.operationId = "";
        this.failedMessage = "";
        this.failureDetectedAt = 0;
        this.suppressAutomaticRecovery = false;
      }
      return this.currentStep();
    }
    if (
      !browser ||
      browser.captchaVisible ||
      browser.loginVisible ||
      !browserBelongsToTask(task, browser)
    ) {
      if (browser?.captchaVisible || browser?.loginVisible) {
        this.succeeded = false;
        this.operationId = "";
      }
      this.stableSamples = 0;
      if (!this.suppressAutomaticRecovery) {
        this.phase = "waiting";
      }
      return this.currentStep();
    }

    const observation = observeManualRecovery({
      task,
      browser,
      previousStableSamples: this.stableSamples
    });
    this.stableSamples = observation.stableSamples;
    if (this.suppressAutomaticRecovery) {
      this.phase = "failed";
      return this.currentStep();
    }
    if (this.operationId) {
      this.phase =
        this.phase === "succeeded"
          ? "succeeded"
          : this.phase === "failed"
            ? "failed"
            : "resuming";
      return this.currentStep();
    }
    if (this.succeeded) {
      this.phase = "succeeded";
      return this.currentStep();
    }
    this.phase = observation.phase;
    if (!observation.shouldResume) {
      return this.currentStep();
    }
    this.operationId = this.operationIdFactory();
    return {
      ...this.currentStep(),
      phase: "verifying",
      shouldResume: true
    };
  }

  markResumeStarted(): void {
    this.phase = nextManualRecoveryPhase(this.phase, {
      type: "resume-started"
    });
  }

  markResumeSucceeded(): void {
    this.phase = "succeeded";
    this.succeeded = true;
    this.suppressAutomaticRecovery = false;
  }

  markResumeFailed(detail = "", code = ""): void {
    this.failedMessage = detail;
    this.stableSamples = 0;
    this.operationId = "";
    this.succeeded = false;
    if (code === "CAPTCHA_VISIBLE") {
      this.phase = "observing";
      this.failureDetectedAt = 0;
      this.suppressAutomaticRecovery = false;
      return;
    }
    this.phase = "failed";
    this.failureDetectedAt = this.nowFactory();
    this.suppressAutomaticRecovery = true;
  }

  retry(): void {
    this.stableSamples = 0;
    this.phase = "observing";
    this.operationId = "";
    this.failedMessage = "";
    this.failureDetectedAt = 0;
    this.suppressAutomaticRecovery = false;
    this.succeeded = false;
  }

  reset(): void {
    this.taskId = "";
    this.stableSamples = 0;
    this.phase = "idle";
    this.operationId = "";
    this.failedMessage = "";
    this.failureDetectedAt = 0;
    this.suppressAutomaticRecovery = false;
    this.succeeded = false;
  }

  isActive(operationId: string): boolean {
    return Boolean(operationId && this.operationId === operationId);
  }
}

export function createManualRecoveryController(
  operationIdFactory: () => string,
  nowFactory: () => number = Date.now
): ManualRecoveryController {
  return new Controller(operationIdFactory, nowFactory);
}

function newOperationId(): string {
  return globalThis.crypto?.randomUUID?.() ||
    `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export interface UseManualRecoveryInput {
  task: TaskRecord | null | undefined;
  browser: BrowserHostState | null | undefined;
  enabled: boolean;
  onRecovered?: (result: ManualRecoveryResult) => void | Promise<void>;
  onChallengeVisible?: () => void;
}

export interface UseManualRecoveryResult {
  view: ManualRecoveryView;
  retry: () => void;
}

export function useManualRecovery({
  task,
  browser,
  enabled,
  onRecovered,
  onChallengeVisible
}: UseManualRecoveryInput): UseManualRecoveryResult {
  const controllerRef = useRef<ManualRecoveryController | null>(null);
  if (!controllerRef.current) {
    controllerRef.current = createManualRecoveryController(newOperationId);
  }
  const controller = controllerRef.current;
  const [polledBrowser, setPolledBrowser] = useState<{
    scopeKey: string;
    state: BrowserHostState;
  } | null>(null);
  const [view, setView] = useState<ManualRecoveryView>(idleManualRecovery);
  const callbacksRef = useRef({ onRecovered, onChallengeVisible });
  callbacksRef.current = { onRecovered, onChallengeVisible };
  const taskRef = useRef(task);
  taskRef.current = task;
  const epochRef = useRef(0);
  const requestedOperationRef = useRef("");
  const taskId = task?.id || "";
  const taskStatus = task?.status || "";
  const errorCode = accessWaitCode(task);
  const scopeKey = `${taskId}|${taskStatus}|${errorCode}`;

  useEffect(() => {
    setPolledBrowser(null);
    requestedOperationRef.current = "";
  }, [taskId, taskStatus, errorCode]);

  useEffect(() => {
    if (!enabled || !isAccessWait(task)) return;
    let cancelled = false;
    const poll = async (): Promise<void> => {
      try {
        const state = await window.pkulaw.getBrowserState();
        if (!cancelled) setPolledBrowser({ scopeKey, state });
      } catch {
        // The next one-second sample retries without changing task state.
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), MANUAL_RECOVERY_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [enabled, scopeKey, taskId, taskStatus, errorCode]);

  useEffect(() => {
    if (
      !isAccessWait(task) ||
      (!browser?.captchaVisible &&
        !browser?.loginVisible &&
        task?.status !== "waiting_login" &&
        task?.status !== "waiting_manual")
    ) {
      return;
    }
    callbacksRef.current.onChallengeVisible?.();
  }, [
    browser?.captchaVisible,
    browser?.loginVisible,
    task?.status,
    taskId,
    errorCode,
    view.phase
  ]);

  useEffect(() => {
    if (!polledBrowser || polledBrowser.scopeKey !== scopeKey) return;
    const currentTask = taskRef.current;
    const step = controller.observe(currentTask, polledBrowser.state);
    setView(manualRecoveryViewForPhase(step.phase, step.detail));
    if (
      !step.shouldResume ||
      !step.operationId ||
      requestedOperationRef.current === step.operationId
    ) {
      return;
    }
    requestedOperationRef.current = step.operationId;
    const operationId = step.operationId;
    const recoveryTaskId = currentTask?.id || "";
    const epoch = epochRef.current;
    controller.markResumeStarted();
    setView(manualRecoveryViewForPhase("resuming"));
    void window.pkulaw
      .resumeTaskAfterManual(recoveryTaskId, operationId)
      .then(async (result) => {
        if (
          epoch !== epochRef.current ||
          !controller.isActive(operationId)
        ) {
          return;
        }
        controller.markResumeSucceeded();
        setView(manualRecoveryViewForPhase("succeeded"));
        try {
          await callbacksRef.current.onRecovered?.(result);
        } catch {
          // Recovery already succeeded; a refresh failure must not show failure.
        }
      })
      .catch((error: unknown) => {
        if (
          epoch !== epochRef.current ||
          !controller.isActive(operationId)
        ) {
          return;
        }
        const code = manualRecoveryErrorCode(error);
        const detail =
          code === "CAPTCHA_VISIBLE"
            ? "验证码仍在更新，程序会继续观察，不会结束后台任务。"
            : `自动恢复失败：${String(error)}。请等待稳定后重新检测。`;
        controller.markResumeFailed(detail, code);
        const failedStep = controller.observe(
          currentTask,
          polledBrowser.state
        );
        setView(
          manualRecoveryViewForPhase(
            failedStep.phase,
            failedStep.detail,
            failedStep.canRetry
          )
        );
      });
  }, [
    controller,
    polledBrowser,
    scopeKey,
    taskId,
    taskStatus,
    errorCode
  ]);

  useEffect(() => {
    if (browser?.captchaVisible || browser?.loginVisible) {
      epochRef.current += 1;
      requestedOperationRef.current = "";
    }
  }, [browser?.captchaVisible, browser?.loginVisible]);

  const retry = useCallback(() => {
    epochRef.current += 1;
    requestedOperationRef.current = "";
    controller.retry();
    setView(manualRecoveryViewForPhase("observing"));
    setPolledBrowser(null);
    void window.pkulaw
      .getBrowserState()
      .then((state) => setPolledBrowser({ scopeKey, state }))
      .catch(() => undefined);
  }, [controller, scopeKey]);

  return { view, retry };
}
