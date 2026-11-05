from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.logging_utils import JsonLineLogger


class FakeTab:
    def __init__(self, url: str, window_name: str = "") -> None:
        self.url = url
        self.window_name = window_name

    def run_js(self, script: str) -> str:
        if "window.name" in script:
            return self.window_name
        return ""


class FakeBrowser:
    def __init__(self, tabs: list[FakeTab]) -> None:
        self.tabs = tabs

    def get_tabs(self) -> list[FakeTab]:
        return self.tabs


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.bridge = BrowserBridge(
            JsonLineLogger(Path(self.tempdir.name) / "engine.jsonl")
        )

    def tearDown(self) -> None:
        self.bridge.disconnect()
        self.tempdir.cleanup()

    def test_marker_selects_main_page_and_ignores_file_and_child(self) -> None:
        file_tab = FakeTab("file:///C:/app/index.html")
        child_tab = FakeTab("https://www.pkulaw.com/child", "fixture-child")
        main_tab = FakeTab("https://login.cug.edu.cn/cas", "view-target")
        self.bridge.browser = FakeBrowser([file_tab, child_tab, main_tab])  # type: ignore[assignment]
        self.bridge.target_key = "view-target"
        self.bridge.target_url = "https://www.pkulaw.com/"

        selected = self.bridge._find_main_tab(timeout_seconds=0.1)

        self.assertIs(selected, main_tab)

    def test_exact_target_url_is_a_fallback_when_marker_is_lost(self) -> None:
        app_tab = FakeTab("file:///C:/app/index.html")
        main_tab = FakeTab("https://reader.cug.edu.cn/cas/login?service=x")
        other_tab = FakeTab("https://www.pkulaw.com/")
        self.bridge.browser = FakeBrowser([app_tab, other_tab, main_tab])  # type: ignore[assignment]
        self.bridge.target_key = "lost-after-navigation"
        self.bridge.target_url = "https://reader.cug.edu.cn/cas/login?service=x"

        selected = self.bridge._find_main_tab(timeout_seconds=0.1)

        self.assertIs(selected, main_tab)

    def test_ambiguous_external_targets_are_not_guessed(self) -> None:
        first = FakeTab("https://login.example.edu/")
        second = FakeTab("https://www.pkulaw.com/")
        self.bridge.browser = FakeBrowser([first, second])  # type: ignore[assignment]
        self.bridge.target_key = "missing"
        self.bridge.target_url = "https://expected.example.edu/"

        selected = self.bridge._find_main_tab(timeout_seconds=0.1)

        self.assertIsNone(selected)

    def test_bound_tab_survives_cross_domain_navigation(self) -> None:
        main_tab = FakeTab("https://reader.cug.edu.cn/cas/login")
        self.bridge.browser = FakeBrowser([main_tab])  # type: ignore[assignment]
        self.bridge.main_tab = main_tab
        self.bridge.target_key = "view-target"
        self.bridge.target_url = "https://www.pkulaw.com/"

        selected: Any = self.bridge._require_tab()

        self.assertIs(selected, main_tab)

    def test_disconnected_page_target_is_refreshed_once(self) -> None:
        class PageDisconnectedError(RuntimeError):
            pass

        class RecoveringTab(FakeTab):
            def __init__(self) -> None:
                super().__init__("https://www.pkulaw.com/", "view-target")
                self.executions = 0

            def run_js(self, script: str):
                if "window.name" in script:
                    return self.window_name
                self.executions += 1
                if self.executions == 1:
                    raise PageDisconnectedError("与页面的连接已断开")
                return True

        tab = RecoveringTab()
        self.bridge.browser = FakeBrowser([tab])  # type: ignore[assignment]
        self.bridge.main_tab = tab
        self.bridge.target_key = "view-target"
        self.bridge.target_url = "https://www.pkulaw.com/"

        result = self.bridge._run_bound_js("return true;")

        self.assertTrue(result)
        self.assertIs(self.bridge.main_tab, tab)
        self.assertEqual(tab.executions, 2)

    def test_download_intent_waits_for_its_own_completed_event(self) -> None:
        intent = self.bridge.create_download_intent(
            task_id="task-1",
            kind="word",
            source_url="https://www.pkulaw.com/chl/test.html",
            expected_suffixes=(".doc", ".docx"),
        )
        cursor = self.bridge.download_cursor()
        self.bridge.note_download_event(
            "browser.download.started",
            {
                "downloadId": "other",
                "filename": "unrelated.txt",
                "url": "https://www.pkulaw.com/download/unrelated.txt",
                "path": "C:/tmp/unrelated.txt",
            },
        )
        self.bridge.note_download_event(
            "browser.download.started",
            {
                "downloadId": "expected",
                "filename": "policy.doc",
                "url": "https://www.pkulaw.com/download/policy.doc",
                "path": "C:/tmp/policy.doc",
            },
        )
        self.bridge.note_download_event(
            "browser.download.completed",
            {
                "downloadId": "other",
                "filename": "unrelated.txt",
                "url": "https://www.pkulaw.com/download/unrelated.txt",
                "path": "C:/tmp/unrelated.txt",
            },
        )
        self.bridge.note_download_event(
            "browser.download.completed",
            {
                "downloadId": "expected",
                "filename": "policy.doc",
                "url": "https://www.pkulaw.com/download/policy.doc",
                "path": "C:/tmp/policy.doc",
            },
        )

        progress_events: list[dict[str, Any]] = []
        event = self.bridge.wait_for_download_intent(
            intent["intentId"],
            cursor,
            timeout_seconds=0.1,
            on_event=progress_events.append,
        )

        assert event is not None
        self.assertEqual(event["downloadId"], "expected")
        self.assertEqual(event["intentId"], intent["intentId"])
        outcomes = [
            item.get("outcome") for item in progress_events
        ]
        self.assertIn("started", outcomes)
        self.assertIn("completed", outcomes)

    def test_download_intent_returns_none_on_timeout(self) -> None:
        intent = self.bridge.create_download_intent(
            task_id="task-timeout",
            kind="zip",
            source_url="https://www.pkulaw.com/chl/test.html",
            expected_suffixes=(".zip",),
        )
        cursor = self.bridge.download_cursor()
        event = self.bridge.wait_for_download_intent(
            intent["intentId"],
            cursor,
            timeout_seconds=0.01,
        )
        self.assertIsNone(event)

    def test_catalog_native_format_mismatch_is_returned_immediately(self) -> None:
        intent = self.bridge.create_download_intent(
            task_id="task-catalog",
            kind="batch-catalog",
            source_url="https://www.pkulaw.com/law/lar",
            expected_suffixes=(".xls",),
        )
        cursor = self.bridge.download_cursor()
        self.bridge.note_download_event(
            "browser.download.started",
            {
                "downloadId": "wrong-catalog",
                "filename": "catalog.xlsx",
                "url": "https://www.pkulaw.com/Download/DownloadCatalog",
                "path": "C:/tmp/catalog.xlsx",
            },
        )

        event = self.bridge.wait_for_download_intent(
            intent["intentId"],
            cursor,
            timeout_seconds=2,
        )

        assert event is not None
        self.assertEqual(event["event"], "mismatch")
        self.assertEqual(event["downloadId"], "wrong-catalog")
        self.assertIn("unexpected suffix", event["reason"])

    def test_download_intent_mismatch_is_forwarded_as_event(self) -> None:
        emitted: list[tuple[str, dict[str, Any]]] = []
        self.bridge.set_event_sink(
            lambda event, data: emitted.append((event, data))
        )
        intent = self.bridge.create_download_intent(
            task_id="task-mismatch",
            kind="word",
            source_url="https://www.pkulaw.com/chl/test.html",
            expected_suffixes=(".doc", ".docx"),
        )

        outcome = self.bridge.download_intents.observe(
            str(intent["intentId"]),
            {
                "event": "started",
                "downloadId": "wrong",
                "filename": "unrelated.txt",
                "url": "https://example.test/download/unrelated.txt",
                "path": "C:/tmp/unrelated.txt",
            },
        )

        self.assertFalse(outcome["accepted"])
        self.assertEqual(emitted[0][0], "DOWNLOAD_EVENT_MISMATCH")
        self.assertEqual(
            emitted[0][1]["intent"]["intentId"],
            intent["intentId"],
        )


if __name__ == "__main__":
    unittest.main()
