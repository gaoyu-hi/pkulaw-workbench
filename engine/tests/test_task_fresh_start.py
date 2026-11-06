from __future__ import annotations

import json
import unittest

from pkulaw_engine.task_fresh_start import build_fresh_task_seed


class FreshTaskSeedTests(unittest.TestCase):
    def _task(self) -> dict[str, object]:
        return {
            "id": "old-task",
            "mode": "single",
            "outputDir": "D:/output",
            "input": {
                "queries": ["人工智能", "人工智能"],
                "keywords": ["人工智能"],
                "matchMode": "keyword",
                "library": "central",
                "province": "北京市",
                "yearMode": "range",
                "startYear": 2020,
                "endYear": 2024,
                "timeliness": "全部（仅记录元数据，不筛选）",
                "pageSize": 20,
                "pageMode": "limit",
                "pageSelection": {"mode": "limit", "maxPages": 5},
                "maxPages": 5,
                "catalogFormat": "xls",
                "fullFormat": "docx",
                "searchStrategyVersion": 2,
                "searchSemanticsVersion": 2,
                "searchId": "old-search",
                "searchIdentity": {"searchId": "old-search"},
                "previewSnapshot": {"previewFingerprint": "old-preview"},
                "previewFingerprint": "old-preview",
                "configGeneration": 3,
                "checkpoint": {"currentPage": 9},
                "downloadedFile": "D:/output/old.docx",
            },
        }

    def test_fresh_seed_strips_checkpoint_and_preview_identity(self) -> None:
        seed = build_fresh_task_seed(
            self._task(),
            may_duplicate_downloads=True,
        )
        payload = json.dumps(seed.input, ensure_ascii=False)

        self.assertEqual(seed.old_task_id, "old-task")
        self.assertTrue(seed.old_task_preserved)
        self.assertTrue(seed.may_duplicate_downloads)
        self.assertEqual(seed.input["queries"], ["人工智能"])
        self.assertEqual(seed.input["province"], "全部省份")
        for forbidden in (
            "searchId",
            "searchIdentity",
            "previewFingerprint",
            "previewSnapshot",
            "configGeneration",
            "checkpoint",
            "downloadedFile",
        ):
            self.assertNotIn(forbidden, payload)

    def test_batch_seed_keeps_batch_formats_and_page_policy(self) -> None:
        task = self._task()
        task["mode"] = "batch"
        task["input"]["library"] = "local"
        task["input"]["province"] = "北京市"

        seed = build_fresh_task_seed(
            task,
            may_duplicate_downloads=False,
        )

        self.assertEqual(seed.mode, "batch")
        self.assertEqual(seed.input["province"], "北京市")
        self.assertEqual(seed.input["pageMode"], "limit")
        self.assertEqual(seed.input["maxPages"], 5)
        self.assertEqual(seed.input["catalogFormat"], "xls")
        self.assertEqual(seed.input["fullFormat"], "docx")

    def test_output_directory_is_copied_without_touching_old_task(self) -> None:
        task = self._task()

        seed = build_fresh_task_seed(
            task,
            may_duplicate_downloads=False,
        )

        self.assertEqual(seed.output_dir, "D:/output")
        self.assertEqual(task["outputDir"], "D:/output")
        self.assertIn("checkpoint", task["input"])


if __name__ == "__main__":
    unittest.main()
