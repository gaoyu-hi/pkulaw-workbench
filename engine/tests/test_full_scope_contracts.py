from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest.mock import MagicMock, patch

from pkulaw_engine.browser_bridge import (
    CAPTCHA_SETTLE_WINDOW_MS,
    REQUIRED_STABLE_CLEAR_SAMPLES,
    BrowserBridge,
)
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.processing_task import DataProcessingManager
from pkulaw_engine.sources.page_navigation import (
    PageNavigationError,
    submit_page_form,
    transition_next_page,
)
from pkulaw_engine.sources.pkulaw import PkulawAdapter
from pkulaw_engine.sources.selector_profile import (
    SelectorProfileError,
    diagnose_selector_profile,
    load_selector_profile,
)
from pkulaw_engine.storage import TaskStore, ValidationError


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "pkulaw-v6"
REAL_WORD_ROOT = Path(__file__).parent / "fixtures" / "real-word-doc"
REAL_XLS_ROOT = Path(__file__).parent / "fixtures" / "real-catalog-xls"
RUN_PENDING_CONTRACTS = os.getenv("PKULAW_RUN_PENDING_CONTRACTS") == "1"


def pending_contract(reason: str):
    return unittest.skipUnless(RUN_PENDING_CONTRACTS, reason)


def _optional_export(module_name: str, export: str) -> Callable[..., Any] | None:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None
    value = getattr(module, export, None)
    return value if callable(value) else None


def _require(export: Callable[..., Any] | None, description: str) -> Callable[..., Any]:
    if export is None:
        raise AssertionError(f"缺少待实现契约：{description}")
    return export


def _fulltext_fixture_body() -> str:
    markup = (FIXTURE_ROOT / "detail-fulltext-clauses.html").read_text(
        encoding="utf-8"
    )
    segments: list[str] = []
    for match in re.finditer(
        r'<p(?: class="navzhang")?>(.*?)</p>'
        r'|<div class="kuan-content">(.*?)</div>',
        markup,
        flags=re.DOTALL,
    ):
        value = re.sub(r"<[^>]+>", "", match.group(1) or match.group(2) or "")
        value = re.sub(r"\s+", " ", value).strip()
        if value:
            segments.append(value)
    return "\n".join(segments)


class _IncrementingClock:
    def __init__(self, step: float) -> None:
        self.step = step
        self.value = 0.0

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


class _FakeTab:
    def __init__(self, state: Mapping[str, Any]) -> None:
        self.state = dict(state)
        self.url = str(state.get("url") or "")

    def run_js(self, _script: str) -> dict[str, Any]:
        return dict(self.state)


class _VisibleDragTextTab(_FakeTab):
    def run_js(self, script: str) -> dict[str, Any]:
        state = dict(self.state)
        state["captchaVisible"] = ".drag_text" in script
        if state["captchaVisible"]:
            state["captchaSelector"] = ".drag_text"
            state["captchaBox"] = {
                "width": 300,
                "height": 34,
                "display": "block",
                "visibility": "visible",
                "opacity": 1,
            }
        return state


class _HiddenOverlayTab(_FakeTab):
    def run_js(self, script: str) -> dict[str, Any]:
        state = dict(self.state)
        state["captchaVisible"] = False
        state["captchaSelector"] = ""
        state["captchaBox"] = {}
        return state


class _AccessStateTab(_FakeTab):
    pass


class FullScopeFixtureTests(unittest.TestCase):
    def test_record_search_fixture_has_results_without_library_root(self) -> None:
        html = (FIXTURE_ROOT / "record-search-results.html").read_text(encoding="utf-8")

        self.assertIn("/law/search/RecordSearch", html)
        self.assertNotIn('id="rightContent"', html)
        self.assertIn('class="list-wrap"', html)
        self.assertNotIn('id="subLibraries"', html)

    def test_visible_dragtext_and_hidden_overlay_are_separate_fixtures(self) -> None:
        visible = (FIXTURE_ROOT / "captcha-dragtext-visible.html").read_text(
            encoding="utf-8"
        )
        hidden = (FIXTURE_ROOT / "captcha-overlay-hidden.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('class="drag_text"', visible)
        self.assertIn("display: block", visible)
        self.assertNotIn('id="captcha-overlay"', visible)
        self.assertIn('id="captcha-overlay"', hidden)
        self.assertIn("display:none", hidden.replace(" ", ""))

    def test_detail_fixture_has_exactly_38_kuan_content_clauses(self) -> None:
        html = (FIXTURE_ROOT / "detail-fulltext-clauses.html").read_text(
            encoding="utf-8"
        )
        clauses = re.findall(
            r'<div class="kuan-content">(.*?)</div>',
            html,
            flags=re.DOTALL,
        )

        self.assertEqual(len(clauses), 38)
        self.assertIn("第一条", clauses[0])
        self.assertIn("第三十八条", clauses[-1])

    def test_real_evidence_samples_exist(self) -> None:
        self.assertTrue(any(REAL_WORD_ROOT.glob("*.doc")))
        catalog = REAL_XLS_ROOT / "碳中和_2026_西藏_目录.xls"
        self.assertTrue(catalog.is_file())
        self.assertEqual(catalog.read_bytes()[:8], bytes.fromhex("d0cf11e0a1b11ae1"))


class AccessStateContractTests(unittest.TestCase):
    def test_transient_captcha_is_not_reported_after_false_resample(self) -> None:
        read_stable_access_state = _require(
            _optional_export(
                "pkulaw_engine.sources.access_state",
                "read_stable_access_state",
            ),
            "AccessState 必须对瞬时验证码元素进行二次确认",
        )
        observations = [
            {
                "url": "https://www.pkulaw.com/law/chl",
                "captchaVisible": True,
                "captchaSelector": ".drag_text",
                "captchaBox": {
                    "width": 300,
                    "height": 34,
                    "inViewport": True,
                    "display": "block",
                    "visibility": "visible",
                    "opacity": "1",
                },
                "loginVisible": False,
                "authenticated": True,
                "quotaVisible": False,
                "pageError": False,
            },
            {
                "url": "https://www.pkulaw.com/law/chl",
                "captchaVisible": False,
                "captchaSelector": "",
                "loginVisible": False,
                "authenticated": True,
                "quotaVisible": False,
                "pageError": False,
            },
        ]

        class SequenceTab:
            calls = 0

            def run_js(self, _script: str) -> dict[str, Any]:
                current = observations[min(self.calls, 1)]
                self.calls += 1
                return dict(current)

        tab = SequenceTab()
        state = read_stable_access_state(
            tab,
            {"captcha": ".drag_text"},
            sleep=lambda _seconds: None,
        )

        self.assertFalse(state.captcha_visible)
        self.assertEqual(state.captcha_selector, "")
        self.assertEqual(tab.calls, 2)

    def test_short_lived_captcha_flash_requires_sustained_confirmation(
        self,
    ) -> None:
        read_stable_access_state = _require(
            _optional_export(
                "pkulaw_engine.sources.access_state",
                "read_stable_access_state",
            ),
            "AccessState 必须区分短暂闪烁和持续验证码",
        )
        visible = {
            "url": "https://www.pkulaw.com/law/chl",
            "captchaVisible": True,
            "captchaSelector": ".drag_text",
            "captchaBox": {
                "width": 300,
                "height": 34,
                "inViewport": True,
                "display": "block",
                "visibility": "visible",
                "opacity": "1",
            },
            "loginVisible": False,
            "authenticated": True,
            "quotaVisible": False,
            "pageError": False,
        }
        absent = {
            **visible,
            "captchaVisible": False,
            "captchaSelector": "",
            "captchaBox": {},
        }
        observations = [visible, visible, absent]

        class SequenceTab:
            calls = 0

            def run_js(self, _script: str) -> dict[str, Any]:
                current = observations[min(self.calls, 2)]
                self.calls += 1
                return dict(current)

        tab = SequenceTab()
        state = read_stable_access_state(
            tab,
            {"captcha": ".drag_text"},
            sleep=lambda _seconds: None,
        )

        self.assertFalse(state.captcha_visible)
        self.assertEqual(tab.calls, 3)

    def test_dragtext_captcha_is_visible(self) -> None:
        read_access_state = _require(
            _optional_export(
                "pkulaw_engine.sources.access_state",
                "read_access_state",
            ),
            "AccessState 必须联合识别可见 .drag_text",
        )
        tab = _VisibleDragTextTab(
            {
                "url": "https://www.pkulaw.com/law/lar",
                "captchaVisible": False,
                "captchaSelector": "",
                "loginVisible": False,
                "authenticated": True,
                "quotaVisible": False,
                "pageError": False,
            }
        )

        state = read_access_state(tab, {"captcha": ".drag_text, #captcha-overlay"})

        self.assertTrue(state.captcha_visible)
        self.assertEqual(state.captcha_selector, ".drag_text")

    def test_partially_clipped_dragtext_is_not_captcha(self) -> None:
        read_access_state = _require(
            _optional_export(
                "pkulaw_engine.sources.access_state",
                "read_access_state",
            ),
            "AccessState 不可把视口外半截占位滑块当验证码",
        )
        tab = _FakeTab(
            {
                "url": "https://www.pkulaw.com/law/chl",
                "captchaVisible": True,
                "captchaSelector": ".drag_text",
                "captchaBox": {
                    "x": -150,
                    "width": 300,
                    "height": 34,
                    "inViewport": True,
                    "substantiallyVisible": False,
                    "hitTestVisible": False,
                    "visibleAreaRatio": 0.5,
                },
                "loginVisible": False,
                "authenticated": True,
                "quotaVisible": False,
                "pageError": False,
            }
        )

        state = read_access_state(tab, {"captcha": ".drag_text"})

        self.assertFalse(state.captcha_visible)
        self.assertEqual(state.captcha_selector, "")

    def test_hidden_overlay_is_not_captcha(self) -> None:
        read_access_state = _require(
            _optional_export(
                "pkulaw_engine.sources.access_state",
                "read_access_state",
            ),
            "AccessState 不可把隐藏 #captcha-overlay 当验证码",
        )
        tab = _HiddenOverlayTab(
            {
                "url": "https://www.pkulaw.com/law/lar",
                "captchaVisible": False,
                "captchaSelector": "",
                "loginVisible": False,
                "authenticated": True,
                "quotaVisible": False,
                "pageError": False,
            }
        )

        state = read_access_state(tab, {"captcha": ".drag_text, #captcha-overlay"})

        self.assertFalse(state.captcha_visible)
        self.assertEqual(state.captcha_selector, "")

    def test_offscreen_dragtext_is_not_captcha(self) -> None:
        read_access_state = _require(
            _optional_export(
                "pkulaw_engine.sources.access_state",
                "read_access_state",
            ),
            "AccessState 不得把屏幕外的 .drag_text 当验证码",
        )
        tab = _FakeTab(
            {
                "url": "https://www.pkulaw.com/law/lar",
                "captchaVisible": True,
                "captchaSelector": ".drag_text",
                "captchaBox": {
                    "x": -150,
                    "y": 83,
                    "width": 300,
                    "height": 34,
                    "viewportWidth": 3,
                    "viewportHeight": 3,
                    "inViewport": False,
                    "display": "block",
                    "visibility": "visible",
                    "opacity": "1",
                },
                "loginVisible": False,
                "authenticated": True,
                "quotaVisible": False,
                "pageError": False,
            }
        )

        state = read_access_state(tab, {"captcha": ".drag_text, #captcha-overlay"})

        self.assertFalse(state.captcha_visible)
        self.assertEqual(state.captcha_selector, "")

    def test_continue_rejected_while_captcha_remains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = BrowserBridge(JsonLineLogger(Path(directory) / "engine.jsonl"))
            tab = _FakeTab({"url": "https://www.pkulaw.com/law/lar"})
            bridge.main_tab = tab  # type: ignore[assignment]
            with (
                patch.object(
                    bridge,
                    "state",
                    return_value={
                        "manualContinueRequired": True,
                        "captchaVisible": True,
                    },
                ),
                patch.object(bridge, "_require_tab", return_value=tab),
            ):
                with self.assertRaises(ValidationError) as context:
                    bridge._user_continue()
            bridge.disconnect()

        self.assertEqual(context.exception.code, "CAPTCHA_VISIBLE")

    def test_repeated_continue_is_idempotent_after_captcha_clears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = BrowserBridge(JsonLineLogger(Path(directory) / "engine.jsonl"))
            tab = _FakeTab({"url": "https://www.pkulaw.com/law/lar"})
            bridge.main_tab = tab  # type: ignore[assignment]
            bridge._manual_wait_active = False
            bridge.manual_continue_at = "2026-11-02T00:00:00Z"
            with (
                patch.object(
                    bridge,
                    "state",
                    return_value={
                        "manualContinueRequired": False,
                        "captchaVisible": False,
                    },
                ),
                patch.object(bridge, "_require_tab", return_value=tab),
                patch(
                    "pkulaw_engine.browser_bridge.utc_now",
                    return_value="2026-11-02T00:00:01Z",
                ) as utc_now,
            ):
                bridge._user_continue()
            bridge.disconnect()

        self.assertEqual(
            bridge.manual_continue_at,
            "2026-11-02T00:00:00Z",
        )
        utc_now.assert_not_called()

    def test_manual_continue_requires_three_stable_samples_over_settle_window(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = BrowserBridge(JsonLineLogger(Path(directory) / "engine.jsonl"))
            tab = _FakeTab({"url": "https://www.pkulaw.com/law/lar"})
            bridge.main_tab = tab  # type: ignore[assignment]
            bridge._manual_wait_active = True
            bridge.manual_continue_at = ""
            states = [
                {"captchaVisible": False},
                {"captchaVisible": False},
                {"captchaVisible": False},
                {"captchaVisible": False},
            ]

            def next_state() -> dict[str, Any]:
                return dict(states.pop(0))

            with (
                patch.object(bridge, "state", side_effect=next_state),
                patch.object(bridge, "_require_tab", return_value=tab),
                patch("pkulaw_engine.browser_bridge.time.sleep") as sleep,
            ):
                bridge._user_continue()
            bridge.disconnect()

        self.assertEqual(REQUIRED_STABLE_CLEAR_SAMPLES, 3)
        self.assertEqual(sleep.call_count, REQUIRED_STABLE_CLEAR_SAMPLES)
        self.assertGreaterEqual(
            sum(call.args[0] for call in sleep.call_args_list),
            CAPTCHA_SETTLE_WINDOW_MS / 1_000,
        )

    def test_manual_continue_rejects_a_transient_captcha_reappearance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = BrowserBridge(JsonLineLogger(Path(directory) / "engine.jsonl"))
            tab = _FakeTab({"url": "https://www.pkulaw.com/law/lar"})
            bridge.main_tab = tab  # type: ignore[assignment]
            states = [
                {"captchaVisible": False},
                {"captchaVisible": True},
                {"captchaVisible": False},
            ]

            def next_state() -> dict[str, Any]:
                return dict(states.pop(0))

            with (
                patch.object(bridge, "state", side_effect=next_state),
                patch.object(bridge, "_require_tab", return_value=tab),
                patch("pkulaw_engine.browser_bridge.time.sleep"),
            ):
                with self.assertRaises(ValidationError) as context:
                    bridge._user_continue()
            bridge.disconnect()

        self.assertEqual(context.exception.code, "CAPTCHA_VISIBLE")
        self.assertFalse(states)


class PaginationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_selector_profile(
            Path(__file__).parents[1]
            / "pkulaw_engine"
            / "sources"
            / "profiles"
            / "pkulaw-v6.json"
        )

    def test_record_search_route_is_results_profile(self) -> None:
        controls = {
            "searchInput": {"ok": True, "strategy": "stable"},
            "searchSubmit": {"ok": True, "strategy": "stable"},
            "exactMode": {"ok": True, "strategy": "stable"},
            "libraryRoot": {"ok": False, "strategy": "missing"},
            "resultsRoot": {"ok": True, "strategy": "stable"},
            "resultLink": {"ok": True, "strategy": "stable"},
            "resultNumber": {"ok": True, "strategy": "stable"},
            "batchCheckbox": {"ok": True, "strategy": "stable"},
            "resultCount": {"ok": True, "strategy": "stable"},
        }
        tab = _FakeTab(
            {
                "url": "https://www.pkulaw.com/law/search/RecordSearch",
                "title": "北大法宝结果页",
                "pageKind": "results",
                "controls": controls,
            }
        )

        diagnostic = diagnose_selector_profile(tab, self.profile)

        self.assertEqual(diagnostic["pageKind"], "results")
        self.assertTrue(diagnostic["pageReady"])
        self.assertTrue(diagnostic["ready"])
        self.assertNotIn("libraryRoot", diagnostic["missingControls"])

    def test_record_search_results_root_supports_real_page_without_wrapper(self) -> None:
        selector = str(self.profile.selectors["resultsRoot"])

        self.assertIn("#rightContent .list-wrap", selector)
        self.assertIn(".list-wrap", [part.strip() for part in selector.split(",")])
        fixture = (
            Path(__file__).with_name("fixtures")
            / "pkulaw-v6"
            / "record-search-results.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn('id="rightContent"', fixture)
        self.assertIn('class="list-wrap"', fixture)

    def test_new_search_returns_to_search_entry_when_results_page_has_no_form(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = JsonLineLogger(Path(directory) / "engine.jsonl")
            bridge = MagicMock()
            bridge.entry_url = "https://www.pkulaw.com/"
            transaction = MagicMock()
            tab = MagicMock()
            tab.url = "https://www.pkulaw.com/law/search/RecordSearch"
            transaction.__enter__.return_value = tab
            bridge.transaction.return_value = transaction
            adapter = PkulawAdapter(bridge, logger)

            result_diagnostics = {
                "detected": True,
                "pageKind": "results",
                "pageReady": True,
                "controls": {
                    "resultsRoot": {"ok": True},
                    "resultLink": {"ok": True},
                },
                "missingControls": [],
            }
            search_diagnostics = {
                "detected": True,
                "pageKind": "results",
                "pageReady": True,
                "controls": {
                    "searchInput": {"ok": True},
                    "searchSubmit": {"ok": True},
                    "exactMode": {"ok": True},
                    "libraryRoot": {"ok": True},
                },
                "missingControls": [],
            }
            with (
                patch(
                    "pkulaw_engine.sources.pkulaw.detect_selector_profile",
                    return_value=(self.profile, ["#main_form"]),
                ),
                patch(
                    "pkulaw_engine.sources.pkulaw.diagnose_selector_profile",
                    return_value=result_diagnostics,
                ),
                patch(
                    "pkulaw_engine.sources.pkulaw.diagnose_for_operation",
                    side_effect=SelectorProfileError(
                        "results page has no search controls",
                        code="PAGE_SELECTOR_MISMATCH",
                    ),
                ),
                patch.object(adapter, "_read_access_state", return_value={}),
                patch.object(adapter, "_raise_for_access"),
                patch.object(
                    adapter,
                    "_diagnose_current_page",
                    return_value=search_diagnostics,
                ),
                patch("pkulaw_engine.sources.pkulaw.time.sleep"),
            ):
                adapter._ensure_search_page(tab)

        tab.get.assert_called_once_with("https://www.pkulaw.com/")

    def test_dead_next_click_uses_controlled_form_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = JsonLineLogger(Path(directory) / "engine.jsonl")
            bridge = MagicMock()
            transaction = MagicMock()
            transaction.__enter__.return_value = MagicMock()
            tab = transaction.__enter__.return_value
            bridge.transaction.return_value = transaction
            adapter = PkulawAdapter(bridge, logger)
            adapter.selector_profile = self.profile
            adapter.selectors = dict(self.profile.selectors)
            states = [
                *[
                    {
                        "page": 1,
                        "totalPages": 3,
                        "rows": [{"policyId": "p1"}],
                    }
                    for _ in range(3)
                ],
                {
                    "page": 2,
                    "totalPages": 3,
                    "rows": [{"policyId": "p2"}],
                },
                {
                    "page": 2,
                    "totalPages": 3,
                    "rows": [{"policyId": "p2"}],
                },
            ]
            clock = [0.0]

            def monotonic() -> float:
                return clock[0]

            def sleep(seconds: float) -> None:
                clock[0] += max(2.2, seconds)

            with (
                patch.object(adapter, "_ensure_operation_ready"),
                patch.object(adapter, "_read_results", side_effect=states),
                patch(
                    "pkulaw_engine.sources.pkulaw.time.monotonic",
                    side_effect=monotonic,
                ),
                patch("pkulaw_engine.sources.pkulaw.time.sleep", side_effect=sleep),
            ):
                tab.run_js.return_value = {"ok": True}
                result = adapter.next_page(2)

        self.assertEqual(result["page"], 2)
        scripts = [str(call.args[0]) for call in tab.run_js.call_args_list]
        self.assertGreaterEqual(len(scripts), 2)
        self.assertEqual(
            sum(
                "right_form.requestSubmit()" in script
                for script in scripts
            ),
            1,
        )

    def test_successful_next_click_does_not_submit_form(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tab = MagicMock()
            states = iter(
                [
                    {
                        "page": 2,
                        "totalPages": 3,
                        "rows": [{"policyId": "p2"}],
                    },
                    {
                        "page": 2,
                        "totalPages": 3,
                        "rows": [{"policyId": "p2"}],
                    },
                ]
            )
            result = transition_next_page(
                tab,
                expected_page=2,
                selectors=self.profile.selectors,
                before_state={
                    "page": 1,
                    "totalPages": 3,
                    "rows": [{"policyId": "p1"}],
                },
                read_state=lambda: next(states),
                click_timeout_seconds=5,
                settle_timeout_seconds=5,
                interval_seconds=0.1,
                clock=lambda: 0.0,
                sleep=lambda _seconds: None,
            )

        self.assertFalse(result.used_form_fallback)
        self.assertEqual(tab.run_js.call_count, 1)

    def test_slow_click_transition_never_submits_duplicate_form(self) -> None:
        tab = MagicMock()
        tab.run_js.return_value = {"ok": True}
        states = iter(
            [
                {
                    "page": 1,
                    "totalPages": 3,
                    "rows": [{"policyId": "p1"}],
                },
                {
                    "page": 1,
                    "totalPages": 3,
                    "rows": [{"policyId": "p1"}],
                },
                {
                    "page": 2,
                    "totalPages": 3,
                    "rows": [{"policyId": "p2-a"}],
                },
                {
                    "page": 2,
                    "totalPages": 3,
                    "rows": [{"policyId": "p2-b"}],
                },
                {
                    "page": 2,
                    "totalPages": 3,
                    "rows": [{"policyId": "p2-b"}],
                },
            ]
        )

        result = transition_next_page(
            tab,
            expected_page=2,
            selectors=self.profile.selectors,
            before_state={
                "page": 1,
                "totalPages": 3,
                "rows": [{"policyId": "p1"}],
            },
            read_state=lambda: next(states),
            click_timeout_seconds=2.5,
            settle_timeout_seconds=3,
            interval_seconds=0.1,
            clock=_IncrementingClock(step=0.4),
            sleep=lambda _seconds: None,
        )

        self.assertEqual(result.state["page"], 2)
        self.assertFalse(result.used_form_fallback)
        self.assertEqual(
            sum(
                "right_form.submit()" in str(call.args[0])
                for call in tab.run_js.call_args_list
            ),
            0,
        )

    def test_unavailable_next_control_uses_direct_form_for_effective_page(self) -> None:
        tab = MagicMock()
        tab.run_js.side_effect = [
            {"ok": False, "reason": "next page unavailable"},
            {"ok": True},
        ]
        states = iter(
            [
                {
                    "page": 21,
                    "totalPages": 20,
                    "resultCount": 17224,
                    "actualPageSize": 100,
                    "rows": [{"policyId": "p21"}],
                },
                {
                    "page": 21,
                    "totalPages": 20,
                    "resultCount": 17224,
                    "actualPageSize": 100,
                    "rows": [{"policyId": "p21"}],
                },
            ]
        )

        result = transition_next_page(
            tab,
            expected_page=21,
            selectors=self.profile.selectors,
            before_state={
                "page": 20,
                "totalPages": 20,
                "resultCount": 17224,
                "actualPageSize": 100,
                "rows": [{"policyId": "p20"}],
            },
            read_state=lambda: next(states),
            allow_direct_page_fallback=True,
            click_timeout_seconds=1,
            settle_timeout_seconds=2,
            interval_seconds=0.1,
            clock=_IncrementingClock(step=0.2),
            sleep=lambda _seconds: None,
        )

        self.assertTrue(result.used_form_fallback)
        self.assertEqual(result.state["page"], 21)
        self.assertEqual(
            sum(
                "right_form.requestSubmit()" in str(call.args[0])
                for call in tab.run_js.call_args_list
            ),
            1,
        )
        scripts = [str(call.args[0]) for call in tab.run_js.call_args_list]
        self.assertEqual(
            sum("right_form.submit()" in script for script in scripts),
            0,
        )

    def test_form_fallback_keeps_live_old_page_index(self) -> None:
        tab = MagicMock()
        tab.run_js.return_value = {"ok": True}

        submit_page_form(
            tab,
            expected_page=22,
            selectors=self.profile.selectors,
        )

        script = str(tab.run_js.call_args.args[0])
        self.assertIn("previousPageIndex", script)
        self.assertIn(
            'setValue(\'input[name="OldPageIndex"]\', previousPageIndex)',
            script,
        )
        self.assertIn(
            'setValue(\'input[name="Pager.PageIndex"]\', 21)',
            script,
        )

    def test_direct_page_fallback_rejects_old_page_as_truncation(self) -> None:
        tab = MagicMock()
        tab.run_js.side_effect = [
            {"ok": False, "reason": "next page unavailable"},
            {"ok": True},
        ]
        with self.assertRaises(PageNavigationError) as context:
            transition_next_page(
                tab,
                expected_page=21,
                selectors=self.profile.selectors,
                before_state={
                    "page": 20,
                    "totalPages": 20,
                    "resultCount": 17224,
                    "actualPageSize": 100,
                    "rows": [{"policyId": "p20"}],
                },
                read_state=lambda: {
                    "page": 20,
                    "totalPages": 20,
                    "resultCount": 17224,
                    "actualPageSize": 100,
                    "rows": [{"policyId": "p20"}],
                },
                allow_direct_page_fallback=True,
                click_timeout_seconds=0.2,
                settle_timeout_seconds=0.2,
                interval_seconds=0.1,
                clock=_IncrementingClock(step=0.3),
                sleep=lambda _seconds: None,
            )

        self.assertEqual(context.exception.code, "RESULT_WINDOW_TRUNCATED")

    def test_transition_rejects_unchanged_result_signature(self) -> None:
        tab = MagicMock()
        tab.run_js.return_value = {"ok": True}
        with self.assertRaises(PageNavigationError):
            transition_next_page(
                tab,
                expected_page=2,
                selectors=self.profile.selectors,
                before_state={
                    "page": 1,
                    "totalPages": 3,
                    "rows": [{"policyId": "same"}],
                },
                read_state=lambda: {
                    "page": 2,
                    "totalPages": 3,
                    "rows": [{"policyId": "same"}],
                },
                click_timeout_seconds=1,
                settle_timeout_seconds=1,
                interval_seconds=0.5,
                clock=_IncrementingClock(step=0.6),
                sleep=lambda _seconds: None,
            )
        self.assertEqual(
            sum(
                "right_form.requestSubmit()" in str(call.args[0])
                for call in tab.run_js.call_args_list
            ),
            0,
        )

    def test_transition_rejects_cross_search_identity(self) -> None:
        tab = MagicMock()
        tab.run_js.return_value = {"ok": True}
        with self.assertRaises(PageNavigationError):
            transition_next_page(
                tab,
                expected_page=2,
                selectors=self.profile.selectors,
                before_state={
                    "page": 1,
                    "totalPages": 3,
                    "rows": [{"policyId": "p1"}],
                    "searchId": "search-a",
                    "queryValue": "碳中和",
                },
                read_state=lambda: {
                    "page": 2,
                    "totalPages": 3,
                    "rows": [{"policyId": "p2"}],
                    "searchId": "search-b",
                    "queryValue": "另一个查询",
                },
                click_timeout_seconds=1,
                settle_timeout_seconds=1,
                interval_seconds=0.5,
                clock=_IncrementingClock(step=0.6),
                sleep=lambda _seconds: None,
            )

    def test_transition_rejects_wrong_landing_page(self) -> None:
        tab = MagicMock()
        tab.run_js.return_value = {"ok": True}
        with self.assertRaises(PageNavigationError):
            transition_next_page(
                tab,
                expected_page=2,
                selectors=self.profile.selectors,
                before_state={
                    "page": 1,
                    "totalPages": 4,
                    "rows": [{"policyId": "p1"}],
                },
                read_state=lambda: {
                    "page": 3,
                    "totalPages": 4,
                    "rows": [{"policyId": "p3"}],
                },
                click_timeout_seconds=1,
                settle_timeout_seconds=1,
                interval_seconds=0.5,
                clock=_IncrementingClock(step=0.6),
                sleep=lambda _seconds: None,
            )


class DocumentAndCatalogContractTests(unittest.TestCase):
    CATALOG_SELECTED = [
        {
            "number": 1,
            "policyId": "a8ac5e480a1e7a211bf6441e182ae98cbdfb",
            "title": "拉萨市科学技术局关于组织申报2026年度市级科技创新平台的通知",
        },
        {
            "number": 2,
            "policyId": "5f8345a17a6e0520bc594f4309849eb8bdfb",
            "title": "西藏自治区发展和改革委员会关于印发《西藏自治区固定资产投资项目节能审查和碳排放评价实施办法》的通知",
        },
        {
            "number": 3,
            "policyId": "e697899c936baab42b729a33f7cfabc2bdfb",
            "title": "拉萨市人民政府国有资产监督管理委员会关于新时代中央企业高质量推进品牌建设的意见",
        },
    ]

    def test_full_detail_extraction_uses_kuan_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            adapter = PkulawAdapter(MagicMock(), JsonLineLogger(Path(directory) / "engine.jsonl"))
            adapter.selector_profile = load_selector_profile(
                Path(__file__).parents[1]
                / "pkulaw_engine"
                / "sources"
                / "profiles"
                / "pkulaw-v6.json"
            )
            adapter.selectors = dict(adapter.selector_profile.selectors)
            tab = MagicMock()
            tab.run_js.return_value = {
                "title": "完整法规",
                "body": _fulltext_fixture_body(),
                "url": "https://www.pkulaw.com/lar/detail.html",
                "downloadAvailable": True,
            }
            with (
                patch.object(
                    adapter,
                    "_read_access_state",
                    return_value={"captcha": False},
                ),
                patch.object(adapter, "_raise_for_access"),
            ):
                adapter._read_detail(tab)

        detail_script = str(tab.run_js.call_args.args[0])
        self.assertIn(".kuan-content", detail_script)
        self.assertIn("p.navzhang", detail_script)
        self.assertIn("table", detail_script)
        self.assertIn("Node.TEXT_NODE", detail_script)
        self.assertIn("textContent", detail_script)
        self.assertIn("bodySegments.join", detail_script)

    def test_real_doc_requires_bundled_offline_converter(self) -> None:
        from pkulaw_engine.processors.documents import (
            DocumentExtractionError,
            extract_text,
        )

        sample = next(REAL_WORD_ROOT.glob("*.doc"))
        with (
            patch(
                "pkulaw_engine.processors.documents.shutil.which",
                return_value=None,
            ),
            patch.dict(
                sys.modules,
                {
                    "win32com": None,
                    "win32com.client": None,
                    "pythoncom": None,
                },
            ),
        ):
            try:
                text, method = extract_text(sample)
            except DocumentExtractionError as exc:
                self.fail(f"发布环境缺少内置 DOC 转换器：{exc}")

        self.assertIn("第一条", text)
        self.assertIn("bundled", method)


    def test_native_catalog_xls_contract(self) -> None:
        resolve_catalog_format = _require(
            _optional_export(
                "pkulaw_engine.catalog_formats",
                "resolve_catalog_format",
            ),
            "CatalogFormatContract 必须区分网站原生 XLS",
        )
        contract = resolve_catalog_format("xls")

        self.assertTrue(contract.native)
        self.assertEqual(contract.requested, "native-xls")
        self.assertEqual(contract.source_suffixes, (".xls",))
        self.assertEqual(contract.final_suffix, ".xls")
        self.assertEqual(contract.final_format, "xls")

    def test_native_xls_output_is_validated_source_copy(self) -> None:
        prepare_catalog_output = _require(
            _optional_export(
                "pkulaw_engine.catalog_formats",
                "prepare_catalog_output",
            ),
            "原生 XLS 输出必须保留网站原件并完成身份校验",
        )
        source = REAL_XLS_ROOT / "碳中和_2026_西藏_目录.xls"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "目录.xls"
            prepared = prepare_catalog_output(
                source,
                requested_format="xls",
                selected=self.CATALOG_SELECTED,
                destination=destination,
            )

            self.assertTrue(destination.is_file())
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertTrue(prepared.contract.native)
            self.assertFalse(prepared.converted)
            self.assertEqual(prepared.source_format, "xls")
            self.assertEqual(prepared.final_format, "xls")

    def test_xlsx_is_derived_from_native_xls(self) -> None:
        prepare_catalog_output = _require(
            _optional_export(
                "pkulaw_engine.catalog_formats",
                "prepare_catalog_output",
            ),
            "XLSX 必须由原生 XLS 本地派生",
        )
        source = REAL_XLS_ROOT / "碳中和_2026_西藏_目录.xls"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "目录.xlsx"
            prepared = prepare_catalog_output(
                source,
                requested_format="xlsx",
                selected=self.CATALOG_SELECTED,
                destination=destination,
            )

            self.assertTrue(destination.is_file())
            self.assertFalse(prepared.contract.native)
            self.assertEqual(prepared.contract.requested, "derived-xlsx")
            self.assertEqual(prepared.source_format, "xls")
            self.assertEqual(prepared.final_format, "xlsx")
            self.assertTrue(prepared.converted)
            from openpyxl import load_workbook

            workbook = load_workbook(destination, data_only=False)
            try:
                sheet = workbook.active
                rows = [
                    [cell.value for cell in row]
                    for row in sheet.iter_rows()
                ]
                header_index = next(
                    index
                    for index, row in enumerate(rows[:30])
                    if row[:2] == ["序号", "标题"]
                )
                self.assertEqual(
                    rows[header_index + 1][1],
                    self.CATALOG_SELECTED[0]["title"],
                )
            finally:
                workbook.close()

    def test_html_xls_is_compatibility_output(self) -> None:
        prepare_catalog_output = _require(
            _optional_export(
                "pkulaw_engine.catalog_formats",
                "prepare_catalog_output",
            ),
            "HTML-XLS 必须标记为兼容输出",
        )
        source = REAL_XLS_ROOT / "碳中和_2026_西藏_目录.xls"
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "目录-兼容.xls"
            prepared = prepare_catalog_output(
                source,
                requested_format="html-xls",
                selected=self.CATALOG_SELECTED,
                destination=destination,
            )

            self.assertTrue(destination.is_file())
            self.assertFalse(prepared.contract.native)
            self.assertEqual(prepared.contract.requested, "compat-html-xls")
            self.assertEqual(prepared.final_format, "html-xls")
            rendered = destination.read_text(encoding="utf-8")
            self.assertIn("<table", rendered.lower())
            self.assertIn("兼容 HTML-XLS", rendered)
            self.assertIn("<th>序号</th>", rendered)
            self.assertIn("<th>标题</th>", rendered)

    def test_catalog_xlsx_conversion_preserves_formula_text(self) -> None:
        write_xlsx_matrix = _require(
            _optional_export(
                "pkulaw_engine.catalog_formats",
                "write_xlsx_matrix",
            ),
            "XLSX 派生必须阻止公式执行",
        )
        from openpyxl import load_workbook

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "formula.xlsx"
            write_xlsx_matrix(
                [
                    ["序号", "标题", "备注"],
                    [1, "测试政策", "=HYPERLINK(\"https://example.test\")"],
                    [2, "另一政策", "\t+SUM(1,2)"],
                ],
                destination,
            )
            workbook = load_workbook(destination, data_only=False)
            try:
                sheet = workbook.active
                self.assertEqual(sheet.cell(2, 3).value, "=HYPERLINK(\"https://example.test\")")
                self.assertEqual(sheet.cell(2, 3).data_type, "s")
                self.assertEqual(sheet.cell(3, 3).value, "\t+SUM(1,2)")
                self.assertEqual(sheet.cell(3, 3).data_type, "s")
            finally:
                workbook.close()

    def test_non_biff_catalog_source_fails_closed(self) -> None:
        prepare_catalog_output = _require(
            _optional_export(
                "pkulaw_engine.catalog_formats",
                "prepare_catalog_output",
            ),
            "网站原生目录必须是 BIFF XLS",
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "伪装目录.xlsx"
            source.write_bytes(b"PK\x03\x04not-a-native-catalog")
            with self.assertRaises(RuntimeError) as context:
                prepare_catalog_output(
                    source,
                    requested_format="xls",
                    selected=[],
                    destination=Path(directory) / "目录.xls",
                )
        self.assertEqual(
            getattr(context.exception, "code", ""),
            "CATALOG_NATIVE_FORMAT_MISMATCH",
        )

    def test_corrupt_biff_catalog_reports_conversion_failure(self) -> None:
        prepare_catalog_output = _require(
            _optional_export(
                "pkulaw_engine.catalog_formats",
                "prepare_catalog_output",
            ),
            "损坏的 BIFF 目录必须返回明确转换失败",
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "损坏目录.xls"
            source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"broken")
            destination = Path(directory) / "目录.xlsx"
            destination.write_bytes(b"existing-formal-output")
            with self.assertRaises(RuntimeError) as context:
                prepare_catalog_output(
                    source,
                    requested_format="xlsx",
                    selected=[],
                    destination=destination,
                )
            self.assertEqual(
                destination.read_bytes(),
                b"existing-formal-output",
            )
        self.assertEqual(
            getattr(context.exception, "code", ""),
            "CATALOG_CONVERSION_FAILED",
        )


class BodyCompletenessContractTests(unittest.TestCase):
    def test_full_fixture_has_six_sections_and_38_continuous_clauses(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "正文完整性必须识别 6 个章节和 38 条连续条文",
        )

        result = assess(_fulltext_fixture_body())

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 6)
        self.assertEqual(result.clause_count, 38)
        self.assertEqual(result.missing_clause_numbers, [])
        self.assertTrue(result.continuous_clause_numbers)

    def test_missing_clause_is_suspicious(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "缺少条文时不得判定正文完整",
        )
        body = "\n".join(
            line
            for line in _fulltext_fixture_body().splitlines()
            if not line.startswith("第十八条")
        )

        result = assess(body)

        self.assertEqual(result.status, "suspicious")
        self.assertIn(18, result.missing_clause_numbers)
        self.assertIn("MISSING_CLAUSE_NUMBERS", result.issues)

    def test_case_compilation_reference_articles_allow_sparse_numbers(
        self,
    ) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "案例汇编中的民法典条文指引是节选引用，不得按整部法典连续编号判定缺条",
        )
        body = "\n".join(
            [
                "民法典颁布五周年典型案例",
                "（一）基本案情",
                "人民法院依法审理并作出裁判。",
                "（二）裁判结果",
                "生效裁判明确了相关民事责任。",
                "（三）典型意义",
                "本案具有示范意义。",
                "（四）民法典条文指引",
                "第一千二百二十九条 因污染环境、破坏生态造成他人损害的，侵权人应当承担侵权责任。",
                "第一千二百三十四条 违反国家规定造成生态环境损害的，依法承担修复责任。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.clause_count, 2)
        self.assertEqual(result.clause_numbers, [1229, 1234])
        self.assertNotIn("MISSING_CLAUSE_NUMBERS", result.issues)

    def test_empty_section_is_suspicious(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "章节后没有条文时必须判定可疑",
        )
        lines = _fulltext_fixture_body().splitlines()
        first = next(i for i, line in enumerate(lines) if line.startswith("第一章"))
        second = next(i for i, line in enumerate(lines) if line.startswith("第二章"))
        body = "\n".join(lines[: first + 1] + lines[second:])

        result = assess(body)

        self.assertEqual(result.status, "suspicious")
        self.assertIn("EMPTY_SECTION", result.issues)

    def test_repeated_chapter_outline_is_not_an_empty_body_section(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "法规目录中的连续章节标题不得被当成正文空章节",
        )
        body = "\n".join(
            [
                "第一章 总则",
                "第二章 组织与职责",
                "第三章 附则",
                "第一章 总则",
                "第一条 【目的】为了规范测试工作，制定本规定。",
                "第二条 【适用范围】本规定适用于正文完整性测试。",
                "第二章 组织与职责",
                "第三条 【职责】主管部门负责组织实施。",
                "第三章 附则",
                "第四条 【施行】本规定自发布之日起施行。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 3)
        self.assertEqual(result.clause_count, 4)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_toc_outline_without_clauses_is_not_empty_sections(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "无条文编号的规划正文也必须去除目录章节",
        )
        body = "\n".join(
            [
                "目录",
                "第一章 发展环境",
                "第二章 指导思想",
                "第三章 主要目标",
                "第一章 发展环境",
                "当前发展环境面临新的机遇和挑战。",
                "第二章 指导思想",
                "坚持高质量发展和统筹协调。",
                "第三章 主要目标",
                "到规划期末形成完整的发展体系。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 3)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_toc_with_compact_first_heading_is_removed(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "目录第一章节号后没有空格时仍须识别并移除整个目录",
        )
        body = "\n".join(
            [
                "目 录",
                "第一章“十四五”时期发展成效",
                "第二章 发展环境",
                "第三章 总体要求",
                "第一章 “十四五”时期发展成效",
                "全县经济社会发展取得显著成效。",
                "第二章 发展环境",
                "当前发展环境面临新的机遇和挑战。",
                "第三章 总体要求",
                "坚持高质量发展和统筹协调。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 3)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_toc_marker_embedded_in_heading_is_removed(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "目录标记与规划纲要标题位于同一行时仍须识别",
        )
        body = "\n".join(
            [
                "苍梧县国民经济和社会发展第十五个五年规划纲要目 录",
                "第一篇 发展基础",
                "第一章 发展成效",
                "第二章 发展环境",
                "第三章 总体要求",
                "第一篇 发展基础",
                "第一章 发展成效",
                "全县综合实力持续提升。",
                "第二章 发展环境",
                "发展环境面临新的机遇。",
                "第三章 总体要求",
                "坚持高质量发展。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 3)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_embedded_toc_marker_split_across_lines_is_removed(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "目录标记嵌在标题末尾且被拆成两行时仍须识别",
        )
        body = "\n".join(
            [
                "苍梧县第十五个五年规划纲要目",
                "录",
                "第一章 发展成效",
                "第二章 发展环境",
                "第三章 总体要求",
                "第一章 发展成效",
                "全县综合实力持续提升。",
                "第二章 发展环境",
                "发展环境面临新的机遇。",
                "第三章 总体要求",
                "坚持高质量发展。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 3)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_toc_with_per_part_chapter_reset_uses_repeated_preface(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "多篇规划重复使用第一至三章时不得把第二篇目录误当成正文起点",
        )
        body = "\n".join(
            [
                "目 录",
                "前 言",
                "第一篇 发展基础",
                "第一章 发展成效",
                "第二章 发展环境",
                "第三章 总体要求",
                "第二篇 重点任务",
                "第一章 产业升级",
                "第二章 城乡融合",
                "第三章 生态保护",
                "前 言",
                "规划实施进入新的阶段。",
                "第一篇 发展基础",
                "第一章 发展成效",
                "全县综合实力持续提升。",
                "第二章 发展环境",
                "发展环境面临新的机遇。",
                "第三章 总体要求",
                "坚持高质量发展。",
                "第二篇 重点任务",
                "第一章 产业升级",
                "加快产业转型升级。",
                "第二章 城乡融合",
                "推进城乡协调发展。",
                "第三章 生态保护",
                "加强生态环境保护。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 6)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_toc_with_per_part_reset_uses_longest_repeated_sequence(
        self,
    ) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "无重复前言的多篇规划必须选择最长目录重复序列作为正文起点",
        )
        body = "\n".join(
            [
                "目 录",
                "第一篇 发展基础",
                "第一章 发展成效",
                "第二章 发展环境",
                "第三章 总体要求",
                "第二篇 重点任务",
                "第一章 产业升级",
                "第二章 城乡融合",
                "第三章 生态保护",
                "第一篇 发展基础",
                "第一章 发展成效",
                "全县综合实力持续提升。",
                "第二章 发展环境",
                "发展环境面临新的机遇。",
                "第三章 总体要求",
                "坚持高质量发展。",
                "第二篇 重点任务",
                "第一章 产业升级",
                "加快产业转型升级。",
                "第二章 城乡融合",
                "推进城乡协调发展。",
                "第三章 生态保护",
                "加强生态环境保护。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 6)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_split_toc_text_nodes_do_not_become_empty_sections(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "真实页面拆分的“目录”文本节点不得破坏目录识别",
        )
        body = "\n".join(
            [
                "目",
                "录",
                "第一编 总则",
                "第一章 目的",
                "第二章 适用",
                "第三章 结构",
                "第二编 实施",
                "第一章 执行",
                "第二章 监督",
                "第三章 保障",
                "第一编 总则",
                "第一章 目的",
                "第一条",
                "为了规范实施，制定本法。",
                "第二章 适用",
                "第二条",
                "本法适用于实施活动。",
                "第三章 结构",
                "第三条",
                "结构应当完整。",
                "第二编 实施",
                "第一章 执行",
                "第四条",
                "主管部门负责执行。",
                "第二章 监督",
                "第五条",
                "有关方面依法开展监督。",
                "第三章 保障",
                "第六条",
                "提供必要保障。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 6)
        self.assertEqual(result.clause_count, 6)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_multi_part_toc_outline_is_removed_before_clauses(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "多编多分编目录不得被当作空正文章节",
        )
        body = "\n".join(
            [
                "目录",
                "第一编 总则",
                "第一章 目的",
                "第二章 适用",
                "第二编 实施",
                "第一章 执行",
                "第二章 监督",
                "第一编 总则",
                "第一章 目的",
                "第一条",
                "为了规范实施，制定本法。",
                "第二章 适用",
                "第二条",
                "本法适用于实施活动。",
                "第二编 实施",
                "第一章 执行",
                "第三条",
                "主管部门负责执行。",
                "第二章 监督",
                "第四条",
                "有关方面依法开展监督。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 4)
        self.assertEqual(result.clause_count, 4)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_clause_number_line_followed_by_body_is_not_empty(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "条文编号和正文分段时必须按下一行正文判断",
        )
        body = "\n".join(
            [
                "第一章 总则",
                "第一条",
                "为了规范测试工作，制定本规定。",
                "第二条",
                "本规定适用于正文完整性测试。",
                "第二章 附则",
                "第三条",
                "本规定自发布之日起施行。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.clause_count, 3)
        self.assertNotIn("EMPTY_CLAUSE_BODY", result.issues)

    def test_article_heading_without_space_is_still_a_clause(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "条文号与条文标题之间缺少空格时不得误报整条缺失",
        )
        body = "\n".join(
            [
                "第一章 燃气设施规划",
                "第一条 调峰及应急储备设施规划",
                "统筹落实季节调峰和日调峰责任。",
                "第二条燃气系统配套设施规划",
                "管道气经营企业应设置抢修维修站点。",
                "第三条 燃气设施更新改造",
                "对不满足安全要求的设施逐步实施更新改造。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.clause_count, 3)
        self.assertEqual(result.missing_clause_numbers, [])

    def test_non_clause_paragraph_counts_as_section_content(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "章节中存在正文段落时不得只因没有条文编号而判空",
        )
        body = "\n".join(
            [
                "第一章 总则",
                "为了规范测试工作，制定本规定。",
                "第二章 工作要求",
                "有关部门应当按照职责做好组织实施和监督检查工作。",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.section_count, 2)
        self.assertNotIn("EMPTY_SECTION", result.issues)

    def test_detail_body_extraction_ignores_interaction_annotations(self) -> None:
        build_script = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "detail_body_extraction_js",
            ),
            "正文提取必须排除本条变迁和法宝AI等交互标注",
        )

        script = build_script("#divFullText")

        self.assertIn(".TiaoYinV2", script)
        self.assertIn(".fbWindow-btn", script)
        self.assertIn("button", script)

    def test_inline_search_highlight_does_not_split_heading(self) -> None:
        build_script = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "detail_body_extraction_js",
            ),
            "正文提取必须保留内联命中高亮所在段落的连续文本",
        )
        node_executable = shutil.which("node")
        self.assertIsNotNone(node_executable, "Node.js 用于运行真实正文提取脚本")
        harness = """
        const Node = { TEXT_NODE: 3, ELEMENT_NODE: 1 };
        const text = value => ({ nodeType: 3, textContent: value });
        const element = (children, matches = () => false) => {
          const node = {
            nodeType: 1,
            childNodes: children,
            matches,
            querySelectorAll: () => []
          };
          Object.defineProperty(node, "textContent", {
            get: () => children.map(child => child.textContent || "").join("")
          });
          return node;
        };
        const mockRoot = element([]);
        const highlighted = element([text("碳中和")]);
        const heading = element([
          text("第二节 减缓气候变化与碳达峰"),
          highlighted
        ]);
        mockRoot.childNodes = [heading];
        const query = () => mockRoot;
        const clean = value => (value || "").replace(/\\s+/g, " ").trim();
        """
        script = (
            harness
            + build_script("#divFullText")
            + "\nconsole.log(JSON.stringify({ bodySegments, body }));\n"
        )
        completed = subprocess.run(
            [str(node_executable), "-"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
            encoding="utf-8",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        extracted = json.loads(completed.stdout)
        self.assertEqual(
            extracted["bodySegments"],
            ["第二节 减缓气候变化与碳达峰碳中和"],
        )
        self.assertEqual(
            extracted["body"],
            "第二节 减缓气候变化与碳达峰碳中和",
        )

    def test_truncated_clause_is_suspicious(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "末尾条文没有正文时不得判定完整",
        )
        lines = _fulltext_fixture_body().splitlines()
        lines[-1] = "第三十八条"

        result = assess("\n".join(lines))

        self.assertEqual(result.status, "suspicious")
        self.assertIn("TRUNCATED_TAIL", result.issues)

    def test_table_placeholder_ellipsis_is_not_truncated_tail(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "绩效目标表中用于填写的省略号不得被当成正文截断",
        )
        body = "\n".join(
            [
                "项目绩效目标申报表",
                "绩效指标 | 一级指标 | 二级指标 | 三级指标 | 指标值",
                "产出指标 | 数量指标 | 矿山生态修复面积（公顷） | XX",
                "修复废弃矿山（矿点）数量（个） | XX",
                "…",
                "质量指标 | 工程质量合格率（%） | ≥XX",
                "…",
                "满意度 | 服务对象满意度指标 | …",
                "…",
            ]
        )

        result = assess(body)

        self.assertEqual(result.status, "complete")
        self.assertNotIn("TRUNCATED_TAIL", result.issues)

    def test_prose_ellipsis_still_marks_truncated_tail(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "正文句子以省略号结束时仍应失败关闭",
        )

        result = assess(
            "现就有关事项通知如下。\n后续事项将在另行印发……"
        )

        self.assertEqual(result.status, "suspicious")
        self.assertIn("TRUNCATED_TAIL", result.issues)

    def test_empty_body_fails_and_plain_paragraphs_can_be_complete(self) -> None:
        assess = _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "assess_body_completeness",
            ),
            "正文完整性必须区分空正文和普通段落",
        )

        self.assertEqual(assess("").status, "failed")
        paragraphs = (
            "这是没有章节和条文编号的政策通知正文。\n"
            "正文内容完整，包含工作要求、联系方式和发布日期。"
        )
        self.assertEqual(assess(paragraphs).status, "complete")

    def test_detail_read_returns_body_completeness(self) -> None:
        assess_error = _optional_export(
            "pkulaw_engine.body_quality",
            "BodyCompletenessError",
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = PkulawAdapter(
                MagicMock(),
                JsonLineLogger(Path(directory) / "engine.jsonl"),
            )
            adapter.selector_profile = load_selector_profile(
                Path(__file__).parents[1]
                / "pkulaw_engine"
                / "sources"
                / "profiles"
                / "pkulaw-v6.json"
            )
            adapter.selectors = dict(adapter.selector_profile.selectors)
            tab = MagicMock()
            tab.run_js.return_value = {
                "title": "完整法规",
                "body": _fulltext_fixture_body(),
                "url": "https://www.pkulaw.com/lar/detail.html",
                "downloadAvailable": True,
            }
            with (
                patch.object(
                    adapter,
                    "_read_access_state",
                    return_value={"captcha": False},
                ),
                patch.object(adapter, "_raise_for_access"),
            ):
                details = adapter._read_detail(tab)

        self.assertEqual(details["bodyCompleteness"]["status"], "complete")
        self.assertEqual(details["bodyCompleteness"]["sectionCount"], 6)
        self.assertEqual(details["bodyCompleteness"]["clauseCount"], 38)
        self.assertIsNotNone(assess_error)

    def test_detail_read_rejects_suspicious_body(self) -> None:
        _require(
            _optional_export(
                "pkulaw_engine.body_quality",
                "BodyCompletenessError",
            ),
            "可疑正文必须返回 BODY_INCOMPLETE",
        )
        body = "\n".join(
            line
            for line in _fulltext_fixture_body().splitlines()
            if not line.startswith("第十八条")
        )
        with tempfile.TemporaryDirectory() as directory:
            adapter = PkulawAdapter(
                MagicMock(),
                JsonLineLogger(Path(directory) / "engine.jsonl"),
            )
            adapter.selector_profile = load_selector_profile(
                Path(__file__).parents[1]
                / "pkulaw_engine"
                / "sources"
                / "profiles"
                / "pkulaw-v6.json"
            )
            adapter.selectors = dict(adapter.selector_profile.selectors)
            tab = MagicMock()
            tab.run_js.return_value = {
                "title": "缺条法规",
                "body": body,
                "url": "https://www.pkulaw.com/lar/detail.html",
                "downloadAvailable": True,
            }
            with (
                patch.object(
                    adapter,
                    "_read_access_state",
                    return_value={"captcha": False},
                ),
                patch.object(adapter, "_raise_for_access"),
            ):
                with self.assertRaises(RuntimeError) as context:
                    adapter._read_detail(tab)

        self.assertEqual(getattr(context.exception, "code", ""), "BODY_INCOMPLETE")


class ProgressAndSourceContractTests(unittest.TestCase):
    def test_partial_processing_progress_is_not_complete(self) -> None:
        build_progress_snapshot = _require(
            _optional_export(
                "pkulaw_engine.progress",
                "build_progress_snapshot",
            ),
            "partial 0/3 不得产生 100% 进度",
        )

        snapshot = build_progress_snapshot(
            stage="partial",
            completed_units=3,
            total_units=3,
            succeeded=0,
            failed=0,
            pending_review=3,
        )

        self.assertLess(snapshot.overall, 1.0)
        self.assertEqual(snapshot.pending_review, 3)

    def test_batch_extraction_failure_keeps_pkulaw_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = TaskStore(root / "pkulaw.sqlite3")
            manager = DataProcessingManager(
                root / "pkulaw.sqlite3",
                MagicMock(),
                JsonLineLogger(root / "engine.jsonl"),
                lambda *_args: None,
            )
            task = store.create_task(
                name="批量来源保留",
                mode="batch",
                task_input={"queries": ["碳中和"]},
                output_dir=str(root / "output"),
            )
            item = store.upsert_task_item(
                task["id"],
                policy_key="id:policy-1",
                title="测试政策",
                status="completed",
                source_type="北大法宝ZIP",
                payload={
                    "candidate": {
                        "policyId": "policy-1",
                        "title": "测试政策",
                        "url": "https://www.pkulaw.com/lar/policy-1.html",
                    },
                    "zipPath": str(root / "全文.zip"),
                },
            )

            document = manager._pending_document(
                store,
                task["id"],
                item,
                {
                    "code": "TEXT_EXTRACTION_FAILED",
                    "message": "旧版DOC转换失败",
                },
                root / "pending",
            )
            store.close()

        self.assertEqual(document["sourceType"], "北大法宝ZIP")
        self.assertEqual(document["retrievalMethod"], "网站原生 Word ZIP")
        self.assertEqual(document["textSourceType"], "")
        self.assertEqual(document["bodyCompleteness"], "failed")
        self.assertEqual(document["reviewStatus"], "pending_review")
        self.assertEqual(document["status"], "pending_review")


if __name__ == "__main__":
    unittest.main()
