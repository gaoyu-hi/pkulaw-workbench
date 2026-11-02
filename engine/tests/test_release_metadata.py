from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import pkulaw_engine


class ReleaseMetadataTests(unittest.TestCase):
    def test_application_and_engine_versions_are_unified(self) -> None:
        root = Path(__file__).resolve().parents[2]
        root_package = json.loads((root / "package.json").read_text("utf-8"))
        desktop_package = json.loads(
            (root / "apps" / "desktop" / "package.json").read_text("utf-8")
        )
        pyproject = tomllib.loads(
            (root / "engine" / "pyproject.toml").read_text("utf-8")
        )

        versions = {
            "rootPackage": root_package["version"],
            "desktopPackage": desktop_package["version"],
            "pyproject": pyproject["project"]["version"],
            "engineRuntime": pkulaw_engine.__version__,
        }

        self.assertEqual(set(versions.values()), {"0.2.2"}, versions)


if __name__ == "__main__":
    unittest.main()
