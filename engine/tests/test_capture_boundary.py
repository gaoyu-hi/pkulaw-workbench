from __future__ import annotations

import unittest

from pkulaw_engine.capture_boundary import CaptureBoundary, CaptureBoundaryRequired


class CaptureBoundaryTests(unittest.TestCase):
    def test_exception_exposes_stable_code_and_serializable_record(self) -> None:
        boundary = CaptureBoundary(
            reason="RESULT_WINDOW_TRUNCATED",
            query="碳中和",
            library="central",
            year="2023",
            province="全部省份",
            completed_policy_count=12,
            remaining_scopes=(
                {"year": "2023", "province": "全部省份", "resultCount": 2600},
            ),
            estimated_result_operations=3,
            detected_at="2026-11-06T00:00:00Z",
        )
        error = CaptureBoundaryRequired(boundary)
        self.assertEqual(error.code, "CAPTURE_BOUNDARY_REACHED")
        payload = error.to_checkpoint()
        self.assertEqual(payload["code"], "CAPTURE_BOUNDARY_REACHED")
        self.assertEqual(payload["boundary"]["query"], "碳中和")
        self.assertEqual(payload["boundary"]["remainingScopes"][0]["resultCount"], 2600)


if __name__ == "__main__":
    unittest.main()
