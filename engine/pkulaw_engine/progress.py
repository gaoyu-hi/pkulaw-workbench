from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


SINGLE_PROGRESS_WEIGHTS = {
    "preflight": 0.05,
    "scan": 0.25,
    "download": 0.65,
    "finalize": 0.05,
}

BATCH_PROGRESS_WEIGHTS = {
    "scan": 0.20,
    "catalog": 0.20,
    "zip": 0.30,
    "publish": 0.30,
}

PROCESSING_PROGRESS_WEIGHTS = {
    "start": 0.05,
    "documents": 0.90,
    "publish": 0.05,
}


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(float(value), maximum))


def _ratio(completed: int, total: int) -> float:
    if total <= 0:
        return 1.0
    return _clamp(completed / total)


@dataclass(frozen=True)
class ProgressSnapshot:
    overall: float
    stage: str
    completed_units: int
    total_units: int
    succeeded: int
    failed: int
    pending_review: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "overall": payload["overall"],
            "stage": payload["stage"],
            "completedUnits": payload["completed_units"],
            "totalUnits": payload["total_units"],
            "succeeded": payload["succeeded"],
            "failed": payload["failed"],
            "pendingReview": payload["pending_review"],
        }


def build_progress_snapshot(
    *,
    stage: str,
    completed_units: int = 0,
    total_units: int = 0,
    succeeded: int = 0,
    failed: int = 0,
    pending_review: int = 0,
    overall: float | None = None,
) -> ProgressSnapshot:
    clean_stage = str(stage or "unknown")
    completed = max(0, int(completed_units))
    total = max(0, int(total_units))
    success = max(0, int(succeeded))
    failures = max(0, int(failed))
    pending = max(0, int(pending_review))

    calculated = _ratio(completed, total) if overall is None else float(overall)
    if (
        clean_stage == "completed"
        and total > 0
        and success >= total
        and failures == 0
        and pending == 0
    ):
        calculated = 1.0
    elif clean_stage in {"partial", "failed", "stopped"} or pending or failures:
        calculated = min(calculated, 0.99)
    return ProgressSnapshot(
        overall=_clamp(calculated),
        stage=clean_stage,
        completed_units=completed,
        total_units=total,
        succeeded=success,
        failed=failures,
        pending_review=pending,
    )


def single_scan_overall(
    *,
    combination_index: int,
    combination_count: int,
    page: int,
    total_pages: int,
) -> float:
    combinations = max(1, int(combination_count))
    index = max(1, int(combination_index))
    fraction = (
        (index - 1) + _ratio(max(1, int(page)), max(1, int(total_pages)))
    ) / combinations
    return _clamp(
        SINGLE_PROGRESS_WEIGHTS["preflight"]
        + SINGLE_PROGRESS_WEIGHTS["scan"] * _clamp(fraction)
    )


def single_download_overall(completed_items: int, total_items: int) -> float:
    return _clamp(
        SINGLE_PROGRESS_WEIGHTS["preflight"]
        + SINGLE_PROGRESS_WEIGHTS["scan"]
        + SINGLE_PROGRESS_WEIGHTS["download"]
        * _ratio(max(0, int(completed_items)), max(1, int(total_items)))
    )


def batch_scan_overall(
    combination_index: int,
    combination_count: int,
    page: int,
    total_pages: int,
) -> float:
    combinations = max(1, int(combination_count))
    index = max(1, int(combination_index))
    fraction = (
        (index - 1) + _ratio(max(1, int(page)), max(1, int(total_pages)))
    ) / combinations
    return _clamp(BATCH_PROGRESS_WEIGHTS["scan"] * _clamp(fraction))


def batch_batch_overall(index: int, total: int, phase: str) -> float:
    total_batches = max(1, int(total))
    batch_index = max(1, int(index))
    phase_offsets = {
        "select": 0.0,
        "catalog": 0.25,
        "zip": 0.625,
        "publish": 1.0,
    }
    offset = phase_offsets.get(str(phase), 0.0)
    batch_weight = (
        BATCH_PROGRESS_WEIGHTS["catalog"]
        + BATCH_PROGRESS_WEIGHTS["zip"]
        + BATCH_PROGRESS_WEIGHTS["publish"]
    )
    return _clamp(
        BATCH_PROGRESS_WEIGHTS["scan"]
        + batch_weight
        * ((batch_index - 1) + offset)
        / total_batches
    )


def processing_document_overall(completed_documents: int, total_documents: int) -> float:
    return _clamp(
        PROCESSING_PROGRESS_WEIGHTS["start"]
        + PROCESSING_PROGRESS_WEIGHTS["documents"]
        * _ratio(
            max(0, int(completed_documents)),
            max(1, int(total_documents)),
        )
    )
