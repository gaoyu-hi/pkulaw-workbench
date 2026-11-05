from __future__ import annotations

from pathlib import Path


def engine_data_entries(
    engine_root: Path,
    platform: str,
) -> list[tuple[str, str]]:
    root = Path(engine_root)
    common = [
        (
            root / "pkulaw_engine" / "sources" / "selectors.json",
            "pkulaw_engine/sources",
        ),
        (
            root / "pkulaw_engine" / "sources" / "profiles",
            "pkulaw_engine/sources/profiles",
        ),
    ]
    if platform == "darwin":
        entries = common
    elif platform == "win32":
        antiword = root / "vendor" / "antiword"
        entries = [
            *common,
            (antiword / "win-x64", "vendor/antiword/win-x64"),
            (antiword / "SOURCE.md", "vendor/antiword"),
            (antiword / "MANIFEST.json", "vendor/antiword"),
            (antiword / "antiword-0.37.tar.gz", "vendor/antiword"),
            (
                antiword
                / "0001-Make-antiword.exe-relocatable-on-Windows.patch",
                "vendor/antiword",
            ),
        ]
    else:
        raise ValueError(
            f"UNSUPPORTED_PYINSTALLER_PLATFORM: {platform}"
        )

    missing = [source for source, _destination in entries if not source.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing PyInstaller source data: "
            + ", ".join(str(path) for path in missing)
        )
    return [
        (str(source.resolve()), destination)
        for source, destination in entries
    ]
