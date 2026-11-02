import { describe, expect, it } from "vitest";
import {
  browserZoomShortcut,
  normalizeBrowserZoomFactor,
  reapplyBrowserZoom,
  stepBrowserZoom
} from "../src/shared/browser-zoom";

describe("browser zoom contract", () => {
  it("accepts the supported zoom range and rejects invalid delta values", () => {
    expect([0.5, 1, 1.25, 1.5, 2].map(normalizeBrowserZoomFactor)).toEqual([
      0.5,
      1,
      1.25,
      1.5,
      2
    ]);
    expect(() => normalizeBrowserZoomFactor(0.49)).toThrow("50%");
    expect(() => normalizeBrowserZoomFactor(2.01)).toThrow("200%");
    expect(() => normalizeBrowserZoomFactor(Number.NaN)).toThrow();
    expect(() => normalizeBrowserZoomFactor(Number.POSITIVE_INFINITY)).toThrow();
    expect(() => normalizeBrowserZoomFactor("1.25")).toThrow();
    expect(() => normalizeBrowserZoomFactor(null)).toThrow();
  });

  it("steps by ten percent and clamps at the allowed limits", () => {
    expect(stepBrowserZoom(1, "in")).toBe(1.1);
    expect(stepBrowserZoom(1, "out")).toBe(0.9);
    expect(stepBrowserZoom(1.25, "in")).toBe(1.35);
    expect(stepBrowserZoom(1.25, "out")).toBe(1.15);
    expect(stepBrowserZoom(0.5, "out")).toBe(0.5);
    expect(stepBrowserZoom(2, "in")).toBe(2);
  });

  it("maps Ctrl shortcuts without touching unrelated keys", () => {
    expect(
      browserZoomShortcut({
        type: "keyDown",
        key: "=",
        code: "Equal",
        control: true
      })
    ).toBe("in");
    expect(
      browserZoomShortcut({
        type: "keyDown",
        key: "-",
        code: "Minus",
        control: true
      })
    ).toBe("out");
    expect(
      browserZoomShortcut({
        type: "keyDown",
        key: "0",
        code: "Digit0",
        control: true
      })
    ).toBe("reset");
    expect(
      browserZoomShortcut({
        type: "keyDown",
        key: "=",
        code: "Equal",
        control: false
      })
    ).toBeNull();
  });

  it("reapplies the active zoom after a page navigation", () => {
    const applied: number[] = [];

    reapplyBrowserZoom(
      {
        setZoomFactor: (factor) => applied.push(factor)
      },
      1.25
    );

    expect(applied).toEqual([1.25]);
  });
});
