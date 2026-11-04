from __future__ import annotations

import unittest

from pkulaw_engine.download_intent import (
    DownloadIntentExhausted,
    DownloadIntentManager,
)


class DownloadIntentManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []
        self.manager = DownloadIntentManager(
            event_sink=lambda event, data: self.events.append((event, data))
        )
        self.intent = self.manager.create(
            task_id="task-1",
            kind="word",
            source_url="https://www.pkulaw.com/chl/test.html",
            expected_suffixes=(".doc", ".docx"),
        )

    def _event(
        self,
        event: str,
        *,
        download_id: str = "download-1",
        filename: str = "policy.doc",
        url: str = "https://www.pkulaw.com/download/policy.doc",
    ) -> dict[str, object]:
        return {
            "event": event,
            "downloadId": download_id,
            "filename": filename,
            "url": url,
            "path": f"C:/tmp/{filename}",
        }

    def test_started_and_completed_match_same_download_id(self) -> None:
        started = self.manager.observe(
            self.intent["intentId"],
            self._event("started"),
        )
        completed = self.manager.observe(
            self.intent["intentId"],
            self._event("completed"),
        )
        self.assertEqual(started["outcome"], "started")
        self.assertEqual(completed["outcome"], "completed")
        self.assertEqual(completed["downloadId"], "download-1")

    def test_unrelated_download_is_ignored(self) -> None:
        ignored = self.manager.observe(
            self.intent["intentId"],
            self._event(
                "started",
                download_id="other",
                filename="other.txt",
                url="https://www.pkulaw.com/download/other.txt",
            ),
        )
        self.assertFalse(ignored["accepted"])
        self.assertEqual(ignored["outcome"], "ignored")
        self.assertEqual(self.events[0][0], "DOWNLOAD_EVENT_MISMATCH")

    def test_cross_host_download_is_ignored(self) -> None:
        ignored = self.manager.observe(
            self.intent["intentId"],
            self._event(
                "started",
                url="https://attacker.example/policy.doc",
            ),
        )
        self.assertFalse(ignored["accepted"])
        self.assertIn("source host", ignored["reason"])

    def test_failed_then_other_completed_is_not_consumed(self) -> None:
        self.manager.observe(self.intent["intentId"], self._event("started"))
        failed = self.manager.observe(
            self.intent["intentId"],
            self._event("failed"),
        )
        unrelated = self.manager.observe(
            self.intent["intentId"],
            self._event("completed", download_id="download-2"),
        )
        self.assertEqual(failed["outcome"], "failed")
        self.assertFalse(unrelated["accepted"])

    def test_second_matching_start_exhausts_intent(self) -> None:
        self.manager.observe(self.intent["intentId"], self._event("started"))
        with self.assertRaises(DownloadIntentExhausted):
            self.manager.observe(
                self.intent["intentId"],
                self._event("started", download_id="download-2"),
            )

    def test_terminal_without_matching_started_is_ignored(self) -> None:
        result = self.manager.observe(
            self.intent["intentId"],
            self._event("completed", download_id="download-2"),
        )
        self.assertFalse(result["accepted"])
        self.assertEqual(result["outcome"], "ignored")

    def test_validation_is_recorded(self) -> None:
        self.manager.observe(self.intent["intentId"], self._event("started"))
        self.manager.observe(self.intent["intentId"], self._event("completed"))
        self.manager.record_validation(
            self.intent["intentId"],
            valid=True,
            actual_kind="ole-doc",
        )
        state = self.manager.state(self.intent["intentId"])
        assert state is not None
        self.assertEqual(state["validation"]["actualKind"], "ole-doc")


if __name__ == "__main__":
    unittest.main()
