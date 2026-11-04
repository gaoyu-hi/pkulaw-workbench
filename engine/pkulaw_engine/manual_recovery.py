from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ACCESS_WAIT_CODES = frozenset(
    {
        "MANUAL_ACTION_REQUIRED",
        "LOGIN_REQUIRED",
        "CAPTCHA_VISIBLE",
    }
)


@dataclass(frozen=True)
class ManualRecoveryDecision:
    action: Literal["continue", "restart", "none"]
    reason: str


def decide_manual_recovery(
    task: dict[str, Any],
    *,
    controller_alive: bool,
) -> ManualRecoveryDecision:
    status = str(task.get("status") or "")
    error = task.get("error")
    code = str(error.get("code") or "") if isinstance(error, dict) else ""
    if (
        status not in {"waiting_login", "waiting_manual"}
        or code not in ACCESS_WAIT_CODES
    ):
        return ManualRecoveryDecision(
            "none",
            "task is not waiting for an access challenge",
        )
    if controller_alive:
        return ManualRecoveryDecision(
            "continue",
            "task controller is alive and waiting",
        )
    return ManualRecoveryDecision(
        "restart",
        "task controller ended with a recoverable access wait",
    )
