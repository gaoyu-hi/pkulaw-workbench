from __future__ import annotations

import unittest

from pkulaw_engine.settings_revision import (
    ArtifactSummary,
    SettingsRevisionPreview,
    build_settings_revision_token,
    validate_artifact_policy,
)
from pkulaw_engine.storage import ValidationError


class ArtifactPolicyTests(unittest.TestCase):
    def test_accepts_only_exact_supported_values(self) -> None:
        self.assertEqual(validate_artifact_policy("preserve"), "preserve")
        self.assertEqual(validate_artifact_policy("delete"), "delete")

        for value in (None, "", "trash", "PRESERVE", "Delete", 1, True):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as context:
                    validate_artifact_policy(value)
                self.assertEqual(
                    context.exception.code,
                    "INVALID_ARTIFACT_POLICY",
                )


class ArtifactSummaryTests(unittest.TestCase):
    def test_normalizes_counts_and_serializes_camel_case(self) -> None:
        summary = ArtifactSummary(
            completed_policy_count="3",
            completed_batch_count=None,
            registered_file_count=-2,
            eligible_file_count="4",
            protected_file_count=0,
            directory_count=1,
            estimated_freed_bytes="2048",
        )

        self.assertEqual(
            summary.to_dict(),
            {
                "completedPolicyCount": 3,
                "completedBatchCount": 0,
                "registeredFileCount": 0,
                "eligibleFileCount": 4,
                "protectedFileCount": 0,
                "directoryCount": 1,
                "estimatedFreedBytes": 2048,
            },
        )

    def test_rejects_non_numeric_count(self) -> None:
        with self.assertRaises(ValueError):
            ArtifactSummary(
                completed_policy_count="many",
                completed_batch_count=0,
                registered_file_count=0,
                eligible_file_count=0,
                protected_file_count=0,
                directory_count=0,
                estimated_freed_bytes=0,
            )


class SettingsRevisionPreviewTests(unittest.TestCase):
    def test_serializes_preview_and_copies_protected_files(self) -> None:
        protected = [{"path": "D:/output/shared.docx", "reason": "SHARED"}]
        preview = SettingsRevisionPreview(
            token="abc",
            changed=True,
            generation=2,
            artifact_summary=ArtifactSummary(
                completed_policy_count=1,
                completed_batch_count=0,
                registered_file_count=1,
                eligible_file_count=0,
                protected_file_count=1,
                directory_count=1,
                estimated_freed_bytes=0,
            ),
            protected_files=protected,
        )
        protected[0]["reason"] = "MUTATED"

        self.assertEqual(
            preview.to_dict(),
            {
                "token": "abc",
                "changed": True,
                "generation": 2,
                "artifactSummary": {
                    "completedPolicyCount": 1,
                    "completedBatchCount": 0,
                    "registeredFileCount": 1,
                    "eligibleFileCount": 0,
                    "protectedFileCount": 1,
                    "directoryCount": 1,
                    "estimatedFreedBytes": 0,
                },
                "protectedFiles": [
                    {
                        "path": "D:/output/shared.docx",
                        "reason": "SHARED",
                    }
                ],
            },
        )


class SettingsRevisionTokenTests(unittest.TestCase):
    def test_token_is_canonical_deterministic_and_sensitive(self) -> None:
        input_a = {
            "queries": ["人工智能"],
            "matchMode": "keyword",
            "previewSnapshot": {"volatile": True},
        }
        reordered = {
            "previewSnapshot": {"volatile": False},
            "matchMode": "keyword",
            "queries": ["人工智能"],
        }
        first = build_settings_revision_token(
            task_id="task-1",
            base_generation=3,
            base_updated_at="2026-11-06T00:00:00Z",
            input=input_a,
            output_dir="D:/output",
        )
        second = build_settings_revision_token(
            task_id="task-1",
            base_generation=3,
            base_updated_at="2026-11-06T00:00:00Z",
            input=reordered,
            output_dir="D:/output",
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(
            first,
            build_settings_revision_token(
                task_id="task-1",
                base_generation=4,
                base_updated_at="2026-11-06T00:00:00Z",
                input=reordered,
                output_dir="D:/output",
            ),
        )
        self.assertNotEqual(
            first,
            build_settings_revision_token(
                task_id="task-1",
                base_generation=3,
                base_updated_at="2026-11-06T00:00:00Z",
                input={**reordered, "queries": ["碳中和"]},
                output_dir="D:/output",
            ),
        )


if __name__ == "__main__":
    unittest.main()
