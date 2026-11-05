from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

from openpyxl import Workbook

from pkulaw_engine.validators.archive import (
    ArchiveLimits,
    ArchiveValidationError,
    validate_zip,
)
from pkulaw_engine.validators.catalog import (
    CatalogValidationError,
    validate_catalog,
)

SELECTED = [
    {
        "number": 1,
        "policyId": "policy-001",
        "title": "批量政策一",
        "url": "https://example.test/pkulaw/detail/policy-001.html",
    },
    {
        "number": 2,
        "policyId": "policy-002",
        "title": "批量政策二",
        "url": "https://example.test/pkulaw/detail/policy-002.html",
    },
]


class CatalogAndArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_xlsx_catalog_is_validated_by_title_and_policy_id(self) -> None:
        path = self.root / "catalog.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["序号", "标题", "原文链接"])
        for policy in SELECTED:
            sheet.append([policy["number"], policy["title"], policy["url"]])
        workbook.save(path)
        result = validate_catalog(path, SELECTED)
        self.assertEqual(result["format"], "xlsx")
        self.assertEqual([row["policyId"] for row in result["rows"]], ["policy-001", "policy-002"])

    def test_html_xls_catalog_is_validated(self) -> None:
        path = self.root / "catalog.xls"
        rows = "".join(
            f"<tr><td>{policy['number']}</td><td>{policy['title']}</td>"
            f"<td><a href='{policy['url']}'>{policy['url']}</a></td></tr>"
            for policy in SELECTED
        )
        path.write_text(
            f"<html><body><table><tr><th>序号</th><th>标题</th><th>原文链接</th></tr>"
            f"{rows}</table></body></html>",
            encoding="utf-8",
        )
        result = validate_catalog(path, SELECTED)
        self.assertEqual(result["format"], "html-xls")
        self.assertEqual(result["selectedCount"], 2)

    @unittest.skipUnless(importlib.util.find_spec("xlwt"), "xlwt is required to create BIFF XLS fixtures")
    def test_biff_xls_catalog_is_validated(self) -> None:
        import xlwt

        path = self.root / "catalog.xls"
        workbook = xlwt.Workbook()
        sheet = workbook.add_sheet("政策目录")
        for column, value in enumerate(["序号", "标题", "原文链接"]):
            sheet.write(0, column, value)
        for row_index, policy in enumerate(SELECTED, start=1):
            sheet.write(row_index, 0, policy["number"])
            sheet.write(row_index, 1, policy["title"])
            sheet.write(row_index, 2, policy["url"])
        workbook.save(str(path))
        result = validate_catalog(path, SELECTED)
        self.assertEqual(result["format"], "xls")

    def test_catalog_title_mismatch_is_rejected(self) -> None:
        path = self.root / "catalog.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["序号", "标题", "原文链接"])
        sheet.append([1, "错误标题", SELECTED[0]["url"]])
        sheet.append([2, SELECTED[1]["title"], SELECTED[1]["url"]])
        workbook.save(path)
        with self.assertRaises(CatalogValidationError):
            validate_catalog(path, SELECTED)

    def test_catalog_shape_headers_counts_and_ids_fail_closed(self) -> None:
        no_rows = self.root / "no-rows.xlsx"
        workbook = Workbook()
        workbook.active.append(["序号", "标题"])
        workbook.save(no_rows)
        with self.assertRaises(CatalogValidationError):
            validate_catalog(no_rows, [])

        missing_headers = self.root / "missing-headers.xlsx"
        workbook = Workbook()
        workbook.active.append(["其他", "字段"])
        workbook.active.append(["1", "政策"])
        workbook.save(missing_headers)
        with self.assertRaises(CatalogValidationError):
            validate_catalog(missing_headers, SELECTED[:1])

        wrong_count = self.root / "wrong-count.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["序号", "标题", "原文链接"])
        sheet.append([1, SELECTED[0]["title"], SELECTED[0]["url"]])
        workbook.save(wrong_count)
        with self.assertRaises(CatalogValidationError):
            validate_catalog(wrong_count, SELECTED)

        wrong_id = self.root / "wrong-id.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["序号", "标题", "原文链接"])
        sheet.append(
            [
                1,
                SELECTED[0]["title"],
                "https://example.test/pkulaw/detail/wrong-id.html",
            ]
        )
        workbook.save(wrong_id)
        with self.assertRaises(CatalogValidationError):
            validate_catalog(wrong_id, SELECTED[:1])

    def test_xlsx_hyperlink_fallback_and_html_encoding_branches(self) -> None:
        linked = self.root / "linked.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["序号", "标题", "详情"])
        sheet.append([1, SELECTED[0]["title"], "打开"])
        sheet.cell(2, 3).hyperlink = SELECTED[0]["url"]
        workbook.save(linked)
        result = validate_catalog(linked, SELECTED[:1])
        self.assertEqual(result["rows"][0]["policyId"], "policy-001")

        encoded = self.root / "encoded.xls"
        encoded.write_bytes(
            (
                "<html><body><table>"
                "<tr><th>序号</th><th>标题</th><th>原文链接</th></tr>"
                f"<tr><td>1</td><td>{SELECTED[0]['title']}</td>"
                f"<td><a href='{SELECTED[0]['url']}'>链接</a></td></tr>"
                "</table></body></html>"
            ).encode("gb18030")
        )
        self.assertEqual(
            validate_catalog(encoded, SELECTED[:1])["format"],
            "html-xls",
        )

        no_table = self.root / "no-table.xls"
        no_table.write_text("<html><body>无表格</body></html>", encoding="utf-8")
        with self.assertRaises(CatalogValidationError):
            validate_catalog(no_table, SELECTED[:1])

        invalid = self.root / "invalid.bin"
        invalid.write_bytes(b"not a spreadsheet")
        with self.assertRaises(CatalogValidationError):
            validate_catalog(invalid, SELECTED[:1])

    def test_valid_zip_and_identity_mapping(self) -> None:
        path = self.root / "documents.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("policy-001_批量政策一.docx", b"doc-one")
            archive.writestr("policy-002_批量政策二.docx", b"doc-two")
        result = validate_zip(path, selected=SELECTED, full_format="docx")
        self.assertTrue(result["crcOk"])
        self.assertTrue(result["identityMappingOk"])
        self.assertEqual(len(result["mapping"]), 2)

    def test_real_truncated_long_title_maps_without_hyphen(
        self,
    ) -> None:
        long_title = (
            "陕西省科学技术协会、中共陕西省委宣传部、中共陕西省委网信办、"
            "陕西省教育厅、陕西省科学技术厅、陕西省自然资源厅、"
            "陕西省生态环境厅、陕西省水利厅、陕西省农业农村厅、"
            "陕西省卫生健康委员会、陕西省应急管理厅、陕西省国资委、"
            "陕西省科学院、陕西省林业局、陕西省总工会、共青团陕西省委、"
            "陕西省妇女联合会、陕西省作家协会、陕西省工商业联合会"
            "关于举办2023年陕西省全国科普日活动的通知"
        )
        selected = [
            {
                "number": 1,
                "policyId": "policy-short",
                "title": "批量政策一",
            },
            {
                "number": 2,
                "policyId": "policy-long",
                "title": long_title,
            },
        ]
        truncated = (
            "陕西省科学技术协会、中共陕西省委宣传部、中共陕西省委网信办、"
            "陕西省教育厅、陕西省科学技术厅、陕西省自然资源厅、陕西省"
            "..."
        )
        path = self._zip_with_files(
            "truncated.docx.zip",
            [
                ("批量政策一.docx", b"doc-one"),
                (
                    f"{truncated}(FBMCLI.14.7131719).docx",
                    b"doc-two",
                ),
            ],
        )

        result = validate_zip(
            path,
            selected=selected,
            full_format="docx",
        )

        mapped = {
            row["policyId"]: row["archiveMember"]
            for row in result["mapping"]
        }
        self.assertIn("policy-long", mapped)
        self.assertIn("FBMCLI.14.7131719", mapped["policy-long"])

    def test_zip_path_traversal_is_rejected(self) -> None:
        path = self.root / "unsafe.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("../policy-001_批量政策一.docx", b"doc-one")
            archive.writestr("policy-002_批量政策二.docx", b"doc-two")
        with self.assertRaises(ArchiveValidationError):
            validate_zip(path, selected=SELECTED, full_format="docx")

    def test_zip_crc_corruption_is_rejected(self) -> None:
        path = self.root / "corrupt.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("policy-001_批量政策一.docx", b"doc-one")
            archive.writestr("policy-002_批量政策二.docx", b"doc-two")
        data = path.read_bytes().replace(b"doc-one", b"bad-one", 1)
        path.write_bytes(data)
        with self.assertRaises(ArchiveValidationError):
            validate_zip(path, selected=SELECTED, full_format="docx")

    def test_zip_empty_document_is_rejected(self) -> None:
        path = self.root / "empty-member.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("policy-001_批量政策一.docx", b"")
            archive.writestr("policy-002_批量政策二.docx", b"doc-two")
        with self.assertRaises(ArchiveValidationError):
            validate_zip(path, selected=SELECTED, full_format="docx")

    def test_zip_duplicate_members_are_rejected(self) -> None:
        path = self.root / "duplicates.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("批量政策一.docx", b"doc-one")
            archive.writestr("批量 政策一.docx", b"doc-one-copy")
        selected = [SELECTED[0], {**SELECTED[1], "title": "批量政策一"}]
        with self.assertRaises(ArchiveValidationError):
            validate_zip(path, selected=selected, full_format="docx")

    def _zip_with_files(self, name: str, files: list[tuple[str, bytes]]) -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, content in files:
                archive.writestr(member, content)
        return path

    def test_zip_archive_size_limit(self) -> None:
        path = self._zip_with_files(
            "archive-size.zip",
            [
                ("policy-001_批量政策一.docx", b"one"),
                ("policy-002_批量政策二.docx", b"two"),
            ],
        )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                path,
                selected=SELECTED,
                full_format="docx",
                limits=ArchiveLimits(max_archive_bytes=8),
            )

    def test_zip_total_and_member_size_limits(self) -> None:
        path = self._zip_with_files(
            "member-size.zip",
            [
                ("policy-001_批量政策一.docx", b"a" * 100),
                ("policy-002_批量政策二.docx", b"b" * 100),
            ],
        )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                path,
                selected=SELECTED,
                full_format="docx",
                limits=ArchiveLimits(max_member_bytes=50),
            )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                path,
                selected=SELECTED,
                full_format="docx",
                limits=ArchiveLimits(max_total_uncompressed_bytes=150),
            )

    def test_zip_member_count_limit(self) -> None:
        path = self._zip_with_files(
            "member-count.zip",
            [
                ("policy-001_批量政策一.docx", b"one"),
                ("policy-002_批量政策二.docx", b"two"),
            ],
        )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                path,
                selected=SELECTED,
                full_format="docx",
                limits=ArchiveLimits(max_members=1),
            )

    def test_zip_compression_ratio_limit(self) -> None:
        path = self._zip_with_files(
            "compression-ratio.zip",
            [
                ("policy-001_批量政策一.docx", b"\0" * 20_000),
                ("policy-002_批量政策二.docx", b"\0" * 20_000),
            ],
        )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                path,
                selected=SELECTED,
                full_format="docx",
                limits=ArchiveLimits(
                    max_member_bytes=50_000,
                    max_total_uncompressed_bytes=50_000,
                    max_compression_ratio=3,
                ),
            )

    def test_zip_depth_and_windows_name_limits(self) -> None:
        deep = self._zip_with_files(
            "deep.zip",
            [
                ("a/b/c/policy-001_批量政策一.docx", b"one"),
                ("policy-002_批量政策二.docx", b"two"),
            ],
        )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                deep,
                selected=SELECTED,
                full_format="docx",
                limits=ArchiveLimits(max_depth=2),
            )

        for member in (
            "CON.docx",
            "policy-001_批量政策一.docx.",
            "policy-002_批量政策二 .docx",
            "bad<name>.docx",
        ):
            with self.subTest(member=member):
                path = self._zip_with_files(
                    f"unsafe-{len(member)}.zip",
                    [(member, b"data"), ("policy-002_批量政策二.docx", b"two")],
                )
                with self.assertRaises(ArchiveValidationError):
                    validate_zip(path, selected=SELECTED, full_format="docx")

    def test_zip_case_insensitive_duplicate_members(self) -> None:
        path = self._zip_with_files(
            "casefold.zip",
            [
                ("Policy.docx", b"one"),
                ("policy.DOCX", b"two"),
            ],
        )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                path,
                selected=SELECTED,
                full_format="docx",
            )

    def test_zip_unsafe_directory_entries_are_rejected(self) -> None:
        path = self._zip_with_files(
            "unsafe-directories.zip",
            [
                ("CON/", b""),
                ("policy-001_批量政策一.docx", b"one"),
                ("policy-002_批量政策二.docx", b"two"),
            ],
        )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                path,
                selected=SELECTED,
                full_format="docx",
            )

    def test_zip_member_filename_length_limit(self) -> None:
        path = self._zip_with_files(
            "long-member-name.zip",
            [
                (f"{'a' * 30}_policy-001_批量政策一.docx", b"one"),
                ("policy-002_批量政策二.docx", b"two"),
            ],
        )
        with self.assertRaises(ArchiveValidationError):
            validate_zip(
                path,
                selected=SELECTED,
                full_format="docx",
                limits=ArchiveLimits(max_filename_length=20),
            )


if __name__ == "__main__":
    unittest.main()
