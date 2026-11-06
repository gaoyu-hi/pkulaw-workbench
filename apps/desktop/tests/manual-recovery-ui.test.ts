import { describe, expect, it } from "vitest";
import {
  AUTO_RECOVERY_FAILURE_GRACE_MS,
  CAPTCHA_SETTLE_WINDOW_MS,
  REQUIRED_STABLE_CLEAR_SAMPLES,
  createManualRecoveryController,
  manualRecoveryErrorCode,
  manualRecoveryViewForPhase,
  observeManualRecovery
} from "../src/renderer/src/useManualRecovery";
import { renderRecoveryCard } from "../src/renderer/src/TaskRuntimePanel";
import type {
  BrowserHostState,
  TaskRecord
} from "../src/shared/protocol";
import { shouldAutoResumeManual } from "../src/shared/task-workspace";

const task = {
  id: "task-1",
  name: "验证码任务",
  mode: "single",
  status: "waiting_manual",
  input: {},
  outputDir: "D:/output",
  progress: 0.2,
  currentStage: "waiting-captcha",
  error: { code: "MANUAL_ACTION_REQUIRED", message: "captcha" },
  checkpoint: {},
  archivedAt: null,
  archiveReason: "",
  startedAt: null,
  finishedAt: null,
  createdAt: "",
  updatedAt: ""
} satisfies TaskRecord;

const clearBrowser = {
  status: "ready",
  targetBound: true,
  captchaVisible: false,
  loginVisible: false,
  authenticated: true,
  selectorReady: true,
  browserLease: {
    available: false,
    leaseId: "lease-1",
    taskId: "task-1",
    mode: "single",
    createdAt: "",
    updatedAt: ""
  }
} as BrowserHostState;

describe("manual recovery UI", () => {
  it("requires three stable clear samples over the settle window", () => {
    const first = observeManualRecovery({
      task,
      browser: clearBrowser,
      previousStableSamples: 0
    });
    const second = observeManualRecovery({
      task,
      browser: clearBrowser,
      previousStableSamples: first.stableSamples
    });
    const third = observeManualRecovery({
      task,
      browser: clearBrowser,
      previousStableSamples: second.stableSamples
    });

    expect(REQUIRED_STABLE_CLEAR_SAMPLES).toBe(3);
    expect(CAPTCHA_SETTLE_WINDOW_MS).toBeGreaterThanOrEqual(2_000);
    expect(first.shouldResume).toBe(false);
    expect(first.phase).toBe("observing");
    expect(second.stableSamples).toBe(2);
    expect(second.shouldResume).toBe(false);
    expect(third.stableSamples).toBe(3);
    expect(third.shouldResume).toBe(true);
    expect(third.phase).toBe("verifying");
  });

  it("never auto-resumes a capture boundary decision", () => {
    expect(
      shouldAutoResumeManual({
        taskId: "task-1",
        leaseTaskId: "task-1",
        taskStatus: "waiting_manual",
        errorCode: "CAPTURE_BOUNDARY_REACHED",
        captchaVisible: false,
        loginVisible: false,
        authenticated: true,
        selectorReady: true,
        stableSamples: 3
      })
    ).toBe(false);
  });

  it("allows a preflight access wait after its temporary lease was released", () => {
    const releasedBrowser = {
      ...clearBrowser,
      browserLease: {
        ...clearBrowser.browserLease,
        available: true,
        leaseId: "",
        taskId: ""
      }
    };
    const preflightTask = {
      ...task,
      error: { code: "LOGIN_REQUIRED", message: "login" },
      checkpoint: {
        accessRecovery: {
          taskId: "task-1",
          browserLeaseTaskId: "task-1",
          waitCode: "LOGIN_REQUIRED"
        }
      }
    } satisfies TaskRecord;

    const first = observeManualRecovery({
      task: preflightTask,
      browser: releasedBrowser,
      previousStableSamples: 0
    });
    const second = observeManualRecovery({
      task: preflightTask,
      browser: releasedBrowser,
      previousStableSamples: first.stableSamples
    });
    const third = observeManualRecovery({
      task: preflightTask,
      browser: releasedBrowser,
      previousStableSamples: second.stableSamples
    });

    expect(first.phase).toBe("observing");
    expect(second.shouldResume).toBe(false);
    expect(third.shouldResume).toBe(true);
  });

  it("resets stability when the challenge reappears or lease belongs elsewhere", () => {
    expect(
      observeManualRecovery({
        task,
        browser: { ...clearBrowser, captchaVisible: true },
        previousStableSamples: 1
      }).stableSamples
    ).toBe(0);
    expect(
      observeManualRecovery({
        task,
        browser: {
          ...clearBrowser,
          browserLease: {
            ...clearBrowser.browserLease,
            taskId: "task-2"
          }
        },
        previousStableSamples: 1
      }).stableSamples
    ).toBe(0);
  });

  it("maps recovery phases to user-facing status", () => {
    expect(manualRecoveryViewForPhase("waiting").detail).toContain(
      "等待人工完成验证码"
    );
    expect(manualRecoveryViewForPhase("verifying").detail).toContain(
      "验证码已清除"
    );
    expect(manualRecoveryViewForPhase("failed").canRetry).toBe(true);
    expect(manualRecoveryViewForPhase("succeeded").detail).toContain(
      "已自动恢复"
    );
  });

  it("requests recovery once and waits for the user after a failure", () => {
    const controller = createManualRecoveryController(
      () => "manual-operation-1"
    );

    expect(controller.observe(task, clearBrowser).shouldResume).toBe(false);
    expect(controller.observe(task, clearBrowser).shouldResume).toBe(false);
    expect(controller.observe(task, clearBrowser)).toMatchObject({
      stableSamples: 3,
      phase: "verifying",
      shouldResume: true,
      operationId: "manual-operation-1"
    });
    expect(controller.observe(task, clearBrowser).shouldResume).toBe(false);

    controller.markResumeStarted();
    controller.markResumeFailed();
    expect(controller.observe(task, clearBrowser)).toMatchObject({
      phase: "failed",
      shouldResume: false
    });

    controller.retry();
    expect(controller.observe(task, clearBrowser).shouldResume).toBe(false);
    expect(controller.observe(task, clearBrowser).shouldResume).toBe(false);
    expect(controller.observe(task, clearBrowser)).toMatchObject({
      stableSamples: 3,
      phase: "verifying",
      shouldResume: true
    });
  });

  it("continues observing after CAPTCHA_VISIBLE during verification", () => {
    const controller = createManualRecoveryController(
      () => "manual-operation-captcha"
    );
    controller.observe(task, clearBrowser);
    controller.observe(task, clearBrowser);
    expect(controller.observe(task, clearBrowser).shouldResume).toBe(true);
    controller.markResumeStarted();
    controller.markResumeFailed(
      "验证码仍然可见",
      "CAPTCHA_VISIBLE"
    );

    expect(controller.observe(task, clearBrowser)).toMatchObject({
      phase: "observing",
      stableSamples: 1,
      canRetry: false,
      shouldResume: false
    });
  });

  it("offers retry only after the automatic-recovery grace window", () => {
    let now = 1_000;
    const controller = createManualRecoveryController(
      () => "manual-operation-grace",
      () => now
    );
    controller.observe(task, clearBrowser);
    controller.observe(task, clearBrowser);
    controller.observe(task, clearBrowser);
    controller.markResumeStarted();
    controller.markResumeFailed(
      "页面复核失败",
      "SOURCE_ACCESS_ERROR"
    );

    expect(controller.observe(task, clearBrowser).canRetry).toBe(false);
    now += AUTO_RECOVERY_FAILURE_GRACE_MS - 1;
    expect(controller.observe(task, clearBrowser).canRetry).toBe(false);
    now += 1;
    expect(controller.observe(task, clearBrowser)).toMatchObject({
      phase: "failed",
      canRetry: true,
      shouldResume: false
    });
  });

  it("recognizes an IPC error code embedded in the error message", () => {
    expect(
      manualRecoveryErrorCode(
        new Error("CAPTCHA_VISIBLE: 滑块或人机验证仍然可见")
      )
    ).toBe("CAPTCHA_VISIBLE");
    expect(
      manualRecoveryErrorCode({
        code: "SOURCE_ACCESS_ERROR",
        message: "source failed"
      })
    ).toBe("SOURCE_ACCESS_ERROR");
  });

  it("carries the active recovery token after three clear samples", () => {
    const recoveryTask = {
      ...task,
      checkpoint: {
        searchRecovery: {
          eventId: "recovery-event-1",
          phase: "waiting",
          governor: { captcha_waits: 2 },
          attemptCount: 0,
          reusedResult: false,
          breakerReason: "",
          action: {
            actionId: "action-1",
            kind: "year-filter-switch",
            request: {
              query: "人工智能",
              library: "central",
              year: "2022"
            },
            attemptCount: 0
          }
        }
      }
    } satisfies TaskRecord;
    const controller = createManualRecoveryController(
      () => "manual-operation-token"
    );

    expect(controller.observe(recoveryTask, clearBrowser).shouldResume).toBe(
      false
    );
    expect(controller.observe(recoveryTask, clearBrowser).shouldResume).toBe(
      false
    );
    expect(controller.observe(recoveryTask, clearBrowser)).toMatchObject({
      shouldResume: true,
      recoveryToken: "recovery-event-1"
    });
  });

  it("renders captcha count, action target, reuse and breaker state", () => {
    const recoveryTask = {
      ...task,
      checkpoint: {
        searchRecovery: {
          eventId: "recovery-event-2",
          phase: "boundary",
          governor: { captcha_waits: 2 },
          attemptCount: 1,
          reusedResult: true,
          breakerReason: "same-action-captcha",
          action: {
            actionId: "action-2",
            kind: "year-filter-switch",
            request: {
              query: "人工智能",
              library: "central",
              year: "2022"
            },
            attemptCount: 1
          }
        }
      }
    } satisfies TaskRecord;

    const text = renderRecoveryCard(recoveryTask);
    expect(text).toContain("第 2 次验证码");
    expect(text).toContain("年份：2022");
    expect(text).toContain("已复用成功结果");
    expect(text).toContain("same-action-captcha");
  });
});
