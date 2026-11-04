from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..policy import extract_policy_id, normalize_title

TITLE_HEADERS = {"标题", "政策标题", "政策名称", "名称"}
NUMBER_HEADERS = {"序号", "编号"}
URL_HEADERS = {"原文链接", "政策网址", "原文网址", "链接"}


class CatalogValidationError(RuntimeError):
    pass


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, str]]]] = []
        self.table_depth = 0
        self.current_table: list[list[tuple[str, str]]] = []
        self.current_row: list[tuple[str, str]] | None = None
        self.current_cell: list[str] | None = None
        self.current_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            self.table_depth += 1
            if self.table_depth == 1:
                self.current_table = []
        elif self.table_depth == 1 and tag == "tr":
            self.current_row = []
        elif self.table_depth == 1 and tag in {"td", "th"} and self.current_row is not None:
            self.current_cell = []
        elif self.current_cell is not None and tag == "br":
            self.current_cell.append(" ")
        elif self.current_cell is not None and tag == "a":
            self.current_href = str(attributes.get("href") or "")

    def handle_data(self, data: str) -> None:
        if self.current_cell is not None:
            self.current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.table_depth == 1 and tag in {"td", "th"} and self.current_cell is not None:
            if self.current_row is not None:
                self.current_row.append(
                    ("".join(self.current_cell).strip(), self.current_href)
                )
            self.current_cell = None
            self.current_href = ""
        elif self.table_depth == 1 and tag == "tr" and self.current_row is not None:
            self.current_table.append(self.current_row)
            self.current_row = None
        elif tag == "table" and self.table_depth:
            if self.table_depth == 1:
                self.tables.append(self.current_table)
            self.table_depth -= 1


def normalize_header(value: Any) -> str:
    return re.sub(r"[\s\u3000\ufeff:：]+", "", str(value or "")).strip()


def _read_xlsx(path: Path) -> tuple[list[list[Any]], dict[tuple[int, int], str]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=False, data_only=True)
    try:
        sheet = workbook.active
        matrix = [list(row) for row in sheet.iter_rows(values_only=True)]
        links: dict[tuple[int, int], str] = {}
        for row_index, row in enumerate(sheet.iter_rows(), start=1):
            for col_index, cell in enumerate(row, start=1):
                if cell.hyperlink and cell.hyperlink.target:
                    links[(row_index, col_index)] = str(cell.hyperlink.target)
        return matrix, links
    finally:
        workbook.close()


def _read_xls(path: Path) -> list[list[Any]]:
    import xlrd

    workbook = xlrd.open_workbook(str(path), on_demand=True)
    try:
        sheet = workbook.sheet_by_index(0)
        return [sheet.row_values(index) for index in range(sheet.nrows)]
    finally:
        workbook.release_resources()


def _read_html_xls(path: Path) -> list[list[Any]]:
    raw = path.read_bytes()
    decoded = None
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            candidate = raw.decode(encoding)
        except UnicodeError:
            continue
        if "<table" in candidate.lower():
            decoded = candidate
            break
    if decoded is None:
        raise CatalogValidationError("文件不是有效的 XLSX、BIFF XLS 或 HTML-XLS 表格")
    parser = _HtmlTableParser()
    parser.feed(decoded)
    if not parser.tables:
        raise CatalogValidationError("HTML-XLS 中没有表格")
    largest = max(parser.tables, key=len)
    return [
        [cell[0] if not cell[1] else f"{cell[0]} [链接]{cell[1]}" for cell in row]
        for row in largest
    ]


def read_catalog_matrix(
    path: Path,
) -> tuple[list[list[Any]], dict[tuple[int, int], str], str]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise CatalogValidationError("目录文件不存在或为空")
    magic = path.read_bytes()[:512]
    if magic.startswith(b"PK\x03\x04"):
        matrix, hyperlinks = _read_xlsx(path)
        return matrix, hyperlinks, "xlsx"
    if magic.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return _read_xls(path), {}, "xls"
    return _read_html_xls(path), {}, "html-xls"


def _cell_text_and_link(cell: Any) -> tuple[str, str]:
    text = str(cell if cell is not None else "").strip()
    match = re.search(r"\s*\[链接\](.+)$", text)
    if match:
        return text[: match.start()].strip(), match.group(1).strip()
    if text.startswith(("http://", "https://")):
        return text, text
    return text, ""


def validate_catalog(
    path: Path,
    selected: list[dict[str, Any]],
) -> dict[str, Any]:
    matrix, hyperlinks, actual_format = read_catalog_matrix(path)

    if len(matrix) < 2:
        raise CatalogValidationError("目录表格没有数据行")

    header_index = None
    title_index = None
    number_index = None
    url_index = None
    for index, row in enumerate(matrix[: min(30, len(matrix))]):
        headers = [normalize_header(value) for value in row]
        title = next((i for i, value in enumerate(headers) if value in TITLE_HEADERS), None)
        number = next((i for i, value in enumerate(headers) if value in NUMBER_HEADERS), None)
        if title is not None and number is not None:
            header_index = index
            title_index = title
            number_index = number
            url_index = next(
                (i for i, value in enumerate(headers) if value in URL_HEADERS),
                None,
            )
            break
    if header_index is None or title_index is None or number_index is None:
        preview = [[str(value or "")[:30] for value in row[:12]] for row in matrix[:6]]
        raise CatalogValidationError(f"目录未找到包含“序号”和“标题”的字段行：{preview!r}")

    data_rows = [
        row
        for row in matrix[header_index + 1 :]
        if len(row) > title_index and str(row[title_index] or "").strip()
    ]
    if len(data_rows) != len(selected):
        raise CatalogValidationError(
            f"目录记录数 {len(data_rows)} 与所选政策数 {len(selected)} 不一致"
        )

    catalog_rows: list[dict[str, Any]] = []
    for offset, row in enumerate(data_rows):
        title = str(row[title_index] or "").strip()
        link = ""
        if url_index is not None and url_index < len(row):
            _, link = _cell_text_and_link(row[url_index])
        if not link:
            link = hyperlinks.get((header_index + 2 + offset, (url_index or 0) + 1), "")
        policy_id = extract_policy_id(link)
        expected = selected[offset]
        if normalize_title(title) != normalize_title(str(expected.get("title") or "")):
            raise CatalogValidationError(
                f"目录第 {offset + 1} 条标题与所选政策不一致：{title!r} != {expected.get('title')!r}"
            )
        if policy_id and expected.get("policyId"):
            if policy_id.lower() != str(expected["policyId"]).lower():
                raise CatalogValidationError(
                    f"目录第 {offset + 1} 条链接政策 ID 不一致：{policy_id} != {expected['policyId']}"
                )
        catalog_rows.append(
            {
                "number": str(row[number_index] or "").strip(),
                "title": title,
                "url": link,
                "policyId": policy_id or str(expected.get("policyId") or ""),
            }
        )

    return {
        "format": actual_format,
        "path": str(path),
        "headerRow": header_index + 1,
        "rows": catalog_rows,
        "selectedCount": len(selected),
        "policyIdsComplete": all(row["policyId"] for row in catalog_rows),
    }
