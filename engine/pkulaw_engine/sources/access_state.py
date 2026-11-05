from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


DEFAULT_ACCESS_SELECTORS: dict[str, str] = {
    "captcha": (
        ".drag_text, #captcha-overlay, .verify-slider, "
        ".geetest_slider, [class*=\"captcha\"]"
    ),
    "login": (
        "#fixture-login, #loginName, #password, "
        "input[name=\"username\"][type=\"password\"], form[action*=\"login\"]"
    ),
    "loggedIn": "#lIsLogin, #IsUserLogin",
}


@dataclass(frozen=True)
class AccessState:
    url: str
    captcha_visible: bool
    captcha_selector: str
    captcha_box: dict[str, float | str] = field(default_factory=dict)
    login_visible: bool = False
    authenticated: bool = False
    quota_visible: bool = False
    page_error: bool = False
    checked_at: str = ""
    title: str = ""
    ready_state: str = ""
    page_text: str = ""

    @property
    def login_required(self) -> bool:
        if self.authenticated:
            return False
        return self.login_visible or "/login" in self.url.lower()

    def as_task_state(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "body": self.page_text,
            "captcha": self.captcha_visible,
            "captchaVisible": self.captcha_visible,
            "captchaSelector": self.captcha_selector,
            "captchaBox": dict(self.captcha_box),
            "captchaCheckedAt": self.checked_at,
            "login": self.login_required,
            "loginVisible": self.login_visible,
            "loggedIn": self.authenticated,
            "authenticated": self.authenticated,
            "quota": self.quota_visible,
            "quotaVisible": self.quota_visible,
            "pageError": self.page_error,
        }


def read_access_state(
    tab: Any,
    selectors: Mapping[str, Any] | None = None,
) -> AccessState:
    configured = {
        **DEFAULT_ACCESS_SELECTORS,
        **{
            str(key): str(value)
            for key, value in dict(selectors or {}).items()
            if value
        },
    }
    observation = tab.run_js(
        f"""
        return (() => {{
          const configured = {json.dumps(configured, ensure_ascii=False)};
          const queryAll = selector => {{
            if (!selector) return [];
            try {{
              return [...document.querySelectorAll(selector)];
            }} catch (_) {{
              return [];
            }}
          }};
          const query = selector => queryAll(selector)[0] || null;
          const inspect = element => {{
            if (!element) return null;
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            const viewportWidth = Math.max(
              0,
              window.innerWidth || document.documentElement?.clientWidth || 0
            );
            const viewportHeight = Math.max(
              0,
              window.innerHeight || document.documentElement?.clientHeight || 0
            );
            const viewportMeasured = viewportWidth > 32 && viewportHeight > 32;
            const intersectsViewport = (
              rect.width > 0 &&
              rect.height > 0 &&
              rect.right > 0 &&
              rect.bottom > 0 &&
              rect.left < viewportWidth &&
              rect.top < viewportHeight
            );
            const visibleWidth = Math.max(
              0,
              Math.min(rect.right, viewportWidth) - Math.max(rect.left, 0)
            );
            const visibleHeight = Math.max(
              0,
              Math.min(rect.bottom, viewportHeight) - Math.max(rect.top, 0)
            );
            const visibleAreaRatio = (
              rect.width > 0 && rect.height > 0
                ? (visibleWidth * visibleHeight) / (rect.width * rect.height)
                : 0
            );
            const substantiallyVisible = (
              !viewportMeasured ||
              (visibleAreaRatio >= 0.75 &&
                visibleWidth >= 24 &&
                visibleHeight >= 12)
            );
            const startsInsideViewport = (
              rect.left >= -1 &&
              rect.top >= -1
            );
            let ancestorVisible = true;
            let ancestor = element.parentElement;
            while (ancestor) {{
              const ancestorStyle = getComputedStyle(ancestor);
              if (
                ancestorStyle.display === "none" ||
                ancestorStyle.visibility === "hidden" ||
                Number(ancestorStyle.opacity || "1") <= 0.01 ||
                ancestor.getAttribute("aria-hidden") === "true" ||
                ancestor.inert === true
              ) {{
                ancestorVisible = false;
                break;
              }}
              ancestor = ancestor.parentElement;
            }}
            const centerX = rect.left + Math.min(
              Math.max(rect.width / 2, 1),
              Math.max(rect.width - 1, 0)
            );
            const centerY = rect.top + Math.min(
              Math.max(rect.height / 2, 1),
              Math.max(rect.height - 1, 0)
            );
            const hit = viewportMeasured
              ? document.elementFromPoint(centerX, centerY)
              : null;
            const hitTestVisible = (
              !!hit &&
              (hit === element || element.contains(hit))
            );
            const visible = (
              element.getClientRects().length > 0 &&
              style.display !== "none" &&
              style.visibility !== "hidden" &&
              Number(style.opacity || "1") > 0 &&
              ancestorVisible &&
              startsInsideViewport &&
              (!viewportMeasured || intersectsViewport) &&
              substantiallyVisible
            );
            return {{
              visible,
              box: {{
                x: rect.x,
                y: rect.y,
                width: rect.width,
                height: rect.height,
                right: rect.right,
                bottom: rect.bottom,
                viewportWidth,
                viewportHeight,
                inViewport: viewportMeasured ? intersectsViewport : null,
                substantiallyVisible,
                hitTestVisible,
                visibleAreaRatio,
                visibleWidth,
                visibleHeight,
                display: style.display,
                visibility: style.visibility,
                opacity: style.opacity
              }}
            }};
          }};
          let captchaVisible = false;
          let captchaSelector = "";
          let captchaBox = {{}};
          for (const selector of String(configured.captcha || "").split(",")) {{
            const trimmed = selector.trim();
            const match = queryAll(trimmed).find(element => inspect(element)?.visible);
            if (match) {{
              captchaVisible = true;
              captchaSelector = trimmed;
              captchaBox = inspect(match)?.box || {{}};
              break;
            }}
          }}
          const loginElement = queryAll(configured.login || "")
            .find(element => inspect(element)?.visible) || null;
          const loggedInElement = query(configured.loggedIn || "");
          const loggedInValue = String(loggedInElement?.value || "").toLowerCase();
          const protectedContent = query(
            "#txtSearch, #subLibraries, .fulltext-wrap, .filtrater-box, " +
            "#pkulaw-results, #rightContent .list-wrap"
          );
          const loginVisible = !!loginElement;
          const authenticated = (
            document.documentElement?.dataset.authReady === "true" ||
            ["1", "true", "yes"].includes(loggedInValue) ||
            (!!protectedContent && !loginVisible)
          );
          const body = String(document.body?.innerText || "");
          const compactBody = body.replace(/\\s+/g, " ");
          const quotaVisible = (
            /(今日|当日|今天).{{0,24}}(下载|正文|访问|检索).{{0,20}}(已用满|用完|耗尽|可达上限|达到上限|已达上限)/.test(compactBody) ||
            /(day-limit|daily-limit|download-limit|quota-limit|limit-exceeded)/i.test(location.pathname)
          );
          const pageError = /页面异常|请求失败|系统繁忙|访问过于频繁|服务暂不可用/.test(
            compactBody.slice(0, 1200)
          );
          return {{
            url: location.href,
            title: document.title,
            readyState: document.readyState,
            pageText: compactBody.slice(0, 1800),
            captchaVisible,
            captchaSelector,
            captchaBox,
            loginVisible,
            authenticated,
            quotaVisible,
            pageError,
            checkedAt: new Date().toISOString()
          }};
        }})()
        """
    )
    if not isinstance(observation, dict):
        raise RuntimeError("网页访问状态脚本没有返回对象")
    box = observation.get("captchaBox")
    captcha_visible = bool(observation.get("captchaVisible"))
    captcha_selector = str(observation.get("captchaSelector") or "")
    if isinstance(box, dict) and (
        box.get("inViewport") is False
        or box.get("substantiallyVisible") is False
    ):
        captcha_visible = False
        captcha_selector = ""
        box = {}
    return AccessState(
        url=str(observation.get("url") or ""),
        captcha_visible=captcha_visible,
        captcha_selector=captcha_selector,
        captcha_box=dict(box) if isinstance(box, dict) else {},
        login_visible=bool(observation.get("loginVisible")),
        authenticated=bool(observation.get("authenticated")),
        quota_visible=bool(observation.get("quotaVisible")),
        page_error=bool(observation.get("pageError")),
        checked_at=str(observation.get("checkedAt") or ""),
        title=str(observation.get("title") or ""),
        ready_state=str(observation.get("readyState") or ""),
        page_text=str(observation.get("pageText") or ""),
    )


def read_stable_access_state(
    tab: Any,
    selectors: Mapping[str, Any] | None = None,
    *,
    interval_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> AccessState:
    first = read_access_state(tab, selectors)
    if not first.captcha_visible:
        return first
    sleep(max(0.0, float(interval_seconds)))
    second = read_access_state(tab, selectors)
    if not second.captcha_visible:
        return second
    sleep(max(0.0, float(interval_seconds)))
    return read_access_state(tab, selectors)
