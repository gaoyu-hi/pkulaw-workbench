from __future__ import annotations

import unittest

from pkulaw_engine.progress import (
    batch_batch_overall,
    batch_scan_overall,
    build_progress_snapshot,
    processing_document_overall,
    single_download_overall,
    single_scan_overall,
)


class ProgressSnapshotTests(unittest.TestCase):
    def test_partial_completion_is_not_one_hundred_percent(self) -> None:
        snapshot = build_progress_snapshot(
            stage="partial",
            overall=1.0,
            completed_units=3,
            total_units=3,
            succeeded=0,
            failed=0,
            pending_review=3,
        )

        self.assertEqual(snapshot.overall, 0.99)
        self.assertEqual(snapshot.completed_units, 3)
        self.assertEqual(snapshot.total_units, 3)
        self.assertEqual(snapshot.pending_review, 3)

    def test_completed_snapshot_requires_no_pending_failures(self) -> None:
        snapshot = build_progress_snapshot(
            stage="completed",
            overall=1.0,
            completed_units=3,
            total_units=3,
            succeeded=3,
            failed=0,
            pending_review=0,
        )

        self.assertEqual(snapshot.overall, 1.0)

    def test_single_scan_second_of_nineteen_pages_advances(self) -> None:
        first = single_scan_overall(
            combination_index=1,
            combination_count=1,
            page=1,
            total_pages=19,
        )
        second = single_scan_overall(
            combination_index=1,
            combination_count=1,
            page=2,
            total_pages=19,
        )

        self.assertAlmostEqual(first, 0.05 + 0.25 / 19)
        self.assertAlmostEqual(second, 0.05 + 0.25 * 2 / 19)
        self.assertGreater(second, first)
        self.assertNotEqual(second, 0.1)

    def test_single_download_progresses_per_item(self) -> None:
        first = single_download_overall(1, 4)
        second = single_download_overall(2, 4)

        self.assertAlmostEqual(first, 0.30 + 0.65 / 4)
        self.assertAlmostEqual(second, 0.30 + 0.65 * 2 / 4)
        self.assertGreater(second, first)

    def test_batch_scan_catalog_zip_and_publish_are_ordered(self) -> None:
        page_one = batch_scan_overall(1, 1, 1, 5)
        page_two = batch_scan_overall(1, 1, 2, 5)
        batch_start = batch_batch_overall(1, 2, "select")
        catalog = batch_batch_overall(1, 2, "catalog")
        archive = batch_batch_overall(1, 2, "zip")
        published = batch_batch_overall(1, 2, "publish")

        self.assertLess(page_one, page_two)
        self.assertLess(page_two, batch_start)
        self.assertLess(batch_start, catalog)
        self.assertLess(catalog, archive)
        self.assertLess(archive, published)
        self.assertAlmostEqual(batch_start, 0.20)
        self.assertAlmostEqual(published, 0.60, places=6)

    def test_processing_progress_is_per_document(self) -> None:
        first = processing_document_overall(1, 3)
        second = processing_document_overall(2, 3)

        self.assertAlmostEqual(first, 0.05 + 0.90 / 3)
        self.assertAlmostEqual(second, 0.05 + 0.90 * 2 / 3)
        self.assertGreater(second, first)

    def test_snapshot_round_trips_with_camel_case_protocol_fields(self) -> None:
        snapshot = build_progress_snapshot(
            stage="download",
            overall=0.55,
            completed_units=2,
            total_units=5,
            succeeded=1,
            failed=1,
            pending_review=0,
        )
        payload = snapshot.to_dict()

        self.assertEqual(payload["stage"], "download")
        self.assertEqual(payload["completedUnits"], 2)
        self.assertEqual(payload["totalUnits"], 5)
        restored = build_progress_snapshot(
            stage=str(payload["stage"]),
            overall=float(payload["overall"]),
            completed_units=int(payload["completedUnits"]),
            total_units=int(payload["totalUnits"]),
            succeeded=int(payload["succeeded"]),
            failed=int(payload["failed"]),
            pending_review=int(payload["pendingReview"]),
        )
        self.assertEqual(restored, snapshot)
