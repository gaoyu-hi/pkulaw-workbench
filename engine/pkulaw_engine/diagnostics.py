from __future__ import annotations

import importlib.metadata
import importlib
import os
import platform
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from .processors.documents import inspect_bundled_antiword
from .protocol import PROTOCOL_VERSION
from .storage import SCHEMA_VERSION, TaskStore, utc_now


DEPENDENCY_PACKAGES = (
    ("DrissionPage", "DrissionPage", "DrissionPage"),
    ("python-docx", "python-docx", "docx"),
    ("openpyxl", "openpyxl", "openpyxl"),
    ("xlrd", "xlrd", "xlrd"),
)


def _package_version(distribution: str, module_name: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            return ""
        return str(getattr(module, "__version__", "") or "import-ok")


def _write_probe(directory: Path) -> tuple[bool, str]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / f".pkulaw-write-test-{uuid.uuid4().hex}"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink()
        return True, ""
    except OSError as exc:
        return False, str(exc)


def collect_diagnostics(
    store: TaskStore,
    data_directory: Path,
    app_version: str,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    database_path = store.database_path
    data_directory = data_directory.resolve()
    writable, write_error = _write_probe(data_directory)
    checks.append(
        {
            "id": "data-directory",
            "label": "数据目录可写",
            "status": "ok" if writable else "error",
            "detail": str(data_directory) if writable else write_error,
        }
    )

    try:
        integrity = str(store.connection.execute("PRAGMA integrity_check").fetchone()[0])
    except Exception as exc:
        integrity = f"error: {exc}"
    schema_version = int(store.connection.execute("PRAGMA user_version").fetchone()[0])
    checks.append(
        {
            "id": "sqlite-integrity",
            "label": "SQLite 完整性",
            "status": "ok" if integrity == "ok" else "error",
            "detail": f"integrity={integrity}, schema={schema_version}",
        }
    )
    if schema_version != SCHEMA_VERSION:
        checks.append(
            {
                "id": "sqlite-schema",
                "label": "SQLite 版本",
                "status": "error",
                "detail": f"当前 {schema_version}，期望 {SCHEMA_VERSION}",
            }
        )

    dependencies: dict[str, str] = {}
    for distribution, label, module_name in DEPENDENCY_PACKAGES:
        installed = _package_version(distribution, module_name)
        dependencies[label] = installed
        checks.append(
            {
                "id": f"dependency-{distribution.lower()}",
                "label": f"依赖 {label}",
                "status": "ok" if installed else "error",
                "detail": installed or "未安装",
            }
        )

    bundled_antiword = inspect_bundled_antiword()
    legacy_converters = {
        "bundled-antiword": (
            bundled_antiword["path"]
            if bundled_antiword["status"] == "available"
            else ""
        ),
        "antiword": shutil.which("antiword") or "",
        "textutil": shutil.which("textutil") or "",
        "libreoffice": shutil.which("soffice") or shutil.which("libreoffice") or "",
        "microsoft-word-com": (
            "available" if _package_version("pywin32", "win32com") else "not-installed"
        ),
    }
    legacy_available = (
        bundled_antiword["status"] == "available"
        or any(
            value and value != "not-installed"
            for key, value in legacy_converters.items()
            if key != "bundled-antiword"
        )
    )
    checks.append(
        {
            "id": "legacy-doc-converter",
            "label": "旧版 DOC 转换链",
            "status": "ok" if legacy_available else "warning",
            "detail": (
                f"内置 antiword {bundled_antiword['version']} 可用；DOCX 不受影响"
                if bundled_antiword["status"] == "available"
                else "旧版 .doc 可由系统后备转换器处理；DOCX 不受影响"
                if legacy_available
                else "未检测到 antiword、LibreOffice 或 pywin32；DOCX 不受影响"
            ),
        }
    )

    task_counts = {
        row["status"]: int(row["count"])
        for row in store.connection.execute(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
        ).fetchall()
    }
    try:
        usage = shutil.disk_usage(data_directory)
        disk = {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        }
    except OSError:
        disk = {"total": 0, "used": 0, "free": 0}

    logs_directory = data_directory / "logs"
    log_files = sorted(
        (path for path in logs_directory.glob("*.jsonl") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    checks.append(
        {
            "id": "logs",
            "label": "运行日志",
            "status": "ok" if log_files else "warning",
            "detail": str(log_files[0]) if log_files else "尚无 JSONL 日志",
        }
    )
    checks.append(
        {
            "id": "long-path",
            "label": "路径长度",
            "status": "ok" if len(str(data_directory)) < 240 else "warning",
            "detail": f"{len(str(data_directory))} 字符；实际写入能力见数据目录检查",
        }
    )

    return {
        "generatedAt": utc_now(),
        "checks": checks,
        "engine": {
            "name": "pkulaw-engine",
            "version": __version__,
            "appVersion": app_version,
            "protocolVersion": PROTOCOL_VERSION,
            "pythonVersion": platform.python_version(),
            "executable": sys.executable,
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        "paths": {
            "dataDirectory": str(data_directory),
            "database": str(database_path),
            "logsDirectory": str(logs_directory),
            "tempDirectory": tempfile.gettempdir(),
        },
        "database": {
            "integrity": integrity,
            "schemaVersion": schema_version,
            "expectedSchemaVersion": SCHEMA_VERSION,
            "taskCounts": task_counts,
            "sizeBytes": database_path.stat().st_size if database_path.is_file() else 0,
        },
        "dependencies": dependencies,
        "legacyConverters": legacy_converters,
        "disk": disk,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processId": os.getpid(),
            "generatedAtMonotonic": time.monotonic(),
        },
    }
