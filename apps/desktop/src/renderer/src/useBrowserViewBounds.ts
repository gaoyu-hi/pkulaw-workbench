import { useEffect, type RefObject } from "react";
import { browserViewBoundsFromRect } from "../../shared/browser-layout";
import type { BrowserBounds } from "../../shared/protocol";

export function browserBoundsForVisibility(
  rect: {
    left: number;
    top: number;
    width: number;
    height: number;
  },
  visible: boolean
): BrowserBounds {
  if (!visible) {
    return { x: 0, y: 0, width: 1, height: 1, visible: false };
  }
  return (
    browserViewBoundsFromRect(rect) ?? {
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      visible: false
    }
  );
}

export function useBrowserViewBounds(
  hostRef: RefObject<HTMLDivElement | null>,
  visible: boolean
): void {
  useEffect(() => {
    const host = hostRef.current;
    if (!host) {
      return;
    }
    const update = (): void => {
      const bounds = browserBoundsForVisibility(
        host.getBoundingClientRect(),
        visible
      );
      void window.pkulaw.setBrowserBounds(bounds);
    };
    if (!visible) {
      update();
      return;
    }
    const observer = new ResizeObserver(update);
    observer.observe(host);
    window.addEventListener("resize", update);
    const frame = requestAnimationFrame(() => {
      requestAnimationFrame(update);
    });
    const retryTimers = [80, 240, 600, 1200].map((delay) =>
      window.setTimeout(update, delay)
    );
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", update);
      for (const timer of retryTimers) window.clearTimeout(timer);
      void window.pkulaw.setBrowserBounds({
        x: 0,
        y: 0,
        width: 1,
        height: 1,
        visible: false
      });
    };
  }, [hostRef, visible]);
}
