from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

PUBLISH_MANIFEST = "_publish_manifest.json"
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    "CLOCK$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class AtomicPublishError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_publish_directory(
    *,
    staging_root: Path,
    final_directory: Path,
    batch_key: str,
    files: dict[str, Path],
    report_name: str,
    report_payload: dict[str, Any],
) -> Path:
    if final_directory.exists():
        raise AtomicPublishError(
            "TARGET_EXISTS",
            f"正式批次目录已存在：{final_directory}",
        )
    planned_names = [*files.keys(), report_name]
    folded_names: set[str] = set()
    for name in planned_names:
        _safe_relative_name(name)
        folded = name.replace("\\", "/").casefold()
        if folded in folded_names:
            raise AtomicPublishError(
                "DUPLICATE_FILE_NAME",
                f"发布文件存在大小写重复名称：{name}",
            )
        folded_names.add(folded)
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / f"{final_directory.name}-{uuid.uuid4()}"
    staging.mkdir(parents=True, exist_ok=False)
    manifest_files: list[dict[str, Any]] = []
    try:
        for name, source in files.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(str(destination) + ".tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
            manifest_files.append(
                {
                    "name": name,
                    "size": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            )

        report_path = staging / report_name
        temporary_report = Path(str(report_path) + ".tmp")
        temporary_report.write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_report, report_path)
        manifest = {
            "version": 1,
            "status": "prepared",
            "batchKey": batch_key,
            "preparedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "files": manifest_files,
            "report": {
                "name": report_name,
                "payload": report_payload,
            },
        }
        manifest["files"].append(
            {
                "name": report_name,
                "size": report_path.stat().st_size,
                "sha256": sha256_file(report_path),
            }
        )
        manifest_path = staging / PUBLISH_MANIFEST
        temporary_manifest = Path(str(manifest_path) + ".tmp")
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_manifest, manifest_path)
        return staging
    except Exception:
        # Keep the partial staging directory for diagnosis. It is outside the
        # published final directory and never registered as a completed batch.
        raise


def publish_directory(staging: Path, final_directory: Path) -> dict[str, Any]:
    if final_directory.exists():
        existing = verify_published_directory(final_directory)
        if existing is not None:
            return existing
        raise AtomicPublishError(
            "TARGET_EXISTS",
            f"正式批次目录已存在且无法核验：{final_directory}",
        )
    final_directory.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final_directory)
    verified = verify_published_directory(final_directory)
    if verified is None:
        raise AtomicPublishError(
            "PUBLISH_VERIFY_FAILED",
            f"批次目录发布后核验失败：{final_directory}",
        )
    return verified


def verify_published_directory(
    final_directory: Path,
    *,
    batch_key: str = "",
) -> dict[str, Any] | None:
    manifest_path = final_directory / PUBLISH_MANIFEST
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    try:
        manifest_version = int(manifest.get("version") or 0)
    except (TypeError, ValueError):
        return None
    if manifest_version != 1:
        return None
    if batch_key and str(manifest.get("batchKey") or "") != batch_key:
        return None
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list) or not manifest_files:
        return None
    report = manifest.get("report")
    if not isinstance(report, dict):
        return None
    report_name = str(report.get("name") or "")
    try:
        _safe_relative_name(report_name)
    except AtomicPublishError:
        return None
    seen_names: set[str] = set()
    for item in manifest_files:
        if not isinstance(item, dict):
            return None
        name = str(item.get("name") or "")
        try:
            path = _safe_published_path(final_directory, name)
        except AtomicPublishError:
            return None
        folded = name.replace("\\", "/").casefold()
        if folded in seen_names:
            return None
        seen_names.add(folded)
        if not path.is_file():
            return None
        try:
            expected_size = int(item.get("size"))
        except (TypeError, ValueError):
            return None
        expected_hash = str(item.get("sha256") or "")
        if expected_size < 0 or len(expected_hash) != 64:
            return None
        if path.stat().st_size != expected_size:
            return None
        if sha256_file(path) != expected_hash:
            return None
    if report_name.replace("\\", "/").casefold() not in seen_names:
        return None
    if not isinstance(report.get("payload"), dict):
        return None
    try:
        report_document = json.loads(
            _safe_published_path(final_directory, report_name).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return None
    if (
        not isinstance(report_document, dict)
        or report_document != report["payload"]
    ):
        return None
    return {
        **manifest,
        "directory": str(final_directory),
        "manifestPath": str(manifest_path),
    }


def _safe_relative_name(name: str) -> None:
    normalized = str(name or "").replace("\\", "/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise AtomicPublishError(
            "UNSAFE_FILE_NAME",
            f"发布文件路径不安全：{name}",
        )
    for part in parts:
        if part.rstrip(" .") != part:
            raise AtomicPublishError(
                "UNSAFE_FILE_NAME",
                f"发布文件含尾随点或空格：{name}",
            )
        if re.search(r'[<>:"|?*\x00-\x1f]', part):
            raise AtomicPublishError(
                "UNSAFE_FILE_NAME",
                f"发布文件含 Windows 非法字符：{name}",
            )
        stem = part.split(".", 1)[0].rstrip(" .").upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise AtomicPublishError(
                "UNSAFE_FILE_NAME",
                f"发布文件名为 Windows 保留名：{name}",
            )


def _safe_published_path(root: Path, name: str) -> Path:
    _safe_relative_name(name)
    root_resolved = root.resolve(strict=False)
    path = (root / name).resolve(strict=False)
    try:
        path.relative_to(root_resolved)
    except ValueError as exc:
        raise AtomicPublishError(
            "UNSAFE_FILE_NAME",
            f"发布文件越过正式目录：{name}",
        ) from exc
    return path


def atomic_replace_many(
    replacements: list[tuple[Path, Path]],
    *,
    post_publish: Callable[[], None] | None = None,
) -> None:
    """Replace a group of files with rollback for ordinary write failures."""
    destinations = [destination for _, destination in replacements]
    if len(
        {
            os.path.normcase(str(path.resolve(strict=False)))
            for path in destinations
        }
    ) != len(destinations):
        raise AtomicPublishError(
            "DUPLICATE_DESTINATION",
            "原子替换目标包含重复路径",
        )
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for _, destination in replacements:
            if destination.exists():
                backup = Path(f"{destination}.{uuid.uuid4()}.bak")
                os.replace(destination, backup)
                backups[destination] = backup
        for temporary, destination in replacements:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, destination)
            published.append(destination)
        if post_publish is not None:
            post_publish()
    except Exception:
        for destination in published:
            try:
                destination.unlink()
            except OSError:
                pass
        for destination, backup in backups.items():
            if backup.exists():
                os.replace(backup, destination)
        raise
    else:
        for backup in backups.values():
            try:
                backup.unlink()
            except OSError:
                pass
