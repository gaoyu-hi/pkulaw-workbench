from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


engine_root = Path(SPECPATH).resolve()
project_root = engine_root.parent

sys.path.insert(0, str(engine_root))
from pkulaw_engine.build_support import engine_data_entries

datas = engine_data_entries(engine_root, sys.platform)
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
