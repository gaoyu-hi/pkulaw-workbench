from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from pkulaw_engine.diagnostics import collect_diagnostics
from pkulaw_engine.storage import TaskStore


class DiagnosticsTests(unittest.TestCase):
    def test_diagnostics_reports_database_and_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="北大法宝 诊断 ") as directory:
            data_directory = Path(directory)
            store = TaskStore(data_directory / "pkulaw.sqlite3")
            try:
                store.create_task(
                    name="诊断占位任务",
                    mode="single",
                    task_input={"matchMode": "title"},
                    output_dir=str(data_directory),
                )
                report = collect_diagnostics(store, data_directory, "0.1.0")
            finally:
                store.close()

            checks = {check["id"]: check for check in report["checks"]}
            self.assertEqual(report["database"]["integrity"], "ok")
            self.assertEqual(report["database"]["schemaVersion"], 6)
            self.assertEqual(checks["data-directory"]["status"], "ok")
            self.assertEqual(checks["sqlite-integrity"]["status"], "ok")
            self.assertEqual(checks["legacy-doc-converter"]["status"], "ok")
            if sys.platform == "win32":
                self.assertIn(
                    "bundled-antiword",
                    report["legacyConverters"],
                )
                self.assertIn(
                    "antiword.exe",
                    report["legacyConverters"]["bundled-antiword"],
                )
            else:
                self.assertTrue(report["legacyConverters"]["textutil"])
            self.assertTrue(report["engine"]["pythonVersion"])
            self.assertIn("DrissionPage", report["dependencies"])

    def test_diagnostics_reports_textutil_as_macos_converter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="北大法宝 诊断 ") as directory:
            data_directory = Path(directory)
            store = TaskStore(data_directory / "pkulaw.sqlite3")
            try:
                with (
                    patch(
                        "pkulaw_engine.diagnostics.inspect_bundled_antiword",
                        return_value={
                            "status": "unavailable",
                            "path": "",
                            "version": "0.37",
                        },
                    ),
                    patch(
                        "pkulaw_engine.diagnostics.shutil.which",
                        side_effect=lambda name: (
                            "/usr/bin/textutil"
                            if name == "textutil"
                            else None
                        ),
                    ),
                    patch(
                        "pkulaw_engine.diagnostics._package_version",
                        return_value="",
                    ),
                ):
                    report = collect_diagnostics(
                        store,
                        data_directory,
                        "0.1.0",
                    )
            finally:
                store.close()

        checks = {check["id"]: check for check in report["checks"]}
        self.assertEqual(
            report["legacyConverters"]["textutil"],
            "/usr/bin/textutil",
        )
        self.assertEqual(checks["legacy-doc-converter"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
