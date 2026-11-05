from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pkulaw_engine.scan_journal import ScanJournal, ScanJournalError


def _page(page: int, *, config: str = "config-1") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "configFingerprint": config,
        "taskId": "task-1",
        "query": "碳中和",
        "library": "local",
        "effectiveYear": "全部年份",
        "page": page,
        "totalPages": 5,
        "resultCount": 450,
        "requestedPageSize": 100,
        "actualPageSize": 100,
        "groupingMode": "ungrouped",
        "searchId": "search-1",
        "resultSignature": f"signature-{page}",
        "observedAt": "2026-11-02T00:00:00Z",
        "rows": [{"policyId": f"policy-{page}", "title": f"第{page}页"}],
    }


class ScanJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.journal = ScanJournal(
            self.root / "task-1.jsonl",
            task_id="task-1",
            config_fingerprint="config-1",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_append_and_reload_without_checkpoint_row_payload(self) -> None:
        self.journal.append(_page(1))
        self.journal.append(_page(2))

        loaded = self.journal.read()
        summary = self.journal.checkpoint_summary()

        self.assertEqual([record["page"] for record in loaded], [1, 2])
        self.assertEqual(summary["path"], str(self.journal.path))
        self.assertEqual(summary["completedPages"], [1, 2])
        self.assertNotIn("rows", json.dumps(summary, ensure_ascii=False))

    def test_truncated_last_line_is_discarded(self) -> None:
        self.journal.append(_page(1))
        with self.journal.path.open("a", encoding="utf-8") as stream:
            stream.write('{"page":2')

        loaded = self.journal.read()

        self.assertEqual([record["page"] for record in loaded], [1])

    def test_append_repairs_a_truncated_tail_before_writing(self) -> None:
        self.journal.append(_page(1))
        with self.journal.path.open("a", encoding="utf-8") as stream:
            stream.write('{"page":2')

        self.journal.append(_page(2))

        self.assertEqual(
            [record["page"] for record in self.journal.read()],
            [1, 2],
        )

    def test_middle_corruption_fails_closed(self) -> None:
        self.journal.append(_page(1))
        self.journal.append(_page(2))
        lines = self.journal.path.read_text(encoding="utf-8").splitlines()
        lines[0] = "{not-json}"
        self.journal.path.write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(ScanJournalError) as context:
            self.journal.read()

        self.assertEqual(context.exception.code, "SCAN_JOURNAL_CORRUPT")

    def test_config_fingerprint_change_refuses_reuse(self) -> None:
        self.journal.append(_page(1))
        changed = ScanJournal(
            self.journal.path,
            task_id="task-1",
            config_fingerprint="config-2",
        )

        with self.assertRaises(ScanJournalError) as context:
            changed.read()

        self.assertEqual(context.exception.code, "SCAN_JOURNAL_CONFIG_MISMATCH")

    def test_search_identity_mismatch_fails_closed(self) -> None:
        self.journal.append(_page(1))
        altered = _page(2)
        altered["searchId"] = "other-search"
        self.journal.append(altered)

        with self.assertRaises(ScanJournalError) as context:
            self.journal.read()

        self.assertEqual(context.exception.code, "SCAN_JOURNAL_IDENTITY_MISMATCH")

    def test_controlled_resume_can_rebind_after_matching_first_page(self) -> None:
        self.journal.append(_page(1))
        resumed = _page(2)
        resumed["searchId"] = "search-2"
        resumed["resumedFrom"] = "search-1"
        resumed["resumeEvidence"] = {
            "page": 1,
            "resultSignature": "signature-1",
        }

        self.journal.append(resumed)

        records = self.journal.read()
        self.assertEqual(
            [record["searchId"] for record in records],
            ["search-1", "search-2"],
        )

    def test_seal_is_idempotent_and_preserves_records(self) -> None:
        self.journal.append(_page(1))
        self.assertFalse(self.journal.checkpoint_summary()["sealed"])

        first = self.journal.seal()
        second = self.journal.seal()

        self.assertEqual(first["sealed"], True)
        self.assertEqual(second["sealed"], True)
        self.assertEqual(len(self.journal.read()), 1)

    def test_empty_completed_scan_can_be_sealed(self) -> None:
        summary = self.journal.seal()

        self.assertTrue(summary["sealed"])
        self.assertTrue(self.journal.path.is_file())
        self.assertEqual(self.journal.read(), [])


if __name__ == "__main__":
    unittest.main()
