from __future__ import annotations

import time
import uuid
from copy import deepcopy
from threading import RLock
from typing import Any, Callable

from .storage import ValidationError

LEASE_MODES = {"single", "batch", "data"}


class BrowserLeaseBusy(ValidationError):
    def __init__(self, lease: dict[str, Any]) -> None:
        mode = str(lease.get("mode") or "unknown")
        task_id = str(lease.get("taskId") or "unknown")
        super().__init__(
            "BROWSER_LEASE_BUSY",
            f"内置浏览器正由任务 {task_id} 使用（模式：{mode}）",
        )
        self.lease = deepcopy(lease)


class BrowserLeaseManager:
    """Process-local, atomic ownership of the single embedded browser."""

    def __init__(
        self,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._lock = RLock()
        self._lease: dict[str, Any] | None = None
        self._event_sink = event_sink
        self._clock = clock or time.time

    def set_event_sink(
        self,
        event_sink: Callable[[str, dict[str, Any]], None] | None,
    ) -> None:
        with self._lock:
            self._event_sink = event_sink

    def acquire(self, task_id: str, mode: str) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        clean_mode = str(mode or "").strip()
        if not clean_task_id:
            raise ValidationError("TASK_ID_REQUIRED", "browser lease requires taskId")
        if clean_mode not in LEASE_MODES:
            raise ValidationError(
                "INVALID_BROWSER_LEASE_MODE",
                f"browser lease mode must be one of {sorted(LEASE_MODES)}",
            )

        with self._lock:
            if self._lease is not None:
                if (
                    self._lease["taskId"] == clean_task_id
                    and self._lease["mode"] == clean_mode
                ):
                    self._lease["updatedAt"] = self._timestamp()
                    result = {**self._lease, "acquired": False}
                    return deepcopy(result)
                raise BrowserLeaseBusy(deepcopy(self._lease))

            now = self._timestamp()
            self._lease = {
                "leaseId": str(uuid.uuid4()),
                "taskId": clean_task_id,
                "mode": clean_mode,
                "createdAt": now,
                "updatedAt": now,
            }
            result = {**self._lease, "acquired": True}

        self._emit("browser.lease-acquired", self.state())
        return deepcopy(result)

    def renew(self, lease_id: str) -> dict[str, Any]:
        with self._lock:
            if self._lease is None or self._lease["leaseId"] != str(lease_id or ""):
                raise ValidationError(
                    "BROWSER_LEASE_NOT_OWNED",
                    "当前进程没有该浏览器租约",
                )
            self._lease["updatedAt"] = self._timestamp()
            return deepcopy(self._lease)

    def release(self, lease_id: str) -> bool:
        clean_lease_id = str(lease_id or "").strip()
        if not clean_lease_id:
            return False
        released: dict[str, Any] | None = None
        with self._lock:
            if self._lease is None or self._lease["leaseId"] != clean_lease_id:
                return False
            released = deepcopy(self._lease)
            self._lease = None
        self._emit(
            "browser.lease-released",
            {**self.state(), "releasedLease": released},
        )
        return True

    def release_for_task(self, task_id: str) -> bool:
        with self._lock:
            if self._lease is None or self._lease["taskId"] != str(task_id or ""):
                return False
            lease_id = str(self._lease["leaseId"])
        return self.release(lease_id)

    def reset(self, reason: str = "application_shutdown") -> bool:
        with self._lock:
            if self._lease is None:
                return False
            released = deepcopy(self._lease)
            self._lease = None
        self._emit(
            "browser.lease-released",
            {
                **self.state(),
                "releasedLease": released,
                "reason": reason,
            },
        )
        return True

    def state(self) -> dict[str, Any]:
        with self._lock:
            if self._lease is None:
                return {
                    "available": True,
                    "leaseId": "",
                    "taskId": "",
                    "mode": "",
                    "createdAt": "",
                    "updatedAt": "",
                }
            return {
                "available": False,
                **deepcopy(self._lease),
            }

    def validate(self, lease_id: str, task_id: str, mode: str) -> dict[str, Any]:
        with self._lock:
            if self._lease is None:
                raise ValidationError(
                    "BROWSER_LEASE_REQUIRED",
                    "浏览器任务必须先取得全局浏览器租约",
                )
            if self._lease["leaseId"] != str(lease_id or ""):
                raise ValidationError(
                    "BROWSER_LEASE_NOT_OWNED",
                    "当前任务的浏览器租约无效",
                )
            if self._lease["taskId"] != str(task_id or ""):
                raise ValidationError(
                    "BROWSER_LEASE_NOT_OWNED",
                    "浏览器租约不属于当前任务",
                )
            if self._lease["mode"] != str(mode or ""):
                raise ValidationError(
                    "BROWSER_LEASE_NOT_OWNED",
                    "浏览器租约模式与当前任务不一致",
                )
            return deepcopy(self._lease)

    def _timestamp(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._clock()))

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(event, data)
        except Exception:
            # Lease correctness must never depend on UI event delivery.
            return
