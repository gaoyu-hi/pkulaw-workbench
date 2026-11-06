from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .sources.library_scope import province_for_library


_ALLOWED_INPUT_KEYS = (
    "queries",
    "keywords",
    "matchMode",
    "library",
    "yearMode",
    "year",
    "startYear",
    "endYear",
    "province",
    "timeliness",
    "pageSize",
    "pageMode",
    "pageSelection",
    "maxPages",
    "catalogFormat",
    "fullFormat",
    "searchStrategyVersion",
    "searchSemanticsVersion",
)


@dataclass(frozen=True)
class FreshTaskSeed:
    mode: str
    input: dict[str, Any]
    output_dir: str
    old_task_id: str
    old_task_preserved: bool
    may_duplicate_downloads: bool
    lease_held: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "input": dict(self.input),
            "outputDir": self.output_dir,
            "oldTaskId": self.old_task_id,
            "oldTaskPreserved": self.old_task_preserved,
            "mayDuplicateDownloads": self.may_duplicate_downloads,
            "leaseHeld": self.lease_held,
        }


def build_fresh_task_seed(
    task: dict[str, Any],
    *,
    may_duplicate_downloads: bool,
    lease_held: bool = False,
) -> FreshTaskSeed:
    source = dict(task.get("input") or {})
    sanitized = {
        key: _copy_value(source[key])
        for key in _ALLOWED_INPUT_KEYS
        if key in source
    }
    queries = [
        str(value).strip()
        for value in (
            source.get("queries")
            or source.get("keywords")
            or []
        )
        if str(value).strip()
    ]
    queries = list(dict.fromkeys(queries))
    if queries:
        sanitized["queries"] = queries
        sanitized["keywords"] = list(queries)
    library = str(
        sanitized.get("library") or source.get("library") or ""
    )
    if library:
        sanitized["library"] = library
        sanitized["province"] = province_for_library(
            library,
            str(sanitized.get("province") or "全部省份"),
        )
    output_dir = str(
        task.get("outputDir")
        or source.get("outputDir")
        or ""
    )
    if output_dir:
        sanitized["outputDir"] = output_dir
    return FreshTaskSeed(
        mode=str(task.get("mode") or ""),
        input=sanitized,
        output_dir=output_dir,
        old_task_id=str(task.get("id") or ""),
        old_task_preserved=True,
        may_duplicate_downloads=bool(may_duplicate_downloads),
        lease_held=bool(lease_held),
    )


def _copy_value(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value
