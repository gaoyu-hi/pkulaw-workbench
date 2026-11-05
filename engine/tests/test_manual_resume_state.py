from __future__ import annotations

import unittest

from pkulaw_engine.controls.manual_resume import (
    checkpoint_after_manual_continue,
)


class ManualResumeStateTests(unittest.TestCase):
    def test_resumed_checkpoint_clears_stale_manual_pause_fields(self) -> None:
        checkpoint = {
            "currentAction": "next-page",
            "pauseKind": "automatic",
            "pauseReason": "检测到滑块或人机验证，请人工处理后继续",
            "manualStatus": "waiting_manual",
            "manualAt": "2026-11-02T13:05:25Z",
        }

        resumed = checkpoint_after_manual_continue(
            checkpoint,
            "2026-11-02T13:11:25Z",
        )

        self.assertEqual(resumed["currentAction"], "next-page")
        self.assertEqual(resumed["resumedAt"], "2026-11-02T13:11:25Z")
        self.assertEqual(resumed["resumeKind"], "automatic")
        for key in (
            "pauseKind",
            "pauseReason",
            "manualStatus",
            "manualAt",
        ):
            self.assertNotIn(key, resumed)


if __name__ == "__main__":
    unittest.main()
