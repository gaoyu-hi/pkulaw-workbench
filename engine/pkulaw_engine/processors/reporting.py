from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import time
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..atomic_publish import atomic_replace_many

CATALOG_COLUMNS = [
    "政策ID",
    "标题",
    "发文字号",
    "制定机关",
    "公布日期",
    "施行日期",
    "时效性",
    "效力位阶",
    "来源类型",
    "来源网址",
    "检索时间",
    "抓取方式",
    "正文来源",
    "提取方法",
    "转换器名称",
    "转换器版本",
    "正文完整性",
    "Word文件",
    "TXT文件",
    "来源证据",
    "Word SHA256",
    "TXT SHA256",
    "官方HTML证据",
    "官方截图证据",
    "HTML SHA256",
    "截图 SHA256",
    "核验状态",
    "处理状态",
    "说明",
]

FORMULA_PREFIXES = ("=", "+", "-", "@")
EXCEL_CELL_MAX_CHARS = 32_767
EXCEL_TRUNCATION_MARKER = "…[完整内容见CSV]"


def spreadsheet_safe_text(value: Any) -> str:
    """Preserve visible text while preventing spreadsheet formula execution."""
    return str(value if value is not None else "")


def xlsx_safe_text(value: Any) -> str:
    text = spreadsheet_safe_text(value)
    if len(text) <= EXCEL_CELL_MAX_CHARS:
        return text
    keep = EXCEL_CELL_MAX_CHARS - len(EXCEL_TRUNCATION_MARKER)
    return f"{text[:keep]}{EXCEL_TRUNCATION_MARKER}"


def csv_safe_text(value: Any) -> str:
    text = spreadsheet_safe_text(value)
    stripped = text.lstrip()
    if stripped.startswith(FORMULA_PREFIXES):
        return f"'{text}"
    return text


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_master_catalog(
    documents: list[dict[str, Any]],
    output_root: Path,
) -> tuple[Path, Path]:
    csv_path = output_root / "06_总目录.csv"
    xlsx_path = output_root / "06_总目录.xlsx"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_csv = csv_path.with_suffix(".csv.tmp")
    temporary_xlsx = xlsx_path.with_suffix(".xlsx.tmp")
    _write_master_catalog_files(
        documents,
        output_root=output_root,
        csv_path=temporary_csv,
        xlsx_path=temporary_xlsx,
    )
    atomic_replace_many(
        [
            (temporary_csv, csv_path),
            (temporary_xlsx, xlsx_path),
        ]
    )
    return csv_path, xlsx_path


def stage_master_catalog(
    documents: list[dict[str, Any]],
    output_root: Path,
    staging_root: Path,
) -> tuple[Path, Path]:
    """Build catalog files without touching their final paths."""
    staging_root.mkdir(parents=True, exist_ok=True)
    csv_path = staging_root / "06_总目录.csv"
    xlsx_path = staging_root / "06_总目录.xlsx"
    _write_master_catalog_files(
        documents,
        output_root=output_root,
        csv_path=csv_path,
        xlsx_path=xlsx_path,
    )
    return csv_path, xlsx_path


def _write_master_catalog_files(
    documents: list[dict[str, Any]],
    *,
    output_root: Path,
    csv_path: Path,
    xlsx_path: Path,
) -> None:
    rows = [_catalog_row(document, output_root) for document in documents]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CATALOG_COLUMNS)
        writer.writeheader()
        writer.writerows(
            {
                column: csv_safe_text(row[column])
                for column in CATALOG_COLUMNS
            }
            for row in rows
        )
        stream.flush()
        os.fsync(stream.fileno())
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "政策总目录"
    header_fill = PatternFill("solid", fgColor="1F3A55")
    header_font = Font(color="FFFFFF", bold=True)
    sheet.append(CATALOG_COLUMNS)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(vertical="center")
    for row_index, row in enumerate(rows, start=2):
        for column_index, column in enumerate(CATALOG_COLUMNS, start=1):
            cell = sheet.cell(row_index, column_index)
            cell.value = xlsx_safe_text(row[column])
            # Force text storage for all catalog values, including strings that
            # begin with an Excel formula sigil after leading whitespace.
            cell.data_type = "s"
    sheet.freeze_panes = "A2"
    widths = {
        "政策ID": 18,
        "标题": 52,
        "发文字号": 22,
        "制定机关": 30,
        "来源网址": 48,
        "Word文件": 42,
        "TXT文件": 42,
        "来源证据": 42,
        "正文来源": 20,
        "提取方法": 24,
        "转换器名称": 20,
        "转换器版本": 18,
        "正文完整性": 16,
        "Word SHA256": 68,
        "TXT SHA256": 68,
        "官方HTML证据": 42,
        "官方截图证据": 42,
        "HTML SHA256": 68,
        "截图 SHA256": 68,
        "说明": 38,
    }
    for index, column in enumerate(CATALOG_COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = widths.get(column, 16)
        for cell in sheet[get_column_letter(index)]:
            cell.alignment = Alignment(vertical="top", wrap_text=column in {"标题", "说明"})
    workbook.save(xlsx_path)
    workbook.close()


def write_task_report(
    *,
    output_root: Path,
    task: dict[str, Any],
    processing_run: dict[str, Any],
    documents: list[dict[str, Any]],
    artifacts: dict[str, str],
    report_path: Path | None = None,
    display_artifacts: dict[str, str] | None = None,
) -> Path:
    status_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    review_counts: dict[str, int] = {}
    for document in documents:
        status_counts[document["status"]] = status_counts.get(document["status"], 0) + 1
        source = document.get("sourceType") or "未识别"
        source_counts[source] = source_counts.get(source, 0) + 1
        review_status = str(
            document.get("reviewStatus")
            or (
                "pending_review"
                if document["status"] in {"pending_review", "failed"}
                else "not_required"
            )
        )
        review_counts[review_status] = review_counts.get(review_status, 0) + 1
    rows = "".join(
        f"""
        <tr>
          <td>{html.escape(document.get("policyId", ""))}</td>
          <td>{html.escape(document.get("title", ""))}</td>
          <td>{html.escape(document.get("sourceType", ""))}</td>
          <td><a href="{html.escape(document.get("sourceUrl", ""))}">{html.escape(document.get("sourceUrl", ""))}</a></td>
          <td>{html.escape(document.get("retrievalMethod", ""))}</td>
          <td>{html.escape(document.get("textSourceType", ""))}</td>
          <td>{html.escape(document.get("extractionMethod", ""))}</td>
          <td>{html.escape(document.get("converterName", ""))}<br>{html.escape(document.get("converterVersion", ""))}</td>
          <td>{html.escape(document.get("bodyCompleteness", ""))}</td>
          <td>{html.escape(document.get("reviewStatus", ""))}</td>
          <td>{html.escape(document.get("sha256", ""))}</td>
          <td>{html.escape(document.get("evidencePath", ""))}</td>
          <td>{html.escape(document.get("status", ""))}</td>
          <td>{html.escape((document.get("error") or {}).get("message", ""))}</td>
        </tr>
        """
        for document in documents
    )
    shown_artifacts = display_artifacts or artifacts
    artifact_rows = "".join(
        f"<li><strong>{html.escape(kind)}</strong>："
        f"{html.escape(shown_artifacts.get(kind, path))}"
        f"<br><code>{file_sha256(Path(path))}</code></li>"
        if Path(path).is_file()
        else f"<li><strong>{html.escape(kind)}</strong>："
        f"{html.escape(shown_artifacts.get(kind, path))}（缺失）</li>"
        for kind, path in artifacts.items()
    )
    target_report_path = report_path or (output_root / "07_任务报告.html")
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>政策采集任务验收报告</title>
  <style>
    body{{margin:0;padding:32px;background:#f2f5f7;color:#253b50;
      font-family:"Microsoft YaHei UI",sans-serif}}
    main{{max-width:1180px;margin:auto;padding:30px;border:1px solid #dce2e8;
      border-radius:18px;background:#fff;box-shadow:0 12px 40px rgba(25,42,58,.08)}}
    h1{{font:500 30px Georgia,"Songti SC",serif}} .meta{{color:#7b8997;font-size:12px}}
    .cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}}
    .card{{padding:16px;border-radius:12px;background:#f5f8fa}}
    .card strong{{display:block;font-size:24px;color:#244f75}}
    table{{width:100%;border-collapse:collapse;font-size:11px}}
    th,td{{padding:8px;border-bottom:1px solid #e6ebef;text-align:left;vertical-align:top}}
    th{{background:#f6f8fa}} a{{color:#28699f}}
  </style>
</head>
<body><main>
  <h1>政策采集任务验收报告</h1>
  <p class="meta">任务：{html.escape(task["name"])} · 运行：{html.escape(processing_run["id"])} · 生成：{generated_at}</p>
  <div class="cards">
    <div class="card"><strong>{len(documents)}</strong>政策记录</div>
    <div class="card"><strong>{status_counts.get("processed", 0)}</strong>已完成正文</div>
    <div class="card"><strong>{review_counts.get("pending_review", 0)}</strong>待核验</div>
  </div>
  <h2>产物</h2><ul>{artifact_rows}</ul>
  <h2>来源统计</h2>
  <pre>{html.escape(json.dumps(source_counts, ensure_ascii=False, indent=2))}</pre>
  <h2>逐条结果</h2>
  <table><thead><tr><th>政策ID</th><th>标题</th><th>原件来源</th><th>网址</th><th>抓取方式</th><th>正文来源</th><th>提取方法</th><th>转换器</th><th>正文完整性</th><th>核验状态</th><th>Word SHA256</th><th>来源证据</th><th>处理状态</th><th>说明</th></tr></thead>
  <tbody>{rows}</tbody></table>
</main></body></html>"""
    target_report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_report_path.with_suffix(".html.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target_report_path)
    return target_report_path


def _catalog_row(document: dict[str, Any], output_root: Path) -> dict[str, str]:
    def relative(path_value: str) -> str:
        if not path_value:
            return ""
        try:
            return str(Path(path_value).resolve().relative_to(output_root.resolve()))
        except ValueError:
            return str(path_value)

    error = document.get("error") or {}
    payload = document.get("payload") or {}
    return {
        "政策ID": str(document.get("policyId") or ""),
        "标题": str(document.get("title") or ""),
        "发文字号": str(document.get("documentNumber") or ""),
        "制定机关": str(document.get("issuingAuthority") or ""),
        "公布日期": str(document.get("publicationDate") or ""),
        "施行日期": str(document.get("effectiveDate") or ""),
        "时效性": str(document.get("validity") or ""),
        "效力位阶": str(document.get("effectLevel") or ""),
        "来源类型": str(document.get("sourceType") or ""),
        "来源网址": str(document.get("sourceUrl") or ""),
        "检索时间": str(document.get("retrievedAt") or ""),
        "抓取方式": str(document.get("retrievalMethod") or ""),
        "正文来源": str(document.get("textSourceType") or ""),
        "提取方法": str(document.get("extractionMethod") or ""),
        "转换器名称": str(document.get("converterName") or ""),
        "转换器版本": str(document.get("converterVersion") or ""),
        "正文完整性": str(document.get("bodyCompleteness") or ""),
        "Word文件": relative(str(document.get("wordPath") or "")),
        "TXT文件": relative(str(document.get("txtPath") or "")),
        "来源证据": relative(str(document.get("evidencePath") or "")),
        "Word SHA256": str(document.get("sha256") or ""),
        "TXT SHA256": str(payload.get("txtSha256") or ""),
        "官方HTML证据": relative(str(payload.get("officialHtml") or "")),
        "官方截图证据": relative(str(payload.get("officialScreenshot") or "")),
        "HTML SHA256": str(payload.get("officialHtmlSha256") or ""),
        "截图 SHA256": str(payload.get("officialScreenshotSha256") or ""),
        "核验状态": str(document.get("reviewStatus") or ""),
        "处理状态": str(document.get("status") or ""),
        "说明": str(error.get("message") or ""),
    }
