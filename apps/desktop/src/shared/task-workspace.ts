import type { TaskStatus } from "./protocol";

export type CollectorWorkspaceMode =
  | "configure"
  | "running"
  | "settings"
  | "manual"
  | "decision";

export type ManualRecoveryPhase =
  | "idle"
  | "waiting"
  | "observing"
  | "verifying"
  | "resuming"
  | "succeeded"
  | "failed";

export const REQUIRED_STABLE_CLEAR_SAMPLES = 3;
export const CAPTCHA_SETTLE_WINDOW_MS = 2_500;
export const AUTO_RECOVERY_FAILURE_GRACE_MS = 30_000;

export interface ManualRecoveryView {
  phase: ManualRecoveryPhase;
  detail: string;
  canRetry: boolean;
}

export const idleManualRecovery: ManualRecoveryView = {
  phase: "idle",
  detail: "",
  canRetry: false
};

const ACCESS_WAIT_CODES = new Set([
  "MANUAL_ACTION_REQUIRED",
  "LOGIN_REQUIRED",
  "CAPTCHA_VISIBLE"
]);

const DECISION_CODES = new Set([
  "PAGE_LIMIT_REACHED",
  "BATCH_SCALE_CONFIRMATION_REQUIRED",
  "CAPTURE_BOUNDARY_REACHED",
  "CANDIDATE_SELECTION_REQUIRED",
  "INSTITUTION_QUOTA_REACHED"
]);

export function deriveCollectorWorkspaceMode(input: {
  hasTask: boolean;
  settingsOpen: boolean;
  taskStatus: TaskStatus | null;
  errorCode: string;
  captchaVisible: boolean;
  loginVisible: boolean;
}): CollectorWorkspaceMode {
  if (!input.hasTask || input.taskStatus === null) {
    return "configure";
  }
  if (DECISION_CODES.has(input.errorCode)) {
    return "decision";
  }
  if (
    input.captchaVisible ||
    input.loginVisible ||
    input.taskStatus === "waiting_login" ||
    ACCESS_WAIT_CODES.has(input.errorCode) ||
    input.taskStatus === "waiting_manual"
  ) {
    return "manual";
  }
  if (input.settingsOpen) {
    return "settings";
  }
  return "running";
}

export type ManualRecoveryEvent =
  | { type: "challenge-visible" }
  | { type: "clear-sample"; stableSamples: number }
  | { type: "verification-failed" }
  | { type: "resume-started" }
  | { type: "resume-succeeded" }
  | { type: "resume-failed" }
  | { type: "reset" };

export function nextManualRecoveryPhase(
  current: ManualRecoveryPhase,
  event: ManualRecoveryEvent
): ManualRecoveryPhase {
  if (event.type === "challenge-visible") {
    return current === "succeeded" ? "succeeded" : "waiting";
  }
  if (event.type === "verification-failed") {
    return current === "succeeded" ? "succeeded" : "waiting";
  }
  if (event.type === "reset") {
    return "idle";
  }
  if (event.type === "clear-sample") {
    if (event.stableSamples >= REQUIRED_STABLE_CLEAR_SAMPLES) {
      return current === "succeeded" ? "succeeded" : "verifying";
    }
    return ["waiting", "observing", "failed"].includes(current)
      ? "observing"
      : current;
  }
  if (event.type === "resume-started") {
    return current === "verifying" ? "resuming" : current;
  }
  if (event.type === "resume-succeeded") {
    return current === "resuming" ? "succeeded" : current;
  }
  return current === "resuming" || current === "verifying"
    ? "failed"
    : current;
}

export function shouldAutoResumeManual(input: {
  taskId: string;
  leaseTaskId: string;
  taskStatus: TaskStatus;
  errorCode: string;
  captchaVisible: boolean;
  loginVisible: boolean;
  authenticated: boolean;
  selectorReady: boolean;
  stableSamples: number;
}): boolean {
  return Boolean(
    input.taskId &&
      input.taskId === input.leaseTaskId &&
      ["waiting_login", "waiting_manual"].includes(input.taskStatus) &&
      ACCESS_WAIT_CODES.has(input.errorCode) &&
      !DECISION_CODES.has(input.errorCode) &&
      !input.captchaVisible &&
      !input.loginVisible &&
      input.authenticated &&
      input.selectorReady &&
      input.stableSamples >= REQUIRED_STABLE_CLEAR_SAMPLES
  );
}
