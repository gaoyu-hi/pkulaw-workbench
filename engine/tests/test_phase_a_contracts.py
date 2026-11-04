from __future__ import annotations

import csv
import importlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from pkulaw_engine.policy import normalize_title
from pkulaw_engine.processors.reporting import write_master_catalog
from pkulaw_engine.sources.pkulaw import PkulawAdapter
from pkulaw_engine.sources.selector_profile import load_selector_profiles


PHASE_A_BASELINE = os.environ.get("PKULAW_PHASE_A_BASELINE") == "1"


class PhaseAFixtureTests(unittest.TestCase):
    def test_transition_fixtures_exist_and_are_marked(self) -> None:
        fixture_root = Path(__file__).with_name("fixtures") / "pkulaw-v6"
        expected = {
            "batch-modal-real-shape.html",
            "custom-page-size.html",
            "download-out-of-order.html",
            "filter-tree.html",
            "grouped-results.html",
            "same-query-rerun.html",
            "same-url-slow-search.html",
            "selector-flicker.html",
            "selector-missing.html",
            "stale-page5.html",
        }
        for name in expected:
            with self.subTest(name=name):
                path = fixture_root / name
                self.assertTrue(path.is_file(), name)
                text = path.read_text(encoding="utf-8")
                self.assertIn('data-pkulaw-profile="pkulaw-v6"', text)
                self.assertNotIn("<script", text.lower())


@unittest.skipUnless(
    PHASE_A_BASELINE,
    "Phase A known-gap tests are run through test:phase-a-baseline",
)
class PhaseAKnownGapTests(unittest.TestCase):
    def test_real_v6_filter_selectors_are_explicit(self) -> None:
        profile = {
            item.profile_id: item for item in load_selector_profiles()
        }["pkulaw-v6"]
        filters = profile.selectors["filters"]
        self.assertTrue(filters["year"])
        self.assertTrue(filters["province"])
        self.assertTrue(filters["timeliness"])

    def test_real_v6_page_size_uses_visible_control(self) -> None:
        profile = {
            item.profile_id: item for item in load_selector_profiles()
        }["pkulaw-v6"]
        self.assertIn(".articleSelect", profile.selectors["pageSize"])
        self.assertIn("#recordPage", profile.selectors["pageSizeItem"])

    def test_ungroup_operation_is_implemented(self) -> None:
        self.assertTrue(hasattr(PkulawAdapter, "ensure_ungrouped_results"))

    def test_search_transaction_contract_is_available(self) -> None:
        module = importlib.import_module("pkulaw_engine.sources.search_transaction")
        self.assertTrue(callable(getattr(module, "SearchTransaction", None)))

    def test_browser_lease_contract_is_available(self) -> None:
        module = importlib.import_module("pkulaw_engine.browser_lease")
        self.assertTrue(callable(getattr(module, "BrowserLeaseManager", None)))

    def test_download_intent_contract_is_available(self) -> None:
        module = importlib.import_module("pkulaw_engine.download_intent")
        self.assertTrue(callable(getattr(module, "DownloadIntentManager", None)))

    def test_archive_resource_limit_contract_is_available(self) -> None:
        module = importlib.import_module("pkulaw_engine.validators.archive")
        self.assertTrue(hasattr(module, "ArchiveLimits"))
        limits = module.ArchiveLimits(
            max_archive_bytes=4096,
            max_total_uncompressed_bytes=8192,
            max_member_bytes=4096,
            max_members=2,
            max_compression_ratio=20,
        )
        selected = [
            {
                "number": 1,
                "policyId": "compressed-policy",
                "title": "高压缩比政策",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resource-limit.zip"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "compressed-policy_高压缩比政策.docx",
                    b"\x00" * (1024 * 1024),
                )
            with self.assertRaises(module.ArchiveValidationError):
                module.validate_zip(
                    path,
                    selected=selected,
                    full_format="docx",
                    limits=limits,
                )

    def test_csv_and_xlsx_neutralize_formulas(self) -> None:
        malicious_title = '=HYPERLINK("https://example.test","点击")'
        documents = [
            {
                "policyId": "formula-policy",
                "title": malicious_title,
                "status": "completed",
                "sourceType": "北大法宝原件",
                "sourceUrl": "https://www.pkulaw.com/chl/formula-policy.html",
                "retrievedAt": "2026-11-01T00:00:00Z",
                "retrievalMethod": "test",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            csv_path, xlsx_path = write_master_catalog(documents, root)

            with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
                csv_title = next(csv.DictReader(stream))["标题"]
            workbook = load_workbook(xlsx_path, data_only=False)
            try:
                sheet = workbook.active
                headers = [cell.value for cell in sheet[1]]
                title_column = headers.index("标题") + 1
                xlsx_title = sheet.cell(2, title_column).value
                xlsx_type = sheet.cell(2, title_column).data_type
            finally:
                workbook.close()

        self.assertEqual(
            normalize_title(csv_title.lstrip("'")),
            normalize_title(malicious_title),
        )
        self.assertTrue(csv_title.startswith("'"))
        self.assertEqual(xlsx_title, malicious_title)
        self.assertEqual(xlsx_type, "s")


if __name__ == "__main__":
    unittest.main()
