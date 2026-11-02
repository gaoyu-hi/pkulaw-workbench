import type { BrowserBounds } from "./protocol";

export interface BrowserViewportRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export function browserViewBoundsFromRect(
  rect: BrowserViewportRect
): BrowserBounds | null {
  if (
    !Number.isFinite(rect.width) ||
    !Number.isFinite(rect.height) ||
    rect.width <= 32 ||
    rect.height <= 32
  ) {
    return null;
  }
  return {
    x: Math.max(0, Math.round(rect.left)),
    y: Math.max(0, Math.round(rect.top)),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    visible: true
  };
}
