from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class YearPartitionRequired(RuntimeError):
    def __init__(
        self,
        *,
        years: list[str],
        expected_result_count: int,
        query: str,
        library: str,
        reason: str,
    ) -> None:
        super().__init__(reason)
        self.years = tuple(years)
        self.expected_result_count = max(0, int(expected_result_count))
        self.query = str(query)
        self.library = str(library)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "required",
            "years": list(self.years),
            "expectedResultCount": self.expected_result_count,
            "query": self.query,
            "library": self.library,
            "reason": str(self),
        }


@dataclass(frozen=True)
class ResultWindowAssessment:
    result_count: int
    reported_total_pages: int
    page_size: int
    expected_total_pages: int
    accessible_capacity: int
    expected_tail_rows: int
    status: str
    code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "resultCount": self.result_count,
            "reportedTotalPages": self.reported_total_pages,
            "pageSize": self.page_size,
            "expectedTotalPages": self.expected_total_pages,
            "accessibleCapacity": self.accessible_capacity,
            "expectedTailRows": self.expected_tail_rows,
            "windowStatus": self.status,
            "code": self.code,
        }


@dataclass(frozen=True)
class ResultWindowPartitionPlan:
    result_count: int
    page_size: int
    partition_count: int
    total_partition_results: int
    status: str
    code: str
    partitions: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resultCount": self.result_count,
            "pageSize": self.page_size,
            "partitionCount": self.partition_count,
            "totalPartitionResults": self.total_partition_results,
            "windowStatus": self.status,
            "code": self.code,
            "partitions": [dict(item) for item in self.partitions],
        }


def assess_result_window(
    *,
    result_count: int,
    reported_total_pages: int,
    page_size: int,
) -> ResultWindowAssessment:
    count = max(0, int(result_count))
    reported = max(0, int(reported_total_pages))
    size = int(page_size)
    if size <= 0:
        raise ValueError("page_size must be positive")
    if count == 0:
        return ResultWindowAssessment(
            result_count=0,
            reported_total_pages=reported,
            page_size=size,
            expected_total_pages=0,
            accessible_capacity=0,
            expected_tail_rows=0,
            status="no_result",
        )
    expected_pages = math.ceil(count / size)
    capacity = reported * size
    status = (
        "complete"
        if reported >= expected_pages
        else "truncated"
    )
    return ResultWindowAssessment(
        result_count=count,
        reported_total_pages=reported,
        page_size=size,
        expected_total_pages=expected_pages,
        accessible_capacity=capacity,
        expected_tail_rows=count - (expected_pages - 1) * size,
        status=status,
        code="RESULT_WINDOW_TRUNCATED" if status == "truncated" else "",
    )


def plan_result_window_partitions(
    *,
    result_count: int,
    page_size: int,
    partitions: list[dict[str, Any]],
) -> ResultWindowPartitionPlan:
    count = max(0, int(result_count))
    size = int(page_size)
    if size <= 0:
        raise ValueError("page_size must be positive")
    assessed: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    total_partition_results = 0
    any_truncated = False
    for raw in partitions:
        key = str(raw.get("key") or raw.get("year") or "").strip()
        if not key:
            raise ValueError("partition key is required")
        if key in seen_keys:
            raise ValueError(f"duplicate partition key: {key}")
        seen_keys.add(key)
        partition_result_count = max(0, int(raw.get("resultCount") or 0))
        reported_pages = max(0, int(raw.get("reportedTotalPages") or 0))
        assessment = assess_result_window(
            result_count=partition_result_count,
            reported_total_pages=reported_pages,
            page_size=size,
        )
        total_partition_results += partition_result_count
        any_truncated = any_truncated or assessment.status == "truncated"
        assessed.append(
            {
                "key": key,
                **assessment.to_dict(),
            }
        )
    if any_truncated:
        status = "truncated"
        code = "RESULT_WINDOW_TRUNCATED"
    elif total_partition_results != count:
        status = "count_mismatch"
        code = "RESULT_WINDOW_PARTITION_MISMATCH"
    else:
        status = "complete"
        code = ""
    return ResultWindowPartitionPlan(
        result_count=count,
        page_size=size,
        partition_count=len(assessed),
        total_partition_results=total_partition_results,
        status=status,
        code=code,
        partitions=tuple(assessed),
    )
