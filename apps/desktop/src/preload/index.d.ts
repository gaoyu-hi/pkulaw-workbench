import type { DesktopApi } from "../shared/protocol";

declare global {
  interface Window {
    pkulaw: DesktopApi;
  }
}

export {};
