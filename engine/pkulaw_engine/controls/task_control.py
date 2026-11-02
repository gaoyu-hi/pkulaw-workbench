from __future__ import annotations

import threading
from threading import Condition
from typing import Any, Callable


class TaskStopped(RuntimeError):
    pass


class TaskControl:
    """Thread-safe cooperative control shared by task workers."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.condition = Condition()
        self.thread: threading.Thread | None = None
        self._stop_requested = False
        self._pause_requested = False
        self._paused = False
        self._resume_requested = False
        self._pause_reason = ""
        self._continue_requested = False
        self._manual_waiting = False
        self._choices: dict[str, str] = {}

    @property
    def stop_requested(self) -> bool:
        with self.condition:
            return self._stop_requested

    @property
    def pause_requested(self) -> bool:
        with self.condition:
            return self._pause_requested

    @property
    def paused(self) -> bool:
        with self.condition:
            return self._paused

    @property
    def resume_requested(self) -> bool:
        with self.condition:
            return self._resume_requested

    def request_pause(self, reason: str = "用户手动暂停") -> bool:
        with self.condition:
            if self._stop_requested or self._pause_requested or self._paused:
                return False
            self._pause_requested = True
            self._resume_requested = False
            self._pause_reason = str(reason or "用户手动暂停")
            self.condition.notify_all()
            return True

    def request_resume(self) -> bool:
        with self.condition:
            if not self._pause_requested and not self._paused:
                return False
            self._pause_requested = False
            self._paused = False
            self._resume_requested = True
            self._pause_reason = ""
            self.condition.notify_all()
            return True

    def request_stop(self) -> bool:
        with self.condition:
            if self._stop_requested:
                return False
            self._stop_requested = True
            self._pause_requested = False
            self._paused = False
            self._resume_requested = False
            self.condition.notify_all()
            return True

    def request_continue(self) -> bool:
        with self.condition:
            if self._stop_requested or self._continue_requested:
                return False
            self._continue_requested = True
            self.condition.notify_all()
            return True

    def request_choice(self, item_id: str, candidate_key: str) -> bool:
        with self.condition:
            self._choices[item_id] = candidate_key
            self.condition.notify_all()
            return True

    def check_stop(self) -> None:
        with self.condition:
            if self._stop_requested:
                raise TaskStopped("task stopped by user")

    def wait_at_boundary(
        self,
        *,
        on_pause: Callable[[str], None] | None = None,
    ) -> bool:
        """Block at the next safe boundary when a manual pause was requested."""
        with self.condition:
            self.check_stop()
            if not self._pause_requested:
                self._resume_requested = False
                return False
            self._pause_requested = False
            self._paused = True
            reason = self._pause_reason
            if on_pause is not None:
                on_pause(reason)
            while self._paused and not self._stop_requested:
                self.condition.wait(timeout=0.5)
            if self._stop_requested:
                self._paused = False
                raise TaskStopped("task stopped while paused")
            self._resume_requested = False
            return True

    def wait_if_paused(
        self,
        *,
        on_pause: Callable[[str], None] | None = None,
    ) -> bool:
        return self.wait_at_boundary(on_pause=on_pause)

    def wait_for_continue(self) -> None:
        with self.condition:
            self._manual_waiting = True
            try:
                while not self._continue_requested and not self._stop_requested:
                    self.condition.wait(timeout=0.5)
                if self._stop_requested:
                    raise TaskStopped("task stopped while waiting for manual action")
                self._continue_requested = False
            finally:
                self._manual_waiting = False

    def manual_wait(self) -> None:
        self.wait_for_continue()

    def wait_for_choice(self, item_id: str) -> str:
        with self.condition:
            while item_id not in self._choices and not self._stop_requested:
                self.condition.wait(timeout=0.5)
            if self._stop_requested:
                raise TaskStopped("task stopped while waiting for candidate choice")
            return self._choices.pop(item_id)

    def snapshot(self) -> dict[str, Any]:
        with self.condition:
            return {
                "taskId": self.task_id,
                "threadAlive": bool(self.thread and self.thread.is_alive()),
                "stopRequested": self._stop_requested,
                "pauseRequested": self._pause_requested,
                "paused": self._paused,
                "resumeRequested": self._resume_requested,
                "pauseReason": self._pause_reason,
                "manualWaiting": self._manual_waiting,
                "continueRequested": self._continue_requested,
            }
