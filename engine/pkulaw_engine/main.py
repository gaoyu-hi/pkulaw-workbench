from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .application import EngineApplication
from .browser_bridge import BrowserBridge
from .logging_utils import JsonLineLogger
from .storage import TaskStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pkulaw local Python engine")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--app-version", default="unknown")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.resolve()
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = JsonLineLogger(logs_dir / "engine.jsonl")

    store: TaskStore | None = None
    try:
        store = TaskStore(data_dir / "pkulaw.sqlite3")
        application = EngineApplication(
            store=store,
            output=sys.stdout,
            logger=logger,
            app_version=args.app_version,
            browser_bridge=BrowserBridge(logger),
        )
        return application.run()
    except Exception as exc:
        logger.write(
            "error",
            "engine.crash",
            "engine terminated with an unhandled exception",
            {"error": repr(exc)},
        )
        print(f"pkulaw-engine fatal error: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if store is not None:
            store.close()
