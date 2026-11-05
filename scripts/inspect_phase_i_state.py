from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def default_database() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "release" / "PkulawWorkbench-portable" / "数据" / "pkulaw.sqlite3"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Read-only phase I task inspection")
    parser.add_argument("--database", type=Path, default=default_database())
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--schema", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()

    uri = f"{args.database.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row

    tables = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    print("tables:", ", ".join(sorted(tables)))
    if args.schema:
        for table in sorted(tables):
            table_columns = [
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            ]
            print(f"{table}: {', '.join(table_columns)}")
        return

    if "tasks" not in tables:
        return

    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
    }
    order_column = next(
        (
            column
            for column in ("updated_at", "created_at", "id")
            if column in columns
        ),
        "rowid",
    )
    rows = connection.execute(
        f"SELECT * FROM tasks ORDER BY {order_column} DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    for row in rows:
        summary = {
            key: row[key]
            for key in (
                "id",
                "title",
                "name",
                "mode",
                "status",
                "error_code",
                "config_json",
                "checkpoint_json",
                "created_at",
                "updated_at",
            )
            if key in columns
        }
        if not args.full and "checkpoint_json" in summary:
            checkpoint_json = summary.pop("checkpoint_json")
            try:
                checkpoint = json.loads(checkpoint_json)
            except (TypeError, json.JSONDecodeError):
                checkpoint = {}
            summary["progress"] = checkpoint.get("progressSnapshot")
            summary["current"] = {
                key: checkpoint.get(key)
                for key in (
                    "currentAction",
                    "query",
                    "library",
                    "year",
                    "currentPage",
                    "totalPages",
                    "searchId",
                    "manualStatus",
                    "pauseReason",
                )
            }
        print(json.dumps(summary, ensure_ascii=False, default=str))

        task_id = row["id"]
        if "task_items" in tables:
            counts = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM task_items
                WHERE task_id = ?
                GROUP BY status
                ORDER BY status
                """,
                (task_id,),
            ).fetchall()
            print(
                "  items:",
                ", ".join(f"{item['status']}={item['count']}" for item in counts),
            )
        if "artifacts" in tables:
            artifact_count = connection.execute(
                "SELECT COUNT(*) FROM artifacts WHERE task_id = ?",
                (task_id,),
            ).fetchone()[0]
            print("  artifacts:", artifact_count)
        if "task_events" in tables:
            event_rows = connection.execute(
                """
                SELECT *
                FROM task_events
                WHERE task_id = ?
                ORDER BY rowid DESC
                LIMIT 8
                """,
                (task_id,),
            ).fetchall()
            for event in event_rows:
                event_summary = {
                    key: event[key]
                    for key in (
                        "created_at",
                        "timestamp",
                        "event",
                        "event_type",
                        "state",
                        "stage",
                        "message",
                        "error_code",
                        "completed_units",
                        "total_units",
                        "payload_json",
                    )
                    if key in event.keys()
                }
                print(
                    "  event:",
                    json.dumps(event_summary, ensure_ascii=False, default=str),
                )


if __name__ == "__main__":
    main()
