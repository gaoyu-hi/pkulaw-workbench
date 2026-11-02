from __future__ import annotations

import hashlib
import html
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .validators.catalog import (
    NUMBER_HEADERS,
    TITLE_HEADERS,
    URL_HEADERS,
    CatalogValidationError,
    normalize_header,
    read_catalog_matrix,
    validate_catalog,
)

CatalogOutputFormat = Literal[
    "native-xls",
    "derived-xlsx",
    "compat-html-xls",
]

BIFF_XLS_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")


@dataclass(frozen=True)
class CatalogFormatContract:
    requested: CatalogOutputFormat
    native: bool
    source_suffixes: tuple[str, ...]
    final_suffix: str
    final_format: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "native": self.native,
            "sourceSuffixes": list(self.source_suffixes),
            "finalSuffix": self.final_suffix,
            "finalFormat": self.final_format,
        }


@dataclass(frozen=True)
class CatalogPreparation:
    contract: CatalogFormatContract
    source_format: str
    final_format: str
    converted: bool
    path: Path
    source_sha256: str
    final_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.contract.to_dict(),
            "sourceFormat": self.source_format,
            "finalFormat": self.final_format,
            "converted": self.converted,
            "path": str(self.path),
            "sourceSha256": self.source_sha256,
            "finalSha256": self.final_sha256,
        }


class CatalogFormatError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = dict(details or {})
        self.recovery = str(
            self.details.get("recovery")
            or "停止当前批次，确认网站目录仍由 catalogExcel 返回 BIFF .xls 后重试"
        )
        self.details.setdefault("recovery", self.recovery)
        super().__init__(message)


_FORMAT_ALIASES: dict[str, CatalogFormatContract] = {
    "xls": CatalogFormatContract(
        requested="native-xls",
        native=True,
        source_suffixes=(".xls",),
        final_suffix=".xls",
        final_format="xls",
    ),
    "native-xls": CatalogFormatContract(
        requested="native-xls",
        native=True,
        source_suffixes=(".xls",),
        final_suffix=".xls",
        final_format="xls",
    ),
    "xlsx": CatalogFormatContract(
        requested="derived-xlsx",
        native=False,
        source_suffixes=(".xls",),
        final_suffix=".xlsx",
        final_format="xlsx",
    ),
    "derived-xlsx": CatalogFormatContract(
        requested="derived-xlsx",
        native=False,
        source_suffixes=(".xls",),
        final_suffix=".xlsx",
        final_format="xlsx",
    ),
    "html-xls": CatalogFormatContract(
        requested="compat-html-xls",
        native=False,
        source_suffixes=(".xls",),
        final_suffix=".xls",
        final_format="html-xls",
    ),
    "compat-html-xls": CatalogFormatContract(
        requested="compat-html-xls",
        native=False,
        source_suffixes=(".xls",),
        final_suffix=".xls",
        final_format="html-xls",
    ),
}


def resolve_catalog_format(value: str) -> CatalogFormatContract:
    key = str(value or "").strip().lower()
    contract = _FORMAT_ALIASES.get(key)
    if contract is None:
        raise CatalogFormatError(
            "INVALID_CATALOG_FORMAT",
            f"不支持的目录格式：{value!r}",
            details={"requestedFormat": str(value or "")},
        )
    return contract


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_signature(path: Path, length: int = 16) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(length).hex()
    except OSError:
        return ""


def _native_mismatch(
    source: Path,
    *,
    reason: str,
) -> CatalogFormatError:
    return CatalogFormatError(
        "CATALOG_NATIVE_FORMAT_MISMATCH",
        "网站原生目录格式不符合固定契约："
        f"{reason}；实际文件={source.name or '<未命名>'}，"
        f"后缀={source.suffix.lower() or '<无>'}，"
        f"签名={_file_signature(source) or '<不可读>'}，"
        f"路径={source}",
        details={
            "actualFile": source.name,
            "actualSuffix": source.suffix.lower(),
            "actualSignature": _file_signature(source),
            "expectedSuffixes": [".xls"],
            "expectedSignature": BIFF_XLS_SIGNATURE.hex(),
            "path": str(source),
            "reason": reason,
            "recovery": "重新触发网站目录下载并确认 catalogExcel 只返回 BIFF .xls；若网站格式变化，停止任务并更新兼容契约",
        },
    )


def _validate_native_source(source: Path) -> None:
    if not source.is_file() or source.stat().st_size <= 0:
        raise _native_mismatch(source, reason="文件不存在或为空")
    if source.suffix.lower() != ".xls":
        raise _native_mismatch(source, reason="网站目录扩展名不是 .xls")
    try:
        with source.open("rb") as handle:
            signature = handle.read(8)
    except OSError as exc:
        raise _native_mismatch(source, reason=f"无法读取文件：{exc}") from exc
    if signature != BIFF_XLS_SIGNATURE:
        raise _native_mismatch(source, reason="网站目录不是 BIFF XLS 签名")


def _temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=destination.suffix or ".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    return Path(raw_path)


def _atomic_copy(source: Path, destination: Path) -> None:
    temp_path = _temporary_path(destination)
    try:
        shutil.copyfile(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)


def write_xlsx_matrix(
    matrix: list[list[Any]],
    destination: Path,
) -> None:
    from openpyxl import Workbook

    temp_path = _temporary_path(destination)
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "政策目录"
        for row_index, row in enumerate(matrix, start=1):
            for column_index, value in enumerate(row, start=1):
                cell = sheet.cell(
                    row=row_index,
                    column=column_index,
                    value="" if value is None else str(value),
                )
                cell.data_type = "s"
                cell.number_format = "@"
        workbook.save(temp_path)
        os.replace(temp_path, destination)
    except Exception as exc:
        raise CatalogFormatError(
            "CATALOG_CONVERSION_FAILED",
            f"原生 XLS 转换为 XLSX 失败：{exc}",
            details={
                "outputFormat": "xlsx",
                "path": str(destination),
                "recovery": "保留网站原生 XLS，检查 openpyxl 运行环境后重试本地转换",
            },
        ) from exc
    finally:
        workbook.close()
        temp_path.unlink(missing_ok=True)


def _html_cell(
    value: Any,
    *,
    header: str,
) -> str:
    text = str(value if value is not None else "")
    escaped = html.escape(text)
    if normalize_header(header) in URL_HEADERS and text.startswith(
        ("http://", "https://", "/", "./", "../")
    ):
        href = html.escape(text, quote=True)
        return f'<a href="{href}">{escaped}</a>'
    return escaped


def write_html_xls_matrix(
    matrix: list[list[Any]],
    destination: Path,
) -> None:
    if not matrix:
        raise CatalogFormatError(
            "CATALOG_CONVERSION_FAILED",
            "原生 XLS 没有可转换的表格内容",
            details={
                "outputFormat": "html-xls",
                "path": str(destination),
                "recovery": "检查网站原生目录内容后重新下载",
            },
        )
    header_index = next(
        (
            index
            for index, row in enumerate(matrix[:30])
            if any(normalize_header(value) in TITLE_HEADERS for value in row)
            and any(normalize_header(value) in NUMBER_HEADERS for value in row)
        ),
        0,
    )
    header = [str(value or "") for value in matrix[header_index]]
    rows: list[str] = []
    for row_index, row in enumerate(matrix):
        cells = []
        for column_index, value in enumerate(row):
            current_header = (
                header[column_index]
                if column_index < len(header)
                else ""
            )
            tag = "th" if row_index == header_index else "td"
            cells.append(
                f"<{tag}>{_html_cell(value, header=current_header)}</{tag}>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    document = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>北大法宝政策目录（兼容 HTML-XLS）</title></head>"
        "<body><table>"
        + "".join(rows)
        + "</table></body></html>"
    )
    temp_path = _temporary_path(destination)
    try:
        temp_path.write_text(document, encoding="utf-8", newline="\n")
        os.replace(temp_path, destination)
    except Exception as exc:
        raise CatalogFormatError(
            "CATALOG_CONVERSION_FAILED",
            f"原生 XLS 转换为兼容 HTML-XLS 失败：{exc}",
            details={
                "outputFormat": "html-xls",
                "path": str(destination),
                "recovery": "保留网站原生 XLS，检查目标目录写入权限后重试",
            },
        ) from exc
    finally:
        temp_path.unlink(missing_ok=True)


def prepare_catalog_output(
    source: Path,
    *,
    requested_format: str,
    selected: list[dict[str, Any]],
    destination: Path,
) -> CatalogPreparation:
    contract = resolve_catalog_format(requested_format)
    source = Path(source)
    destination = Path(destination)
    _validate_native_source(source)
    try:
        source_validation = validate_catalog(source, selected)
        matrix, _links, source_format = read_catalog_matrix(source)
    except CatalogValidationError as exc:
        raise CatalogFormatError(
            "CATALOG_NATIVE_FORMAT_MISMATCH",
            f"网站原生 XLS 无法通过目录身份校验：{exc}",
            details={
                "actualFile": source.name,
                "actualSuffix": source.suffix.lower(),
                "actualSignature": _file_signature(source),
                "expectedSuffixes": [".xls"],
                "expectedSignature": BIFF_XLS_SIGNATURE.hex(),
                "path": str(source),
                "reason": str(exc),
                "recovery": "停止当前批次，核对所选政策行与网站原生目录身份后重新生成批次",
            },
        ) from exc
    except Exception as exc:
        raise CatalogFormatError(
            "CATALOG_CONVERSION_FAILED",
            f"网站原生 BIFF XLS 无法读取或解析：{exc}",
            details={
                "actualFile": source.name,
                "actualSuffix": source.suffix.lower(),
                "actualSignature": _file_signature(source),
                "expectedSuffixes": [".xls"],
                "expectedSignature": BIFF_XLS_SIGNATURE.hex(),
                "path": str(source),
                "reason": str(exc),
                "recovery": "保留损坏的网站原生 XLS，重新下载目录并检查 xlrd 解析环境",
            },
        ) from exc
    if source_format != "xls" or source_validation.get("format") != "xls":
        raise _native_mismatch(source, reason="目录实际格式不是 BIFF XLS")

    converted = contract.final_format != "xls"
    prepared_path = _temporary_path(destination)
    try:
        if contract.final_format == "xlsx":
            write_xlsx_matrix(matrix, prepared_path)
        elif contract.final_format == "html-xls":
            write_html_xls_matrix(matrix, prepared_path)
        else:
            _atomic_copy(source, prepared_path)

        try:
            output_validation = validate_catalog(prepared_path, selected)
        except CatalogValidationError as exc:
            raise CatalogFormatError(
                "CATALOG_CONVERSION_FAILED",
                f"派生目录未通过身份校验：{exc}",
                details={
                    "outputFormat": contract.final_format,
                    "path": str(prepared_path),
                    "recovery": "保留网站原生 XLS，检查派生目录内容后重试",
                },
            ) from exc
        if output_validation.get("format") != contract.final_format:
            raise CatalogFormatError(
                "CATALOG_CONVERSION_FAILED",
                "派生目录实际格式与请求不一致："
                f"{output_validation.get('format')} != {contract.final_format}",
                details={
                    "outputFormat": contract.final_format,
                    "actualFormat": output_validation.get("format"),
                    "path": str(prepared_path),
                    "recovery": "检查本地目录转换器后重试，未通过前不会启动 ZIP",
                },
            )
        os.replace(prepared_path, destination)
    finally:
        prepared_path.unlink(missing_ok=True)

    return CatalogPreparation(
        contract=contract,
        source_format="xls",
        final_format=contract.final_format,
        converted=converted,
        path=destination,
        source_sha256=_sha256(source),
        final_sha256=_sha256(destination),
    )
