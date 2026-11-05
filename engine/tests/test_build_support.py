from __future__ import annotations

import unittest
from pathlib import Path

from pkulaw_engine.build_support import engine_data_entries


class BuildSupportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine_root = Path(__file__).parents[1]

    def test_windows_pyinstaller_datas_include_antiword(self) -> None:
        entries = engine_data_entries(self.engine_root, "win32")
        destinations = {destination for _source, destination in entries}

        self.assertIn("pkulaw_engine/sources", destinations)
        self.assertIn("pkulaw_engine/sources/profiles", destinations)
        self.assertIn("vendor/antiword/win-x64", destinations)
        self.assertTrue(
            any(
                Path(source).name == "antiword.exe"
                or (Path(source) / "antiword.exe").is_file()
                for source, _destination in entries
            )
        )
        self.assertTrue(
            all(Path(source).exists() for source, _destination in entries)
        )

    def test_macos_pyinstaller_datas_exclude_windows_antiword(self) -> None:
        entries = engine_data_entries(self.engine_root, "darwin")
        destinations = {destination for _source, destination in entries}

        self.assertEqual(
            destinations,
            {
                "pkulaw_engine/sources",
                "pkulaw_engine/sources/profiles",
            },
        )
        self.assertFalse(
            any("antiword" in str(source) for source, _destination in entries)
        )

    def test_unknown_pyinstaller_platform_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "UNSUPPORTED_PYINSTALLER_PLATFORM: linux",
        ):
            engine_data_entries(self.engine_root, "linux")


if __name__ == "__main__":
    unittest.main()
