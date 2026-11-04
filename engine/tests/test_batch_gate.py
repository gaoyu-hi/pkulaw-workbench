from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pkulaw_engine.atomic_publish import (
    prepare_publish_directory,
    publish_directory,
)
from pkulaw_engine.batch_task import BatchTaskConfig, BatchTaskManager
from pkulaw_engine.batch_execution import BatchExecutionContextError
from pkulaw_engine.catalog_formats import (
    CatalogFormatError,
    CatalogPreparation,
    resolve_catalog_format,
)
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.selections.pagination import PageSelection
from pkulaw_engine.selections.years import YearSelection
from pkulaw_engine.sources.search_transaction import SearchSession
from pkulaw_engine.storage import TaskStore
from pkulaw_engine.validators.catalog import CatalogValidationError
from pkulaw_engine.validators.archive import ArchiveValidationError


REAL_XLS = (
    Path(__file__).parent
    / "fixtures"
    / "real-catalog-xls"
    / "碳中和_2026_西藏_目录.xls"
)

REAL_SELECTED = [
    {
        "number": 1,
        "policyId": "a8ac5e480a1e7a211bf6441e182ae98cbdfb",
        "title": "拉萨市科学技术局关于组织申报2026年度市级科技创新平台的通知",
        "url": "https://www.pkulaw.com/law/detail/a8ac5e480a1e7a211bf6441e182ae98cbdfb.html",
        "library": "local",
        "year": "2026",
    },
    {
        "number": 2,
        "policyId": "5f8345a17a6e0520bc594f4309849eb8bdfb",
        "title": "西藏自治区发展和改革委员会关于印发《西藏自治区固定资产投资项目节能审查和碳排放评价实施办法》的通知",
        "url": "https://www.pkulaw.com/law/detail/5f8345a17a6e0520bc594f4309849eb8bdfb.html",
        "library": "local",
        "year": "2026",
    },
    {
        "number": 3,
        "policyId": "e697899c936baab42b729a33f7cfabc2bdfb",
        "title": "拉萨市人民政府国有资产监督管理委员会关于新时代中央企业高质量推进品牌建设的意见",
        "url": "https://www.pkulaw.com/law/detail/e697899c936baab42b729a33f7cfabc2bdfb.html",
        "library": "local",
        "year": "2026",
    },
]


class _BatchAdapter:
    search_url = "https://www.pkulaw.com/chl/search"

    def __init__(
        self,
        state: dict[str, object],
        *,
        verification_ok: bool = True,
        change_after_catalog: bool = False,
    ) -> None:
        self.state = state
        self.verification_ok = verification_ok
        self.change_after_catalog = change_after_catalog
        self.downloads: list[str] = []
        self.download_calls: list[dict[str, object]] = []

    def select_batch(self, _selected: list[dict[str, object]]) -> dict[str, object]:
        return {"ok": True}

    def verify_batch_selection(
        self,
        selected: list[dict[str, object]],
    ) -> dict[str, object]:
        if not self.verification_ok:
            raise RuntimeError("batch row identity mismatch")
        return {
            "ok": True,
            "page": 1,
            "expectedCount": len(selected),
            "actualCount": len(selected),
            "checked": [str(item["policyId"]) for item in selected],
        }

    def read_results(self) -> dict[str, object]:
        return dict(self.state)

    def download_batch_file(self, *, kind: str, **kwargs: object) -> dict[str, object]:
        self.downloads.append(kind)
        self.download_calls.append({"kind": kind, **kwargs})
        if kind == "catalog" and self.change_after_catalog:
            session = dict(self.state["searchSession"])  # type: ignore[arg-type]
            session["resultSignature"] = "changed-after-catalog"
            session["searchId"] = "changed-search"
            self.state["searchSession"] = session
        return {"ok": True}


class BatchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        logs = self.root / "logs"
        logs.mkdir()
        self.logger = JsonLineLogger(logs / "engine.jsonl")
        self.store = TaskStore(self.root / "pkulaw.sqlite3")
        self.task = self.store.create_task(
            name="批量门禁",
            mode="batch",
            task_input={"queries": ["批量测试"]},
            output_dir=str(self.root / "output"),
        )
        self.config = BatchTaskConfig(
            queries=("批量测试",),
            match_mode="keyword",
            library="central",
            year_selection=YearSelection(mode="all"),
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            page_size=50,
            page_selection=PageSelection(mode="last"),
            output_dir=(self.root / "output").resolve(),
            catalog_format="xlsx",
            full_format="docx",
        )
        self.selected = [
            {
                "number": 1,
                "policyId": "policy-1",
                "title": "批量政策",
                "url": "https://www.pkulaw.com/chl/policy-1.html",
                "library": "central",
                "year": "全部年份",
            }
        ]
        self.batch = {
            "batchKey": "batch:test",
            "query": "批量测试",
            "library": "central",
            "year": "全部年份",
            "page": 1,
            "rowStart": 1,
            "rowEnd": 1,
            "selected": self.selected,
            "catalogFormat": "xlsx",
            "fullFormat": "docx",
        }
        self.session = SearchSession(
            search_id="search-1",
            query="批量测试",
            match_mode="keyword",
            library="central",
            year="全部年份",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=50,
            actual_page_size=50,
            page=1,
            total_pages=1,
            result_count=1,
            row_count=1,
            grouping_mode="ungrouped",
            result_signature="signature-1",
            observed_at="2026-11-01T00:00:00Z",
            url="https://www.pkulaw.com/chl/search?page=1",
            actual_year="全部年份",
            actual_province="全部省份",
            actual_timeliness="全部（仅记录元数据，不筛选）",
        )
        self.state = {
            "searchSession": self.session.to_dict(),
            "rows": self.selected,
            "page": 1,
            "totalPages": 1,
            "resultCount": 1,
        }

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def _manager(self, browser_bridge: MagicMock) -> BatchTaskManager:
        return BatchTaskManager(
            self.store.database_path,
            browser_bridge,
            self.logger,
            lambda _event, _data: None,
        )

    def _bridge(self) -> MagicMock:
        bridge = MagicMock()
        bridge.download_cursor.return_value = 0
        bridge.create_download_intent.side_effect = [
            {"intentId": "catalog-intent"},
            {"intentId": "zip-intent"},
        ]
        catalog_path = self.root / "catalog.xls"
        zip_path = self.root / "documents.zip"
        catalog_path.write_bytes(b"native-catalog")
        zip_path.write_bytes(b"zip")
        bridge.wait_for_download_intent.side_effect = [
            {
                "event": "completed",
                "path": str(catalog_path),
                "downloadId": "catalog-download",
            },
            {
                "event": "completed",
                "path": str(zip_path),
                "downloadId": "zip-download",
            },
        ]
        return bridge

    def _prepared_catalog(self) -> CatalogPreparation:
        source = self.root / "native-source.xls"
        destination = self.root / "derived-catalog.xlsx"
        source.write_bytes(b"native-catalog")
        destination.write_bytes(b"derived-catalog")
        return CatalogPreparation(
            contract=resolve_catalog_format("xlsx"),
            source_format="xls",
            final_format="xlsx",
            converted=True,
            path=destination,
            source_sha256="source-hash",
            final_sha256="final-hash",
        )

    def _publish_completed_batch(
        self,
        *,
        event_sink: object | None = None,
    ) -> tuple[BatchTaskManager, dict[str, object], Path]:
        self.store.upsert_task_batch(
            self.task["id"],
            batch_key=self.batch["batchKey"],
            query=self.batch["query"],
            library=self.batch["library"],
            page=1,
            row_start=1,
            row_end=1,
            status="pending",
            catalog_format="xlsx",
            full_format="docx",
            selected=self.selected,
        )
        batch = self.store.list_task_batches(self.task["id"])[0]
        manager = BatchTaskManager(
            self.store.database_path,
            self._bridge(),
            self.logger,
            event_sink or (lambda _event, _data: None),
        )
        controller = TaskControl(str(self.task["id"]))
        source_catalog = self.root / "published-catalog.xlsx"
        source_zip = self.root / "published-documents.zip"
        source_catalog.write_bytes(b"catalog")
        source_zip.write_bytes(b"zip")
        final_dir = (
            self.config.output_dir
            / "batch_files"
            / "中央法规"
            / "中央政策_全部年份_1_001-001"
        )
        report_payload = {
            "catalog": {"rows": self.selected},
            "mapping": self.selected,
            "policyIds": ["policy-1"],
        }
        staging = prepare_publish_directory(
            staging_root=self.config.output_dir / "batch_files" / ".publishing",
            final_directory=final_dir,
            batch_key=self.batch["batchKey"],
            files={
                "目录.xlsx": source_catalog,
                "全文.zip": source_zip,
            },
            report_name="验收报告.json",
            report_payload=report_payload,
        )
        published = publish_directory(staging, final_dir)
        manager._register_completed_batch(
            store=self.store,
            task_id=str(self.task["id"]),
            batch=batch,
            selected=self.selected,
            index=1,
            total=1,
            batch_year="全部年份",
            controller=controller,
            final_catalog=final_dir / "目录.xlsx",
            final_zip=final_dir / "全文.zip",
            final_report=final_dir / "验收报告.json",
            report_payload=report_payload,
            search_session=self.session,
            recovered=False,
            published=published,
        )
        completed = self.store.list_task_batches(self.task["id"])[0]
        return manager, completed, final_dir

    def test_catalog_failure_does_not_start_zip(self) -> None:
        adapter = _BatchAdapter(self.state, change_after_catalog=True)
        bridge = self._bridge()
        manager = self._manager(bridge)
        controller = TaskControl(str(self.task["id"]))

        with patch(
            "pkulaw_engine.batch_task.prepare_catalog_output",
            side_effect=CatalogFormatError(
                "CATALOG_NATIVE_FORMAT_MISMATCH",
                "invalid catalog",
            ),
        ):
            with self.assertRaises(CatalogFormatError):
                manager._process_batch(
                    self.store,
                    str(self.task["id"]),
                    self.batch,
                    1,
                    1,
                    self.config,
                    adapter,  # type: ignore[arg-type]
                    controller,
                    self.session,
                )

        self.assertEqual(adapter.downloads, ["catalog"])

    def test_xlsx_request_receives_native_xls_before_derivation(self) -> None:
        selected = [dict(item) for item in REAL_SELECTED]
        state = {**self.state, "rows": selected, "resultCount": len(selected)}
        adapter = _BatchAdapter(state)
        batch = {
            **self.batch,
            "id": "batch-real-xls",
            "library": "local",
            "year": "2026",
            "rowEnd": len(selected),
            "selected": selected,
        }
        config = BatchTaskConfig(
            **{
                **self.config.__dict__,
                "library": "local",
                "year_selection": YearSelection(mode="single", year=2026),
            }
        )
        session_data = {
            **self.session.to_dict(),
            "library": "local",
            "year": "2026",
            "actualYear": "2026",
        }
        session = SearchSession.from_dict(session_data)
        state["searchSession"] = session.to_dict()
        native_path = self.root / "网站原生目录.xls"
        native_path.write_bytes(REAL_XLS.read_bytes())
        zip_path = self.root / "documents.zip"
        zip_path.write_bytes(b"zip")
        bridge = self._bridge()
        bridge.wait_for_download_intent.side_effect = [
            {
                "event": "completed",
                "path": str(native_path),
                "downloadId": "native-catalog",
            },
            {
                "event": "completed",
                "path": str(zip_path),
                "downloadId": "documents-zip",
            },
        ]
        manager = self._manager(bridge)

        with patch(
            "pkulaw_engine.batch_task.validate_zip",
            return_value={
                "format": "zip",
                "crcOk": True,
                "identityMappingOk": True,
                "mapping": selected,
            },
        ):
            manager._process_batch(
                self.store,
                str(self.task["id"]),
                batch,
                1,
                1,
                config,
                adapter,  # type: ignore[arg-type]
                TaskControl(str(self.task["id"])),
                session,
            )

        self.assertEqual(
            adapter.download_calls[0],
            {
                "kind": "catalog",
                "catalog_format": "xls",
            },
        )
        first_intent = bridge.create_download_intent.call_args_list[0].kwargs
        self.assertEqual(first_intent["kind"], "batch-catalog")
        self.assertEqual(first_intent["expected_suffixes"], (".xls",))
        completed = self.store.list_task_batches(self.task["id"])[0]
        final_catalog = Path(completed["catalogPath"])
        self.assertEqual(final_catalog.suffix, ".xlsx")
        self.assertTrue(final_catalog.is_file())

    def test_catalog_derivation_failure_does_not_start_zip(self) -> None:
        selected = [dict(item) for item in REAL_SELECTED]
        state = {**self.state, "rows": selected, "resultCount": len(selected)}
        adapter = _BatchAdapter(state)
        batch = {
            **self.batch,
            "id": "batch-derivation-failure",
            "library": "local",
            "year": "2026",
            "rowEnd": len(selected),
            "selected": selected,
        }
        config = BatchTaskConfig(
            **{
                **self.config.__dict__,
                "library": "local",
                "year_selection": YearSelection(mode="single", year=2026),
                "catalog_format": "xlsx",
            }
        )
        session_data = {
            **self.session.to_dict(),
            "library": "local",
            "year": "2026",
            "actualYear": "2026",
        }
        session = SearchSession.from_dict(session_data)
        state["searchSession"] = session.to_dict()
        native_path = self.root / "网站原生目录.xls"
        native_path.write_bytes(REAL_XLS.read_bytes())
        bridge = self._bridge()
        bridge.wait_for_download_intent.return_value = {
            "event": "completed",
            "path": str(native_path),
            "downloadId": "native-catalog",
        }
        manager = self._manager(bridge)

        with patch(
            "pkulaw_engine.batch_task.prepare_catalog_output",
            side_effect=RuntimeError("simulated derivation failure"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "simulated derivation failure",
            ):
                manager._process_batch(
                    self.store,
                    str(self.task["id"]),
                    batch,
                    1,
                    1,
                    config,
                    adapter,  # type: ignore[arg-type]
                    TaskControl(str(self.task["id"])),
                    session,
                )

        self.assertEqual(adapter.downloads, ["catalog"])
        self.assertEqual(bridge.create_download_intent.call_count, 1)

    def test_catalog_native_mismatch_returns_full_recovery_context(self) -> None:
        adapter = _BatchAdapter(self.state)
        bridge = self._bridge()
        wrong_path = self.root / "网站目录.xlsx"
        wrong_path.write_bytes(b"PK\x03\x04not-biff")
        bridge.wait_for_download_intent.side_effect = None
        bridge.wait_for_download_intent.return_value = {
            "event": "mismatch",
            "intentId": "catalog-intent",
            "downloadId": "wrong-catalog",
            "errorCode": "CATALOG_NATIVE_FORMAT_MISMATCH",
            "actualFile": wrong_path.name,
            "actualSuffix": ".xlsx",
            "actualSignature": "504b0304",
            "actualPath": str(wrong_path),
            "reason": "unexpected suffix .xlsx; expected ['.xls']",
            "recovery": "重新下载网站原生 BIFF XLS",
        }
        manager = self._manager(bridge)

        with self.assertRaises(CatalogFormatError) as context:
            manager._process_batch(
                self.store,
                str(self.task["id"]),
                self.batch,
                1,
                1,
                self.config,
                adapter,  # type: ignore[arg-type]
                TaskControl(str(self.task["id"])),
                self.session,
            )

        error = context.exception
        self.assertEqual(error.code, "CATALOG_NATIVE_FORMAT_MISMATCH")
        self.assertEqual(error.details["intentId"], "catalog-intent")
        self.assertEqual(error.details["downloadId"], "wrong-catalog")
        self.assertEqual(error.details["actualFile"], wrong_path.name)
        self.assertEqual(error.details["actualSuffix"], ".xlsx")
        self.assertEqual(error.details["actualSignature"], "504b0304")
        self.assertEqual(error.details["actualPath"], str(wrong_path))
        self.assertIn("恢复建议", str(error))
        self.assertEqual(adapter.downloads, ["catalog"])
        self.assertEqual(bridge.create_download_intent.call_count, 1)

    def test_selection_mismatch_stops_before_catalog(self) -> None:
        adapter = _BatchAdapter(self.state, verification_ok=False)
        bridge = self._bridge()
        manager = self._manager(bridge)
        controller = TaskControl(str(self.task["id"]))

        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            manager._process_batch(
                self.store,
                str(self.task["id"]),
                self.batch,
                1,
                1,
                self.config,
                adapter,  # type: ignore[arg-type]
                controller,
                self.session,
            )

        self.assertEqual(adapter.downloads, [])
        bridge.create_download_intent.assert_not_called()

    def test_page_identity_change_after_catalog_stops_before_zip(self) -> None:
        adapter = _BatchAdapter(self.state, change_after_catalog=True)
        bridge = self._bridge()
        manager = self._manager(bridge)
        controller = TaskControl(str(self.task["id"]))
        catalog_result = {
            "format": "xlsx",
            "rows": [
                {
                    "policyId": "policy-1",
                    "title": "批量政策",
                }
            ],
        }

        with (
            patch(
                "pkulaw_engine.batch_task.prepare_catalog_output",
                return_value=self._prepared_catalog(),
            ),
            patch(
                "pkulaw_engine.batch_task.validate_catalog",
                return_value=catalog_result,
            ),
        ):
            with self.assertRaises(BatchExecutionContextError):
                manager._process_batch(
                    self.store,
                    str(self.task["id"]),
                    self.batch,
                    1,
                    1,
                    self.config,
                    adapter,  # type: ignore[arg-type]
                    controller,
                    self.session,
                )

        self.assertEqual(adapter.downloads, ["catalog"])
        self.assertEqual(bridge.create_download_intent.call_count, 1)

    def test_published_batch_recovers_after_database_failure(self) -> None:
        adapter = _BatchAdapter(self.state)
        bridge = self._bridge()
        manager = self._manager(bridge)
        controller = TaskControl(str(self.task["id"]))
        self.store.upsert_task_batch(
            self.task["id"],
            batch_key=self.batch["batchKey"],
            query=self.batch["query"],
            library=self.batch["library"],
            page=1,
            row_start=1,
            row_end=1,
            status="pending",
            catalog_format="xlsx",
            full_format="docx",
            selected=self.selected,
        )
        batch = self.store.list_task_batches(self.task["id"])[0]
        source_catalog = self.root / "catalog.xlsx"
        source_zip = self.root / "documents.zip"
        source_catalog.write_bytes(b"catalog")
        source_zip.write_bytes(b"zip")
        final_dir = self.config.output_dir / "batch_files" / "中央法规" / "batch-1"
        staging = prepare_publish_directory(
            staging_root=self.config.output_dir / "batch_files" / ".publishing",
            final_directory=final_dir,
            batch_key=self.batch["batchKey"],
            files={
                "目录.xlsx": source_catalog,
                "全文.zip": source_zip,
            },
            report_name="验收报告.json",
            report_payload={
                "batchKey": self.batch["batchKey"],
                "policyIds": ["policy-1"],
            },
        )
        published = publish_directory(staging, final_dir)
        report_payload = {
            "catalog": {
                "rows": [
                    {
                        "policyId": "policy-1",
                        "title": "批量政策",
                    }
                ]
            },
            "mapping": [
                {
                    "policyId": "policy-1",
                    "title": "批量政策",
                }
            ],
        }

        with patch.object(
            self.store,
            "complete_task_batch_atomic",
            side_effect=OSError("simulated database failure"),
        ):
            with self.assertRaises(OSError):
                manager._register_completed_batch(
                    store=self.store,
                    task_id=str(self.task["id"]),
                    batch=batch,
                    selected=self.selected,
                    index=1,
                    total=1,
                    batch_year="全部年份",
                    controller=controller,
                    final_catalog=final_dir / "目录.xlsx",
                    final_zip=final_dir / "全文.zip",
                    final_report=final_dir / "验收报告.json",
                    report_payload=report_payload,
                    search_session=self.session,
                    recovered=False,
                    published=published,
                )

        pending = self.store.list_task_batches(self.task["id"])[0]
        self.assertEqual(pending["status"], "pending")
        self.assertTrue((final_dir / "全文.zip").is_file())

        manager._register_completed_batch(
            store=self.store,
            task_id=str(self.task["id"]),
            batch=batch,
            selected=self.selected,
            index=1,
            total=1,
            batch_year="全部年份",
            controller=controller,
            final_catalog=final_dir / "目录.xlsx",
            final_zip=final_dir / "全文.zip",
            final_report=final_dir / "验收报告.json",
            report_payload=report_payload,
            search_session=self.session,
            recovered=True,
            published=published,
        )
        completed = self.store.list_task_batches(self.task["id"])[0]
        self.assertEqual(completed["status"], "completed")

    def test_published_directory_recovers_without_redownload(self) -> None:
        self.store.upsert_task_batch(
            self.task["id"],
            batch_key=self.batch["batchKey"],
            query=self.batch["query"],
            library=self.batch["library"],
            page=1,
            row_start=1,
            row_end=1,
            status="running",
            catalog_format="xlsx",
            full_format="docx",
            selected=self.selected,
        )
        batch = self.store.list_task_batches(self.task["id"])[0]
        source_catalog = self.root / "recover-catalog.xlsx"
        source_zip = self.root / "recover-documents.zip"
        source_catalog.write_bytes(b"catalog")
        source_zip.write_bytes(b"zip")
        final_dir = (
            self.config.output_dir
            / "batch_files"
            / "中央法规"
            / "中央政策_全部年份_1_001-001"
        )
        report_payload = {
            "catalog": {"rows": self.selected},
            "mapping": self.selected,
            "policyIds": ["policy-1"],
        }
        staging = prepare_publish_directory(
            staging_root=self.config.output_dir / "batch_files" / ".publishing",
            final_directory=final_dir,
            batch_key=self.batch["batchKey"],
            files={
                "目录.xlsx": source_catalog,
                "全文.zip": source_zip,
            },
            report_name="验收报告.json",
            report_payload=report_payload,
        )
        publish_directory(staging, final_dir)
        adapter = _BatchAdapter(self.state)
        bridge = self._bridge()
        manager = self._manager(bridge)

        manager._process_batch(
            self.store,
            str(self.task["id"]),
            batch,
            1,
            1,
            self.config,
            adapter,  # type: ignore[arg-type]
            TaskControl(str(self.task["id"])),
            self.session,
        )

        self.assertEqual(adapter.downloads, [])
        bridge.create_download_intent.assert_not_called()
        recovered = self.store.list_task_batches(self.task["id"])[0]
        self.assertEqual(recovered["status"], "completed")

    def test_zip_validation_failure_does_not_publish_batch_directory(self) -> None:
        adapter = _BatchAdapter(self.state)
        bridge = self._bridge()
        manager = self._manager(bridge)
        controller = TaskControl(str(self.task["id"]))
        final_dir = self.config.output_dir / "batch_files" / "中央法规" / "batch-1"

        with (
            patch(
                "pkulaw_engine.batch_task.prepare_catalog_output",
                return_value=self._prepared_catalog(),
            ),
            patch(
                "pkulaw_engine.batch_task.validate_catalog",
                return_value={
                    "format": "xlsx",
                    "rows": [
                        {
                            "policyId": "policy-1",
                            "title": "批量政策",
                        }
                    ],
                },
            ),
            patch(
                "pkulaw_engine.batch_task.validate_zip",
                side_effect=ArchiveValidationError("invalid zip"),
            ),
        ):
            with self.assertRaises(ArchiveValidationError):
                manager._process_batch(
                    self.store,
                    str(self.task["id"]),
                    self.batch,
                    1,
                    1,
                    self.config,
                    adapter,  # type: ignore[arg-type]
                    controller,
                    self.session,
                )

        self.assertEqual(adapter.downloads, ["catalog", "zip"])
        self.assertFalse(final_dir.exists())

    def test_completed_batch_integrity_accepts_valid_manifest(self) -> None:
        _manager, completed, _final_dir = self._publish_completed_batch()
        manager = self._manager(self._bridge())

        self.assertIsNone(
            manager._completed_batch_integrity_error(completed)
        )

    def test_completed_batch_integrity_fails_closed_for_bad_files(self) -> None:
        manager, completed, final_dir = self._publish_completed_batch()
        (final_dir / "全文.zip").write_bytes(b"tampered")
        tampered = manager._completed_batch_integrity_error(completed)
        self.assertIsNotNone(tampered)
        assert tampered is not None
        self.assertEqual(
            tampered["code"],
            "BATCH_ARTIFACT_INTEGRITY_MISMATCH",
        )

        (final_dir / "全文.zip").unlink()
        missing = manager._completed_batch_integrity_error(completed)
        self.assertIsNotNone(missing)
        assert missing is not None
        self.assertEqual(missing["code"], "BATCH_ARTIFACT_MISSING")

    def test_completed_batch_integrity_rejects_database_path_mismatch(
        self,
    ) -> None:
        manager, completed, final_dir = self._publish_completed_batch()
        unregistered = final_dir / "未登记报告.json"
        unregistered.write_text("{}", encoding="utf-8")
        mismatched = {**completed, "reportPath": str(unregistered)}

        error = manager._completed_batch_integrity_error(mismatched)

        self.assertIsNotNone(error)
        assert error is not None
        self.assertEqual(
            error["code"],
            "BATCH_ARTIFACT_DATABASE_MISMATCH",
        )

    def test_event_failure_after_sqlite_commit_does_not_roll_back_batch(
        self,
    ) -> None:
        def failing_sink(_event: str, _data: dict[str, object]) -> None:
            raise RuntimeError("event transport unavailable")

        manager, completed, _final_dir = self._publish_completed_batch(
            event_sink=failing_sink
        )

        self.assertEqual(completed["status"], "completed")
        self.assertIsNone(
            manager._completed_batch_integrity_error(completed)
        )


if __name__ == "__main__":
    unittest.main()
