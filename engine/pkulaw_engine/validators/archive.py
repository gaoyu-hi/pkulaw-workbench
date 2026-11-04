from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..policy import normalize_title

FORMAT_SUFFIXES = {
    "docx": (".doc", ".docx"),
    "wps": (".wps", ".doc", ".docx"),
    "pdf": (".pdf",),
    "txt": (".txt",),
    "html": (".html", ".htm"),
}


class ArchiveValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveLimits:
    max_archive_bytes: int = 512 * 1024 * 1024
    max_total_uncompressed_bytes: int = 1024 * 1024 * 1024
    max_member_bytes: int = 256 * 1024 * 1024
    max_members: int = 1000
    max_compression_ratio: float = 200.0
    max_depth: int = 16
    max_filename_length: int = 240


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


def validate_zip(
    path: Path,
    *,
    selected: list[dict[str, Any]],
    full_format: str,
    limits: ArchiveLimits | None = None,
) -> dict[str, Any]:
    effective_limits = limits or ArchiveLimits()
    if not path.is_file() or path.stat().st_size <= 0:
        raise ArchiveValidationError("ZIP 文件不存在或为空")
    if path.stat().st_size > effective_limits.max_archive_bytes:
        raise ArchiveValidationError(
            f"ZIP 文件大小 {path.stat().st_size} 超过限制 "
            f"{effective_limits.max_archive_bytes}"
        )
    accepted = FORMAT_SUFFIXES.get(full_format)
    if not accepted:
        raise ArchiveValidationError(f"不支持的全文格式：{full_format}")

    try:
        with zipfile.ZipFile(path) as archive:
            all_infos = archive.infolist()
            infos = [info for info in all_infos if not info.is_dir()]
            if len(all_infos) > effective_limits.max_members:
                raise ArchiveValidationError(
                    f"ZIP 成员数 {len(all_infos)} 超过限制 "
                    f"{effective_limits.max_members}"
                )
            total_uncompressed = sum(max(0, info.file_size) for info in infos)
            if total_uncompressed > effective_limits.max_total_uncompressed_bytes:
                raise ArchiveValidationError(
                    f"ZIP 解压总大小 {total_uncompressed} 超过限制 "
                    f"{effective_limits.max_total_uncompressed_bytes}"
                )
            normalized_members: set[str] = set()
            for info in all_infos:
                normalized_name = info.filename.replace("\\", "/")
                member = PurePosixPath(normalized_name)
                if (
                    member.is_absolute()
                    or ".." in member.parts
                    or re_drive_prefix(normalized_name)
                ):
                    raise ArchiveValidationError(f"ZIP 内含不安全路径：{info.filename}")
                if len(member.parts) - 1 > effective_limits.max_depth:
                    raise ArchiveValidationError(
                        f"ZIP 成员目录层级过深：{info.filename}"
                    )
                if len(info.filename) > effective_limits.max_filename_length:
                    raise ArchiveValidationError(
                        f"ZIP 成员名过长：{info.filename[:80]}..."
                    )
                _validate_windows_member_name(info.filename)
                folded = normalized_name.casefold()
                if folded in normalized_members:
                    raise ArchiveValidationError(
                        f"ZIP 内存在大小写重复成员：{info.filename}"
                    )
                normalized_members.add(folded)
            for info in infos:
                if info.file_size > effective_limits.max_member_bytes:
                    raise ArchiveValidationError(
                        f"ZIP 单成员过大：{info.filename} "
                        f"({info.file_size} bytes)"
                    )
                ratio = (
                    info.file_size / max(1, info.compress_size)
                    if info.file_size
                    else 0
                )
                if ratio > effective_limits.max_compression_ratio:
                    raise ArchiveValidationError(
                        f"ZIP 成员压缩比过大：{info.filename} ({ratio:.1f})"
                    )
            bad = archive.testzip()
            if bad:
                raise ArchiveValidationError(f"ZIP CRC 校验失败：{bad}")
            documents = [
                info
                for info in infos
                if info.filename.lower().endswith(accepted)
            ]
            if len(documents) != len(selected):
                raise ArchiveValidationError(
                    f"ZIP 全文数量 {len(documents)} 与本批政策数 {len(selected)} 不一致"
                )
            if any(info.file_size <= 0 for info in documents):
                raise ArchiveValidationError("ZIP 内存在零字节全文文件")

            normalized_names = [
                normalize_title(PurePosixPath(info.filename.replace("\\", "/")).stem)
                for info in documents
            ]
            if len(set(normalized_names)) != len(normalized_names):
                raise ArchiveValidationError("ZIP 内全文文件名标准化后存在重复")

            remaining = set(range(len(selected)))
            mapping: list[dict[str, Any]] = []
            for info in documents:
                member_name = info.filename.replace("\\", "/")
                stem = PurePosixPath(member_name).stem
                title_stem = re.sub(
                    r"\((?:FBM[-\s]?CLI)(?:\.[0-9]+){1,4}\)\s*$",
                    "",
                    stem,
                    flags=re.IGNORECASE,
                )
                stem_normalized = normalize_title(title_stem)
                truncated_title = bool(
                    re.search(r"(?:\.{3,}|…+)\s*$", title_stem)
                )
                stem_lower = stem.lower()
                matches = []
                for index in remaining:
                    policy = selected[index]
                    title_normalized = normalize_title(str(policy.get("title") or ""))
                    policy_id = str(policy.get("policyId") or "").lower()
                    truncated_prefix_match = (
                        truncated_title
                        and len(stem_normalized) >= 24
                        and title_normalized.startswith(stem_normalized)
                    )
                    if (
                        title_normalized
                        and (
                            title_normalized in stem_normalized
                            or stem_normalized in title_normalized
                            or truncated_prefix_match
                        )
                    ) or (policy_id and policy_id in stem_lower):
                        matches.append(index)
                if len(matches) != 1:
                    raise ArchiveValidationError(
                        f"ZIP 成员 {member_name!r} 无法唯一映射到目录中的政策"
                    )
                selected_index = matches[0]
                remaining.remove(selected_index)
                content_hash = hashlib.sha256(archive.read(info)).hexdigest()
                mapping.append(
                    {
                        "policyId": selected[selected_index].get("policyId"),
                        "title": selected[selected_index].get("title"),
                        "archiveMember": member_name,
                        "size": info.file_size,
                        "sha256": content_hash,
                    }
                )
            if remaining:
                raise ArchiveValidationError("ZIP 中有已选政策没有对应的全文文件")
    except zipfile.BadZipFile as exc:
        raise ArchiveValidationError(f"下载文件不是有效 ZIP：{exc}") from exc

    return {
        "zipPath": str(path),
        "selectedCount": len(selected),
        "documentCount": len(documents),
        "crcOk": True,
        "pathTraversalOk": True,
        "nonEmptyOk": True,
        "uniqueFilesOk": True,
        "identityMappingOk": True,
        "limits": {
            "maxArchiveBytes": effective_limits.max_archive_bytes,
            "maxTotalUncompressedBytes": effective_limits.max_total_uncompressed_bytes,
            "maxMemberBytes": effective_limits.max_member_bytes,
            "maxMembers": effective_limits.max_members,
            "maxCompressionRatio": effective_limits.max_compression_ratio,
            "maxDepth": effective_limits.max_depth,
            "maxFilenameLength": effective_limits.max_filename_length,
        },
        "mapping": mapping,
    }


def re_drive_prefix(value: str) -> bool:
    return bool(len(value) >= 2 and value[0].isalpha() and value[1] == ":")


def _validate_windows_member_name(filename: str) -> None:
    normalized = filename.replace("\\", "/")
    for component in PurePosixPath(normalized).parts:
        if not component or component in {".", ".."}:
            continue
        if component.rstrip(" .") != component:
            raise ArchiveValidationError(
                f"ZIP 成员名含尾随点或空格：{filename}"
            )
        if re.search(r'[<>:"|?*\x00-\x1f]', component):
            raise ArchiveValidationError(
                f"ZIP 成员名含 Windows 非法字符：{filename}"
            )
        stem = component.split(".", 1)[0].rstrip(" .").upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise ArchiveValidationError(
                f"ZIP 成员名为 Windows 保留名：{filename}"
            )
