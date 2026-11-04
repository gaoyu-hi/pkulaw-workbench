from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def output_files(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    return sorted(
        str(path)
        for path in directory.rglob("*")
        if path.is_file()
    )


def download_id(payload: dict[str, Any]) -> str:
    return str((payload.get("lastDownload") or {}).get("downloadId") or "")


def build_k10_evidence(root: Path) -> dict[str, Any]:
    base = root / "K10_分页与规模门禁"
    tail = load_json(
        base
        / "K10_数据管理_2024_尾页_修复后"
        / "真实多页尾页只读验收.json"
    )
    page_wait = load_json(base / "K10_第2页上限" / "K10_page_limit_wait.json")
    page_stop = load_json(base / "K10_第2页上限" / "K10_page_limit_stop.json")
    page_before = load_json(base / "K10_第2页上限" / "browser_before.json")
    page_after = load_json(base / "K10_第2页上限" / "browser_after.json")

    scale_wait = load_json(base / "K10_规模确认" / "K10_scale_wait.json")
    scale_stop = load_json(base / "K10_规模确认" / "K10_scale_stop.json")
    scale_after = load_json(base / "K10_规模确认" / "browser_after.json")

    pages = list(tail.get("pages") or [])
    require(int(tail.get("totalPages") or 0) == 9, "tail totalPages is not 9")
    require(int(tail.get("pagesScanned") or 0) == 9, "tail scan did not reach 9 pages")
    require(len(pages) == 9, "tail page evidence is incomplete")
    require(
        [int(page.get("page") or 0) for page in pages] == list(range(1, 10)),
        "tail pages are not sequential",
    )
    require(
        all(int(page.get("rowCount") or 0) == 20 for page in pages),
        "tail page row count is not 20",
    )
    require(
        pages[-1].get("nextAvailable") is False,
        "tail page still reports next page as available",
    )
    require(
        tail.get("detailOpened") is False
        and tail.get("downloadClicked") is False
        and int(tail.get("downloadEventsStarted") or 0) == 0,
        "tail scan opened a detail or download",
    )

    page_task = page_wait["task"]
    require(
        (page_task.get("error") or {}).get("code") == "PAGE_LIMIT_REACHED",
        "page-limit gate did not fire",
    )
    page_checkpoint = page_task["checkpoint"]
    require(page_checkpoint.get("currentPage") == 2, "page-limit page is not 2")
    require(page_checkpoint.get("totalPages") == 9, "page-limit total pages is not 9")
    require(page_checkpoint.get("unscannedPages") == 7, "unscanned pages is not 7")
    require(page_checkpoint.get("scannedPages") == 2, "scanned pages is not 2")
    require(not page_wait.get("batches"), "page-limit task created batches")
    require(not page_wait.get("items"), "page-limit task created items")
    require(page_stop["task"]["status"] == "stopped", "page-limit task not stopped")
    require(
        page_stop["task"]["checkpoint"].get("stopPreviousStatus")
        == "waiting_manual",
        "page-limit stop did not preserve waiting_manual provenance",
    )

    scale_task = scale_wait["task"]
    scale_estimate = scale_task["checkpoint"].get("scaleEstimate") or {}
    require(
        (scale_task.get("error") or {}).get("code")
        == "BATCH_SCALE_CONFIRMATION_REQUIRED",
        "scale confirmation gate did not fire",
    )
    require(scale_estimate.get("requiresConfirmation") is True, "scale is not large")
    require(scale_estimate.get("estimatedPages") == 18, "scale pages is not 18")
    require(scale_estimate.get("estimatedRows") == 348, "scale rows is not 348")
    require(
        scale_estimate.get("estimatedBatches") == 7,
        "scale batches is not 7",
    )
    require(
        scale_task["checkpoint"].get("scaleConfirmed") is False,
        "large scale was confirmed unexpectedly",
    )
    require(not scale_wait.get("batches"), "scale task created batches before confirmation")
    require(not scale_wait.get("items"), "scale task created items before confirmation")
    require(scale_stop["task"]["status"] == "stopped", "scale task not stopped")
    require(
        scale_stop["task"]["checkpoint"].get("stopPreviousStatus")
        == "waiting_manual",
        "scale stop did not preserve waiting_manual provenance",
    )

    baseline_download = download_id(page_before)
    require(bool(baseline_download), "missing baseline download id")
    for name, payload in (
        ("page-limit", page_after),
        ("scale-confirmation", scale_after),
    ):
        require(
            download_id(payload) == baseline_download,
            f"{name} produced a new download event",
        )
        require(
            (payload.get("browserLease") or {}).get("available") is True,
            f"{name} did not release the browser lease",
        )

    page_output_files = output_files(base / "K10_第2页上限" / "output")
    scale_output_files = output_files(base / "K10_规模确认" / "output")
    require(not page_output_files, "page-limit task wrote output files")
    require(not scale_output_files, "scale-confirmation task wrote output files")

    return {
        "status": "PASS",
        "tailScan": {
            "keyword": tail.get("keyword"),
            "year": tail.get("year"),
            "library": tail.get("library"),
            "pageSize": tail.get("pageSize"),
            "resultCount": tail.get("resultCount"),
            "totalPages": tail.get("totalPages"),
            "pagesScanned": tail.get("pagesScanned"),
            "tailPage": pages[-1]["page"],
            "tailRowCount": pages[-1]["rowCount"],
            "tailNextAvailable": pages[-1]["nextAvailable"],
            "detailOpened": tail.get("detailOpened"),
            "downloadClicked": tail.get("downloadClicked"),
            "downloadEventsStarted": tail.get("downloadEventsStarted"),
        },
        "pageLimit": {
            "taskId": page_task["id"],
            "errorCode": page_task["error"]["code"],
            "stoppedStatus": page_stop["task"]["status"],
            "currentPage": page_checkpoint.get("currentPage"),
            "totalPages": page_checkpoint.get("totalPages"),
            "scannedPages": page_checkpoint.get("scannedPages"),
            "unscannedPages": page_checkpoint.get("unscannedPages"),
            "batchCount": len(page_wait.get("batches") or []),
            "itemCount": len(page_wait.get("items") or []),
            "outputFiles": page_output_files,
        },
        "scaleConfirmation": {
            "taskId": scale_task["id"],
            "errorCode": scale_task["error"]["code"],
            "stoppedStatus": scale_stop["task"]["status"],
            "scaleEstimate": scale_estimate,
            "scaleConfirmed": scale_task["checkpoint"].get("scaleConfirmed"),
            "batchCount": len(scale_wait.get("batches") or []),
            "itemCount": len(scale_wait.get("items") or []),
            "outputFiles": scale_output_files,
        },
        "downloads": {
            "baselineDownloadId": baseline_download,
            "pageLimitLastDownloadId": download_id(page_after),
            "scaleConfirmationLastDownloadId": download_id(scale_after),
            "newDownloads": 0,
        },
    }


def build_k11_evidence(root: Path, engine_log: Path) -> dict[str, Any]:
    observed: list[dict[str, Any]] = []
    if engine_log.exists():
        for raw in engine_log.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            data = dict(event.get("data") or {})
            error = dict(data.get("error") or {})
            code = str(error.get("code") or "")
            if code not in {
                "MANUAL_ACTION_REQUIRED",
                "DOWNLOAD_UNCLEAR",
                "APPLICATION_INTERRUPTED",
            }:
                continue
            observed.append(
                {
                    "timestamp": event.get("timestamp"),
                    "event": event.get("event"),
                    "taskId": data.get("taskId"),
                    "status": data.get("status"),
                    "currentStage": data.get("currentStage"),
                    "code": code,
                    "message": error.get("message"),
                }
            )

    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in observed:
        key = (
            str(item.get("taskId") or ""),
            str(item.get("code") or ""),
            str(item.get("currentStage") or ""),
        )
        unique.setdefault(key, item)

    directory = root / "K11_异常与恢复"
    directory.mkdir(parents=True, exist_ok=True)
    return {
        "status": "RECORDED_FROM_NATURAL_EVENTS",
        "policy": (
            "Only real-site anomalies observed during the authorized run are "
            "recorded. Login expiry, captcha, quota exhaustion and forced "
            "process termination were not deliberately induced in K11."
        ),
        "observed": list(unique.values()),
        "notifiedButNotReproducedInFinalRun": [
            "机构下载额度提示",
            "批量 Word ZIP 下载页未创建或站点限额",
        ],
        "boundariesConfirmed": {
            "unsafeContinueUsed": False,
            "captchaBypassed": False,
            "quotaBypassed": False,
            "cookieExported": False,
            "passwordsSaved": False,
            "tailBatchDownloadExecuted": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--engine-log", required=True, type=Path)
    args = parser.parse_args()

    k10 = build_k10_evidence(args.root)
    k11 = build_k11_evidence(args.root, args.engine_log)
    k10_path = args.root / "K10_分页与规模门禁" / "K10_VERIFICATION.json"
    k10_path.write_text(
        json.dumps(k10, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    k11_path = args.root / "K11_异常与恢复" / "K11_NATURAL_ANOMALIES.json"
    k11_path.write_text(
        json.dumps(k11, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"k10": str(k10_path), "k11": str(k11_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
