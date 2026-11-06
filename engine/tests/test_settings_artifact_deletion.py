from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pkulaw_engine.storage import TaskStore, ValidationError


def _paused_task(store: TaskStore, output_dir: Path, mode: str = "single"):
    task = store.create_task(
        name="设置成果清理任务",
        mode=mode,
        task_input={"queries": ["测试"]},
        output_dir=str(output_dir),
    )
    store.update_task(task["id"], status="paused", current_stage="paused")
    return task


class SettingsArtifactDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = TaskStore(self.root / "pkulaw.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def test_preview_counts_bytes_and_permanently_deletes_owned_outputs(
        self,
    ) -> None:
        output = self.root / "任务输出"
        nested = output / "nested"
        nested.mkdir(parents=True)
        owned_word = nested / "owned.docx"
        owned_zip = nested / "batch.zip"
        catalog = output / "catalog.xlsx"
        owned_word.write_text("owned", encoding="utf-8")
        owned_zip.write_text("zip", encoding="utf-8")
        catalog.write_text("catalog", encoding="utf-8")

        task = _paused_task(self.store, output)
        self.store.upsert_task_item(
            task["id"],
            policy_key="id:p1",
            title="政策一",
            status="completed",
            payload={"downloadedFile": str(owned_word)},
        )
        self.store.upsert_task_batch(
            task["id"],
            batch_key="batch:1",
            query="测试",
            library="central",
            page=1,
            row_start=1,
            row_end=1,
            status="completed",
            catalog_format="xlsx",
            full_format="docx",
            selected=[{"policyId": "p1"}],
            catalog_path=str(catalog),
            zip_path=str(owned_zip),
        )
        self.store.upsert_processing_run(
            "run-1",
            task["id"],
            status="completed",
            progress=1.0,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            task["id"],
            "run-1",
            kind="catalog",
            path=str(catalog),
        )

        preview = self.store.settings_artifact_preview(task["id"])
        self.assertEqual(
            preview["artifactSummary"]["estimatedFreedBytes"],
            5 + 3 + 7,
        )
        self.assertEqual(preview["artifactSummary"]["protectedFileCount"], 0)

        deleted = self.store.permanently_delete_task_outputs(task["id"])
        self.assertEqual(deleted["protectedFiles"], [])
        self.assertEqual(deleted["failedFiles"], [])
        self.assertEqual(deleted["freedBytes"], 15)
        self.assertFalse(owned_word.exists())
        self.assertFalse(owned_zip.exists())
        self.assertFalse(catalog.exists())
        self.assertFalse(nested.exists())
        self.assertTrue(output.exists())

        counts = self.store.clear_task_results(task["id"])
        self.assertEqual(
            counts,
            {
                "task_items": 1,
                "task_batches": 1,
                "processing_runs": 1,
                "policy_documents": 0,
                "task_artifacts": 1,
            },
        )
        self.assertIsNotNone(self.store.get_task(task["id"]))
        self.assertEqual(self.store.list_task_items(task["id"]), [])
        self.assertEqual(self.store.list_task_batches(task["id"]), [])

    def test_shared_and_unregistered_files_are_protected(self) -> None:
        output = self.root / "共享输出"
        output.mkdir()
        owned = output / "owned.txt"
        shared = output / "shared.txt"
        unmanaged = output / "unmanaged.txt"
        owned.write_text("owned", encoding="utf-8")
        shared.write_text("shared", encoding="utf-8")
        unmanaged.write_text("unmanaged", encoding="utf-8")

        task = _paused_task(self.store, output)
        other = self.store.create_task(
            name="共享任务",
            mode="single",
            task_input={},
            output_dir=str(output),
        )
        self.store.upsert_processing_run(
            "run-task",
            task["id"],
            status="completed",
            progress=1.0,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            task["id"],
            "run-task",
            kind="owned",
            path=str(owned),
        )
        self.store.add_task_artifact(
            task["id"],
            "run-task",
            kind="shared",
            path=str(shared),
        )
        self.store.upsert_processing_run(
            "run-other",
            other["id"],
            status="completed",
            progress=1.0,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            other["id"],
            "run-other",
            kind="shared",
            path=str(shared),
        )

        preview = self.store.settings_artifact_preview(task["id"])
        protected_paths = {
            item["path"] for item in preview["protectedFiles"]
        }
        self.assertIn(str(shared.resolve()), protected_paths)
        self.assertNotIn(str(unmanaged.resolve()), {
            item["path"] for item in preview["eligibleFiles"]
        })

        deleted = self.store.permanently_delete_task_outputs(task["id"])
        self.assertFalse(owned.exists())
        self.assertTrue(shared.exists())
        self.assertTrue(unmanaged.exists())
        self.assertEqual(
            {item["path"] for item in deleted["protectedFiles"]},
            {str(shared.resolve())},
        )

    def test_unlink_failure_is_reported_without_claiming_success(self) -> None:
        output = self.root / "失败输出"
        output.mkdir()
        artifact = output / "busy.txt"
        artifact.write_text("busy", encoding="utf-8")
        task = _paused_task(self.store, output)
        self.store.upsert_processing_run(
            "run-fail",
            task["id"],
            status="completed",
            progress=1.0,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            task["id"],
            "run-fail",
            kind="busy",
            path=str(artifact),
        )

        with mock.patch(
            "pathlib.Path.unlink",
            side_effect=OSError("file is busy"),
        ):
            deleted = self.store.permanently_delete_task_outputs(task["id"])

        self.assertEqual(deleted["failedFiles"], [str(artifact.resolve())])
        self.assertEqual(deleted["deletedFiles"], [])
        self.assertEqual(deleted["freedBytes"], 0)
        self.assertTrue(artifact.exists())

    def test_preview_accepts_paused_but_rejects_running_task(self) -> None:
        output = self.root / "状态输出"
        output.mkdir()
        running = self.store.create_task(
            name="运行任务",
            mode="single",
            task_input={},
            output_dir=str(output),
        )
        self.store.update_task(running["id"], status="running")
        with self.assertRaises(ValidationError) as context:
            self.store.settings_artifact_preview(running["id"])
        self.assertEqual(context.exception.code, "TASK_NOT_PAUSED")
        with self.assertRaises(ValidationError) as clear_context:
            self.store.clear_task_results(running["id"])
        self.assertEqual(clear_context.exception.code, "TASK_NOT_PAUSED")

        paused = self.store.create_task(
            name="暂停任务",
            mode="single",
            task_input={},
            output_dir=str(output),
        )
        self.store.update_task(paused["id"], status="paused")
        preview = self.store.settings_artifact_preview(paused["id"])
        self.assertEqual(preview["artifactSummary"]["registeredFileCount"], 0)

    def test_path_safety_classification_matches_cleanup_preview(self) -> None:
        output = self.root / "分类输出"
        output.mkdir()
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        task = self.store.create_task(
            name="分类任务",
            mode="single",
            task_input={},
            output_dir=str(output),
        )
        self.store.upsert_processing_run(
            "run-classify",
            task["id"],
            status="completed",
            progress=1.0,
            current_stage="completed",
        )
        self.store.add_task_artifact(
            task["id"],
            "run-classify",
            kind="outside",
            path=str(outside),
        )

        self.store.update_task(task["id"], status="stopped", current_stage="stopped")
        cleanup = self.store.cleanup_preview([task["id"]])
        cleanup_reason = next(
            item["reason"]
            for item in cleanup["files"]
            if item["path"] == str(outside.resolve())
        )

        self.store.update_task(task["id"], status="paused", current_stage="paused")
        settings = self.store.settings_artifact_preview(task["id"])
        settings_reason = next(
            item["reason"]
            for item in settings["files"]
            if item["path"] == str(outside.resolve())
        )

        self.assertEqual(cleanup_reason, "OUTSIDE_TASK_OUTPUT")
        self.assertEqual(settings_reason, cleanup_reason)


if __name__ == "__main__":
    unittest.main()
