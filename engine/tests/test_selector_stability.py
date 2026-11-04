from __future__ import annotations

import unittest

from pkulaw_engine.sources.selector_profile import (
    SelectorProfileError,
    diagnose_for_operation,
)


def _diagnostics(
    *,
    available: set[str],
    detected: bool = True,
    page_kind: str = "results",
) -> dict[str, object]:
    controls = {
        key: {
            "ok": key in available,
            "strategy": "stable" if key in available else "missing",
        }
        for key in (
            "resultsRoot",
            "resultLink",
            "resultNumber",
            "resultCount",
            "libraryRoot",
            "searchInput",
            "searchSubmit",
        )
    }
    return {
        "profileId": "pkulaw-v6" if detected else "",
        "detected": detected,
        "pageKind": page_kind,
        "controls": controls,
    }


class SelectorStabilityTests(unittest.TestCase):
    def _run(
        self,
        diagnostics: list[dict[str, object]],
        operation: str = "read-results",
    ) -> dict[str, object]:
        state = {"index": 0, "clock": 0.0}

        def observer() -> dict[str, object]:
            index = min(state["index"], len(diagnostics) - 1)
            state["index"] += 1
            return diagnostics[index]

        def clock() -> float:
            return state["clock"]

        def sleep(seconds: float) -> None:
            state["clock"] += seconds

        return diagnose_for_operation(
            observer,
            operation,
            timeout_seconds=2.0,
            interval_seconds=0.25,
            stable_samples=2,
            clock=clock,
            sleep=sleep,
        )

    def test_transient_missing_control_recovers(self) -> None:
        missing = _diagnostics(available={"resultsRoot"})
        ready = _diagnostics(
            available={"resultsRoot", "resultCount"}
        )
        result = self._run([missing, ready, ready])
        self.assertTrue(result["operationReady"])
        self.assertGreaterEqual(int(result["stabilitySamples"]), 2)

    def test_flickering_controls_are_unstable(self) -> None:
        ready = _diagnostics(
            available={"resultsRoot", "resultCount"}
        )
        missing = _diagnostics(available={"resultsRoot"})
        with self.assertRaises(SelectorProfileError) as context:
            self._run([ready, missing, ready, missing, ready, missing])
        self.assertEqual(context.exception.code, "PAGE_SELECTOR_UNSTABLE")

    def test_persistent_missing_control_is_mismatch(self) -> None:
        missing = _diagnostics(available={"resultsRoot"})
        with self.assertRaises(SelectorProfileError) as context:
            self._run([missing] * 10)
        self.assertEqual(context.exception.code, "PAGE_SELECTOR_MISMATCH")

    def test_unknown_page_is_mismatch(self) -> None:
        unknown = _diagnostics(
            available=set(),
            detected=False,
            page_kind="unknown",
        )
        with self.assertRaises(SelectorProfileError) as context:
            self._run([unknown] * 10)
        self.assertEqual(context.exception.code, "PAGE_SELECTOR_MISMATCH")

    def test_results_operation_does_not_require_library_root(self) -> None:
        ready_without_library_root = _diagnostics(
            available={"resultsRoot", "resultCount"}
        )
        result = self._run(
            [ready_without_library_root, ready_without_library_root],
            operation="read-results",
        )
        self.assertTrue(result["operationReady"])
        self.assertNotIn(
            "libraryRoot",
            result["operationControls"],
        )


if __name__ == "__main__":
    unittest.main()
