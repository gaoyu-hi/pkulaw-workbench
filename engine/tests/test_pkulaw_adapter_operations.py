from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.sources.pkulaw import (
    DetailPageUnavailable,
    DownloadUnclear,
    FilterNotApplied,
    InstitutionQuotaReached,
    LoginRequired,
    ManualActionRequired,
    PageSizeNotApplied,
    PageStructureChanged,
    PkulawAdapter,
    ResultGroupingUnavailable,
    SearchTransitionUnconfirmed,
    SelectorProfileMismatch,
    YearFilterUnavailable,
    _is_page_context_transition,
)
from pkulaw_engine.sources.search_transaction import (
    SearchRequestIdentity,
    SearchSession,
)
from pkulaw_engine.sources.selector_profile import load_selector_profiles


class PageDisconnectedError(RuntimeError):
    pass


class PkulawAdapterOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.logger = JsonLineLogger(self.root / "engine.jsonl")
        self.bridge = MagicMock()
        self.tab = MagicMock()
        transaction = MagicMock()
        transaction.__enter__.return_value = self.tab
        self.bridge.transaction.return_value = transaction
        self.adapter = PkulawAdapter(self.bridge, self.logger)
        self.adapter.selector_profile = next(
            profile
            for profile in load_selector_profiles()
            if profile.profile_id == "fixture"
        )
        self.adapter.selectors = dict(self.adapter.selector_profile.selectors)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _request(self, **changes: object) -> SearchRequestIdentity:
        request = SearchRequestIdentity(
            query="测试政策",
            match_mode="title",
            library="central",
            year="全部年份",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=1,
        )
        return replace(request, **changes)

    def _session(
        self,
        request: SearchRequestIdentity,
        **changes: object,
    ) -> SearchSession:
        session = SearchSession.from_observation(
            request,
            {
                "page": 1,
                "totalPages": 1,
                "resultCount": 1,
                "actualPageSize": request.requested_page_size,
                "groupingMode": "ungrouped",
                "actualYear": request.year,
                "actualProvince": request.province,
                "actualTimeliness": request.timeliness,
                "rows": [{"policyId": "policy-1"}],
                "url": "https://example.test/search",
            },
            search_id="search-1",
            observed_at="2026-11-01T00:00:00Z",
        )
        return replace(session, **changes)

    def test_read_results_next_page_and_go_to_page(self) -> None:
        with patch.object(self.adapter, "_ensure_operation_ready"):
            with patch.object(
                self.adapter,
                "_read_results",
                return_value={"page": 2, "totalPages": 3, "rows": []},
            ):
                self.assertEqual(self.adapter.read_results()["page"], 2)
                self.assertEqual(self.adapter.next_page(2)["page"], 2)
                self.assertEqual(self.adapter.go_to_page(2)["page"], 2)

            with patch.object(
                self.adapter,
                "_read_results",
                return_value={"page": 2, "totalPages": 3, "rows": []},
            ):
                with self.assertRaises(PageStructureChanged):
                    self.adapter.go_to_page(1)

            with (
                patch.object(
                    self.adapter,
                    "read_results",
                    return_value={
                        "page": 1,
                        "totalPages": 3,
                        "rows": [],
                    },
                ),
                patch.object(
                    self.adapter,
                    "next_page",
                    side_effect=[
                        {"page": 2, "totalPages": 3},
                        {"page": 3, "totalPages": 3},
                    ],
                ),
            ):
                self.assertEqual(self.adapter.go_to_page(3)["page"], 3)

    def test_selector_gate_and_next_page_recovery_paths(self) -> None:
        with (
            patch.object(
                self.adapter,
                "_diagnose_current_page",
                return_value={"ready": True},
            ),
            patch.object(self.adapter, "selector_profile", None),
        ):
            with self.assertRaises(SelectorProfileMismatch):
                self.adapter._require_selector_ready(self.tab)

        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_results",
                side_effect=[
                    {"page": 1, "totalPages": 3, "rows": [{"policyId": "p1"}]},
                    PageDisconnectedError("page transition"),
                    {"page": 1, "totalPages": 3, "rows": [{"policyId": "p1"}]},
                    {"page": 2, "totalPages": 3, "rows": [{"policyId": "p2"}]},
                    {"page": 2, "totalPages": 3, "rows": [{"policyId": "p2"}]},
                ],
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 0, 0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.return_value = {"ok": True}
            self.assertEqual(self.adapter.next_page(2)["page"], 2)

        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_results",
                side_effect=[
                    {"page": 1, "totalPages": 3, "rows": [{"policyId": "p1"}]},
                    ValueError("unexpected page failure"),
                ],
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.return_value = {"ok": True}
            with self.assertRaisesRegex(ValueError, "unexpected page failure"):
                self.adapter.next_page(2)

    def test_next_page_success_and_failure(self) -> None:
        with patch.object(self.adapter, "_ensure_operation_ready"):
            self.tab.run_js.return_value = {
                "ok": False,
                "reason": "unavailable",
            }
            with self.assertRaises(PageStructureChanged):
                self.adapter.next_page(2)

            self.tab.run_js.return_value = {"ok": True}
            with (
                patch.object(
                    self.adapter,
                    "_read_results",
                    side_effect=[
                        {
                            "page": 1,
                            "totalPages": 2,
                            "rows": [{"policyId": "p1"}],
                        },
                        {
                            "page": 2,
                            "totalPages": 2,
                            "rows": [{"policyId": "p2"}],
                        },
                        {
                            "page": 2,
                            "totalPages": 2,
                            "rows": [{"policyId": "p2"}],
                        },
                    ],
                ),
                patch(
                    "pkulaw_engine.sources.pkulaw.time.monotonic",
                    side_effect=[0, 0, 0],
                ),
                patch("pkulaw_engine.sources.pkulaw.time.sleep"),
            ):
                self.assertEqual(self.adapter.next_page(2)["page"], 2)

            with (
                patch.object(
                    self.adapter,
                    "_read_results",
                    return_value={
                        "page": 4,
                        "totalPages": 5,
                        "rows": [{"policyId": "p4"}],
                    },
                ),
                patch(
                    "pkulaw_engine.sources.pkulaw.time.monotonic",
                    side_effect=range(100),
                ),
                patch("pkulaw_engine.sources.pkulaw.time.sleep"),
            ):
                self.tab.run_js.return_value = {"ok": True}
                with self.assertRaises(ManualActionRequired):
                    self.adapter.next_page(5)

    def test_page_window_extension_reloads_stale_matching_page_number(
        self,
    ) -> None:
        request = self._request(library="local")
        session = self._session(
            request,
            page=20,
            total_pages=173,
            reported_total_pages=20,
            result_count=17224,
            row_count=1,
        )
        self.adapter.current_search_request = request
        self.adapter.current_search_session = session
        self.adapter.search_url = session.url
        stale = {
            "page": 21,
            "totalPages": 20,
            "resultCount": 17224,
            "actualPageSize": 1,
            "groupingMode": "ungrouped",
            "actualYear": request.year,
            "actualProvince": request.province,
            "actualTimeliness": request.timeliness,
            "rows": [{"policyId": "policy-20"}],
            "url": "https://example.test/search?page=21",
        }
        extended = {
            **stale,
            "totalPages": 173,
            "rows": [{"policyId": "policy-21"}],
        }
        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_results",
                side_effect=[stale, extended, extended],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = [
                {"ok": False, "reason": "next page unavailable"},
                {"ok": True},
            ]

            result = self.adapter.next_page(21)

        self.assertEqual(result["page"], 21)
        self.assertEqual(result["rows"], [{"policyId": "policy-21"}])
        self.assertEqual(self.adapter.current_search_session.page, 21)
        scripts = [str(call.args[0]) for call in self.tab.run_js.call_args_list]
        self.assertEqual(
            sum("right_form.requestSubmit()" in script for script in scripts),
            1,
        )

    def test_batch_selection_and_verification(self) -> None:
        batch = [
            {
                "number": 1,
                "policyId": "p1",
                "title": "政策一",
            }
        ]
        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
        ):
            self.tab.run_js.return_value = {
                "ok": True,
                "actual": ["p1"],
            }
            self.assertTrue(self.adapter.select_batch(batch)["ok"])

            self.tab.run_js.return_value = {
                "ok": False,
                "reason": "mismatch",
            }
            with self.assertRaises(PageStructureChanged):
                self.adapter.select_batch(batch)

            self.tab.run_js.return_value = {
                "ok": True,
                "checked": ["p1"],
                "expectedCount": 1,
                "actualCount": 1,
                "page": 1,
            }
            with patch.object(
                self.adapter,
                "_read_results",
                return_value={"page": 1},
            ):
                self.assertTrue(
                    self.adapter.verify_batch_selection(batch)["ok"]
                )

            self.tab.run_js.return_value = {
                "ok": False,
                "reason": "mismatch",
            }
            with patch.object(
                self.adapter,
                "_read_results",
                return_value={"page": 1},
            ):
                with self.assertRaises(PageStructureChanged):
                    self.adapter.verify_batch_selection(batch)

    def test_batch_verification_accepts_expected_subset_of_full_page(
        self,
    ) -> None:
        batch = [
            {
                "number": number,
                "policyId": f"p{number}",
                "title": f"政策{number}",
            }
            for number in range(1, 51)
        ]
        matches = [
            {
                "number": number,
                "id": f"p{number}",
                "title": f"政策{number}",
                "checked": number <= 50,
            }
            for number in range(1, 101)
        ]
        checked = [f"p{number}" for number in range(1, 51)]
        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch.object(
                self.adapter,
                "_read_results",
                return_value={"page": 1},
            ),
        ):
            self.tab.run_js.return_value = {
                "ok": False,
                "matches": matches,
                "checked": checked,
                "expectedCount": 50,
                "actualCount": 100,
                "page": 1,
            }

            result = self.adapter.verify_batch_selection(batch)

        self.assertTrue(result["ok"])
        self.assertEqual(result["expectedCount"], 50)
        self.assertEqual(result["actualCount"], 100)

    def test_batch_selection_reapplies_after_delayed_checkbox_revert(
        self,
    ) -> None:
        batch = [
            {
                "number": 1,
                "policyId": "p1",
                "title": "政策一",
            }
        ]
        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = [
                {"ok": True, "actual": ["p1"]},
                {"ok": False, "actual": ["p2"]},
                {"ok": True, "actual": ["p1"]},
                *[
                    {"ok": True, "actual": ["p1"]}
                    for _ in range(12)
                ],
            ]

            result = self.adapter.select_batch(batch)

        self.assertTrue(result["ok"])
        self.assertEqual(result["actual"], ["p1"])
        self.assertEqual(result["stableSamples"], 12)

    def test_download_batch_file_success_and_failures(self) -> None:
        with self.assertRaisesRegex(Exception, "kind"):
            self.adapter.download_batch_file(kind="bad")

        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = [
                {"ok": True},
                {"dialog": True, "radios": 2},
                {"ok": True, "checked": ["catalogExcel"]},
            ]
            result = self.adapter.download_batch_file(kind="catalog")
            self.assertTrue(result["ok"])

            self.tab.run_js.side_effect = [
                {"ok": False, "reason": "missing button"},
            ]
            with self.assertRaises(PageStructureChanged):
                self.adapter.download_batch_file(kind="catalog")

            self.tab.run_js.side_effect = [
                {"ok": True},
                {"dialog": True, "radios": 2},
                {"ok": False, "reason": "submit"},
            ]
            with self.assertRaises(DownloadUnclear):
                self.adapter.download_batch_file(kind="zip")

        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 21],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = [
                {"ok": True},
                {"dialog": False, "radios": 0},
            ]
            with self.assertRaisesRegex(ManualActionRequired, "弹窗未出现"):
                self.adapter.download_batch_file(kind="catalog")

    def test_catalog_download_always_requests_native_xls(self) -> None:
        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = [
                {"ok": True},
                {"dialog": True, "radios": 2},
                {"ok": True, "checked": ["catalogExcel"]},
            ]

            result = self.adapter.download_batch_file(
                kind="catalog",
                catalog_format="xlsx",
            )

        self.assertEqual(result["catalogFormat"], "xls")
        submit_script = str(self.tab.run_js.call_args_list[-1].args[0])
        self.assertIn('catalogInput.value = "xls"', submit_script)

    def test_batch_zip_requests_observe_cooldown(self) -> None:
        self.adapter._last_batch_zip_request_at = 100.0
        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                return_value=110.0,
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep") as sleep,
        ):
            self.tab.run_js.side_effect = [
                {"ok": True},
                {"dialog": True, "radios": 2},
                {"ok": True, "checked": ["fullWord"]},
            ]
            result = self.adapter.download_batch_file(kind="zip")
        self.assertTrue(result["ok"])
        sleep.assert_any_call(55.0)

    def test_open_detail_read_detail_and_word_download(self) -> None:
        with self.assertRaises(PageStructureChanged):
            self.adapter.open_detail({"url": ""})

        with (
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "read_detail",
                return_value={"title": "详情"},
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = lambda script: (
                False
                if "服务器无法处理您的请求" in str(script)
                else True
            )
            self.assertEqual(
                self.adapter.open_detail(
                    {"url": "https://www.pkulaw.com/chl/p1.html"}
                )["title"],
                "详情",
            )
            self.assertEqual(
                self.adapter.read_detail()["title"],
                "详情",
            )

        with (
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
        ):
            self.tab.run_js.side_effect = None
            self.tab.run_js.return_value = {
                "title": "详情",
                "issuingAuthority": "机关",
                "documentNumber": "文号",
                "publicationDate": "2024-01-01",
                "effectiveDate": "2024-02-01",
                "validity": "现行有效",
                "effectLevel": "法律",
                "body": (
                    "这是用于验证详情页正文完整提取的测试政策内容，"
                    "长度足够并且结尾完整。"
                ),
            }
            self.assertEqual(self.adapter._read_detail(self.tab)["title"], "详情")

        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
        ):
            self.tab.run_js.return_value = {"ok": True, "method": "fullWord"}
            self.assertTrue(self.adapter.click_word_download()["ok"])
            self.tab.run_js.return_value = {"ok": False, "reason": "missing"}
            with self.assertRaises(DownloadUnclear):
                self.adapter.click_word_download()

        with (
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 21],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.return_value = False
            with self.assertRaisesRegex(ManualActionRequired, "详情页未完成加载"):
                self.adapter.open_detail(
                    {"url": "https://www.pkulaw.com/chl/p1.html"}
                )

        with (
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
        ):
            self.tab.run_js.return_value = {
                "title": "",
                "body": (
                    "这是用于验证详情页标题缺失的完整正文内容，"
                    "长度足够并且结尾完整。"
                ),
            }
            with self.assertRaisesRegex(PageStructureChanged, "标题为空"):
                self.adapter._read_detail(self.tab)

            self.tab.run_js.return_value = {"title": "详情", "body": ""}
            with self.assertRaises(RuntimeError) as context:
                self.adapter._read_detail(self.tab)
        self.assertEqual(
            getattr(context.exception, "code", ""),
            "BODY_INCOMPLETE",
        )

    def test_word_download_accepts_json_string_result(self) -> None:
        with (
            patch.object(self.adapter, "_ensure_operation_ready"),
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
        ):
            self.tab.run_js.return_value = json.dumps(
                {"ok": True, "method": "fullWord"}
            )

            result = self.adapter.click_word_download()

        self.assertEqual(result, {"ok": True, "method": "fullWord"})
        self.assertEqual(self.tab.run_js.call_count, 1)
        self.assertIn("JSON.stringify", str(self.tab.run_js.call_args.args[0]))

    def test_server_error_detail_page_fails_item_without_manual_wait(
        self,
    ) -> None:
        with (
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 21],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = lambda script: (
                "服务器无法处理您的请求" in str(script)
            )

            with self.assertRaises(DetailPageUnavailable) as context:
                self.adapter.open_detail(
                    {"url": "https://www.pkulaw.com/lar/missing.html"}
                )

        self.assertEqual(context.exception.code, "DETAIL_PAGE_UNAVAILABLE")

    def test_return_to_results_and_library_handoff(self) -> None:
        with self.assertRaises(PageStructureChanged):
            self.adapter.return_to_results()

        self.adapter.search_url = "https://www.pkulaw.com/chl/search"
        with (
            patch.object(
                self.adapter,
                "_read_access_state",
                return_value={},
            ),
            patch.object(self.adapter, "_raise_for_access"),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.adapter.return_to_results()
            self.tab.get.assert_called_with(self.adapter.search_url)

        self.tab.run_js.return_value = {
            "ok": True,
            "changed": False,
            "hiddenLibrary": "chl",
        }
        self.adapter._ensure_library("chl")

        self.tab.reset_mock()
        self.tab.run_js.side_effect = [
            {
                "ok": True,
                "changed": True,
                "hiddenLibrary": "chl",
                "hiddenValues": ["chl"],
            },
            {
                "ok": True,
                "changed": False,
                "hiddenLibrary": "lar",
                "hiddenValues": ["lar"],
            },
            {
                "ok": True,
                "changed": False,
                "hiddenLibrary": "lar",
                "hiddenValues": ["lar"],
            },
        ]
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.adapter._ensure_library("lar")
        self.assertIn(
            "setTimeout(() => link.click(), 0);",
            str(self.tab.run_js.call_args_list[0].args[0]),
        )
        self.tab.run_js.side_effect = None

        self.tab.reset_mock()
        self.adapter.selectors["libraryHomeUrl"] = (
            "https://www.pkulaw.com/law?way=listCrumbs"
        )
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 0, 0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = [
                {"ok": False, "reason": "target library missing"},
                {
                    "ok": True,
                    "changed": False,
                    "hiddenLibrary": "chl",
                },
                {
                    "ok": True,
                    "changed": False,
                    "hiddenLibrary": "chl",
                },
            ]
            self.adapter._ensure_library("chl")
        self.tab.get.assert_called_once_with(
            "https://www.pkulaw.com/law?way=listCrumbs"
        )
        self.tab.run_js.side_effect = None

        self.tab.run_js.return_value = {
            "ok": True,
            "changed": False,
            "hiddenLibrary": "lar",
        }
        with self.assertRaises(SearchTransitionUnconfirmed):
            self.adapter._ensure_library("chl")

        with (
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 9],
            )
        ):
            with self.assertRaises(SearchTransitionUnconfirmed):
                self.adapter._ensure_library("chl")

        with (
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            self.tab.run_js.side_effect = [
                PageDisconnectedError("context destroyed"),
                {
                    "ok": True,
                    "changed": False,
                    "hiddenLibrary": "chl",
                },
                {
                    "ok": True,
                    "changed": False,
                    "hiddenLibrary": "chl",
                },
            ]
            self.adapter._ensure_library("chl")

        with patch(
            "pkulaw_engine.sources.pkulaw.time.monotonic",
            side_effect=[0, 0],
        ):
            self.tab.run_js.side_effect = ValueError("unexpected")
            with self.assertRaisesRegex(ValueError, "unexpected"):
                self.adapter._ensure_library("chl")

    def test_filter_page_size_and_grouping_actions(self) -> None:
        original_filters = self.adapter.selectors.get("filters")
        self.adapter.selectors["filters"] = {}
        self.assertEqual(
            self.adapter._apply_filter_action("year", "全部年份"),
            {"ok": True, "changed": False},
        )
        self.assertEqual(
            self.adapter._apply_filter_action("year", "2024")["code"],
            "YEAR_FILTER_UNAVAILABLE",
        )
        self.adapter.selectors["filters"] = original_filters

        with patch.object(
            self.adapter,
            "_wait_for_filter_option",
        ) as wait_for_option:
            self.assertEqual(
                self.adapter._apply_filter_action(
                    "province",
                    "全部省份",
                ),
                {"ok": True, "changed": False},
            )
        wait_for_option.assert_not_called()

        with patch.object(
            self.adapter,
            "_wait_for_filter_option",
            return_value={"ok": True, "changed": False},
        ):
            self.tab.run_js.return_value = {
                "ok": True,
                "changed": True,
                "actual": "2024",
            }
            self.assertTrue(
                self.adapter._apply_filter_action("year", "2024")["ok"]
            )
        with patch.object(
            self.adapter,
            "_wait_for_filter_option",
            return_value={
                "ok": False,
                "changed": False,
                "code": "YEAR_FILTER_UNAVAILABLE",
                "reason": "missing",
            },
        ):
            with self.assertRaises(Exception):
                self.adapter._apply_filter_action("year", "2025")

        self.tab.run_js.return_value = {
            "ok": True,
            "changed": False,
            "visible": 20,
            "hidden": 20,
        }
        self.assertTrue(self.adapter._apply_page_size_action(20)["ok"])
        self.tab.run_js.return_value = {
            "ok": False,
            "reason": "missing",
        }
        with self.assertRaises(PageSizeNotApplied):
            self.adapter._apply_page_size_action(100)

        self.tab.run_js.return_value = {"ok": True, "changed": True}
        self.assertTrue(self.adapter.ensure_ungrouped_results()["ok"])
        self.tab.run_js.return_value = {"ok": False, "changed": False}
        with self.assertRaises(ResultGroupingUnavailable):
            self.adapter.ensure_ungrouped_results()

        self.adapter.selectors["groupUngroup"] = ""
        self.assertFalse(
            self.adapter.ensure_ungrouped_results()["ok"]
        )

    def test_available_filter_values_reads_real_year_tree_once(self) -> None:
        self.tab.run_js.return_value = {
            "ok": True,
            "values": ["2024", "2023", "2022"],
        }

        values = self.adapter.available_filter_values("year")

        self.assertEqual(values, ["2024", "2023", "2022"])
        self.assertEqual(self.tab.run_js.call_count, 1)
        script = self.tab.run_js.call_args.args[0]
        self.assertIn('select[name=\\"year\\"]', script)
        self.assertIn("values:[...new Set(values)]", script)

    def test_available_filter_entries_keeps_labels_and_counts(self) -> None:
        self.tab.run_js.return_value = {
            "ok": True,
            "values": [],
            "entries": [
                {
                    "value": "北京市",
                    "label": "北京市 (115)",
                    "count": 115,
                },
                {
                    "value": "天津市",
                    "label": "天津市 (93)",
                    "count": 93,
                },
            ],
        }

        entries = self.adapter.available_filter_entries("province")

        self.assertEqual(
            entries,
            [
                {
                    "value": "北京市",
                    "label": "北京市 (115)",
                    "count": 115,
                },
                {
                    "value": "天津市",
                    "label": "天津市 (93)",
                    "count": 93,
                },
            ],
        )

    def test_filter_entries_fallback_to_v6_tree_selectors(self) -> None:
        self.adapter.selectors.pop("filters", None)
        self.adapter.selectors.pop("yearFilter", None)
        self.tab.run_js.return_value = {
            "ok": True,
            "values": ["2023"],
            "entries": [
                {
                    "value": "2023",
                    "label": "2023 (3983)",
                    "count": 3983,
                }
            ],
        }

        entries = self.adapter.available_filter_entries("year")

        self.assertEqual(entries[0]["count"], 3983)
        script = self.tab.run_js.call_args.args[0]
        self.assertIn('IssueDate', script)

        entries = self.adapter.available_filter_entries("province")
        self.assertEqual(entries[0]["count"], 3983)
        script = self.tab.run_js.call_args.args[0]
        self.assertIn('IssueDepartment', script)
        self.assertIn(':has(', script)
        self.assertNotIn('::has(', script)

    def test_filter_option_waits_for_delayed_tree_population(self) -> None:
        self.tab.run_js.side_effect = [
            {
                "ok": False,
                "changed": False,
                "reason": "filter root missing",
            },
            {
                "ok": False,
                "changed": False,
                "reason": "exact filter option missing",
            },
            {
                "ok": True,
                "changed": False,
                "expected": "2026",
            },
        ]
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep") as sleep,
        ):
            ready = self.adapter._wait_for_filter_option(
                key="year",
                value="2026",
                root_selector=self.adapter.selectors["filters"]["year"],
            )
        self.assertTrue(ready["ok"])
        self.assertEqual(self.tab.run_js.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

        self.tab.run_js.side_effect = None
        self.tab.run_js.return_value = {
            "ok": False,
            "changed": False,
            "reason": "exact filter option missing",
            "available": ["公布年份"],
        }
        with (
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0, 9],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            unavailable = self.adapter._wait_for_filter_option(
                key="year",
                value="2026",
                root_selector=self.adapter.selectors["filters"]["year"],
            )
        self.assertFalse(unavailable["ok"])
        self.assertEqual(unavailable["available"], ["公布年份"])

    def test_page_size_rows_and_access_guards(self) -> None:
        self.assertTrue(
            self.adapter._page_size_rows_match(
                {
                    "page": 1,
                    "totalPages": 2,
                    "resultCount": 15,
                    "rows": [{}] * 10,
                },
                expected_page_size=10,
            )
        )
        self.assertTrue(
            self.adapter._page_size_rows_match(
                {
                    "page": 2,
                    "totalPages": 2,
                    "resultCount": 15,
                    "rows": [{}] * 5,
                },
                expected_page_size=10,
            )
        )
        with self.assertRaises(InstitutionQuotaReached):
            self.adapter._raise_for_access({"quota": True, "url": "u"})
        with self.assertRaises(LoginRequired):
            self.adapter._raise_for_access({"login": True, "url": "u"})
        with self.assertRaises(ManualActionRequired):
            self.adapter._raise_for_access({"captcha": True})
        with self.assertRaises(PageStructureChanged):
            self.adapter._raise_for_access({"pageError": True})

        self.assertTrue(
            _is_page_context_transition(PageDisconnectedError("x"))
        )
        self.assertTrue(
            _is_page_context_transition(RuntimeError("context was destroyed"))
        )
        self.assertFalse(_is_page_context_transition(ValueError("other")))

    def test_search_observation_and_wait_failure_paths(self) -> None:
        with patch.object(
            self.adapter,
            "_read_results",
            return_value={"page": 1, "rows": []},
        ):
            self.tab.run_js.return_value = {"pageIndex": 2}
            observation = self.adapter._observe_search_transition(
                self.tab,
                "token",
            )
        self.assertFalse(observation["ready"])

        with (
            patch.object(
                self.adapter,
                "_read_results",
                side_effect=ValueError("unexpected"),
            ),
            patch(
                "pkulaw_engine.sources.pkulaw.time.monotonic",
                side_effect=[0, 0],
            ),
            patch("pkulaw_engine.sources.pkulaw.time.sleep"),
        ):
            with self.assertRaisesRegex(ValueError, "unexpected"):
                self.adapter._wait_for_results(1)

    def test_submit_search_selects_exact_mode_by_match_mode(self) -> None:
        self.adapter.selectors["librarySearchActions"] = {
            "chl": "https://www.pkulaw.com/law/chl",
            "lar": "https://www.pkulaw.com/law/lar",
        }
        for match_mode in ("title", "keyword"):
            with self.subTest(match_mode=match_mode):
                self.tab.run_js.return_value = {
                    "ok": True,
                    "searchType": "Title" if match_mode == "title" else "FullText",
                }
                with patch.object(
                    self.adapter,
                    "_ensure_search_mode",
                    return_value={"ok": True, "checked": True},
                ) as ensure_mode:
                    result = self.adapter._submit_search(
                        self.tab,
                        request=self._request(match_mode=match_mode),
                        library_code="chl",
                    )
                ensure_mode.assert_called_once_with(
                    self.tab,
                    match_mode=match_mode,
                    expected_search_types=(
                        ("Title",)
                        if match_mode == "title"
                        else ("FullText", "Fulltext", "Keyword", "Content")
                    ),
                )
                self.assertTrue(result["ok"])
                submitted_js = str(self.tab.run_js.call_args.args[0])
                self.assertIn(
                    "https://www.pkulaw.com/law/chl",
                    submitted_js,
                )
                self.assertIn("mainForm.submit()", submitted_js)

    def test_ensure_search_mode_keeps_exact_and_selects_area(self) -> None:
        self.adapter.selectors["exactMode"] = "#accurate"
        request_modes = (
            ("title", "#accurate", "Title"),
            (
                "keyword",
                "#accurate",
                "FullText",
            ),
        )
        for match_mode, selector, search_type in request_modes:
            with self.subTest(match_mode=match_mode):
                self.tab.run_js.return_value = {
                    "ok": True,
                    "checked": True,
                    "available": [search_type],
                }
                result = self.adapter._ensure_search_mode(
                    self.tab,
                    match_mode=match_mode,
                    expected_search_types=(search_type,),
                )
                script = str(self.tab.run_js.call_args.args[0])
                self.assertTrue(result["ok"])
                self.assertIn(selector, script)
                self.assertIn("const requireTarget = true;", script)

    def test_search_mode_is_implicit_without_exact_control(self) -> None:
        self.tab.run_js.return_value = {
            "ok": True,
            "checked": True,
            "available": ["Title"],
        }
        result = self.adapter._ensure_search_mode(
            self.tab,
            match_mode="title",
            expected_search_types=("Title",),
        )
        self.assertTrue(result["ok"])
        script = str(self.tab.run_js.call_args.args[0])
        self.assertIn('document.querySelector("")', script)
        self.assertIn("const requireTarget = false;", script)

    def test_search_refinement_filter_and_page_size_fail_closed(self) -> None:
        request = self._request(year="2024")
        session = self._session(request, actual_year="")
        state = {
            "page": 1,
            "totalPages": 1,
            "resultCount": 1,
            "rows": [{"policyId": "policy-1"}],
        }
        unchanged = (session, state, {"ok": True, "changed": False})
        with patch.object(
            self.adapter,
            "_run_refinement",
            side_effect=[unchanged],
        ):
            with self.assertRaises(YearFilterUnavailable):
                self.adapter._apply_search_refinements(
                    request=request,
                    session=session,
                    state=state,
                )

    def test_empty_exact_title_skips_missing_refinement_controls(self) -> None:
        request = self._request(year="全部年份")
        session = self._session(
            request,
            actual_page_size=20,
            grouping_mode="",
            result_count=0,
            row_count=0,
            page=1,
        )
        state = {
            "page": 1,
            "totalPages": 1,
            "resultCount": 0,
            "pageSize": 20,
            "rows": [],
            "empty": True,
            "groupingMode": "",
        }
        with patch.object(self.adapter, "_run_refinement") as refinement:
            actual_session, actual_state = (
                self.adapter._apply_search_refinements(
                    request=request,
                    session=session,
                    state=state,
                )
            )
        refinement.assert_not_called()
        self.assertIs(actual_session, session)
        self.assertIs(actual_state, state)

    def test_empty_exact_title_skips_read_results_operation_gate(self) -> None:
        request = self._request(requested_page_size=20)
        session = self._session(
            request,
            actual_page_size=20,
            grouping_mode="",
            result_count=0,
            row_count=0,
            page=1,
        )
        state = {
            "page": 1,
            "totalPages": 1,
            "resultCount": 0,
            "pageSize": 20,
            "rows": [],
            "empty": True,
            "groupingMode": "",
            "searchSession": session.to_dict(),
        }
        with (
            patch.object(self.adapter, "_ensure_search_page"),
            patch.object(
                self.adapter,
                "_ensure_operation_ready",
            ) as operation_ready,
            patch.object(self.adapter, "_ensure_library"),
            patch.object(
                self.adapter,
                "_mark_search_transition",
                return_value={"markerApplied": False},
            ),
            patch.object(
                self.adapter,
                "_submit_search",
                return_value={"ok": True},
            ),
            patch.object(
                self.adapter,
                "_wait_for_search_transaction",
                return_value=session,
            ),
            patch.object(
                self.adapter,
                "_apply_search_refinements",
                return_value=(session, state),
            ),
        ):
            result = self.adapter.search(
                keyword=request.query,
                match_mode="title",
                library="central",
                year="全部年份",
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                page_size=20,
            )
        self.assertIs(result, state)
        operation_ready.assert_called_once_with("search")

        request = self._request(province="湖北省")
        session = self._session(request, actual_province="湖南省")
        unchanged = (session, state, {"ok": True, "changed": False})
        with patch.object(
            self.adapter,
            "_run_refinement",
            side_effect=[unchanged, unchanged],
        ):
            with self.assertRaises(FilterNotApplied):
                self.adapter._apply_search_refinements(
                    request=request,
                    session=session,
                    state=state,
                )

        request = self._request(requested_page_size=20)
        session = self._session(
            request,
            actual_page_size=10,
            grouping_mode="ungrouped",
        )
        unchanged = (session, state, {"ok": True, "changed": False})
        with patch.object(
            self.adapter,
            "_run_refinement",
            side_effect=[
                unchanged,
                unchanged,
                unchanged,
                unchanged,
                unchanged,
            ],
        ):
            with self.assertRaises(PageSizeNotApplied):
                self.adapter._apply_search_refinements(
                    request=request,
                    session=session,
                    state=state,
                )

        request = self._request()
        session = self._session(request, grouping_mode="grouped")
        unchanged = (session, state, {"ok": True, "changed": False})
        with patch.object(
            self.adapter,
            "_run_refinement",
            side_effect=[
                unchanged,
                unchanged,
                unchanged,
                unchanged,
                unchanged,
            ],
        ):
            with self.assertRaises(ResultGroupingUnavailable):
                self.adapter._apply_search_refinements(
                    request=request,
                    session=session,
                    state=state,
                )

    def test_search_refinement_final_revalidation_fails_closed(self) -> None:
        state = {
            "page": 1,
            "totalPages": 1,
            "resultCount": 1,
            "rows": [{"policyId": "policy-1"}],
        }
        cases = [
            (
                {"year": "2024"},
                {"actual_year": ""},
                YearFilterUnavailable,
                state,
            ),
            (
                {"province": "湖北省"},
                {"actual_province": "湖南省"},
                FilterNotApplied,
                state,
            ),
            (
                {"requested_page_size": 20},
                {"actual_page_size": 10},
                PageSizeNotApplied,
                state,
            ),
            (
                {},
                {},
                PageSizeNotApplied,
                {**state, "rows": []},
            ),
            (
                {},
                {"grouping_mode": "grouped"},
                ResultGroupingUnavailable,
                state,
            ),
        ]
        for request_changes, session_changes, error_type, case_state in cases:
            with self.subTest(error_type=error_type.__name__):
                request = self._request(**request_changes)
                session = self._session(request, **session_changes)
                changed = (session, case_state, {"ok": True, "changed": True})
                with patch.object(
                    self.adapter,
                    "_run_refinement",
                    side_effect=[changed] * 5,
                ):
                    with self.assertRaises(error_type):
                        self.adapter._apply_search_refinements(
                            request=request,
                            session=session,
                            state=case_state,
                        )

    def test_run_refinement_maps_specific_error_codes(self) -> None:
        request = self._request()
        session = self._session(request)
        state = {"page": 1, "rows": [{"policyId": "policy-1"}]}
        cases = [
            ("YEAR_FILTER_UNAVAILABLE", YearFilterUnavailable),
            ("PAGE_SIZE_NOT_APPLIED", PageSizeNotApplied),
            ("RESULT_GROUPING_UNAVAILABLE", ResultGroupingUnavailable),
            ("FILTER_NOT_APPLIED", FilterNotApplied),
            ("OTHER", SearchTransitionUnconfirmed),
        ]
        for code, error_type in cases:
            with self.subTest(code=code):
                error = SearchTransitionUnconfirmed("refinement failed")
                error.code = code
                with (
                    patch.object(
                        self.adapter,
                        "_mark_search_transition",
                        return_value={"markerApplied": True},
                    ),
                    patch.object(
                        self.adapter,
                        "_clear_search_transition_marker",
                    ),
                    patch.object(
                        self.adapter,
                        "_wait_for_search_transaction",
                        side_effect=error,
                    ),
                ):
                    with self.assertRaises(error_type):
                        self.adapter._run_refinement(
                            request=request,
                            session=session,
                            state=state,
                            action=lambda: {"ok": True, "changed": True},
                            required_filter_keys=(),
                        )

    def test_run_refinement_rejects_unsuccessful_action(self) -> None:
        request = self._request()
        session = self._session(request)
        with (
            patch.object(
                self.adapter,
                "_mark_search_transition",
                return_value={"markerApplied": True},
            ),
            patch.object(
                self.adapter,
                "_clear_search_transition_marker",
            ) as clear,
        ):
            with self.assertRaises(FilterNotApplied):
                self.adapter._run_refinement(
                    request=request,
                    session=session,
                    state={},
                    action=lambda: {
                        "ok": False,
                        "changed": False,
                        "reason": "missing",
                    },
                    required_filter_keys=(),
                )
        clear.assert_called_once()


if __name__ == "__main__":
    unittest.main()
