from __future__ import annotations

from typing import Any, Mapping


_MANUAL_PAUSE_FIELDS = {
    "pauseKind",
    "pauseReason",
    "manualStatus",
    "manualAt",
}


def checkpoint_after_manual_continue(
    checkpoint: Mapping[str, Any],
    resumed_at: str,
) -> dict[str, Any]:
    resumed = {
        key: value
        for key, value in checkpoint.items()
        if key not in _MANUAL_PAUSE_FIELDS
    }
    resumed["resumedAt"] = resumed_at
    resumed["resumeKind"] = "automatic"
    return resumed
