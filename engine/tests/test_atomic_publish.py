from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pkulaw_engine.atomic_publish import (
    PUBLISH_MANIFEST,
    AtomicPublishError,
    atomic_replace_many,
    prepare_publish_directory,
    publish_directory,
    sha256_file,
    verify_published_directory,
)


class AtomicPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.staging_root = self.root / ".publishing"
        self.final = self.root / "batch" / "batch-1"
        self.catalog = self.root / "catalog.xlsx"
        self.zip = self.root / "documents.zip"
        self.catalog.write_bytes(b"catalog")
        self.zip.write_bytes(b"zip")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _prepare(self) -> Path:
        return prepare_publish_directory(
            staging_root=self.staging_root,
            final_directory=self.final,
            batch_key="batch:1",
            files={
                "目录.xlsx": self.catalog,
                "全文.zip": self.zip,
            },
            report_name="验收报告.json",
            report_payload={
                "batchKey": "batch:1",
                "policyIds": ["policy-1"],
            },
        )

    def test_prepare_then_publish_directory_atomically(self) -> None:
        staging = self._prepare()
        self.assertTrue((staging / PUBLISH_MANIFEST).is_file())
        self.assertFalse(self.final.exists())

        manifest = publish_directory(staging, self.final)

        self.assertEqual(manifest["batchKey"], "batch:1")
        self.assertTrue((self.final / "目录.xlsx").is_file())
        self.assertTrue((self.final / "全文.zip").is_file())
        self.assertTrue((self.final / "验收报告.json").is_file())
        self.assertFalse(staging.exists())
        verified = verify_published_directory(
            self.final,
            batch_key="batch:1",
        )
        assert verified is not None
        self.assertTrue(verified["files"])

    def test_tampered_published_file_fails_verification(self) -> None:
        staging = self._prepare()
        publish_directory(staging, self.final)
        (self.final / "全文.zip").write_bytes(b"changed")
        self.assertIsNone(verify_published_directory(self.final))

    def test_existing_invalid_target_is_not_overwritten(self) -> None:
        staging = self._prepare()
        self.final.mkdir(parents=True)
        (self.final / "existing.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(AtomicPublishError):
            publish_directory(staging, self.final)
        self.assertEqual(
            (self.final / "existing.txt").read_text(encoding="utf-8"),
            "keep",
        )

    def test_failure_before_publish_preserves_staging_only(self) -> None:
        staging = self._prepare()
        self.assertFalse(self.final.exists())
        manifest = json.loads(
            (staging / PUBLISH_MANIFEST).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["status"], "prepared")
        self.assertTrue((staging / "目录.xlsx").is_file())
        self.assertTrue((staging / "全文.zip").is_file())

    def test_published_manifest_supports_db_recovery_without_republish(self) -> None:
        staging = self._prepare()
        first = publish_directory(staging, self.final)
        second = publish_directory(staging, self.final)
        self.assertEqual(first["batchKey"], second["batchKey"])
        self.assertEqual(second["directory"], str(self.final))

    def test_multi_file_replace_rolls_back_existing_files(self) -> None:
        first = self.root / "group" / "first.txt"
        second = self.root / "group" / "second.txt"
        first.parent.mkdir(parents=True)
        first.write_text("old-first", encoding="utf-8")
        second.write_text("old-second", encoding="utf-8")
        temp_first = self.root / "first.tmp"
        temp_second = self.root / "missing.tmp"
        temp_first.write_text("new-first", encoding="utf-8")

        with self.assertRaises(OSError):
            atomic_replace_many(
                [
                    (temp_first, first),
                    (temp_second, second),
                ]
            )

        self.assertEqual(first.read_text(encoding="utf-8"), "old-first")
        self.assertEqual(second.read_text(encoding="utf-8"), "old-second")

    def test_post_publish_verification_failure_rolls_back_all_files(self) -> None:
        first = self.root / "group" / "first.txt"
        second = self.root / "group" / "second.txt"
        first.parent.mkdir(parents=True)
        first.write_text("old-first", encoding="utf-8")
        second.write_text("old-second", encoding="utf-8")
        temp_first = self.root / "new-first.tmp"
        temp_second = self.root / "new-second.tmp"
        temp_first.write_text("new-first", encoding="utf-8")
        temp_second.write_text("new-second", encoding="utf-8")

        def fail_after_publish() -> None:
            raise OSError("verification failed")

        with self.assertRaises(OSError):
            atomic_replace_many(
                [
                    (temp_first, first),
                    (temp_second, second),
                ],
                post_publish=fail_after_publish,
            )

        self.assertEqual(first.read_text(encoding="utf-8"), "old-first")
        self.assertEqual(second.read_text(encoding="utf-8"), "old-second")

    def test_duplicate_destinations_are_rejected_before_replacement(self) -> None:
        destination = self.root / "single.txt"
        destination.write_text("old", encoding="utf-8")
        with self.assertRaises(AtomicPublishError):
            atomic_replace_many(
                [
                    (self.catalog, destination),
                    (self.zip, destination),
                ]
            )
        self.assertEqual(destination.read_text(encoding="utf-8"), "old")

    def test_tampered_published_directory_is_never_overwritten(self) -> None:
        first_staging = self._prepare()
        replacement = self._prepare()
        publish_directory(first_staging, self.final)
        tampered = b"tampered"
        (self.final / "全文.zip").write_bytes(tampered)

        with self.assertRaises(AtomicPublishError):
            publish_directory(replacement, self.final)

        self.assertEqual((self.final / "全文.zip").read_bytes(), tampered)

    def test_manifest_path_traversal_is_rejected(self) -> None:
        staging = self._prepare()
        publish_directory(staging, self.final)
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        manifest_path = self.final / PUBLISH_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].append(
            {
                "name": "../outside.txt",
                "size": outside.stat().st_size,
                "sha256": sha256_file(outside),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.assertIsNone(verify_published_directory(self.final))

    def test_case_insensitive_duplicate_publish_names_are_rejected(self) -> None:
        with self.assertRaises(AtomicPublishError):
            prepare_publish_directory(
                staging_root=self.staging_root,
                final_directory=self.final,
                batch_key="batch:duplicate",
                files={
                    "目录.xlsx": self.catalog,
                    "目录.XLSX": self.zip,
                },
                report_name="验收报告.json",
                report_payload={"policyIds": ["policy-1"]},
            )


if __name__ == "__main__":
    unittest.main()
