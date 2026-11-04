from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

from DrissionPage import Chromium


TERMINAL_TASK_STATUSES = {
    "completed",
    "partial",
    "stopped",
    "failed",
}
ATTENTION_TASK_STATUSES = {
    "waiting_login",
    "waiting_manual",
    "paused",
}
TERMINAL_RUN_STATUSES = {
    "completed",
    "partial",
    "stopped",
    "failed",
}
ATTENTION_RUN_STATUSES = {
    "waiting_login",
    "waiting_manual",
    "paused",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("state")
    subparsers.add_parser("tasks")
    subparsers.add_parser("browser")
    subparsers.add_parser("institutions")
    select_institution = subparsers.add_parser("select-institution")
    select_institution.add_argument("--institution-id", required=True)
    subparsers.add_parser("diagnostics")
    subparsers.add_parser("logs")
    configure = subparsers.add_parser("configure-institution")
    configure.add_argument("--name", required=True)
    configure.add_argument("--entry-url", required=True)

    preview = subparsers.add_parser("preview")
    preview.add_argument("--mode", choices=("single", "batch"), required=True)
    preview.add_argument("--query", action="append", required=True)
    preview.add_argument(
        "--match-mode",
        choices=("title", "keyword"),
        default="keyword",
    )
    preview.add_argument(
        "--library",
        choices=("central", "local", "both"),
        default="central",
    )
    preview.add_argument(
        "--year-mode",
        choices=("all", "single", "range"),
        default="all",
    )
    preview.add_argument("--year", type=int)
    preview.add_argument("--year-start", type=int)
    preview.add_argument("--year-end", type=int)
    preview.add_argument("--province", default="全部省份")
    preview.add_argument(
        "--timeliness",
        default="全部（仅记录元数据，不筛选）",
    )
    preview.add_argument("--page-size", type=int, default=10)
    preview.add_argument(
        "--page-mode",
        choices=("last", "limit"),
        default="last",
    )
    preview.add_argument("--max-pages", type=int)
    preview.add_argument("--output-dir", type=Path, required=True)

    for mode in ("single", "batch"):
        create = subparsers.add_parser(f"create-{mode}")
        create.add_argument("--name", required=True)
        create.add_argument("--query", action="append", required=True)
        create.add_argument(
            "--match-mode",
            choices=("title", "keyword"),
            default="title",
        )
        create.add_argument(
            "--library",
            choices=("central", "local", "both"),
            default="central",
        )
        create.add_argument(
            "--year-mode",
            choices=("all", "single", "range"),
            default="all",
        )
        create.add_argument("--year", type=int)
        create.add_argument("--year-start", type=int)
        create.add_argument("--year-end", type=int)
        create.add_argument("--province", default="全部省份")
        create.add_argument(
            "--timeliness",
            default="全部（仅记录元数据，不筛选）",
        )
        create.add_argument("--page-size", type=int, default=10)
        create.add_argument(
            "--page-mode",
            choices=("last", "limit"),
            default="last",
        )
        create.add_argument("--max-pages", type=int)
        create.add_argument("--output-dir", type=Path, required=True)
        create.add_argument("--no-start", action="store_true")
        if mode == "batch":
            create.add_argument(
                "--catalog-format",
                choices=("xlsx", "xls", "html-xls"),
                default="xlsx",
            )
            create.add_argument(
                "--full-format",
                choices=("docx",),
                default="docx",
            )

    for mode in ("single", "batch"):
        details = subparsers.add_parser(f"{mode}-details")
        details.add_argument("--task-id", required=True)

        wait = subparsers.add_parser(f"wait-{mode}")
        wait.add_argument("--task-id", required=True)
        wait.add_argument("--timeout", type=float, default=900)
        wait.add_argument("--interval", type=float, default=2)

        control = subparsers.add_parser(f"control-{mode}")
        control.add_argument("--task-id", required=True)
        actions = [
            "pause",
            "resume",
            "stop",
            "continue",
            "continue-to-last",
            "retry",
        ]
        if mode == "batch":
            actions.append("confirm-scale")
        control.add_argument("--action", choices=actions, required=True)

    choose = subparsers.add_parser("choose-candidate")
    choose.add_argument("--task-id", required=True)
    choose.add_argument("--item-id", required=True)
    choose.add_argument("--candidate-key", required=True)

    data_start = subparsers.add_parser("start-data")
    data_start.add_argument("--task-id", required=True)
    data_start.add_argument("--government-fallback", action="store_true")
    data_start.add_argument("--government-search-template", default="")

    data_wait = subparsers.add_parser("wait-data")
    data_wait.add_argument("--processing-id", required=True)
    data_wait.add_argument("--timeout", type=float, default=900)
    data_wait.add_argument("--interval", type=float, default=2)

    data_latest = subparsers.add_parser("latest-data")
    data_latest.add_argument("--task-id", required=True)

    return parser.parse_args()


def javascript_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


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


def create_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.year_mode == "single":
        if args.year is None:
            raise SystemExit("--year is required for single year mode")
        year_selection = {"mode": "single", "year": args.year}
    elif args.year_mode == "range":
        if args.year_start is None or args.year_end is None:
            raise SystemExit("--year-start and --year-end are required")
        year_selection = {
            "mode": "range",
            "startYear": args.year_start,
            "endYear": args.year_end,
        }
    else:
        year_selection = {"mode": "all"}

    if args.page_mode == "limit":
        if args.max_pages is None:
            raise SystemExit("--max-pages is required for limit mode")
        page_selection = {"mode": "limit", "maxPages": args.max_pages}
    else:
        page_selection = {"mode": "last"}

    payload: dict[str, Any] = {
        "queries": args.query,
        "keywords": args.query,
        "matchMode": args.match_mode,
        "library": args.library,
        "year": (
            str(args.year)
            if args.year_mode == "single"
            else "全部年份"
        ),
        "yearMode": args.year_mode,
        "yearStart": args.year_start,
        "yearEnd": args.year_end,
        "yearSelection": year_selection,
        "province": args.province,
        "timeliness": args.timeliness,
        "pageSize": args.page_size,
        "pageMode": page_selection["mode"],
        "pageSelection": page_selection,
        "outputDir": str(args.output_dir.resolve()),
    }
    if page_selection["mode"] == "limit":
        payload["maxPages"] = page_selection["maxPages"]
    if args.command == "create-batch":
        payload["catalogFormat"] = args.catalog_format
        payload["fullFormat"] = args.full_format
    return payload


def wait_for_task(
    tab: Any,
    *,
    mode: str,
    task_id: str,
    timeout: float,
    interval: float,
) -> dict[str, Any]:
    method = (
        "getTaskDetails"
        if mode == "single"
        else "getBatchTaskDetails"
    )
    deadline = time.monotonic() + timeout
    history: list[dict[str, Any]] = []
    last_signature = ""
    while time.monotonic() < deadline:
        details = call_api(
            tab,
            f"window.pkulaw.{method}({javascript_json(task_id)})",
        )
        task = details["task"]
        signature = "|".join(
            [
                str(task["status"]),
                str(task["currentStage"]),
                str(task.get("progress")),
                str((task.get("error") or {}).get("code", "")),
            ]
        )
        if signature != last_signature:
            history.append(
                {
                    "observedAt": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(),
                    ),
                    "status": task["status"],
                    "currentStage": task["currentStage"],
                    "progress": task.get("progress"),
                    "error": task.get("error"),
                    "checkpoint": task.get("checkpoint"),
                }
            )
            last_signature = signature
        if task["status"] in TERMINAL_TASK_STATUSES:
            details["observedHistory"] = history
            return details
        if task["status"] in ATTENTION_TASK_STATUSES:
            details["observedHistory"] = history
            return details
        time.sleep(interval)
    details["observedHistory"] = history
    details["waitTimedOut"] = True
    return details


def wait_for_processing(
    tab: Any,
    *,
    processing_id: str,
    timeout: float,
    interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    history: list[dict[str, Any]] = []
    last_signature = ""
    while time.monotonic() < deadline:
        details = call_api(
            tab,
            f"window.pkulaw.getDataProcessing({javascript_json(processing_id)})",
        )
        run = details["run"]
        signature = "|".join(
            [
                str(run["status"]),
                str(run["currentStage"]),
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
                    "status": run["status"],
                    "currentStage": run["currentStage"],
                    "progress": run.get("progress"),
                    "error": run.get("error"),
                    "summary": run.get("summary"),
                }
            )
            last_signature = signature
        if run["status"] in TERMINAL_RUN_STATUSES:
            details["observedHistory"] = history
            return details
        if run["status"] in ATTENTION_RUN_STATUSES:
            details["observedHistory"] = history
            return details
        time.sleep(interval)
    details["observedHistory"] = history
    details["waitTimedOut"] = True
    return details


def main() -> int:
    args = parse_args()
    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    tabs = browser.get_tabs()
    ui_tabs = [
        tab
        for tab in tabs
        if str(tab.url).startswith("file://")
        and "/out/renderer/index.html" in str(tab.url)
    ]
    if len(ui_tabs) != 1:
        raise RuntimeError(
            f"expected exactly one packaged renderer tab, found {len(ui_tabs)}"
        )
    tab = ui_tabs[0]

    if args.command == "configure-institution":
        profile = call_api(
            tab,
            (
                "window.pkulaw.saveInstitution("
                "{"
                f"name: {javascript_json(args.name)}, "
                f"entryUrl: {javascript_json(args.entry_url)}"
                "})"
            ),
        )
        result = {
            "profile": profile,
            "selected": call_api(
                tab,
                (
                    "window.pkulaw.selectInstitution("
                    f"{javascript_json(profile['id'])})"
                ),
            ),
        }
    elif args.command == "preview":
        result = call_api(
            tab,
            (
                "window.pkulaw.previewTask("
                f"{javascript_json(args.mode)}, "
                f"{javascript_json(create_payload(args))})"
            ),
        )
    elif args.command == "state":
        result = call_api(
            tab,
            """({
              app: await window.pkulaw.getAppInfo(),
              backend: await window.pkulaw.getBackendState(),
              browser: await window.pkulaw.getBrowserState(),
              tasks: await window.pkulaw.listTasks(200),
              diagnostics: await window.pkulaw.getDiagnostics()
            })""",
        )
    elif args.command == "tasks":
        result = call_api(tab, "window.pkulaw.listTasks(200, true)")
    elif args.command == "browser":
        result = call_api(tab, "window.pkulaw.getBrowserState()")
    elif args.command == "institutions":
        result = call_api(tab, "window.pkulaw.listInstitutions()")
    elif args.command == "select-institution":
        result = call_api(
            tab,
            (
                "window.pkulaw.selectInstitution("
                f"{javascript_json(args.institution_id)})"
            ),
        )
    elif args.command == "diagnostics":
        result = call_api(tab, "window.pkulaw.getDiagnostics()")
    elif args.command == "logs":
        result = call_api(tab, "window.pkulaw.getRecentLogs()")
    elif args.command in {"create-single", "create-batch"}:
        mode = args.command.removeprefix("create-")
        payload = create_payload(args)
        operation_id = str(uuid.uuid4())
        task_input = {
            "name": args.name,
            "mode": mode,
            "input": payload,
            "outputDir": str(args.output_dir.resolve()),
            "operationId": operation_id,
        }
        created = call_api(
            tab,
            f"window.pkulaw.createTask({javascript_json(task_input)})",
        )
        result = {"created": created, "started": None}
        if not args.no_start:
            start_method = (
                "startSingleTask"
                if mode == "single"
                else "startBatchTask"
            )
            result["started"] = call_api(
                tab,
                (
                    f"window.pkulaw.{start_method}("
                    f"{javascript_json(created['id'])}, "
                    f"{javascript_json(operation_id)})"
                ),
            )
    elif args.command in {"single-details", "batch-details"}:
        mode, _ = args.command.split("-", 1)
        method = (
            "getTaskDetails"
            if mode == "single"
            else "getBatchTaskDetails"
        )
        result = call_api(
            tab,
            f"window.pkulaw.{method}({javascript_json(args.task_id)})",
        )
    elif args.command in {"wait-single", "wait-batch"}:
        mode = args.command.removeprefix("wait-")
        result = wait_for_task(
            tab,
            mode=mode,
            task_id=args.task_id,
            timeout=args.timeout,
            interval=args.interval,
        )
    elif args.command in {"control-single", "control-batch"}:
        mode = args.command.removeprefix("control-")
        methods = {
            "pause": (
                "pauseSingleTask"
                if mode == "single"
                else "pauseBatchTask"
            ),
            "resume": (
                "resumeSingleTask"
                if mode == "single"
                else "resumeBatchTask"
            ),
            "stop": (
                "stopSingleTask"
                if mode == "single"
                else "stopBatchTask"
            ),
            "continue": (
                "continueSingleTask"
                if mode == "single"
                else "continueBatchTask"
            ),
            "continue-to-last": (
                "continueSingleToLast"
                if mode == "single"
                else "continueBatchToLast"
            ),
            "retry": (
                "retrySingleTask"
                if mode == "single"
                else "retryBatchTask"
            ),
            "confirm-scale": "confirmBatchScale",
        }
        method = methods[args.action]
        call_args = [javascript_json(args.task_id)]
        if args.action in {"resume", "retry"}:
            call_args.append(javascript_json(str(uuid.uuid4())))
        result = call_api(
            tab,
            f"window.pkulaw.{method}({', '.join(call_args)})",
        )
    elif args.command == "choose-candidate":
        result = call_api(
            tab,
            (
                "window.pkulaw.chooseCandidate("
                f"{javascript_json(args.task_id)}, "
                f"{javascript_json(args.item_id)}, "
                f"{javascript_json(args.candidate_key)})"
            ),
        )
    elif args.command == "start-data":
        options = {
            "governmentFallback": args.government_fallback,
            "governmentSearchTemplate": args.government_search_template,
        }
        result = call_api(
            tab,
            (
                "window.pkulaw.startDataProcessing("
                f"{javascript_json(args.task_id)}, "
                f"{javascript_json(options)}, "
                f"{javascript_json(str(uuid.uuid4()))})"
            ),
        )
    elif args.command == "wait-data":
        result = wait_for_processing(
            tab,
            processing_id=args.processing_id,
            timeout=args.timeout,
            interval=args.interval,
        )
    elif args.command == "latest-data":
        result = call_api(
            tab,
            (
                "window.pkulaw.getLatestDataProcessing("
                f"{javascript_json(args.task_id)})"
            ),
        )
    else:
        raise RuntimeError(f"unsupported command: {args.command}")

    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
