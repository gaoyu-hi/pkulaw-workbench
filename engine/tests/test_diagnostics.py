from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
            self.assertIn(
                "bundled-antiword",
                report["legacyConverters"],
            )
            self.assertIn(
                "antiword.exe",
                report["legacyConverters"]["bundled-antiword"],
            )
            self.assertTrue(report["engine"]["pythonVersion"])
            self.assertIn("DrissionPage", report["dependencies"])


if __name__ == "__main__":
    unittest.main()
