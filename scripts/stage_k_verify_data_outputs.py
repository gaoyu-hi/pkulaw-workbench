from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from DrissionPage import Chromium
from openpyxl import load_workbook


DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def evaluate(tab: Any, expression: str) -> Any:
    response = tab.run_cdp(
        "Runtime.evaluate",
        expression=expression,
        awaitPromise=True,
        returnByValue=True,
        userGesture=True,
    )
    if response.get("exceptionDetails"):
        details = response["exceptionDetails"]
        raise RuntimeError(
            details.get("exception", {}).get("description")
            or details.get("text")
            or "renderer evaluation failed"
        )
    remote = response.get("result") or {}
    if remote.get("subtype") == "error":
        raise RuntimeError(remote.get("description") or "renderer evaluation failed")
    return remote.get("value")


def call_api(tab: Any, expression: str) -> Any:
    return evaluate(tab, f"(async () => await ({expression}))()")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_csv(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    unsafe = []
    for row_index, row in enumerate(rows, start=1):
        for column_index, value in enumerate(row, start=1):
            if value.startswith(DANGEROUS_PREFIXES):
                unsafe.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "value": value[:120],
                    }
                )
    return {
        "rows": len(rows),
        "columns": max((len(row) for row in rows), default=0),
        "formulaUnsafeCells": unsafe,
    }


def inspect_xlsx(path: Path) -> dict[str, Any]:
    workbook = load_workbook(path, data_only=False, read_only=False)
    unsafe = []
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                value = cell.value
                if cell.data_type == "f":
                    unsafe.append(
                        {
                            "sheet": worksheet.title,
                            "cell": cell.coordinate,
                            "value": str(value)[:120],
                        }
                    )
                elif isinstance(value, str) and value.startswith(DANGEROUS_PREFIXES):
                    unsafe.append(
                        {
                            "sheet": worksheet.title,
                            "cell": cell.coordinate,
                            "value": value[:120],
                        }
                    )
    return {
        "sheets": workbook.sheetnames,
        "rows": {
            worksheet.title: worksheet.max_row
            for worksheet in workbook.worksheets
        },
        "formulaUnsafeCells": unsafe,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="taskId:processingId",
    )
    args = parser.parse_args()

    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    ui_tabs = [
        tab
        for tab in browser.get_tabs()
        if str(tab.url).startswith("file://")
        and "/out/renderer/index.html" in str(tab.url)
    ]
    if len(ui_tabs) != 1:
        raise RuntimeError(
            f"expected one packaged renderer tab, found {len(ui_tabs)}"
        )
    tab = ui_tabs[0]

    results = []
    for value in args.run:
        task_id, processing_id = value.split(":", 1)
        details = call_api(
            tab,
            f"window.pkulaw.getDataProcessing({json.dumps(processing_id)})",
        )
        run = details["run"]
        artifacts = []
        for artifact in details.get("artifacts") or []:
            path = Path(str(artifact.get("path") or ""))
            actual_hash = file_sha256(path) if path.is_file() else ""
            artifacts.append(
                {
                    "kind": artifact.get("kind"),
                    "path": str(path),
                    "expectedSha256": artifact.get("sha256"),
                    "actualSha256": actual_hash,
                    "exists": path.is_file(),
                    "hashMatches": bool(
                        actual_hash
                        and actual_hash.lower()
                        == str(artifact.get("sha256") or "").lower()
                    ),
                }
            )
        documents = []
        for document in details.get("documents") or []:
            word_path = Path(str(document.get("wordPath") or ""))
            txt_path = Path(str(document.get("txtPath") or ""))
            evidence_path = Path(str(document.get("evidencePath") or ""))
            payload = dict(document.get("payload") or {})
            documents.append(
                {
                    "policyId": document.get("policyId"),
                    "title": document.get("title"),
                    "status": document.get("status"),
                    "sourceType": document.get("sourceType"),
                    "wordExists": word_path.is_file(),
                    "txtExists": txt_path.is_file(),
                    "evidenceExists": evidence_path.is_file(),
                    "wordShaMatches": bool(
                        word_path.is_file()
                        and file_sha256(word_path).lower()
                        == str(document.get("sha256") or "").lower()
                    ),
                    "txtShaMatches": bool(
                        txt_path.is_file()
                        and file_sha256(txt_path).lower()
                        == str(payload.get("txtSha256") or "").lower()
                    ),
                    "textMethod": payload.get("textMethod"),
                    "error": document.get("error"),
                }
            )
        csv_check = None
        xlsx_check = None
        for artifact in artifacts:
            if artifact["kind"] == "总目录CSV":
                csv_check = inspect_csv(Path(artifact["path"]))
            elif artifact["kind"] == "总目录XLSX":
                xlsx_check = inspect_xlsx(Path(artifact["path"]))
        results.append(
            {
                "taskId": task_id,
                "processingId": processing_id,
                "status": run.get("status"),
                "summary": run.get("summary"),
                "error": run.get("error"),
                "artifacts": artifacts,
                "documents": documents,
                "csv": csv_check,
                "xlsx": xlsx_check,
            }
        )

    output = {
        "runs": results,
        "allArtifactsPresent": all(
            artifact["exists"] and artifact["hashMatches"]
            for result in results
            for artifact in result["artifacts"]
        ),
        "allCsvFormulaSafe": all(
            not (result["csv"] or {}).get("formulaUnsafeCells")
            for result in results
            if result["csv"] is not None
        ),
        "allXlsxFormulaSafe": all(
            not (result["xlsx"] or {}).get("formulaUnsafeCells")
            for result in results
            if result["xlsx"] is not None
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
