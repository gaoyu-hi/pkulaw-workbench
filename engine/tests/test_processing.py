from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from docx import Document
from openpyxl import load_workbook

from pkulaw_engine.processing_task import (
    DataProcessingManager,
    can_use_government_fallback,
    materialize_zip_member,
    select_processable_items,
)
from pkulaw_engine.processors.documents import (
    DocumentExtractionError,
    extract_text,
    normalize_text,
)
from pkulaw_engine.processors.reporting import (
    EXCEL_CELL_MAX_CHARS,
    EXCEL_TRUNCATION_MARKER,
    file_sha256,
    stage_master_catalog,
    write_master_catalog,
    write_task_report,
)
from pkulaw_engine.sources.government import (
    GovernmentSourceUnavailable,
    select_exact_official_candidate,
)
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.storage import TaskStore


class ProcessingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_partial_processing_keeps_counts_and_below_complete_progress(self) -> None:
        from pkulaw_engine.controls.task_control import TaskControl

        database = self.root / "partial-progress.sqlite3"
        store = TaskStore(database)
        self.addCleanup(store.close)
        task = store.create_task(
            name="0/3 待核验",
            mode="single",
            task_input={"queries": ["碳中和"], "matchMode": "keyword"},
            output_dir=str(self.root / "partial-progress-output"),
        )
        source = self.root / "source.docx"
        document = Document()
        document.add_paragraph("来源文件存在，但本测试强制注入正文提取失败。")
        document.save(source)
        for index in range(3):
            store.upsert_task_item(
                task["id"],
                policy_key=f"id:partial-{index}",
                title=f"待核验政策 {index}",
                status="completed",
                source_type="北大法宝原件",
                progress=1.0,
                payload={
                    "downloadedFile": str(source),
                    "candidate": {
                        "policyId": f"partial-{index}",
                        "title": f"待核验政策 {index}",
                        "url": f"https://example.test/{index}",
                    },
                },
                current_stage="completed",
            )
        store.upsert_processing_run(
            "run-partial",
            task["id"],
            status="pending",
            progress=0.0,
            current_stage="pending",
        )
        manager = DataProcessingManager(
            database,
            MagicMock(),
            JsonLineLogger(self.root / "processing.jsonl"),
            lambda *_args: None,
        )
        controller = TaskControl(str(task["id"]))

        with patch(
            "pkulaw_engine.processing_task.extract_document",
            side_effect=DocumentExtractionError(
                "DOC_TEXT_EXTRACTION_FAILED",
                "强制转换失败",
            ),
        ):
            manager._run(
                "run-partial",
                str(task["id"]),
                {"governmentFallback": False},
                controller,
            )

        run = store.get_processing_run("run-partial")
        store.close()
        assert run is not None
        snapshot = run["progressSnapshot"]
        self.assertEqual(run["status"], "partial")
        self.assertLess(run["progress"], 1.0)
        self.assertEqual(run["summary"]["processed"], 0)
        self.assertEqual(run["summary"]["pending"], 3)
        self.assertEqual(snapshot["completedUnits"], 3)
        self.assertEqual(snapshot["totalUnits"], 3)
        self.assertEqual(snapshot["succeeded"], 0)
        self.assertEqual(snapshot["failed"], 0)
        self.assertEqual(snapshot["pendingReview"], 3)
        self.assertLess(snapshot["overall"], 1.0)

    def test_processing_uses_only_items_with_real_download_sources(self) -> None:
        from pkulaw_engine.controls.task_control import TaskControl

        database = self.root / "qualified-sources.sqlite3"
        store = TaskStore(database)
        task = store.create_task(
            name="只处理合格下载源",
            mode="single",
            task_input={"queries": ["碳中和"], "matchMode": "keyword"},
            output_dir=str(self.root / "qualified-output"),
        )
        source = self.root / "qualified.docx"
        document = Document()
        document.add_paragraph(
            "第一章 总则\n"
            "第一条 这是具备真实下载来源并应进入数据处理的完整政策正文。\n"
            "第二条 正文内容完整。"
        )
        document.save(source)
        completed = store.upsert_task_item(
            task["id"],
            policy_key="id:qualified",
            title="合格来源政策",
            status="completed",
            source_type="北大法宝原件",
            payload={
                "selectedCandidate": {
                    "policyId": "qualified",
                    "title": "合格来源政策",
                    "url": "https://example.test/qualified",
                },
                "downloadedFile": str(source),
                "detail": {"title": "合格来源政策"},
            },
            current_stage="completed",
        )
        for status, policy_key in (
            ("failed", "id:failed-no-source"),
            ("pending", "id:pending-no-source"),
            ("skipped", "id:skipped-no-source"),
        ):
            store.upsert_task_item(
                task["id"],
                policy_key=policy_key,
                title=f"{status} 无来源政策",
                status=status,
                source_type="",
                payload={},
                error={
                    "code": (
                        "OUT_OF_SCOPE_BY_SAMPLING_ACCEPTANCE"
                        if status == "skipped"
                        else "SOURCE_NOT_COMPLETED"
                    ),
                    "message": "没有可处理下载源",
                },
            )
        store.upsert_processing_run(
            "run-qualified",
            task["id"],
            status="pending",
            progress=0.0,
            current_stage="pending",
        )
        manager = DataProcessingManager(
            database,
            MagicMock(),
            JsonLineLogger(self.root / "qualified.jsonl"),
            lambda *_args: None,
        )
        manager._run(
            "run-qualified",
            str(task["id"]),
            {"governmentFallback": False},
            TaskControl(str(task["id"])),
        )

        run = store.get_processing_run("run-qualified")
        documents = store.list_policy_documents(task["id"])
        items = {
            item["policyKey"]: item["status"]
            for item in store.list_task_items(task["id"])
        }
        assert run is not None
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["summary"]["totalDocuments"], 1)
        self.assertEqual(
            [document["policyKey"] for document in documents],
            [completed["policyKey"]],
        )
        self.assertEqual(
            items,
            {
                "id:qualified": "completed",
                "id:failed-no-source": "failed",
                "id:pending-no-source": "pending",
                "id:skipped-no-source": "skipped",
            },
        )
        store.close()

    def test_stop_does_not_overwrite_newer_completed_checkpoint(self) -> None:
        import time

        database = self.root / "stop-race.sqlite3"
        store = TaskStore(database)
        task = store.create_task(
            name="停止竞态",
            mode="single",
            task_input={"queries": ["测试"]},
            output_dir=str(self.root / "stop-race-output"),
        )
        initial_snapshot = {
            "overall": 0.575,
            "stage": "documents",
            "completedUnits": 7,
            "totalUnits": 12,
            "succeeded": 7,
            "failed": 0,
            "pendingReview": 0,
        }
        store.upsert_processing_run(
            "run-stop-race",
            task["id"],
            status="running",
            progress=0.575,
            current_stage="document-7-12",
            summary={
                "completedDocuments": 7,
                "totalDocuments": 12,
                "currentAction": "after-policy",
                "progressSnapshot": initial_snapshot,
            },
        )
        manager = DataProcessingManager(
            database,
            MagicMock(),
            JsonLineLogger(self.root / "stop-race.jsonl"),
            lambda *_args: None,
        )

        class AliveThread:
            def is_alive(self) -> bool:
                return True

        class RacingController:
            def __init__(self) -> None:
                self.condition = threading.Condition()
                self.thread = AliveThread()

            def request_stop(self) -> bool:
                newer_snapshot = {
                    "overall": 0.65,
                    "stage": "documents",
                    "completedUnits": 8,
                    "totalUnits": 12,
                    "succeeded": 8,
                    "failed": 0,
                    "pendingReview": 0,
                }
                racing_store = TaskStore(database)
                try:
                    racing_store.upsert_processing_run(
                        "run-stop-race",
                        task["id"],
                        status="running",
                        progress=0.65,
                        current_stage="document-8-12",
                        summary={
                            "completedDocuments": 8,
                            "totalDocuments": 12,
                            "currentAction": "after-policy",
                            "progressSnapshot": newer_snapshot,
                        },
                    )
                finally:
                    racing_store.close()
                time.sleep(0.01)
                return True

        manager.controllers["run-stop-race"] = RacingController()  # type: ignore[assignment]
        manager.stop("run-stop-race")

        run = store.get_processing_run("run-stop-race")
        assert run is not None
        try:
            self.assertEqual(run["summary"]["completedDocuments"], 8)
            self.assertEqual(run["progressSnapshot"]["overall"], 0.65)
            self.assertEqual(run["progress"], 0.65)
        finally:
            store.close()

    def test_government_fallback_boundary(self) -> None:
        exact_task = {
            "mode": "single",
            "input": {"matchMode": "title"},
        }
        broad_task = {
            "mode": "single",
            "input": {"matchMode": "keyword"},
        }
        missing_item = {
            "status": "failed",
            "error": {"code": "NO_EXACT_MATCH"},
        }
        access_item = {
            "status": "failed",
            "error": {"code": "INSTITUTION_QUOTA_REACHED"},
        }
        ambiguous_item = {
            "status": "waiting_manual",
            "error": None,
        }
        self.assertTrue(
            can_use_government_fallback(
                exact_task, missing_item, {"governmentFallback": True}
            )
        )
        self.assertFalse(
            can_use_government_fallback(
                broad_task, missing_item, {"governmentFallback": True}
            )
        )
        self.assertFalse(
            can_use_government_fallback(
                exact_task, access_item, {"governmentFallback": True}
            )
        )
        self.assertFalse(
            can_use_government_fallback(
                exact_task, ambiguous_item, {"governmentFallback": True}
            )
        )
        self.assertFalse(
            can_use_government_fallback(
                exact_task, missing_item, {"governmentFallback": False}
            )
        )

    def test_completed_government_source_remains_processable(self) -> None:
        task = {
            "mode": "single",
            "input": {"matchMode": "title"},
        }
        item = {
            "status": "completed",
            "payload": {
                "governmentDocumentId": "government-document-1",
                "sourceUrl": "https://example.gov.cn/policy",
            },
        }

        self.assertTrue(
            can_use_government_fallback(
                task,
                item,
                {"governmentFallback": True},
            )
        )
        self.assertEqual(
            select_processable_items(
                task,
                [item],
                {"governmentFallback": True},
            ),
            [item],
        )
        self.assertEqual(
            select_processable_items(
                task,
                [item],
                {"governmentFallback": False},
            ),
            [],
        )

    def test_docx_extraction_and_invalid_docx(self) -> None:
        from pkulaw_engine.processors.documents import extract_document

        path = self.root / "policy.docx"
        document = Document()
        document.add_heading("测试政策", level=1)
        document.add_paragraph("这是用于验证正文提取的完整政策内容。")
        document.save(path)
        text, method = extract_text(path)
        self.assertIn("这是用于验证正文提取的完整政策内容", text)
        self.assertEqual(method, "python-docx")
        result = extract_document(path)
        self.assertEqual(result.artifact_method, "python-docx")
        self.assertEqual(result.text_source_type, "word-text")
        self.assertEqual(result.converter_name, "python-docx")
        self.assertTrue(result.converter_version)

        broken = self.root / "broken.docx"
        broken.write_bytes(b"not-a-docx")
        with self.assertRaises(DocumentExtractionError):
            extract_text(broken)

    def test_all_real_doc_samples_extract_with_bundled_converter(self) -> None:
        from pkulaw_engine.processors.documents import extract_document

        engine_root = Path(__file__).parents[1]
        samples = sorted(
            (Path(__file__).with_name("fixtures") / "real-word-doc").glob("*.doc")
        )
        self.assertEqual(len(samples), 3)
        with (
            patch(
                "pkulaw_engine.processors.documents.shutil.which",
                return_value=None,
            ),
            patch.dict(
                sys.modules,
                {
                    "win32com": None,
                    "win32com.client": None,
                    "pythoncom": None,
                },
            ),
        ):
            results = [
                extract_document(sample, runtime_root=engine_root)
                for sample in samples
            ]

        for sample, result in zip(samples, results, strict=True):
            with self.subTest(sample=sample.name):
                self.assertGreater(len(result.text), 100)
                self.assertEqual(result.artifact_method, "bundled-antiword")
                self.assertEqual(result.text_source_type, "word-text")
                self.assertEqual(result.converter_name, "antiword")
                self.assertEqual(result.converter_version, "0.37")
                self.assertEqual(
                    result.converter_sha256,
                    "1dde37af1bb049c03e71c9d20eb99a2a3e011fe9170306b3a154be5c8515788f",
                )

    def test_bundled_converter_receives_short_temporary_path(
        self,
    ) -> None:
        from pkulaw_engine.processors import documents

        sample = next(
            (Path(__file__).with_name("fixtures") / "real-word-doc").glob("*.doc")
        )
        long_dir = self.root
        for index in range(8):
            long_dir = long_dir / (
                f"{index}-" + ("超长政策目录" * 8)
            )
        long_dir.mkdir(parents=True)
        source = long_dir / "完整长标题政策样本.doc"
        shutil.copy2(sample, source)
        self.assertGreater(len(str(source)), 260)

        def fake_runner(path: Path, _binary: Path) -> str:
            self.assertLess(len(str(path)), 260)
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), sample.read_bytes())
            return "这是通过短临时路径成功提取的完整政策正文内容。"

        with (
            patch.object(
                documents,
                "_validate_bundled_antiword",
                return_value=None,
            ),
            patch.object(
                documents,
                "_run_bundled_antiword",
                side_effect=fake_runner,
            ),
            patch.object(documents.shutil, "which", return_value=None),
            patch.dict(
                sys.modules,
                {
                    "win32com": None,
                    "win32com.client": None,
                    "pythoncom": None,
                },
            ),
        ):
            result = documents.extract_document(
                source,
                runtime_root=Path(__file__).parents[1],
            )

        self.assertIn("短临时路径", result.text)
        self.assertEqual(result.converter_name, "antiword")

    def test_vendored_antiword_manifest_matches_locked_files(self) -> None:
        vendor_root = Path(__file__).parents[1] / "vendor" / "antiword"
        manifest = json.loads(
            (vendor_root / "MANIFEST.json").read_text(encoding="utf-8")
        )
        binary = vendor_root / manifest["binary"]
        source = vendor_root / manifest["sourceArchive"]
        patch = (
            vendor_root
            / "0001-Make-antiword.exe-relocatable-on-Windows.patch"
        )
        self.assertEqual(
            hashlib.sha256(binary.read_bytes()).hexdigest(),
            manifest["binarySha256"],
        )
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest(),
            manifest["sourceSha256"],
        )
        self.assertEqual(
            hashlib.sha256(patch.read_bytes()).hexdigest(),
            manifest["relocatablePatchSha256"],
        )
        license_path = binary.parent / "LICENSE.txt"
        self.assertTrue(license_path.is_file())
        self.assertEqual(
            hashlib.sha256(license_path.read_bytes()).hexdigest(),
            manifest["licenseSha256"],
        )
        self.assertTrue((vendor_root / "SOURCE.md").is_file())

    def test_bundled_converter_missing_and_corrupt_fail_unavailable(self) -> None:
        from pkulaw_engine.processors.documents import extract_document

        sample = next(
            (Path(__file__).with_name("fixtures") / "real-word-doc").glob("*.doc")
        )
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory)
            with (
                patch(
                    "pkulaw_engine.processors.documents.shutil.which",
                    return_value=None,
                ),
                patch.dict(
                    sys.modules,
                    {
                        "win32com": None,
                        "win32com.client": None,
                        "pythoncom": None,
                    },
                ),
            ):
                with self.assertRaises(DocumentExtractionError) as missing:
                    extract_document(sample, runtime_root=runtime_root)

                binary = (
                    runtime_root
                    / "vendor"
                    / "antiword"
                    / "win-x64"
                    / "antiword.exe"
                )
                binary.parent.mkdir(parents=True)
                binary.write_bytes(b"not-the-locked-binary")
                with self.assertRaises(DocumentExtractionError) as corrupt:
                    extract_document(sample, runtime_root=runtime_root)

        self.assertEqual(
            getattr(missing.exception, "code", ""),
            "DOC_CONVERTER_UNAVAILABLE",
        )
        self.assertEqual(
            getattr(corrupt.exception, "code", ""),
            "DOC_CONVERTER_UNAVAILABLE",
        )

    def test_bundled_converter_version_mismatch_and_timeout_fail_closed(self) -> None:
        import pkulaw_engine.processors.documents as documents

        sample = next(
            (Path(__file__).with_name("fixtures") / "real-word-doc").glob("*.doc")
        )
        engine_root = Path(__file__).parents[1]
        common_patches = (
            patch.object(
                documents.shutil,
                "which",
                return_value=None,
            ),
            patch.dict(
                sys.modules,
                {
                    "win32com": None,
                    "win32com.client": None,
                    "pythoncom": None,
                },
            ),
        )
        with common_patches[0], common_patches[1]:
            with patch.object(
                documents,
                "EXPECTED_ANTIWORD_VERSION",
                "9.99",
            ):
                with self.assertRaises(DocumentExtractionError) as mismatch:
                    documents.extract_document(
                        sample,
                        runtime_root=engine_root,
                    )

        with (
            patch.object(documents.shutil, "which", return_value=None),
            patch.dict(
                sys.modules,
                {
                    "win32com": None,
                    "win32com.client": None,
                    "pythoncom": None,
                },
            ),
            patch.object(
                documents,
                "_validate_bundled_antiword",
                return_value=None,
            ),
            patch.object(
                documents.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(
                    cmd="antiword",
                    timeout=1,
                ),
            ),
        ):
            with self.assertRaises(DocumentExtractionError) as timeout:
                documents.extract_document(
                    sample,
                    runtime_root=engine_root,
                )

        self.assertEqual(
            getattr(mismatch.exception, "code", ""),
            "DOC_CONVERTER_UNAVAILABLE",
        )
        self.assertEqual(
            getattr(timeout.exception, "code", ""),
            "DOC_TEXT_EXTRACTION_FAILED",
        )

    def test_conversion_failure_does_not_publish_txt_or_processed_document(
        self,
    ) -> None:
        database = self.root / "processing.sqlite3"
        store = TaskStore(database)
        try:
            task = store.create_task(
                name="转换失败不发布",
                mode="batch",
                task_input={"queries": ["测试"]},
                output_dir=str(self.root / "output"),
            )
            archive = self.root / "batch.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("policy.doc", b"bad-doc")
            item = store.upsert_task_item(
                task["id"],
                policy_key="id:policy-1",
                title="测试政策",
                status="completed",
                source_type="北大法宝ZIP",
                payload={
                    "candidate": {
                        "policyId": "policy-1",
                        "title": "测试政策",
                    },
                    "archive": {
                        "archiveMember": "policy.doc",
                    },
                    "zipFile": str(archive),
                },
            )
            manager = DataProcessingManager(
                database,
                MagicMock(),
                JsonLineLogger(self.root / "engine.jsonl"),
                lambda *_args: None,
            )
            txt_root = self.root / "txt"
            with patch(
                "pkulaw_engine.processing_task.extract_document",
                side_effect=DocumentExtractionError(
                    "DOC_CONVERTER_UNAVAILABLE",
                    "内置转换器不可用",
                ),
            ):
                document = manager._process_batch_item(
                    store,
                    task,
                    item,
                    self.root / "source",
                    txt_root,
                    self.root / "evidence",
                    self.root / "pending",
                )

            self.assertEqual(document["status"], "pending_review")
            self.assertEqual(document["sourceType"], "北大法宝ZIP")
            self.assertEqual(document["textSourceType"], "")
            self.assertEqual(document["extractionMethod"], "")
            self.assertEqual(document["bodyCompleteness"], "failed")
            self.assertEqual(document["reviewStatus"], "pending_review")
            self.assertEqual(
                document["error"]["code"],
                "DOC_CONVERTER_UNAVAILABLE",
            )
            self.assertFalse(
                txt_root.exists()
                and any(txt_root.glob("*.txt"))
            )
        finally:
            store.close()

    def test_single_conversion_failure_preserves_pkulaw_original_source(
        self,
    ) -> None:
        database = self.root / "single-failure.sqlite3"
        store = TaskStore(database)
        try:
            task = store.create_task(
                name="单篇转换失败",
                mode="single",
                task_input={"matchMode": "title"},
                output_dir=str(self.root / "single-output"),
            )
            word = self.root / "single.docx"
            document = Document()
            document.add_paragraph("这是已经成功取得但正文转换失败的北大法宝原始文件。")
            document.save(word)
            item = store.upsert_task_item(
                task["id"],
                policy_key="id:single-1",
                title="单篇政策",
                status="completed",
                source_type="北大法宝原件",
                payload={
                    "selectedCandidate": {
                        "policyId": "single-1",
                        "title": "单篇政策",
                        "url": "https://example.test/single-1",
                    },
                    "downloadedFile": str(word),
                    "detail": {"title": "单篇政策"},
                },
            )
            manager = DataProcessingManager(
                database,
                MagicMock(),
                JsonLineLogger(self.root / "single-engine.jsonl"),
                lambda *_args: None,
            )
            with patch(
                "pkulaw_engine.processing_task.extract_document",
                side_effect=DocumentExtractionError(
                    "DOC_TEXT_EXTRACTION_FAILED",
                    "转换失败",
                ),
            ):
                result = manager._process_single_item(
                    store,
                    task,
                    item,
                    self.root / "single-source",
                    self.root / "single-txt",
                    self.root / "single-evidence",
                    self.root / "single-pending",
                )

            self.assertEqual(result["sourceType"], "北大法宝原件")
            self.assertEqual(result["bodyCompleteness"], "failed")
            self.assertEqual(result["reviewStatus"], "pending_review")
            self.assertEqual(result["textSourceType"], "")
        finally:
            store.close()

    def test_docx_and_doc_write_actual_converter_provenance(self) -> None:
        database = self.root / "converter-provenance.sqlite3"
        store = TaskStore(database)
        try:
            manager = DataProcessingManager(
                database,
                MagicMock(),
                JsonLineLogger(self.root / "converter-engine.jsonl"),
                lambda *_args: None,
            )
            task = store.create_task(
                name="转换器来源",
                mode="single",
                task_input={"matchMode": "title"},
                output_dir=str(self.root / "converter-output"),
            )
            docx_path = self.root / "modern.docx"
            modern = Document()
            modern.add_paragraph(
                "第一章 总则\n第一条 这是用于验证 DOCX 转换器来源的完整正文。\n"
                "第二条 正文内容完整。"
            )
            modern.save(docx_path)
            legacy_path = next(
                path
                for path in (
                    Path(__file__).with_name("fixtures")
                    / "real-word-doc"
                ).glob("*.doc")
                if path.name.startswith("中国互联网协会")
            )
            cases = (
                (docx_path, "python-docx", "python-docx"),
                (legacy_path, "bundled-antiword", "antiword"),
            )
            for index, (source, method, converter) in enumerate(cases, start=1):
                with self.subTest(method=method):
                    item = store.upsert_task_item(
                        task["id"],
                        policy_key=f"id:converter-{index}",
                        title=f"转换器政策 {index}",
                        status="completed",
                        source_type="北大法宝原件",
                        payload={
                            "selectedCandidate": {
                                "policyId": f"converter-{index}",
                                "title": f"转换器政策 {index}",
                                "url": f"https://example.test/converter-{index}",
                            },
                            "downloadedFile": str(source),
                            "detail": {"title": f"转换器政策 {index}"},
                        },
                    )
                    document = manager._process_single_item(
                        store,
                        task,
                        item,
                        self.root / f"converter-source-{index}",
                        self.root / f"converter-txt-{index}",
                        self.root / f"converter-evidence-{index}",
                        self.root / f"converter-pending-{index}",
                    )
                    self.assertEqual(document["status"], "processed")
                    self.assertEqual(document["sourceType"], "北大法宝原件")
                    self.assertEqual(document["textSourceType"], "word-text")
                    self.assertEqual(document["extractionMethod"], method)
                    self.assertEqual(document["converterName"], converter)
                    self.assertTrue(document["converterVersion"])
                    self.assertEqual(document["bodyCompleteness"], "complete")
                    self.assertEqual(document["reviewStatus"], "not_required")
        finally:
            store.close()

    def test_suspicious_word_body_is_published_for_review(self) -> None:
        database = self.root / "suspicious.sqlite3"
        store = TaskStore(database)
        try:
            task = store.create_task(
                name="可疑正文",
                mode="single",
                task_input={"matchMode": "title"},
                output_dir=str(self.root / "suspicious-output"),
            )
            word = self.root / "suspicious.docx"
            document = Document()
            document.add_paragraph(
                "第一章 总则\n第一条 这一条存在。\n"
                "第三条 第二条缺失，因此本节应待核验。"
            )
            document.save(word)
            item = store.upsert_task_item(
                task["id"],
                policy_key="id:suspicious-1",
                title="可疑政策",
                status="completed",
                source_type="北大法宝原件",
                payload={
                    "selectedCandidate": {
                        "policyId": "suspicious-1",
                        "title": "可疑政策",
                    },
                    "downloadedFile": str(word),
                    "detail": {"title": "可疑政策"},
                },
            )
            manager = DataProcessingManager(
                database,
                MagicMock(),
                JsonLineLogger(self.root / "suspicious-engine.jsonl"),
                lambda *_args: None,
            )

            result = manager._process_single_item(
                store,
                task,
                item,
                self.root / "suspicious-source",
                self.root / "suspicious-txt",
                self.root / "suspicious-evidence",
                self.root / "suspicious-pending",
            )

            self.assertEqual(result["status"], "pending_review")
            self.assertEqual(result["bodyCompleteness"], "suspicious")
            self.assertEqual(result["reviewStatus"], "pending_review")
            self.assertTrue(Path(result["txtPath"]).is_file())
        finally:
            store.close()

    def test_complete_detail_body_supersedes_suspicious_word_body(self) -> None:
        database = self.root / "detail-fallback.sqlite3"
        store = TaskStore(database)
        try:
            task = store.create_task(
                name="详情正文补全",
                mode="single",
                task_input={"matchMode": "title"},
                output_dir=str(self.root / "detail-fallback-output"),
            )
            word = self.root / "detail-fallback.docx"
            document = Document()
            for line in (
                "目录",
                "第一章 总则",
                "第二章 工作要求",
                "第三章 附则",
                "第一章 总则",
                "第一节 目的",
                "Word 文件只保留了第一章正文。",
                "第三章 附则",
                "Word 文件只保留了第三章正文。",
            ):
                document.add_paragraph(line)
            document.save(word)
            page_body = "\n".join(
                (
                    "目录",
                    "第一章 总则",
                    "第二章 工作要求",
                    "第三章 附则",
                    "第一章 总则",
                    "第一节 目的",
                    "详情页第一章正文完整存在。",
                    "第二章 工作要求",
                    "详情页第二章正文完整存在。",
                    "第三章 附则",
                    "详情页第三章正文完整存在。",
                )
            )
            item = store.upsert_task_item(
                task["id"],
                policy_key="id:detail-fallback",
                title="详情页补全政策",
                status="completed",
                source_type="北大法宝原件",
                payload={
                    "selectedCandidate": {
                        "policyId": "detail-fallback",
                        "title": "详情页补全政策",
                    },
                    "downloadedFile": str(word),
                    "detail": {
                        "title": "详情页补全政策",
                        "body": page_body,
                    },
                },
            )
            manager = DataProcessingManager(
                database,
                MagicMock(),
                JsonLineLogger(self.root / "detail-fallback.jsonl"),
                lambda *_args: None,
            )

            result = manager._process_single_item(
                store,
                task,
                item,
                self.root / "detail-fallback-source",
                self.root / "detail-fallback-txt",
                self.root / "detail-fallback-evidence",
                self.root / "detail-fallback-pending",
            )

            self.assertEqual(result["status"], "processed")
            self.assertEqual(result["sourceType"], "北大法宝原件")
            self.assertEqual(result["textSourceType"], "detail-page-text")
            self.assertEqual(result["extractionMethod"], "detail-page-fallback")
            self.assertEqual(result["bodyCompleteness"], "complete")
            self.assertEqual(result["reviewStatus"], "not_required")
            text = Path(result["txtPath"]).read_text(encoding="utf-8")
            self.assertIn("详情页第二章正文完整存在", text)
        finally:
            store.close()

    def test_zip_member_materialization_creates_nested_parent(self) -> None:
        archive_path = self.root / "words.zip"
        member = "中央法规/政策01.docx"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(member, b"validated-word-bytes")
        destination = self.root / "processed" / "01_来源Word" / "中央法规" / "政策01.docx"

        materialize_zip_member(archive_path, member, destination)

        self.assertEqual(destination.read_bytes(), b"validated-word-bytes")
        self.assertFalse(Path(str(destination) + ".tmp").exists())

    def test_government_requires_one_exact_official_candidate(self) -> None:
        official = {
            "title": "测试 政策",
            "url": "https://www.gov.cn/zhengce/test.html",
            "official": "true",
        }
        selected = select_exact_official_candidate(
            [
                {
                    "title": "近似政策",
                    "url": "https://www.gov.cn/zhengce/near.html",
                    "official": "true",
                },
                official,
            ],
            "测试政策",
            fixture_mode=False,
        )
        self.assertEqual(selected["url"], official["url"])

        with self.assertRaises(GovernmentSourceUnavailable):
            select_exact_official_candidate(
                [
                    official,
                    {
                        "title": "测试政策",
                        "url": "https://www.gov.cn/zhengce/duplicate.html",
                        "official": "true",
                    },
                ],
                "测试政策",
                fixture_mode=False,
            )
        with self.assertRaises(GovernmentSourceUnavailable):
            select_exact_official_candidate(
                [
                    {
                        "title": "测试政策",
                        "url": "https://example.test/test.html",
                        "official": "false",
                    }
                ],
                "测试政策",
                fixture_mode=False,
            )

    def test_master_catalog_and_report(self) -> None:
        word = self.root / "word.docx"
        txt = self.root / "policy.txt"
        evidence = self.root / "evidence.json"
        word.write_bytes(b"word")
        txt.write_text("这是测试正文内容，长度足够用于目录生成。", encoding="utf-8")
        evidence.write_text("{}", encoding="utf-8")
        documents = [
            {
                "taskId": "t1",
                "policyKey": "id:p1",
                "policyId": "p1",
                "title": "测试政策",
                "documentNumber": "测发〔2024〕1号",
                "issuingAuthority": "测试机关",
                "publicationDate": "2024-01-01",
                "effectiveDate": "2024-02-01",
                "validity": "现行有效",
                "effectLevel": "规范性文件",
                "sourceType": "北大法宝原件",
                "textSourceType": "word-text",
                "extractionMethod": "bundled-antiword",
                "converterName": "antiword",
                "converterVersion": "0.37",
                "bodyCompleteness": "complete",
                "reviewStatus": "not_required",
                "sourceUrl": "https://example.test/p1",
                "retrievedAt": "2024-01-02T00:00:00Z",
                "retrievalMethod": "单篇 Word 下载",
                "wordPath": str(word),
                "txtPath": str(txt),
                "evidencePath": str(evidence),
                "sha256": "abc",
                "status": "processed",
                "error": None,
            }
        ]
        output = self.root / "processed"
        output.mkdir()
        csv_path, xlsx_path = write_master_catalog(documents, output)
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            self.assertEqual(len(rows), 1)
            self.assertIn("正文来源", reader.fieldnames or [])
            self.assertIn("提取方法", reader.fieldnames or [])
            self.assertIn("转换器名称", reader.fieldnames or [])
            self.assertIn("转换器版本", reader.fieldnames or [])
            self.assertIn("正文完整性", reader.fieldnames or [])
            self.assertIn("核验状态", reader.fieldnames or [])
            self.assertEqual(rows[0]["正文来源"], "word-text")
            self.assertEqual(rows[0]["提取方法"], "bundled-antiword")
            self.assertEqual(rows[0]["转换器名称"], "antiword")
            self.assertEqual(rows[0]["转换器版本"], "0.37")
            self.assertEqual(rows[0]["正文完整性"], "complete")
            self.assertEqual(rows[0]["核验状态"], "not_required")
            self.assertIn("Word SHA256", reader.fieldnames or [])
            self.assertIn("官方截图证据", reader.fieldnames or [])
        workbook = load_workbook(xlsx_path, read_only=True)
        try:
            self.assertEqual(workbook.active.max_row, 2)
            headers = [
                cell.value
                for cell in next(workbook.active.iter_rows(max_row=1))
            ]
            for header in (
                "正文来源",
                "提取方法",
                "转换器名称",
                "转换器版本",
                "正文完整性",
                "核验状态",
            ):
                self.assertIn(header, headers)
            values = {
                header: workbook.active.cell(2, index + 1).value
                for index, header in enumerate(headers)
            }
            self.assertEqual(values["正文来源"], "word-text")
            self.assertEqual(values["提取方法"], "bundled-antiword")
            self.assertEqual(values["转换器名称"], "antiword")
            self.assertEqual(values["转换器版本"], "0.37")
            self.assertEqual(values["正文完整性"], "complete")
            self.assertEqual(values["核验状态"], "not_required")
        finally:
            workbook.close()
        report = write_task_report(
            output_root=output,
            task={"name": "测试任务"},
            processing_run={"id": "run-1"},
            documents=documents,
            artifacts={
                "总目录CSV": str(csv_path),
                "总目录XLSX": str(xlsx_path),
            },
        )
        self.assertTrue(report.is_file())
        report_html = report.read_text(encoding="utf-8")
        self.assertIn(
            "政策采集任务验收报告",
            report_html,
        )
        self.assertIn(file_sha256(csv_path), report_html)
        self.assertIn(file_sha256(xlsx_path), report_html)
        self.assertIn("正文来源", report_html)
        self.assertIn("bundled-antiword", report_html)
        self.assertIn("0.37", report_html)
        self.assertIn("complete", report_html)
        self.assertIn("not_required", report_html)
        payload = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(payload, {})

    def test_master_catalog_neutralizes_spreadsheet_formulas(self) -> None:
        formulas = (
            '=HYPERLINK("https://example.test","点击")',
            "+SUM(1,2)",
            "-2+3",
            "@SUM(1,1)",
            "\t=1+1",
            "\r+1+1",
            "\n-1-1",
        )
        for index, formula in enumerate(formulas, start=1):
            with self.subTest(formula=formula):
                output = self.root / f"formula-{index}"
                output.mkdir()
                documents = [
                    {
                        "policyId": f"formula-{index}",
                        "title": formula,
                        "status": "processed",
                        "sourceType": "北大法宝原件",
                        "sourceUrl": "https://example.test/policy",
                        "retrievedAt": "2026-11-01T00:00:00Z",
                        "retrievalMethod": "test",
                        "sha256": "",
                        "payload": {},
                        "error": None,
                    }
                ]
                csv_path, xlsx_path = write_master_catalog(documents, output)
                with csv_path.open(
                    encoding="utf-8-sig",
                    newline="",
                ) as stream:
                    csv_title = next(csv.DictReader(stream))["标题"]
                self.assertTrue(csv_title.startswith("'"))

                workbook = load_workbook(xlsx_path, data_only=False)
                try:
                    sheet = workbook.active
                    headers = [cell.value for cell in sheet[1]]
                    title_column = headers.index("标题") + 1
                    cell = sheet.cell(2, title_column)
                    self.assertEqual(cell.value, formula)
                    self.assertEqual(cell.data_type, "s")
                finally:
                    workbook.close()

    def test_master_catalog_handles_very_long_cell(self) -> None:
        output = self.root / "long-cell"
        output.mkdir()
        long_title = "长" * 40_000
        documents = [
            {
                "policyId": "long-cell",
                "title": long_title,
                "status": "processed",
                "sourceType": "北大法宝原件",
                "sourceUrl": "https://example.test/policy",
                "retrievedAt": "2026-11-01T00:00:00Z",
                "retrievalMethod": "test",
                "sha256": "",
                "payload": {},
                "error": None,
            }
        ]
        csv_path, xlsx_path = write_master_catalog(documents, output)
        with csv_path.open(
            encoding="utf-8-sig",
            newline="",
        ) as stream:
            self.assertEqual(next(csv.DictReader(stream))["标题"], long_title)
        workbook = load_workbook(xlsx_path, data_only=False)
        try:
            title = str(workbook.active.cell(2, 2).value)
            self.assertEqual(len(title), EXCEL_CELL_MAX_CHARS)
            self.assertTrue(title.endswith(EXCEL_TRUNCATION_MARKER))
        finally:
            workbook.close()

    def test_staged_catalog_and_report_do_not_touch_existing_outputs(self) -> None:
        output = self.root / "staged-output"
        output.mkdir()
        final_csv = output / "06_总目录.csv"
        final_xlsx = output / "06_总目录.xlsx"
        final_report = output / "07_任务报告.html"
        final_csv.write_text("old-csv", encoding="utf-8")
        final_xlsx.write_bytes(b"old-xlsx")
        final_report.write_text("old-report", encoding="utf-8")
        staging = output / ".processing-publishing" / "run-1"
        documents = [
            {
                "policyId": "stage-1",
                "title": "暂存政策",
                "status": "processed",
                "sourceType": "北大法宝原件",
                "sourceUrl": "https://example.test/stage-1",
                "retrievedAt": "2026-11-01T00:00:00Z",
                "retrievalMethod": "test",
                "sha256": "",
                "payload": {},
                "error": None,
            }
        ]

        staged_csv, staged_xlsx = stage_master_catalog(
            documents,
            output,
            staging,
        )
        staged_report = write_task_report(
            output_root=output,
            task={"name": "暂存任务"},
            processing_run={"id": "run-1"},
            documents=documents,
            artifacts={
                "总目录CSV": str(staged_csv),
                "总目录XLSX": str(staged_xlsx),
            },
            report_path=staging / "07_任务报告.html",
            display_artifacts={
                "总目录CSV": str(final_csv),
                "总目录XLSX": str(final_xlsx),
            },
        )

        self.assertTrue(staged_csv.is_file())
        self.assertTrue(staged_xlsx.is_file())
        self.assertTrue(staged_report.is_file())
        self.assertEqual(final_csv.read_text(encoding="utf-8"), "old-csv")
        self.assertEqual(final_xlsx.read_bytes(), b"old-xlsx")
        self.assertEqual(
            final_report.read_text(encoding="utf-8"),
            "old-report",
        )
        report_html = staged_report.read_text(encoding="utf-8")
        self.assertIn(str(final_csv), report_html)
        self.assertIn(file_sha256(staged_csv), report_html)

    def test_normalize_text(self) -> None:
        self.assertEqual(
            normalize_text("第一段 \r\n\r\n\r\n 第二段\x00"),
            "第一段\n\n第二段",
        )


if __name__ == "__main__":
    unittest.main()
