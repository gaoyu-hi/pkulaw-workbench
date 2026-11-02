from __future__ import annotations

import hashlib
import unittest
import tempfile
from pathlib import Path

from pkulaw_engine.policy import (
    candidate_score,
    extract_policy_id,
    group_results,
    normalize_title,
    policy_identity,
    policy_filename,
    validate_downloaded_word,
    validate_word_attachment,
)


class PolicyUtilityTests(unittest.TestCase):
    def test_normalize_title_handles_width_and_punctuation(self) -> None:
        self.assertEqual(
            normalize_title("关于—测试 政策（试行）"),
            normalize_title("关于-测试政策(试行)"),
        )
        self.assertEqual(
            normalize_title("ＡＢＣ１２３"),
            normalize_title("abc123"),
        )

    def test_legacy_doc_attachment_requires_ole_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.doc"
            path.write_bytes(b"not-an-ole-document")
            valid, reason = validate_word_attachment(path, ".doc")
            self.assertFalse(valid)
            self.assertIn("OLE", reason)

            path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
            valid, reason = validate_word_attachment(path, ".doc")
            self.assertTrue(valid)
            self.assertEqual(reason, "")

    def test_downloaded_word_dispatches_by_actual_extension(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            legacy_doc = root / "legacy.DOC"
            legacy_doc.write_bytes(
                b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32
            )
            valid, reason = validate_downloaded_word(legacy_doc)
            self.assertTrue(valid, reason)

            docx = root / "modern.docx"
            with zipfile.ZipFile(docx, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("word/document.xml", "<w:document/>")
            valid, reason = validate_downloaded_word(docx)
            self.assertTrue(valid, reason)

    def test_downloaded_word_rejects_invalid_or_unknown_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = {
                "empty.doc": b"",
                "fake.doc": b"not-an-ole-document",
                "fake.docx": b"not-a-docx-package",
                "policy.pdf": b"%PDF-1.7",
            }
            for name, content in cases.items():
                with self.subTest(name=name):
                    path = root / name
                    path.write_bytes(content)
                    valid, reason = validate_downloaded_word(path)
                    self.assertFalse(valid)
                    self.assertTrue(reason)

    def test_real_acceptance_doc_samples_pass_unified_validation(self) -> None:
        fixture_root = (
            Path(__file__).with_name("fixtures") / "real-word-doc"
        )
        samples = sorted(fixture_root.glob("*.doc"))
        self.assertEqual(len(samples), 3)
        expected_hashes = {
            "中华人民共和国国防动员法(2026修订)(FBM-CLI.1.5351111).doc":
                "c9223bf3db5a2e6365933c2fe019096dec062e79bf673f462d5a64beec264809",
            "中国互联网协会关于征集《电信和互联网行业网络数据安全自律公约》第二批签署单位的通知(FBM-CLI.6.347117).doc":
                "e1c5a3718393633a3d76a39da957bc010eb61580dfc69448ca1be5c40bfcac26",
            "中国互联网协会关于征集《电信和互联网行业网络数据安全自律公约》签署单位履约情况的通知(FBM-CLI.6.351448).doc":
                "f96609c90c54ebf2da219cff8e9d94a27ab708300d568a7f6b8dbdfba5b3079a",
        }
        self.assertEqual({path.name for path in samples}, set(expected_hashes))
        for path in samples:
            with self.subTest(path=path.name):
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    expected_hashes[path.name],
                )
                valid, reason = validate_downloaded_word(path)
                self.assertTrue(valid, reason)

    def test_duplicate_titles_become_one_candidate_group(self) -> None:
        rows = [
            {
                "policyId": "v1",
                "title": "同名政策",
                "url": "https://example.test/pkulaw/detail/v1.html",
                "publicationDate": "2023-01-01",
            },
            {
                "policyId": "v2",
                "title": "同名 政策",
                "url": "https://example.test/pkulaw/detail/v2.html",
                "publicationDate": "2024-01-01",
            },
            {
                "policyId": "unique",
                "title": "唯一政策",
                "url": "https://example.test/pkulaw/detail/unique.html",
            },
        ]
        groups = group_results(rows, identity_mode="title")
        duplicates = next(
            group for group in groups if group["policyKey"] == "title:同名政策"
        )
        self.assertEqual(duplicates["policyKey"], "title:同名政策")
        self.assertEqual(len(duplicates["candidates"]), 2)
        self.assertEqual(duplicates["candidates"][0]["policyId"], "v2")
        self.assertTrue(any(group["policyKey"] == "id:unique" for group in groups))

    def test_keyword_mode_keeps_same_title_policies_by_stable_id(self) -> None:
        rows = [
            {
                "policyId": "policy-1",
                "title": "同名政策",
                "url": "https://example.test/pkulaw/detail/policy-1.html",
                "issuingAuthority": "机关甲",
            },
            {
                "policyId": "policy-2",
                "title": "同名 政策",
                "url": "https://example.test/pkulaw/detail/policy-2.html",
                "publicationDate": "2024-01-01",
            },
        ]

        groups = group_results(rows, identity_mode="keyword")

        self.assertEqual(
            [group["policyKey"] for group in groups],
            ["id:policy-1", "id:policy-2"],
        )
        self.assertTrue(all(len(group["candidates"]) == 1 for group in groups))

    def test_keyword_mode_merges_duplicate_ids_and_fills_metadata(self) -> None:
        rows = [
            {
                "policyId": "Policy-1",
                "title": "同名政策",
                "url": "https://example.test/pkulaw/detail/policy-1.html",
                "query": "碳中和",
            },
            {
                "policyId": "policy-1",
                "title": "同名政策",
                "issuingAuthority": "国务院",
                "publicationDate": "2024-01-01",
                "query": "碳达峰",
            },
        ]

        groups = group_results(rows, identity_mode="keyword")

        self.assertEqual(len(groups), 1)
        candidate = groups[0]["candidates"][0]
        self.assertEqual(candidate["issuingAuthority"], "国务院")
        self.assertEqual(candidate["publicationDate"], "2024-01-01")
        self.assertEqual(
            candidate["observedQueries"],
            ["碳中和", "碳达峰"],
        )

    def test_keyword_mode_without_stable_id_requires_review(self) -> None:
        rows = [
            {
                "title": "没有稳定政策的行",
                "url": "https://example.test/search/result-without-id",
            }
        ]

        groups = group_results(rows, identity_mode="keyword")

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["identityStatus"], "pending_review")
        self.assertEqual(
            groups[0]["identityError"]["code"],
            "STABLE_POLICY_ID_MISSING",
        )
        self.assertEqual(groups[0]["candidates"], [])

    def test_group_results_requires_explicit_identity_mode(self) -> None:
        with self.assertRaises(TypeError):
            group_results([])  # type: ignore[call-arg]

    def test_candidate_score_uses_metadata(self) -> None:
        candidate = {
            "title": "测试政策",
            "documentNumber": "国发〔2024〕1号",
            "publicationDate": "2024-01-01",
            "issuingAuthority": "国务院",
        }
        score = candidate_score(
            candidate,
            {
                "title": "测试政策",
                "documentNumber": "国发〔2024〕1号",
                "publicationDate": "2024-01-01",
                "issuingAuthority": "国务院",
            },
        )
        self.assertEqual(score, 200)
        self.assertEqual(
            candidate_score(
                {"title": "不同标题"},
                {"title": "测试政策"},
            ),
            0,
        )
        self.assertEqual(
            candidate_score(
                {
                    "title": "测试政策",
                    "documentNumber": "错误文号",
                },
                {
                    "title": "测试政策",
                    "documentNumber": "国发〔2024〕1号",
                },
            ),
            100,
        )

    def test_policy_identity_and_extract_policy_id_fallbacks(self) -> None:
        self.assertEqual(
            extract_policy_id(
                "https://www.pkulaw.com/chl/policy-001.html?x=1"
            ),
            "policy-001",
        )
        self.assertEqual(extract_policy_id("not-a-policy-url"), "")
        self.assertEqual(
            policy_identity(
                {
                    "policyId": "Policy-001",
                    "url": "https://www.pkulaw.com/chl/policy-001.html",
                }
            ),
            "id:policy-001",
        )
        self.assertEqual(
            policy_identity(
                {
                    "url": " HTTPS://Example.test/Policy/?x=1#part ",
                }
            ),
            "url:https://example.test/policy",
        )

    def test_word_validation_rejects_shape_and_read_failures(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.docx"
            valid, reason = validate_downloaded_word(missing)
            self.assertFalse(valid)
            self.assertIn("不存在", reason)

            wrong_suffix = root / "policy.pdf"
            wrong_suffix.write_bytes(b"content")
            valid, reason = validate_downloaded_word(wrong_suffix)
            self.assertFalse(valid)
            self.assertIn("扩展名", reason)

            incomplete = root / "incomplete.docx"
            with zipfile.ZipFile(incomplete, "w") as archive:
                archive.writestr("word/document.xml", "<document/>")
            valid, reason = validate_downloaded_word(incomplete)
            self.assertFalse(valid)
            self.assertIn("必要内部文件", reason)

            valid, reason = validate_word_attachment(
                root / "policy.txt",
                ".txt",
            )
            self.assertFalse(valid)
            self.assertIn("不支持", reason)

            short_doc = root / "short.doc"
            short_doc.write_bytes(b"short")
            valid, reason = validate_word_attachment(short_doc, ".doc")
            self.assertFalse(valid)
            self.assertIn("为空", reason)

    def test_policy_filename_is_stable_and_safe(self) -> None:
        name = policy_filename(
            library="central",
            title='关于/测试:*?"政策',
            publication_date="2024-05-01",
            source_url="https://example.test/pkulaw/detail/test-001.html",
            suffix=".docx",
        )
        self.assertIn("中央政策_2024_", name)
        self.assertIn("test-001", name)
        self.assertTrue(name.endswith(".docx"))
        self.assertNotRegex(name, r'[<>:"/\\|?*]')


if __name__ == "__main__":
    unittest.main()
