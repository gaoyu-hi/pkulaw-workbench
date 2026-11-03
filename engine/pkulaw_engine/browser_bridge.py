from __future__ import annotations

import time
from contextlib import contextmanager
from threading import Condition, RLock
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

from DrissionPage import Chromium

from .download_intent import DownloadIntentManager
from .logging_utils import JsonLineLogger
from .protocol import utc_now
from .sources.access_state import (
    DEFAULT_ACCESS_SELECTORS,
    AccessState,
    read_stable_access_state,
)
from .storage import ValidationError


def normalize_target_url(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            parsed.query,
            "",
        )
    )


def _is_page_disconnected(exc: Exception) -> bool:
    message = str(exc)
    return (
        exc.__class__.__name__ == "PageDisconnectedError"
        or "与页面的连接已断开" in message
        or "page disconnected" in message.lower()
    )


class _BoundJsRunner:
    def __init__(
        self,
        run_js: Callable[[str], Any],
    ) -> None:
        self._run_js = run_js

    def run_js(self, script: str) -> Any:
        return self._run_js(script)


class BrowserBridge:
    """Owns the DrissionPage connection to an Electron WebContentsView."""

    def __init__(
        self,
        logger: JsonLineLogger,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.logger = logger
        self._event_sink = event_sink
        self.browser: Chromium | None = None
        self.main_tab: Any | None = None
        self.entry_url = ""
        self.origin = ""
        self.session_key = ""
        self.target_url = ""
        self.target_key = ""
        self.view_id = ""
        self.manual_continue_at = ""
        self.last_download_event: dict[str, Any] | None = None
        self.last_browser_event: dict[str, Any] | None = None
        self._manual_wait_active = False
        self._operation_lock = RLock()
        self._download_condition = Condition()
        self._download_events: list[dict[str, Any]] = []
        self._download_sequence = 0
        self.download_intents = DownloadIntentManager(
            event_sink=self._note_download_intent_event
        )

    def set_event_sink(
        self,
        event_sink: Callable[[str, dict[str, Any]], None] | None,
    ) -> None:
        self._event_sink = event_sink

    def connect(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._operation_lock:
            return self._connect(params)

    def _connect(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            debug_port = int(params.get("debugPort") or 0)
        except (TypeError, ValueError) as exc:
            raise ValidationError("INVALID_DEBUG_PORT", "debugPort must be an integer") from exc
        entry_url = str(params.get("entryUrl") or "").strip()
        target_url = str(params.get("targetUrl") or entry_url).strip()
        target_key = str(params.get("targetKey") or "").strip()
        session_key = str(params.get("sessionKey") or "").strip()
        view_id = str(params.get("viewId") or "").strip()
        if not 1 <= debug_port <= 65535:
            raise ValidationError("INVALID_DEBUG_PORT", "debugPort is outside the valid range")
        if not entry_url.startswith(("http://", "https://")):
            raise ValidationError("INVALID_ENTRY_URL", "entryUrl must be HTTP or HTTPS")
        if not normalize_target_url(target_url):
            raise ValidationError("INVALID_TARGET_URL", "targetUrl must be HTTP or HTTPS")
        if not session_key:
            raise ValidationError("SESSION_KEY_REQUIRED", "sessionKey is required")

        self.entry_url = entry_url
        parsed = urlparse(entry_url)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.session_key = session_key
        self.target_url = normalize_target_url(target_url)
        self.target_key = target_key
        self.view_id = view_id
        self.manual_continue_at = ""
        self._manual_wait_active = False

        if self.browser is not None:
            self.disconnect()

        deadline = time.time() + 15
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                self.browser = Chromium(f"127.0.0.1:{debug_port}")
                tab = self._find_main_tab(timeout_seconds=5)
                if tab is not None:
                    self.main_tab = tab
                    state = self.state()
                    self.logger.write(
                        "info",
                        "browser.connected",
                        "DrissionPage connected to Electron WebContentsView",
                        {
                            "address": f"127.0.0.1:{debug_port}",
                            "sessionKey": session_key,
                            "viewId": view_id,
                            "targetKey": target_key,
                            "url": state["url"],
                        },
                    )
                    return state
            except Exception as exc:
                last_error = exc
                self.browser = None
                self.main_tab = None
            time.sleep(0.5)

        raise RuntimeError(f"无法连接 Electron 内置网页：{last_error!r}")

    def state(self) -> dict[str, Any]:
        with self._operation_lock:
            return self._state()

    def _state(self) -> dict[str, Any]:
        access = self._read_access_state()
        if access.captcha_visible:
            self._manual_wait_active = True

        tabs = []
        for tab_item in self.browser.get_tabs() if self.browser else []:
            url = str(tab_item.url)
            if url.startswith(("http://", "https://")):
                tabs.append({"url": url, "title": str(tab_item.title)})

        return {
            "url": access.url,
            "title": access.title,
            "readyState": access.ready_state,
            "authenticated": access.authenticated,
            "loginVisible": access.login_visible,
            "captchaVisible": access.captcha_visible,
            "captchaSelector": access.captcha_selector,
            "captchaBox": dict(access.captcha_box),
            "captchaCheckedAt": access.checked_at,
            "quotaVisible": access.quota_visible,
            "pageError": access.page_error,
            "pageText": access.page_text[:500],
            "sessionKey": self.session_key,
            "targetBound": self.main_tab is not None,
            "targetUrl": access.url,
            "viewId": self.view_id,
            "manualContinueRequired": access.captcha_visible,
            "manualContinueAt": self.manual_continue_at,
            "tabs": tabs,
            "lastDownload": self.last_download_event,
            "lastBrowserEvent": self.last_browser_event,
        }

    def _read_access_state(self) -> AccessState:
        return read_stable_access_state(
            _BoundJsRunner(self._run_bound_js),
            DEFAULT_ACCESS_SELECTORS,
        )

    def user_continue(self) -> dict[str, Any]:
        with self._operation_lock:
            return self._user_continue()

    def _user_continue(self) -> dict[str, Any]:
        tab = self._require_tab()
        # The local fixture uses this to simulate the user finishing the visible
        # verification. Real sites remain user-driven in later stages.
        tab.run_js(
            """
            const captcha = document.querySelector('#captcha-overlay');
            if (captcha) captcha.style.display = 'none';
            const status = document.querySelector('#manual-status');
            if (status) status.textContent = '人工处理已确认，等待程序重新核验';
            """
        )
        samples: list[dict[str, Any]] = []
        for _ in range(2):
            time.sleep(0.25)
            samples.append(self.state())
        if any(sample.get("captchaVisible") for sample in samples):
            raise ValidationError(
                "CAPTCHA_VISIBLE",
                "滑块或人机验证仍然可见，请完成验证后再继续",
            )
        if not self._manual_wait_active and self.manual_continue_at:
            return self.state()
        self._manual_wait_active = False
        self.manual_continue_at = utc_now()
        self.logger.write("info", "browser.manual_continue", "User continued after manual verification")
        return self.state()

    def fixture_login(self) -> dict[str, Any]:
        with self._operation_lock:
            return self._fixture_login()

    def _fixture_login(self) -> dict[str, Any]:
        tab = self._require_tab()
        button = tab.ele("#fixture-login", timeout=5)
        if not button:
            raise ValidationError("FIXTURE_LOGIN_NOT_FOUND", "本地夹具登录按钮不存在")
        button.click()
        time.sleep(0.8)
        return self.state()

    def fixture_show_captcha(self) -> dict[str, Any]:
        with self._operation_lock:
            return self._fixture_show_captcha()

    def _fixture_show_captcha(self) -> dict[str, Any]:
        tab = self._require_tab()
        button = tab.ele("#show-captcha", timeout=5)
        if not button:
            raise ValidationError("FIXTURE_CAPTCHA_NOT_FOUND", "本地夹具滑块按钮不存在")
        button.click()
        time.sleep(0.2)
        return self.state()

    def fixture_download(self) -> dict[str, Any]:
        with self._operation_lock:
            return self._fixture_download()

    def _fixture_download(self) -> dict[str, Any]:
        tab = self._require_tab()
        button = tab.ele("#download", timeout=5)
        if not button:
            raise ValidationError("FIXTURE_DOWNLOAD_NOT_FOUND", "本地夹具下载按钮不存在")
        button.click()
        return {"clicked": True, "requestedAt": utc_now()}

    def fixture_open_child(self) -> dict[str, Any]:
        with self._operation_lock:
            return self._fixture_open_child()

    def _fixture_open_child(self) -> dict[str, Any]:
        tab = self._require_tab()
        button = tab.ele("#open-child", timeout=5)
        if not button:
            raise ValidationError("FIXTURE_CHILD_NOT_FOUND", "本地夹具子窗口按钮不存在")
        button.click()
        time.sleep(0.5)
        return self.state()

    def note_download_event(self, event: str, data: dict[str, Any]) -> None:
        with self._download_condition:
            self._download_sequence += 1
            download_event = {
                "event": event,
                **data,
                "sequence": self._download_sequence,
                "recordedAt": utc_now(),
            }
            self.last_download_event = download_event
            self._download_events.append(download_event)
            if len(self._download_events) > 500:
                self._download_events = self._download_events[-500:]
            self._download_condition.notify_all()
        self.logger.write(
            "info",
            "browser.download_event",
            "Electron download event received",
            {"event": event, **data},
        )

    def note_browser_event(self, event: str, data: dict[str, Any]) -> None:
        self.last_browser_event = {
            "event": event,
            **data,
            "recordedAt": utc_now(),
        }
        self.logger.write(
            "info",
            "browser.event",
            "Browser lifecycle event received",
            {"event": event, **data},
        )

    def disconnect(self) -> None:
        with self._operation_lock:
            self.browser = None
            self.main_tab = None
            self.entry_url = ""
            self.origin = ""
            self.session_key = ""
            self.target_url = ""
            self.target_key = ""
            self.view_id = ""

    def download_cursor(self) -> int:
        with self._download_condition:
            return self._download_sequence

    def create_download_intent(
        self,
        *,
        task_id: str,
        kind: str,
        source_url: str,
        expected_suffixes: tuple[str, ...],
        max_downloads: int = 1,
    ) -> dict[str, Any]:
        return self.download_intents.create(
            task_id=task_id,
            kind=kind,
            source_url=source_url or self.target_url,
            expected_suffixes=expected_suffixes,
            max_downloads=max_downloads,
        )

    def wait_for_download_intent(
        self,
        intent_id: str,
        cursor: int,
        timeout_seconds: float = 75,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_seconds
        processed_sequence = int(cursor)
        while True:
            with self._download_condition:
                events = [
                    dict(event)
                    for event in self._download_events
                    if int(event.get("sequence") or 0) > processed_sequence
                ]
            for event in events:
                processed_sequence = max(
                    processed_sequence,
                    int(event.get("sequence") or 0),
                )
                outcome = self.download_intents.observe(intent_id, event)
                if (
                    outcome.get("accepted")
                    or outcome.get("outcome") == "mismatch"
                ) and on_event is not None:
                    try:
                        on_event(
                            {
                                **event,
                                "event": (
                                    "mismatch"
                                    if outcome.get("outcome") == "mismatch"
                                    else event.get("event")
                                ),
                                "outcome": outcome.get("outcome"),
                                "downloadIntent": self.download_intents.state(
                                    intent_id
                                )
                                or outcome.get("intent"),
                            }
                        )
                    except Exception as exc:
                        self.logger.write(
                            "warn",
                            "download.intent_progress_callback_failed",
                            "Failed to report download intent progress",
                            {
                                "intentId": intent_id,
                                "event": event.get("event"),
                                "error": repr(exc),
                            },
                        )
                if outcome.get("outcome") in {
                    "completed",
                    "failed",
                    "mismatch",
                }:
                    return {
                        **event,
                        "event": (
                            "mismatch"
                            if outcome.get("outcome") == "mismatch"
                            else event.get("event")
                        ),
                        "intentId": intent_id,
                        "downloadIntent": outcome.get("intent"),
                        "reason": outcome.get("reason"),
                        "errorCode": outcome.get("errorCode"),
                        "actualFile": outcome.get("actualFile"),
                        "actualSuffix": outcome.get("actualSuffix"),
                        "actualSignature": outcome.get("actualSignature"),
                        "actualPath": outcome.get("actualPath"),
                        "expectedSuffixes": outcome.get("expectedSuffixes"),
                        "recovery": outcome.get("recovery"),
                    }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            with self._download_condition:
                self._download_condition.wait(timeout=min(remaining, 0.5))

    def record_download_intent_validation(
        self,
        intent_id: str,
        *,
        valid: bool,
        actual_kind: str,
        detail: str = "",
    ) -> None:
        self.download_intents.record_validation(
            intent_id,
            valid=valid,
            actual_kind=actual_kind,
            detail=detail,
        )

    def _note_download_intent_event(
        self,
        event: str,
        data: dict[str, Any],
    ) -> None:
        self.logger.write(
            "warn",
            event,
            "Download event was ignored because it did not match the active intent",
            data,
        )
        if self._event_sink is not None:
            try:
                self._event_sink(event, data)
            except Exception as exc:
                self.logger.write(
                    "warn",
                    "download.intent_event_emit_failed",
                    "Failed to forward download intent event",
                    {"event": event, "error": repr(exc)},
                )

    def wait_for_download_completion(
        self,
        cursor: int,
        timeout_seconds: float = 75,
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout_seconds
        with self._download_condition:
            while True:
                for event in self._download_events:
                    if (
                        int(event.get("sequence") or 0) > cursor
                        and event.get("event") in {"completed", "failed"}
                    ):
                        return dict(event)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._download_condition.wait(timeout=remaining)

    @contextmanager
    def transaction(self):
        with self._operation_lock:
            self._run_bound_js("return true;")
            yield self._require_tab()

    def _run_bound_js(self, script: str):
        last_error: Exception | None = None
        for attempt in range(2):
            tab = self._require_tab()
            try:
                return tab.run_js(script)
            except Exception as exc:
                last_error = exc
                if not _is_page_disconnected(exc):
                    raise
                self.main_tab = None
                if attempt == 0:
                    time.sleep(0.2)
        raise last_error or RuntimeError("内置网页目标连接已断开")

    def _require_tab(self):
        if self.browser is None:
            raise ValidationError("BROWSER_NOT_CONNECTED", "当前没有连接内置浏览器")
        if self.main_tab is not None:
            try:
                current_url = str(self.main_tab.url or "")
                if normalize_target_url(current_url):
                    return self.main_tab
            except Exception:
                self.main_tab = None
        tab = self._find_main_tab(timeout_seconds=2)
        if tab is None:
            raise RuntimeError("未找到当前机构的内置网页目标")
        self.main_tab = tab
        return tab

    def _find_main_tab(self, timeout_seconds: float):
        if self.browser is None:
            return None
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            external_tabs = []
            for tab in self.browser.get_tabs():
                url = str(tab.url or "")
                normalized_url = normalize_target_url(url)
                if not normalized_url:
                    continue
                external_tabs.append(tab)
                if self.target_key and self._tab_target_key(tab) == self.target_key:
                    return tab
            for tab in external_tabs:
                if normalize_target_url(str(tab.url or "")) == self.target_url:
                    return tab
            candidates = [
                tab
                for tab in external_tabs
                if (urlparse(str(tab.url or "")).path.rstrip("/") or "/") != "/child"
            ]
            if len(candidates) == 1:
                return candidates[0]
            time.sleep(0.2)
        return None

    @staticmethod
    def _tab_target_key(tab: Any) -> str:
        try:
            return str(tab.run_js("return String(window.name || '')") or "")
        except Exception:
            return ""
