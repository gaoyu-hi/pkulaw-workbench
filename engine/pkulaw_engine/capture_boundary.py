from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CaptureBoundary:
    reason: str
    query: str
    library: str
    year: str
    province: str
    completed_policy_count: int
    remaining_scopes: tuple[dict[str, Any], ...]
    estimated_result_operations: int
    detected_at: str

    def to_checkpoint(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "query": self.query,
            "library": self.library,
            "year": self.year,
            "province": self.province,
            "completedPolicyCount": max(0, int(self.completed_policy_count)),
            "remainingScopes": [
                dict(scope)
                for scope in self.remaining_scopes
            ],
            "estimatedResultOperations": max(
                0,
                int(self.estimated_result_operations),
            ),
            "detectedAt": self.detected_at,
        }


class CaptureBoundaryRequired(RuntimeError):
    code = "CAPTURE_BOUNDARY_REACHED"

    def __init__(self, boundary: CaptureBoundary) -> None:
        super().__init__(boundary.reason)
        self.boundary = boundary

    def to_checkpoint(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "boundary": self.boundary.to_checkpoint(),
        }
