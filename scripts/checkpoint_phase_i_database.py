from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def default_database() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "release" / "PkulawWorkbench-portable" / "数据" / "pkulaw.sqlite3"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=default_database())
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    connection = sqlite3.connect(args.database)
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{args.database}{suffix}")
        if sidecar.exists() and sidecar.stat().st_size == 0:
            sidecar.unlink()

    print(
        {
            "database": str(args.database),
            "checkpoint": checkpoint,
            "integrity": integrity,
        }
    )
    return 0 if integrity == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
