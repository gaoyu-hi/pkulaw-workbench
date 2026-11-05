from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class JsonLineLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def write(
        self,
        level: str,
        event: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        line = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": level,
            "source": "engine",
            "event": event,
            "message": message,
            "data": data or {},
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(line, ensure_ascii=False) + "\n")
