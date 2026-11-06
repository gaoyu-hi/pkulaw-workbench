from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping


class PageNavigationError(RuntimeError):
    code = "PAGE_TRANSITION_UNCONFIRMED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code or "PAGE_TRANSITION_UNCONFIRMED"


@dataclass(frozen=True)
class PageNavigationResult:
    state: dict[str, Any]
    used_form_fallback: bool
    stability_samples: int


def row_signature(state: Mapping[str, Any]) -> tuple[Any, ...]:
    rows = list(state.get("rows") or [])
    return (
        tuple(
            (
                int(row.get("number") or 0),
                str(row.get("policyId") or row.get("url") or ""),
            )
            for row in rows
        ),
        bool(state.get("empty")),
    )


def search_context_matches(
    before_state: Mapping[str, Any],
    state: Mapping[str, Any],
) -> bool:
    for key in ("searchId", "queryValue", "library", "searchType"):
        expected = str(before_state.get(key) or "")
        actual = str(state.get(key) or "")
        if expected and actual and expected != actual:
            return False
    return True


def click_next_page(tab: Any, selectors: Mapping[str, Any]) -> dict[str, Any]:
    return tab.run_js(
        f"""
        return (() => {{
          const query = selector => {{
            if (!selector) return null;
            try {{
              return document.querySelector(selector);
            }} catch (_) {{
              return null;
            }}
          }};
          const visible = element => !!(
            element &&
            element.getClientRects().length &&
            getComputedStyle(element).display !== "none" &&
            getComputedStyle(element).visibility !== "hidden"
          );
          const pagination = query({json.dumps(selectors.get("pagination") or "")});
          let next = query({json.dumps(selectors.get("nextPage") or "")});
          if (!next) {{
            const scopes = [pagination, document].filter(Boolean);
            next = scopes.flatMap(scope => [...scope.querySelectorAll("a,button")]).find(
              item => (item.innerText || item.textContent || "").trim() === "下一页"
            ) || null;
          }}
          const listItem = next ? next.closest("li") : null;
          const disabled = !!(
            (next && (
              next.disabled ||
              next.classList.contains("disabled") ||
              next.getAttribute("aria-disabled") === "true"
            )) ||
            (listItem && listItem.classList.contains("disabled"))
          );
          if (!next || disabled || !visible(next)) {{
            return {{ok:false, reason:"next page unavailable"}};
          }}
          setTimeout(() => next.click(), 250);
          return {{ok:true, strategy:"next-click"}};
        }})()
        """
    ) or {}


def submit_page_form(
    tab: Any,
    *,
    expected_page: int,
    selectors: Mapping[str, Any],
) -> dict[str, Any]:
    page_index = max(0, int(expected_page) - 1)
    right_form = str(selectors.get("rightForm") or "#right_form")
    return tab.run_js(
        f"""
        return (() => {{
          const form = document.querySelector({json.dumps(right_form)});
          if (!form) return {{ok:false, reason:"right form missing"}};
          const setValue = (selector, value) => {{
            const input = form.querySelector(selector) ||
              document.querySelector(selector);
            if (input) input.value = String(value);
          }};
          const currentIndexControl =
            form.querySelector('input[name="Pager.PageIndex"]') ||
            document.querySelector('input[name="Pager.PageIndex"]');
          const previousPageIndex = Math.max(
            0,
            Number(currentIndexControl?.value || 0)
          );
          setValue('input[name="OldPageIndex"]', previousPageIndex);
          setValue('input[name="Pager.PageIndex"]', {page_index});
          setValue('input[name="PageIndex"]', {page_index});
          if (typeof form.requestSubmit !== "function") {{
            return {{ok:false, reason:"right form submit missing"}};
          }}
          setTimeout(() => {{
            const right_form = form;
            right_form.requestSubmit();
          }}, 100);
          return {{
            ok:true,
            strategy:"right-form-submit",
            pageIndex:{page_index}
          }};
        }})()
        """
    ) or {}


def transition_next_page(
    tab: Any,
    *,
    expected_page: int,
    selectors: Mapping[str, Any],
    before_state: Mapping[str, Any],
    read_state: Callable[[], dict[str, Any]],
    ensure_ready: Callable[[], Any] | None = None,
    read_error_is_transition: Callable[[Exception], bool] | None = None,
    click_timeout_seconds: float = 4.0,
    settle_timeout_seconds: float = 16.0,
    interval_seconds: float = 0.35,
    stable_samples: int = 3,
    allow_direct_page_fallback: bool = False,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> PageNavigationResult:
    before_signature = row_signature(before_state)
    direct_beyond_reported = (
        allow_direct_page_fallback
        and int(expected_page) > int(before_state.get("totalPages") or 0)
    )
    clicked = click_next_page(tab, selectors)
    if not clicked.get("ok"):
        if not allow_direct_page_fallback:
            raise PageNavigationError(
                f"翻页失败：{clicked!r}",
                code="PAGE_STRUCTURE_CHANGED",
            )
    else:
        direct_beyond_reported = False

    transition_probe = {
        "sawTransition": False,
        "lastState": None,
    }

    def read_stable_state(timeout: float) -> tuple[dict[str, Any] | None, int]:
        deadline = clock() + max(0.0, timeout)
        stable = 0
        last_signature: tuple[Any, ...] | None = None
        while clock() < deadline:
            sleep(max(0.01, interval_seconds))
            try:
                state = read_state()
            except Exception as exc:
                if read_error_is_transition is not None and read_error_is_transition(exc):
                    stable = 0
                    continue
                raise
            identity = row_signature(state)
            transition_probe["lastState"] = dict(state)
            if (
                int(state.get("page") or 0)
                != int(before_state.get("page") or 0)
                or identity != before_signature
                or not search_context_matches(before_state, state)
            ):
                transition_probe["sawTransition"] = True
            if int(state.get("page") or 0) != int(expected_page):
                stable = 0
                last_signature = identity
                continue
            if not search_context_matches(before_state, state):
                stable = 0
                last_signature = identity
                continue
            if identity == before_signature:
                stable = 0
                last_signature = identity
                continue
            if last_signature == identity:
                stable += 1
            else:
                stable = 1
                last_signature = identity
            if stable >= max(2, int(stable_samples)):
                return state, stable
        return None, stable

    state, stable = (
        read_stable_state(click_timeout_seconds)
        if clicked.get("ok")
        else (None, 0)
    )
    used_form_fallback = not bool(clicked.get("ok"))
    if (
        state is None
        and clicked.get("ok")
        and bool(transition_probe["sawTransition"])
    ):
        # A live transition can take longer than the click-poll window. Give
        # the already-started navigation time to settle before raising.
        state, stable = read_stable_state(settle_timeout_seconds)
        if state is None:
            raise PageNavigationError(
                f"未能核验第 {expected_page} 页；点击已触发页面变化，"
                "但页码、结果或签名未稳定",
                code="PAGE_TRANSITION_UNCONFIRMED",
            )
    if state is None:
        submitted = submit_page_form(
            tab,
            expected_page=expected_page,
            selectors=selectors,
        )
        if not submitted.get("ok"):
            raise PageNavigationError(
                f"翻页表单回退失败：{submitted!r}",
                code=(
                    "RESULT_WINDOW_TRUNCATED"
                    if direct_beyond_reported
                    else "PAGE_STRUCTURE_CHANGED"
                ),
            )
        used_form_fallback = True
        state, stable = read_stable_state(settle_timeout_seconds)
    if state is None:
        raise PageNavigationError(
            f"未能核验第 {expected_page} 页；页码、结果或签名未稳定",
            code=(
                "RESULT_WINDOW_TRUNCATED"
                if direct_beyond_reported
                else "PAGE_TRANSITION_UNCONFIRMED"
            ),
        )
    if ensure_ready is not None:
        ensure_ready()
    return PageNavigationResult(
        state=dict(state),
        used_form_fallback=used_form_fallback,
        stability_samples=stable,
    )
