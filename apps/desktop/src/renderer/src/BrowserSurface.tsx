import { useRef } from "react";
import type { BrowserHostState } from "../../shared/protocol";
import {
  DEFAULT_BROWSER_ZOOM_FACTOR,
  stepBrowserZoom
} from "../../shared/browser-zoom";
import { useBrowserViewBounds } from "./useBrowserViewBounds";

export { browserBoundsForVisibility } from "./useBrowserViewBounds";

export interface BrowserSurfaceProps {
  browser: BrowserHostState;
  visible: boolean;
  variant: "overview" | "task";
  onRefresh: () => void;
  onContinue: () => void;
}

const browserStatusLabels: Record<BrowserHostState["status"], string> = {
  idle: "未启动",
  loading: "加载中",
  ready: "已连接",
  waiting_login: "等待登录",
  waiting_manual: "等待人工",
  failed: "异常"
};

function BrowserZoomControls({
  factor,
  disabled,
  onChange
}: {
  factor: number;
  disabled: boolean;
  onChange: (factor: number) => void;
}): React.JSX.Element {
  return (
    <div className="browser-zoom-controls" title="Ctrl + / Ctrl - / Ctrl 0">
      <button
        type="button"
        className="icon-button"
        aria-label="缩小网页"
        disabled={disabled || factor <= 0.5}
        onClick={() => onChange(stepBrowserZoom(factor, "out"))}
      >
        −
      </button>
      <span data-testid="browser-zoom-value">
        {Math.round(factor * 100)}%
      </span>
      <button
        type="button"
        className="icon-button"
        aria-label="放大网页"
        disabled={disabled || factor >= 2}
        onClick={() => onChange(stepBrowserZoom(factor, "in"))}
      >
        +
      </button>
      <button
        type="button"
        className="text-button"
        aria-label="重置网页缩放"
        disabled={disabled || factor === DEFAULT_BROWSER_ZOOM_FACTOR}
        onClick={() => onChange(DEFAULT_BROWSER_ZOOM_FACTOR)}
      >
        100%
      </button>
    </div>
  );
}

export default function BrowserSurface({
  browser,
  visible,
  variant,
  onRefresh,
  onContinue
}: BrowserSurfaceProps): React.JSX.Element {
  const hostRef = useRef<HTMLDivElement>(null);
  useBrowserViewBounds(hostRef, visible);

  return (
    <section
      className={`browser-surface ${variant} ${visible ? "visible" : "collapsed"}`}
      data-browser-visible={visible ? "true" : "false"}
    >
      <div className="browser-toolbar">
        <div className="browser-navigation">
          <button
            type="button"
            className="icon-button"
            disabled={!browser.canGoBack || !visible}
            onClick={() =>
              void window.pkulaw.browserNavigate("back")
            }
            aria-label="后退"
          >
            ‹
          </button>
          <button
            type="button"
            className="icon-button"
            disabled={!browser.institutionId || !visible}
            onClick={onRefresh}
            aria-label="刷新"
          >
            ↻
          </button>
          <button
            type="button"
            className="icon-button"
            disabled={!browser.institutionId || !visible}
            onClick={() =>
              void window.pkulaw.browserNavigate("home")
            }
            aria-label="主页"
          >
            ⌂
          </button>
          <div className="browser-address" title={browser.url}>
            <span className="lock-dot" />
            {browser.url || "等待打开机构入口"}
          </div>
        </div>
        <div className="browser-toolbar-right">
          <BrowserZoomControls
            factor={browser.zoomFactor}
            disabled={!browser.targetBound || !visible}
            onChange={(factor) =>
              void window.pkulaw.setBrowserZoom(factor)
            }
          />
          <span className={`browser-status ${browser.status}`}>
            {browserStatusLabels[browser.status]}
          </span>
        </div>
      </div>

      <div ref={hostRef} className="browser-view-host">
        {!visible && (
          <div className="browser-collapsed-placeholder">
            浏览器已收起，Session 保持
          </div>
        )}
        {visible && !browser.institutionId && (
          <div className="browser-placeholder">
            <div className="browser-placeholder-mark">◎</div>
            <strong>选择机构后在这里显示网页</strong>
            <p>网页由 Electron 承载，不会弹出外部 Edge。</p>
          </div>
        )}
      </div>

      {browser.captchaVisible && visible && (
        <div className="manual-banner">
          <strong>等待人工完成滑块或验证码</strong>
          <p>验证码仍可见时不能继续；清除后将自动复核。</p>
          <button type="button" onClick={onContinue}>
            重新检测
          </button>
        </div>
      )}
    </section>
  );
}
