from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

from DrissionPage import Chromium


TERMINAL_RUN_STATUSES = {
    "completed",
    "partial",
    "failed",
    "stopped",
}
ATTENTION_RUN_STATUSES = {
    "waiting_login",
    "waiting_manual",
    "paused",
}


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


def wait_for_processing(
    tab: Any,
    processing_id: str,
    *,
    timeout: float,
    interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    history: list[dict[str, Any]] = []
    last_signature = ""
    while time.monotonic() < deadline:
        details = call_api(
            tab,
            f"window.pkulaw.getDataProcessing({json.dumps(processing_id)})",
        )
        run = details["run"]
        signature = "|".join(
            [
                str(run.get("status")),
                str(run.get("currentStage")),
                str(run.get("progress")),
                str((run.get("error") or {}).get("code", "")),
            ]
        )
        if signature != last_signature:
            history.append(
                {
                    "observedAt": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(),
                    ),
                    "status": run.get("status"),
                    "currentStage": run.get("currentStage"),
                    "progress": run.get("progress"),
                    "summary": run.get("summary"),
                    "error": run.get("error"),
                }
            )
            last_signature = signature
        if run.get("status") in TERMINAL_RUN_STATUSES:
            details["observedHistory"] = history
            return details
        if run.get("status") in ATTENTION_RUN_STATUSES:
            details["observedHistory"] = history
            return details
        time.sleep(interval)
    details["observedHistory"] = history
    details["waitTimedOut"] = True
    return details


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--task-id", action="append", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
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

    summary: list[dict[str, Any]] = []
    for task_id in args.task_id:
        started = call_api(
            tab,
            (
                "window.pkulaw.startDataProcessing("
                f"{json.dumps(task_id)}, "
                '{"governmentFallback":false,"governmentSearchTemplate":""}, '
                f"{json.dumps(str(uuid.uuid4()))})"
            ),
        )
        processing_id = str((started.get("run") or {}).get("id") or "")
        if not processing_id:
            raise RuntimeError(f"processing run was not created: {started!r}")
        result = wait_for_processing(
            tab,
            processing_id,
            timeout=args.timeout,
            interval=1.0,
        )
        path = args.output_dir / f"{task_id}.json"
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run = result["run"]
        summary.append(
            {
                "taskId": task_id,
                "processingId": processing_id,
                "status": run.get("status"),
                "currentStage": run.get("currentStage"),
                "summary": run.get("summary"),
                "error": run.get("error"),
                "evidence": str(path),
            }
        )
        if run.get("status") not in {"completed"}:
            break

    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
