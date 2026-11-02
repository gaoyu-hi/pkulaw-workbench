from __future__ import annotations

from contextlib import contextmanager
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse

from pkulaw_engine.sources.pkulaw import PkulawAdapter, SelectorProfileMismatch
from pkulaw_engine.sources.selector_profile import (
    SelectorProfile,
    SelectorProfileError,
    detect_selector_profile,
    diagnose_selector_profile,
    load_selector_profile,
    load_selector_profiles,
    operation_required_controls,
    profile_from_markers,
)


class _Logger:
    def write(self, *_args: object, **_kwargs: object) -> None:
        pass


class _FixtureTab:
    def __init__(self, url: str, *, missing_control: str = "") -> None:
        self.url = url
        self.missing_control = missing_control
        self.search_submitted = False

    def get(self, url: str) -> None:
        self.url = url

    def run_js(self, script: str) -> dict[str, object]:
        path = urlparse(self.url).path
        if "const markers =" in script:
            return {
                '[data-pkulaw-profile="fixture"]': path != "/pkulaw/detail/demo.html",
                "#subLibraries": path in {"/", "/pkulaw/"},
                "#pkulaw-results": path == "/pkulaw/search",
            }
        if "const controls = {};" in script:
            on_search_page = path == "/pkulaw/"
            return {
                "url": self.url,
                "title": "fixture",
                "controls": {
                    "searchInput": {
                        "ok": on_search_page and self.missing_control != "searchInput",
                        "strategy": "stable" if on_search_page else "missing",
                        "matched": "#txtSearch",
                        "text": "",
                    },
                    "searchSubmit": {
                        "ok": on_search_page and self.missing_control != "searchSubmit",
                        "strategy": "stable" if on_search_page else "missing",
                        "matched": "#pkulaw-search-submit",
                        "text": "精确检索",
                    },
                },
            }
        if "quota:" in script and "captcha:" in script:
            return {
                "url": self.url,
                "title": "fixture",
                "body": "",
                "captcha": False,
                "login": False,
                "quota": False,
            }
        if "input.value =" in script:
            self.search_submitted = True
        return {}


class _FixtureBridge:
    entry_url = "http://fixture.test/"

    def __init__(self, tab: _FixtureTab) -> None:
        self.tab = tab

    @contextmanager
    def transaction(self):
        yield self.tab


class SelectorProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profiles = load_selector_profiles()

    def test_fixture_and_v6_profiles_are_separate(self) -> None:
        by_id = {profile.profile_id: profile for profile in self.profiles}

        self.assertEqual(set(by_id), {"fixture", "pkulaw-v6"})
        self.assertEqual(
            by_id["fixture"].selectors["searchSubmit"],
            "#pkulaw-search-submit",
        )
        self.assertEqual(by_id["fixture"].selectors["login"], "#fixture-login")
        self.assertEqual(by_id["pkulaw-v6"].selectors["searchInput"], "#txtSearch")
        self.assertEqual(by_id["pkulaw-v6"].selectors["searchSubmit"], "#btnSearch")
        self.assertEqual(by_id["pkulaw-v6"].selectors["exactMode"], "#accurate")
        self.assertNotIn(
            "titleSearchType",
            by_id["pkulaw-v6"].required_controls,
        )
        self.assertIn(
            "titleSearchType",
            by_id["pkulaw-v6"].diagnostic_controls,
        )
        self.assertEqual(by_id["pkulaw-v6"].selectors["libraryRoot"], "#subLibraries")
        self.assertIn("#leftContent", by_id["pkulaw-v6"].selectors["yearFilter"])
        self.assertIn(
            "#rightContent",
            by_id["pkulaw-v6"].selectors["groupUngroup"],
        )
        self.assertIn(
            "IssueDepartment",
            by_id["pkulaw-v6"].selectors["provinceFilter"],
        )
        self.assertIn(
            "#divFullText",
            by_id["pkulaw-v6"].selectors["detailBody"],
        )
        self.assertIn(
            "#downloadword",
            by_id["pkulaw-v6"].selectors["downloadWord"],
        )
        self.assertEqual(
            set(by_id["pkulaw-v6"].required_by_page),
            {"search", "results", "detail"},
        )
        self.assertEqual(
            by_id["pkulaw-v6"].version,
            "pkulaw-v6-2026-11-01-3",
        )
        self.assertIn(".articleSelect", by_id["pkulaw-v6"].selectors["pageSizeTrigger"])
        self.assertIn("#recordPage", by_id["pkulaw-v6"].selectors["pageSizeMenu"])
        self.assertIn("#recordPage dd", by_id["pkulaw-v6"].selectors["pageSizeItem"])
        self.assertIn("Pager.PageSize", by_id["pkulaw-v6"].selectors["pageSizeHidden"])
        self.assertTrue(
            all(by_id["pkulaw-v6"].selectors["filters"].values())
        )
        self.assertIn(
            "input[name=\"group\"][value=\"2\"]",
            by_id["pkulaw-v6"].selectors["groupUngroup"],
        )
        self.assertNotEqual(
            by_id["pkulaw-v6"].selectors["searchSubmit"],
            by_id["fixture"].selectors["searchSubmit"],
        )
        self.assertNotEqual(
            by_id["pkulaw-v6"].selectors["login"],
            by_id["fixture"].selectors["login"],
        )

    def test_profile_detection_uses_page_markers(self) -> None:
        fixture, fixture_evidence = profile_from_markers(
            {'[data-pkulaw-profile="fixture"]': True},
            self.profiles,
        )
        v6, v6_evidence = profile_from_markers(
            {"#main_form": True, "#btnSearch": True},
            self.profiles,
        )

        self.assertEqual(fixture.profile_id, "fixture")  # type: ignore[union-attr]
        self.assertEqual(fixture_evidence, ['[data-pkulaw-profile="fixture"]'])
        self.assertEqual(v6.profile_id, "pkulaw-v6")  # type: ignore[union-attr]
        self.assertEqual(v6_evidence, ["#main_form", "#btnSearch"])

        generic_detail, _ = profile_from_markers(
            {".fulltext-wrap": True},
            self.profiles,
        )
        self.assertIsNone(generic_detail)
        fulltext_detail, evidence = profile_from_markers(
            {".tiao-wrap .kuan-content": True},
            self.profiles,
        )
        self.assertEqual(
            fulltext_detail.profile_id,  # type: ignore[union-attr]
            "pkulaw-v6",
        )
        self.assertEqual(evidence, [".tiao-wrap .kuan-content"])

    def test_unknown_and_ambiguous_profiles_fail_safely(self) -> None:
        unknown, evidence = profile_from_markers({}, self.profiles)
        self.assertIsNone(unknown)
        self.assertEqual(evidence, [])

        with self.assertRaises(SelectorProfileError):
            profile_from_markers(
                {'[data-pkulaw-profile="fixture"]': True, "#main_form": True},
                self.profiles,
            )

    def test_missing_profile_diagnostic_is_not_ready(self) -> None:
        class UnusedTab:
            def run_js(self, _script: str) -> dict[str, object]:
                return {}

        diagnostics = diagnose_selector_profile(UnusedTab(), None)

        self.assertFalse(diagnostics["ready"])
        self.assertEqual(diagnostics["missingControls"], ["profile"])

    def test_invalid_profile_payloads_and_empty_directories_fail_closed(self) -> None:
        for payload in (
            {"version": "v1", "selectors": {}},
            {"profileId": "broken", "version": "v1", "selectors": []},
            {
                "profileId": "broken",
                "version": "v1",
                "selectors": {},
                "detection": [],
            },
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    SelectorProfile.from_dict(payload)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SelectorProfileError):
                load_selector_profiles(directory)

    def test_legacy_flat_profile_is_wrapped_and_unknown_operations_reject(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(
                json.dumps(
                    {
                        "version": "legacy-v1",
                        "searchInput": "#legacy-search",
                        "searchSubmit": "#legacy-submit",
                    }
                ),
                encoding="utf-8",
            )
            profile = load_selector_profile(path)
        self.assertEqual(profile.profile_id, "legacy")
        self.assertEqual(
            profile.selectors["searchInput"],
            "#legacy-search",
        )

        with self.assertRaisesRegex(
            ValueError,
            "unsupported selector operation",
        ):
            operation_required_controls("unknown-operation")

    def test_detector_rejects_non_object_marker_state(self) -> None:
        class InvalidMarkerTab:
            def run_js(self, _script: str) -> list[str]:
                return ["unexpected"]

        with self.assertRaisesRegex(
            SelectorProfileError,
            "无法读取页面选择器特征",
        ):
            detect_selector_profile(InvalidMarkerTab(), self.profiles)

    def test_resume_from_detail_rediscovers_fixture_profile(self) -> None:
        tab = _FixtureTab("http://fixture.test/pkulaw/detail/demo.html")
        adapter = PkulawAdapter(_FixtureBridge(tab), _Logger())  # type: ignore[arg-type]

        adapter._ensure_search_page(tab)

        self.assertEqual(adapter.selector_profile.profile_id, "fixture")  # type: ignore[union-attr]
        self.assertEqual(tab.url, "http://fixture.test/pkulaw/")
        self.assertTrue(adapter.selector_diagnostics["ready"])

    def test_missing_required_control_stops_before_search_submit(self) -> None:
        for missing_control in ("searchInput", "searchSubmit"):
            with self.subTest(missing_control=missing_control):
                tab = _FixtureTab(
                    "http://fixture.test/pkulaw/",
                    missing_control=missing_control,
                )
                adapter = PkulawAdapter(  # type: ignore[arg-type]
                    _FixtureBridge(tab),
                    _Logger(),
                )

                with self.assertRaises(SelectorProfileMismatch) as context:
                    adapter.search(
                        keyword="测试",
                        library="central",
                        year="全部年份",
                        province="全部省份",
                        timeliness="全部（仅记录元数据，不筛选）",
                        page_size=20,
                    )

                self.assertEqual(context.exception.code, "PAGE_SELECTOR_MISMATCH")
                self.assertFalse(tab.search_submitted)


if __name__ == "__main__":
    unittest.main()
