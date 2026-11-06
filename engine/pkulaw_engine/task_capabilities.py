from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ACCESS_WAIT_CODES = frozenset(
    {
        "MANUAL_ACTION_REQUIRED",
        "LOGIN_REQUIRED",
        "CAPTCHA_VISIBLE",
    }
)
TERMINAL_STATUSES = frozenset(
    {"stopped", "failed", "partial", "completed"}
)
RESUME_BLOCK_CODES = frozenset(
    {
        "INSTITUTION_QUOTA_REACHED",
        "PAGE_SELECTOR_MISMATCH",
        "PAGE_SELECTOR_UNSTABLE",
        "PAGE_STRUCTURE_CHANGED",
        "APPLICATION_INTERRUPTED",
        "DOWNLOAD_INTENT_EXHAUSTED",
        "CAPTURE_BOUNDARY_REACHED",
        "SCAN_JOURNAL_CONFIG_MISMATCH",
    }
)
RESUME_CHECKPOINT_KEYS = frozenset(
    {
        "currentPage",
        "currentAction",
        "searchIdentity",
        "scanJournal",
        "adaptiveSearch",
        "pageMode",
        "combinationIndex",
        "completedItems",
        "completedBatches",
        "currentBatchKey",
        "currentYear",
    }
)


@dataclass(frozen=True)
class TaskCapabilities:
    can_continue_manual: bool
    can_resume_from_checkpoint: bool
    can_edit_settings: bool
    can_create_fresh_task: bool
    lease_held: bool
    lease_task_id: str
    resume_block_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "canContinueManual": self.can_continue_manual,
            "canResumeFromCheckpoint": (
                self.can_resume_from_checkpoint
            ),
            "canEditSettings": self.can_edit_settings,
            "canCreateFreshTask": self.can_create_fresh_task,
            "leaseHeld": self.lease_held,
            "leaseTaskId": self.lease_task_id,
            "resumeBlockReason": self.resume_block_reason,
        }


def derive_task_capabilities(
    task: dict[str, Any],
    *,
    controller_alive: bool,
    lease: dict[str, Any],
) -> TaskCapabilities:
    status = str(task.get("status") or "")
    error = task.get("error")
    code = str(error.get("code") or "") if isinstance(error, dict) else ""
    checkpoint = dict(task.get("checkpoint") or {})
    archived = bool(task.get("archivedAt"))
    lease_task_id = str(lease.get("taskId") or "")
    lease_held = bool(lease_task_id)
    access_wait = (
        status in {"waiting_login", "waiting_manual"}
        and code in ACCESS_WAIT_CODES
    )
    resumable_checkpoint = any(
        key in checkpoint
        and checkpoint.get(key) not in (None, "", [], {})
        for key in RESUME_CHECKPOINT_KEYS
    )
    resume_block_reason = ""
    if status not in {"paused", "stopped", "failed", "partial"}:
        if access_wait and not controller_alive:
            resume_block_reason = ""
        else:
            resume_block_reason = "任务当前不是可恢复断点状态"
    elif code in RESUME_BLOCK_CODES or code.startswith(
        "BATCH_ARTIFACT_"
    ):
        resume_block_reason = (
            f"当前错误不允许直接复用断点：{code}"
        )
    elif not resumable_checkpoint:
        resume_block_reason = "检查点没有可恢复的进度位置"
    can_resume = bool(
        not archived
        and not resume_block_reason
        and (
            status == "paused"
            or status in {"stopped", "failed", "partial"}
            or (access_wait and not controller_alive)
        )
    )
    can_create_fresh = bool(
        status in TERMINAL_STATUSES
        and not controller_alive
        and not archived
    )
    return TaskCapabilities(
        can_continue_manual=bool(
            access_wait and controller_alive
        ),
        can_resume_from_checkpoint=can_resume,
        can_edit_settings=bool(
            status == "paused" and not archived
        ),
        can_create_fresh_task=can_create_fresh,
        lease_held=lease_held,
        lease_task_id=lease_task_id,
        resume_block_reason=resume_block_reason,
    )
