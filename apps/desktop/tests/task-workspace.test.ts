import { describe, expect, it } from "vitest";
import {
  deriveCollectorWorkspaceMode,
  nextManualRecoveryPhase,
  shouldAutoResumeManual
} from "../src/shared/task-workspace";

describe("collector workspace mode", () => {
  it("derives configure, running, settings, manual and decision modes", () => {
    expect(
      deriveCollectorWorkspaceMode({
        hasTask: false,
        settingsOpen: false,
        taskStatus: null,
        errorCode: "",
        captchaVisible: false,
        loginVisible: false
      })
    ).toBe("configure");
    expect(
      deriveCollectorWorkspaceMode({
        hasTask: true,
        settingsOpen: false,
        taskStatus: "running",
        errorCode: "",
        captchaVisible: false,
        loginVisible: false
      })
    ).toBe("running");
    expect(
      deriveCollectorWorkspaceMode({
        hasTask: true,
        settingsOpen: true,
        taskStatus: "running",
        errorCode: "",
        captchaVisible: false,
        loginVisible: false
      })
    ).toBe("settings");
    expect(
      deriveCollectorWorkspaceMode({
        hasTask: true,
        settingsOpen: false,
        taskStatus: "waiting_manual",
        errorCode: "MANUAL_ACTION_REQUIRED",
        captchaVisible: true,
        loginVisible: false
      })
    ).toBe("manual");
    expect(
      deriveCollectorWorkspaceMode({
        hasTask: true,
        settingsOpen: false,
        taskStatus: "waiting_manual",
        errorCode: "PAGE_LIMIT_REACHED",
        captchaVisible: false,
        loginVisible: false
      })
    ).toBe("decision");
  });

  it("gives an active access challenge priority over an open settings panel", () => {
    expect(
      deriveCollectorWorkspaceMode({
        hasTask: true,
        settingsOpen: true,
        taskStatus: "waiting_manual",
        errorCode: "MANUAL_ACTION_REQUIRED",
        captchaVisible: true,
        loginVisible: false
      })
    ).toBe("manual");
  });
});

describe("manual recovery", () => {
  it("requires three stable clear samples before auto-resuming the originating task", () => {
    const base = {
      taskId: "task-1",
      leaseTaskId: "task-1",
      taskStatus: "waiting_manual" as const,
      errorCode: "MANUAL_ACTION_REQUIRED",
      captchaVisible: false,
      loginVisible: false,
      authenticated: true,
      selectorReady: true
    };

    expect(shouldAutoResumeManual({ ...base, stableSamples: 1 })).toBe(false);
    expect(shouldAutoResumeManual({ ...base, stableSamples: 2 })).toBe(false);
    expect(shouldAutoResumeManual({ ...base, stableSamples: 3 })).toBe(true);
    expect(
      shouldAutoResumeManual({
        ...base,
        leaseTaskId: "task-2",
        stableSamples: 3
      })
    ).toBe(false);
  });

  it("never auto-resumes decision gates or unsafe browser states", () => {
    const base = {
      taskId: "task-1",
      leaseTaskId: "task-1",
      taskStatus: "waiting_manual" as const,
      errorCode: "MANUAL_ACTION_REQUIRED",
      captchaVisible: false,
      loginVisible: false,
      authenticated: true,
      selectorReady: true,
      stableSamples: 3
    };

    expect(
      shouldAutoResumeManual({
        ...base,
        errorCode: "BATCH_SCALE_CONFIRMATION_REQUIRED"
      })
    ).toBe(false);
    expect(
      shouldAutoResumeManual({
        ...base,
        errorCode: "PAGE_LIMIT_REACHED"
      })
    ).toBe(false);
    expect(
      shouldAutoResumeManual({
        ...base,
        errorCode: "CANDIDATE_SELECTION_REQUIRED"
      })
    ).toBe(false);
    expect(
      shouldAutoResumeManual({
        ...base,
        errorCode: "INSTITUTION_QUOTA_REACHED"
      })
    ).toBe(false);
    expect(
      shouldAutoResumeManual({ ...base, captchaVisible: true })
    ).toBe(false);
    expect(
      shouldAutoResumeManual({ ...base, loginVisible: true })
    ).toBe(false);
    expect(
      shouldAutoResumeManual({ ...base, authenticated: false })
    ).toBe(false);
    expect(
      shouldAutoResumeManual({ ...base, selectorReady: false })
    ).toBe(false);
  });

  it("reaches verification only after the third stable clear sample", () => {
    expect(
      nextManualRecoveryPhase("waiting", {
        type: "clear-sample",
        stableSamples: 1
      })
    ).toBe("observing");
    expect(
      nextManualRecoveryPhase("observing", {
        type: "clear-sample",
        stableSamples: 2
      })
    ).toBe("observing");
    expect(
      nextManualRecoveryPhase("observing", {
        type: "clear-sample",
        stableSamples: 3
      })
    ).toBe("verifying");
    expect(
      nextManualRecoveryPhase("verifying", {
        type: "resume-started"
      })
    ).toBe("resuming");
    expect(
      nextManualRecoveryPhase("resuming", {
        type: "resume-succeeded"
      })
    ).toBe("succeeded");
  });

  it("returns to waiting whenever the challenge reappears", () => {
    for (const current of [
      "observing",
      "verifying",
      "resuming",
      "failed"
    ] as const) {
      expect(
        nextManualRecoveryPhase(current, { type: "challenge-visible" })
      ).toBe("waiting");
    }
    expect(
      nextManualRecoveryPhase("succeeded", { type: "challenge-visible" })
    ).toBe("succeeded");
  });
});
