from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, cast

from .storage import ValidationError


ArtifactPolicy = Literal["preserve", "delete"]


def validate_artifact_policy(value: Any) -> ArtifactPolicy:
    if isinstance(value, str) and value in {"preserve", "delete"}:
        return cast(ArtifactPolicy, value)
    raise ValidationError(
        "INVALID_ARTIFACT_POLICY",
        "artifactPolicy must be preserve or delete",
    )


def _nonnegative_int(value: Any, field: str) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc


@dataclass(frozen=True)
class ArtifactSummary:
    completed_policy_count: int
    completed_batch_count: int
    registered_file_count: int
    eligible_file_count: int
    protected_file_count: int
    directory_count: int
    estimated_freed_bytes: int

    def __post_init__(self) -> None:
        for field in (
            "completed_policy_count",
            "completed_batch_count",
            "registered_file_count",
            "eligible_file_count",
            "protected_file_count",
            "directory_count",
            "estimated_freed_bytes",
        ):
            object.__setattr__(
                self,
                field,
                _nonnegative_int(getattr(self, field), field),
            )

    def to_dict(self) -> dict[str, int]:
        return {
            "completedPolicyCount": self.completed_policy_count,
            "completedBatchCount": self.completed_batch_count,
            "registeredFileCount": self.registered_file_count,
            "eligibleFileCount": self.eligible_file_count,
            "protectedFileCount": self.protected_file_count,
            "directoryCount": self.directory_count,
            "estimatedFreedBytes": self.estimated_freed_bytes,
        }


@dataclass(frozen=True)
class SettingsRevisionPreview:
    token: str
    changed: bool
    generation: int
    artifact_summary: ArtifactSummary
    protected_files: tuple[dict[str, Any], ...]

    def __init__(
        self,
        *,
        token: str,
        changed: bool,
        generation: int,
        artifact_summary: ArtifactSummary,
        protected_files: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        object.__setattr__(self, "token", str(token or ""))
        object.__setattr__(self, "changed", bool(changed))
        object.__setattr__(self, "generation", max(0, int(generation or 0)))
        object.__setattr__(self, "artifact_summary", artifact_summary)
        object.__setattr__(
            self,
            "protected_files",
            tuple(copy.deepcopy(dict(item)) for item in protected_files),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "changed": self.changed,
            "generation": self.generation,
            "artifactSummary": self.artifact_summary.to_dict(),
            "protectedFiles": [
                copy.deepcopy(dict(item))
                for item in self.protected_files
            ],
        }


def _normalized_revision_input(
    task_input: Mapping[str, Any],
    output_dir: str,
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(task_input))
    normalized.pop("previewSnapshot", None)
    normalized.pop("configGeneration", None)
    normalized["outputDir"] = str(output_dir or "").strip()
    return normalized


def build_settings_revision_token(
    *,
    task_id: str,
    base_generation: int,
    base_updated_at: str,
    input: Mapping[str, Any],
    output_dir: str,
) -> str:
    payload = {
        "taskId": str(task_id or "").strip(),
        "baseGeneration": max(0, int(base_generation or 0)),
        "baseUpdatedAt": str(base_updated_at or ""),
        "input": _normalized_revision_input(input, output_dir),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
