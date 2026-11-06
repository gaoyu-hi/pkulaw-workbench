from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .scan_journal import config_fingerprint
from .storage import SEARCH_SEMANTICS_VERSION


DERIVED_SCAN_KEYS = frozenset(
    {
        "scanJournal",
        "scanJournalSealed",
        "scanCompleted",
        "currentPage",
        "currentTotalPages",
        "totalPages",
        "previewSnapshot",
        "resultWindow",
        "resultWindowProbe",
        "yearPartition",
        "yearStats",
        "yearWindows",
        "originalYearSelection",
        "effectiveYearSelection",
        "yearWindowAdjustment",
        "searchId",
        "currentQuery",
        "currentLibrary",
        "currentYear",
        "currentAction",
        "groupingMode",
        "resultCount",
        "rowCount",
        "progressSnapshot",
        "currentDownloadIntent",
        "downloadProgress",
        "currentCatalogFile",
        "currentZipFile",
        "searchRecovery",
        "pendingSearchAction",
        "interruptedPreviousStatus",
        "stopRequestedAt",
    }
)


@dataclass(frozen=True)
class TaskRevisionPlan:
    changed: bool
    generation: int
    task_input: dict[str, Any]
    checkpoint: dict[str, Any]


def _normalized_input(
    task_input: dict[str, Any],
    output_dir: str,
    *,
    current_semantics: bool,
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(task_input))
    normalized.pop("previewSnapshot", None)
    normalized.pop("configGeneration", None)
    if current_semantics:
        normalized["searchSemanticsVersion"] = SEARCH_SEMANTICS_VERSION
    normalized["outputDir"] = str(output_dir or "").strip()
    return normalized


def build_task_revision(
    task: dict[str, Any],
    new_input: dict[str, Any],
    output_dir: str,
) -> TaskRevisionPlan:
    if not isinstance(new_input, dict):
        raise ValueError("task settings input must be an object")
    current_input = dict(task.get("input") or {})
    current_output = str(
        output_dir
        if output_dir
        else task.get("outputDir") or current_input.get("outputDir") or ""
    ).strip()
    previous_input = _normalized_input(
        current_input,
        str(task.get("outputDir") or current_input.get("outputDir") or ""),
        current_semantics=False,
    )
    candidate_input = _normalized_input(
        new_input,
        current_output,
        current_semantics=True,
    )
    checkpoint = copy.deepcopy(dict(task.get("checkpoint") or {}))
    generation = int(checkpoint.get("configGeneration") or 0)
    previous_fingerprint = config_fingerprint(previous_input)
    candidate_fingerprint = config_fingerprint(candidate_input)
    changed = candidate_fingerprint != previous_fingerprint
    if not changed:
        return TaskRevisionPlan(
            changed=False,
            generation=generation,
            task_input=previous_input,
            checkpoint=checkpoint,
        )

    history = [
        dict(item)
        for item in checkpoint.get("configHistory") or []
        if isinstance(item, dict)
    ]
    history.append(
        {
            "generation": generation,
            "configFingerprint": str(
                checkpoint.get("configFingerprint")
                or previous_fingerprint
            ),
            **{
                key: copy.deepcopy(checkpoint[key])
                for key in DERIVED_SCAN_KEYS
                if key in checkpoint
            },
        }
    )
    checkpoint["configHistory"] = history[-20:]
    for key in DERIVED_SCAN_KEYS:
        checkpoint.pop(key, None)
    generation += 1
    checkpoint["configGeneration"] = generation
    checkpoint["configFingerprint"] = candidate_fingerprint
    return TaskRevisionPlan(
        changed=True,
        generation=generation,
        task_input=candidate_input,
        checkpoint=checkpoint,
    )
