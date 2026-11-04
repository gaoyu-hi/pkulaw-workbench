from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.sources.pkulaw import (
    ManualActionRequired,
    PkulawAdapter,
    SearchTransitionUnconfirmed,
    SelectorProfileMismatch,
    SelectorUnstable,
    YearFilterUnavailable,
)
from pkulaw_engine.sources.selector_profile import (
    SelectorProfileError,
    load_selector_profile,
)


class PageDisconnectedError(RuntimeError):
    pass


class PkulawAdapterDiagnosticTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.logger = JsonLineLogger(self.root / "engine.jsonl")
        self.bridge = MagicMock()
        self.tab = MagicMock()
        transaction = MagicMock()
        transaction.__enter__.return_value = self.tab
        self.bridge.transaction.return_value = transaction
        self.profile_path = (
            Path(__file__).parents[1]
            / "pkulaw_engine"
            / "sources"
            / "profiles"
            / "pkulaw-v6.json"
        )
        self.adapter = PkulawAdapter(
            self.bridge,
            self.logger,
            selector_path=self.profile_path,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_explicit_selector_path_and_diagnostic_fallbacks(self) -> None:
        self.assertEqual(
            self.adapter.selector_profile.profile_id,
            "pkulaw-v6",
        )
        with patch(
            "pkulaw_engine.sources.pkulaw.detect_selector_profile",
            side_effect=SelectorProfileError("unknown"),
        ):
            result = self.adapter._diagnose_current_page(self.tab)
        self.assertFalse(result["detected"])
        self.assertEqual(result["missingControls"], ["profile"])

        diagnostic = {
            "profileId": "pkulaw-v6",
            "version": "v1",
            "detected": True,
            "ready": True,
            "pageKind": "search",
            "pageReady": True,
            "missingControls": [],
            "pageMissingControls": [],
            "controls": {},
            "groups": {},
            "detectionEvidence": [],
            "message": "ok",
        }
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.detect_selector_profile",
                return_value=(None, []),
            ) as detect,
            patch(
                "pkulaw_engine.sources.pkulaw.diagnose_selector_profile",
                return_value=diagnostic,
            ) as diagnose,
        ):
            result = self.adapter._diagnose_current_page(self.tab)
        self.assertTrue(result["ready"])
        detect.assert_called_once()
        self.assertEqual(
            diagnose.call_args.kwargs["detection_evidence"],
            ["explicit-selector-path"],
        )

    def test_require_selector_and_operation_gates(self) -> None:
        with patch.object(
            self.adapter,
            "_diagnose_current_page",
            return_value={
                "ready": False,
                "missingControls": ["searchInput"],
            },
        ):
            with self.assertRaises(SelectorProfileMismatch):
                self.adapter._require_selector_ready(self.tab)

        with patch.object(
            self.adapter,
            "_diagnose_current_page",
            return_value={"ready": True},
        ):
            self.assertEqual(
                self.adapter._require_selector_ready(self.tab).profile_id,
                "pkulaw-v6",
            )

        success = {
            "profileId": "pkulaw-v6",
            "version": "v1",
            "stabilitySamples": 2,
            "operationControls": ["searchInput"],
        }
        with patch(
            "pkulaw_engine.sources.pkulaw.diagnose_for_operation",
            return_value=success,
        ):
            self.assertEqual(
                self.adapter._ensure_operation_ready("search")["version"],
                "v1",
            )

        with patch(
            "pkulaw_engine.sources.pkulaw.diagnose_for_operation",
            side_effect=SelectorProfileError(
                "unstable",
                code="PAGE_SELECTOR_UNSTABLE",
            ),
        ):
            with self.assertRaises(SelectorUnstable):
                self.adapter._ensure_operation_ready("search")

        with patch(
            "pkulaw_engine.sources.pkulaw.diagnose_for_operation",
            side_effect=SelectorProfileError("missing"),
        ):
            with self.assertRaises(SelectorProfileMismatch):
                self.adapter._ensure_operation_ready("search")

    def test_selector_diagnostics_waits_for_transient_results_controls(
        self,
    ) -> None:
        missing = {
            "profileId": "pkulaw-v6",
            "version": "v1",
            "detected": True,
            "ready": False,
            "pageKind": "results",
            "pageReady": False,
            "missingControls": [
                "resultLink",
                "resultNumber",
                "batchCheckbox",
                "resultCount",
            ],
            "controls": {
                "resultsRoot": {"ok": True},
                "resultLink": {"ok": False},
                "resultNumber": {"ok": False},
                "batchCheckbox": {"ok": False},
                "batchSort": {"ok": False},
                "resultCount": {"ok": False},
            },
            "message": "结果列表尚未渲染",
        }
        ready = {
            "profileId": "pkulaw-v6",
            "version": "v1",
            "detected": True,
            "ready": True,
            "pageKind": "results",
            "pageReady": True,
            "missingControls": [],
            "controls": {
                "resultsRoot": {"ok": True},
                "resultLink": {"ok": True},
                "resultNumber": {"ok": True},
                "batchCheckbox": {"ok": True},
                "batchSort": {"ok": True},
                "resultCount": {"ok": True},
            },
            "message": "选择器诊断通过",
        }
        with patch.object(
            self.adapter,
            "diagnose_current_page",
            side_effect=[missing, missing, ready, ready],
        ):
            result = self.adapter.diagnose_current_page_stable(
                timeout_seconds=1.0
            )
        self.assertTrue(result["ready"])
        self.assertTrue(result["operationReady"])
        self.assertGreaterEqual(result["stabilitySamples"], 2)

    def test_audit_results_and_detail_branches(self) -> None:
        diagnosis = {
            "profileId": "pkulaw-v6",
            "version": "v1",
            "detected": True,
            "ready": True,
            "pageKind": "results",
            "pageReady": True,
            "controls": {
                "downloadWord": {
                    "ok": True,
                }
            },
            "missingControls": [],
        }
        with (
            patch.object(
                self.adapter,
                "_diagnose_current_page",
                return_value=diagnosis,
            ),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={
                    "url": "results",
                    "captcha": False,
                    "login": False,
                    "loggedIn": True,
                    "quota": False,
                    "pageError": False,
                },
            ),
            patch.object(
                self.adapter,
                "_read_results",
                return_value={"rows": []},
            ),
        ):
            result = self.adapter.audit_current_page()
        self.assertEqual(result["pageKind"], "results")
        self.assertTrue(result["downloadAvailable"])

        detail_diagnosis = {
            **diagnosis,
            "pageKind": "detail",
        }
        with (
            patch.object(
                self.adapter,
                "_diagnose_current_page",
                return_value=detail_diagnosis,
            ),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={
                    "url": "detail",
                    "captcha": False,
                    "login": False,
                    "loggedIn": True,
                    "quota": False,
                    "pageError": False,
                },
            ),
            patch.object(
                self.adapter,
                "_read_detail",
                return_value={"title": "详情"},
            ),
        ):
            result = self.adapter.audit_current_page()
        self.assertEqual(result["detail"]["title"], "详情")

    def test_read_results_fills_missing_policy_id_and_marker_cleanup(self) -> None:
        self.tab.run_js.return_value = {
            "page": 1,
            "rows": [
                {
                    "title": "政策",
                    "url": "https://www.pkulaw.com/chl/policy-001.html",
                }
            ],
        }
        result = self.adapter._read_results(self.tab)
        self.assertEqual(result["rows"][0]["policyId"], "policy-001")
        result_script = str(self.tab.run_js.call_args.args[0])
        self.assertIn("nextItem.classList.contains('disabled')", result_script)

        self.tab.run_js.return_value = True
        self.adapter._clear_search_transition_marker("token")
        self.tab.run_js.side_effect = RuntimeError("disconnected")
        self.adapter._clear_search_transition_marker("token")

    def test_selector_profile_error_messages_include_missing_control(self) -> None:
        adapter = PkulawAdapter(self.bridge, self.logger)
        adapter.bridge.entry_url = ""
        with patch.object(
            adapter,
            "_diagnose_current_page",
            return_value={
                "ready": False,
                "profileId": "",
                "missingControls": ["searchInput", "searchSubmit"],
            },
        ):
            with self.assertRaisesRegex(
                SelectorProfileMismatch,
                "searchInput, searchSubmit",
            ):
                adapter._require_selector_ready(self.tab)

        self.assertEqual(
            load_selector_profile(self.profile_path).profile_id,
            "pkulaw-v6",
        )

    def test_search_page_gate_uses_cached_profile_or_fails(self) -> None:
        ready_diagnostic = {
            "profileId": "pkulaw-v6",
            "ready": True,
            "pageReady": True,
            "controls": {
                "searchInput": {"ok": True},
                "searchSubmit": {"ok": True},
                "exactMode": {"ok": True},
                "libraryRoot": {"ok": True},
            },
        }
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.detect_selector_profile",
                return_value=(None, []),
            ),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.diagnose_selector_profile",
                return_value=ready_diagnostic,
            ),
        ):
            self.adapter._ensure_search_page(self.tab)
        self.assertEqual(
            self.adapter.selector_diagnostics["profileId"],
            "pkulaw-v6",
        )

        adapter = PkulawAdapter(self.bridge, self.logger)
        adapter.bridge.entry_url = ""
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.detect_selector_profile",
                return_value=(None, []),
            ),
            patch.object(
                adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(adapter, "_raise_for_access"),
        ):
            with self.assertRaises(SelectorProfileMismatch):
                adapter._ensure_search_page(self.tab)

        with patch(
            "pkulaw_engine.sources.pkulaw.detect_selector_profile",
            side_effect=SelectorProfileError("missing"),
        ):
            with self.assertRaises(SelectorProfileMismatch):
                adapter._ensure_search_page(self.tab)

    def test_search_page_gate_navigates_and_reports_unstable(self) -> None:
        ready_diagnostic = {
            "profileId": "pkulaw-v6",
            "ready": True,
            "pageReady": True,
        }
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.detect_selector_profile",
                side_effect=[(None, []), (self.adapter.selector_profile, [])],
            ),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.diagnose_selector_profile",
                return_value={
                    "profileId": "pkulaw-v6",
                    "ready": False,
                    "pageReady": False,
                    "missingControls": ["searchInput"],
                },
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.diagnose_for_operation",
                side_effect=SelectorProfileError(
                    "unstable",
                    code="PAGE_SELECTOR_UNSTABLE",
                ),
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.adapter.bridge.entry_url = "https://www.pkulaw.com/"
            self.tab.url = "https://www.pkulaw.com/"
            with self.assertRaises(SelectorUnstable):
                self.adapter._ensure_search_page(self.tab)

    def test_search_page_gate_recovers_after_entry_navigation(self) -> None:
        profile = self.adapter.profiles[0]
        self.adapter.selector_profile = None
        self.adapter.bridge.entry_url = "https://entry.example.test/"
        self.tab.url = "https://old.example.test/"
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.detect_selector_profile",
                side_effect=[
                    (None, []),
                    (profile, ["post-navigation"]),
                ],
            ),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.diagnose_selector_profile",
                return_value={
                    "profileId": profile.profile_id,
                    "ready": True,
                    "pageReady": True,
                    "controls": {
                        "searchInput": {"ok": True},
                        "searchSubmit": {"ok": True},
                        "exactMode": {"ok": True},
                        "libraryRoot": {"ok": True},
                    },
                },
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.adapter._ensure_search_page(self.tab)
        self.tab.get.assert_called_with("https://entry.example.test/")

    def test_search_page_gate_reports_entry_navigation_timeout(self) -> None:
        self.adapter.selector_profile = None
        self.adapter.bridge.entry_url = "https://entry.example.test/"
        self.tab.url = "https://old.example.test/"
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.detect_selector_profile",
                return_value=(None, []),
            ),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 16],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            with self.assertRaisesRegex(
                SelectorProfileMismatch,
                "机构入口页面",
            ):
                self.adapter._ensure_search_page(self.tab)

    def test_search_page_gate_uses_relative_entry_and_times_out(self) -> None:
        profile = self.adapter.profiles[0]
        self.adapter.bridge.entry_url = ""
        self.tab.url = "https://example.test/root"
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.detect_selector_profile",
                return_value=(profile, ["current-page"]),
            ),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.diagnose_selector_profile",
                return_value={
                    "profileId": profile.profile_id,
                    "ready": False,
                    "pageReady": False,
                    "missingControls": ["searchInput"],
                },
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.diagnose_for_operation",
                side_effect=SelectorProfileError("missing"),
            ),
            patch.object(
                self.adapter,
                "_diagnose_current_page",
                return_value={"pageReady": False},
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 16],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            with self.assertRaisesRegex(
                SelectorProfileMismatch,
                "页面结构可能已变化",
            ):
                self.adapter._ensure_search_page(self.tab)
        self.tab.get.assert_called_once()

    def test_wait_for_results_stable_transition_and_timeout(self) -> None:
        state = {
            "url": "https://www.pkulaw.com/chl/search",
            "page": 1,
            "rows": [{"policyId": "p1"}],
        }
        with (
            patch.object(
                self.adapter,
                "_read_results",
                return_value=state,
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            result = self.adapter._wait_for_results(2)
        self.assertEqual(result["page"], 1)

        with (
            patch.object(
                self.adapter,
                "_read_results",
                side_effect=PageDisconnectedError("disconnected"),
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 2],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            with self.assertRaises(ManualActionRequired):
                self.adapter._wait_for_results(1)

    def test_search_rejects_mode_and_unconfirmed_submission(self) -> None:
        with self.assertRaisesRegex(Exception, "match_mode"):
            self.adapter.search(
                keyword="x",
                match_mode="bad",
                library="central",
                year="全部年份",
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                page_size=20,
            )

        with (
            patch.object(self.adapter, "_ensure_search_page"),
            patch.object(
                self.adapter,
                "_ensure_operation_ready",
                return_value={},
            ),
            patch.object(self.adapter, "_ensure_library"),
            patch.object(
                self.adapter,
                "_mark_search_transition",
                return_value={},
            ),
            patch.object(
                self.adapter,
                "_submit_search",
                return_value={"ok": False, "code": "OTHER"},
            ),
        ):
            with self.assertRaises(SearchTransitionUnconfirmed):
                self.adapter.search(
                    keyword="x",
                    library="central",
                    year="2024",
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=20,
                )

        with (
            patch.object(self.adapter, "_ensure_search_page"),
            patch.object(
                self.adapter,
                "_ensure_operation_ready",
                return_value={},
            ),
            patch.object(self.adapter, "_ensure_library"),
            patch.object(
                self.adapter,
                "_mark_search_transition",
                return_value={},
            ),
            patch.object(
                self.adapter,
                "_submit_search",
                return_value={
                    "ok": False,
                    "code": "YEAR_FILTER_UNAVAILABLE",
                },
            ),
        ):
            with self.assertRaises(YearFilterUnavailable):
                self.adapter.search(
                    keyword="x",
                    library="central",
                    year="1999",
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    page_size=20,
                )


if __name__ == "__main__":
    unittest.main()
