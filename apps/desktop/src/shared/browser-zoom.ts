export const MIN_BROWSER_ZOOM_FACTOR = 0.5;
export const DEFAULT_BROWSER_ZOOM_FACTOR = 1;
export const MAX_BROWSER_ZOOM_FACTOR = 2;
export const BROWSER_ZOOM_STEP = 0.1;

export type BrowserZoomShortcut = "in" | "out" | "reset";

export interface BrowserZoomInput {
  type?: string;
  key?: string;
  code?: string;
  control?: boolean;
  meta?: boolean;
}

export interface BrowserZoomTarget {
  setZoomFactor(factor: number): void;
}

export function normalizeBrowserZoomFactor(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error("网页缩放必须是 50% 到 200% 之间的有效数字");
  }
  if (
    value < MIN_BROWSER_ZOOM_FACTOR ||
    value > MAX_BROWSER_ZOOM_FACTOR
  ) {
    throw new Error("网页缩放必须在 50% 到 200% 之间");
  }
  return Math.round(value * 100) / 100;
}

export function reapplyBrowserZoom(
  target: BrowserZoomTarget,
  factor: number
): void {
  target.setZoomFactor(normalizeBrowserZoomFactor(factor));
}

export function stepBrowserZoom(
  current: number,
  direction: "in" | "out"
): number {
  const normalized = normalizeBrowserZoomFactor(current);
  const delta = direction === "in" ? BROWSER_ZOOM_STEP : -BROWSER_ZOOM_STEP;
  return normalizeBrowserZoomFactor(
    Math.min(
      MAX_BROWSER_ZOOM_FACTOR,
      Math.max(
        MIN_BROWSER_ZOOM_FACTOR,
        Math.round((normalized + delta) * 100) / 100
      )
    )
  );
}

export function browserZoomShortcut(
  input: BrowserZoomInput
): BrowserZoomShortcut | null {
  if (
    input.type !== "keyDown" ||
    (!input.control && !input.meta)
  ) {
    return null;
  }
  const key = String(input.key || "");
  const code = String(input.code || "");
  if (key === "0" || code === "Digit0" || code === "Numpad0") {
    return "reset";
  }
  if (
    key === "+" ||
    key === "=" ||
    code === "Equal" ||
    code === "NumpadAdd"
  ) {
    return "in";
  }
  if (
    key === "-" ||
    key === "_" ||
    code === "Minus" ||
    code === "NumpadSubtract"
  ) {
    return "out";
  }
  return null;
}

export function zoomFactorForShortcut(
  current: number,
  shortcut: BrowserZoomShortcut
): number {
  if (shortcut === "reset") return DEFAULT_BROWSER_ZOOM_FACTOR;
  return stepBrowserZoom(current, shortcut);
}
