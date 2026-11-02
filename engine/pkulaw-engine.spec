from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


engine_root = Path(SPECPATH).resolve()
project_root = engine_root.parent

datas = [
    (
        str(engine_root / "pkulaw_engine" / "sources" / "selectors.json"),
        "pkulaw_engine/sources",
    ),
    (
        str(engine_root / "pkulaw_engine" / "sources" / "profiles"),
        "pkulaw_engine/sources/profiles",
    ),
    (
        str(engine_root / "vendor" / "antiword" / "win-x64"),
        "vendor/antiword/win-x64",
    ),
    (
        str(engine_root / "vendor" / "antiword" / "SOURCE.md"),
        "vendor/antiword",
    ),
    (
        str(engine_root / "vendor" / "antiword" / "MANIFEST.json"),
        "vendor/antiword",
    ),
    (
        str(engine_root / "vendor" / "antiword" / "antiword-0.37.tar.gz"),
        "vendor/antiword",
    ),
    (
        str(
            engine_root
            / "vendor"
            / "antiword"
            / "0001-Make-antiword.exe-relocatable-on-Windows.patch"
        ),
        "vendor/antiword",
    ),
]
for package in ("DrissionPage", "docx", "openpyxl", "xlrd"):
    datas += collect_data_files(package)

hiddenimports = []
for package in ("DrissionPage", "docx", "openpyxl", "xlrd"):
    hiddenimports += collect_submodules(package)

a = Analysis(
    [str(engine_root / "pkulaw_engine_entry.py")],
    pathex=[str(engine_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pkulaw-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="pkulaw-engine",
)
