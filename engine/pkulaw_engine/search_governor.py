from __future__ import annotations

import time
from typing import Callable, Literal

SearchActionKind = Literal["keyword-submission", "year-filter-switch"]


class SearchActionGovernor:
    """Paces result-affecting actions and signals repeated CAPTCHA pressure."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        minimum_interval_seconds: float = 1.0,
        captcha_interval_seconds: float = 10.0,
        captcha_window_actions: int = 5,
    ) -> None:
        self.clock = clock
        self.sleep = sleep
        self.minimum_interval_seconds = minimum_interval_seconds
        self.captcha_interval_seconds = captcha_interval_seconds
        self.captcha_window_actions = captcha_window_actions

        self._last_completed_at: float | None = None
        self._captcha_cooldown_until: float | None = None
        self._result_action_count = 0
        self._keyword_submissions = 0
        self._year_filter_switches = 0
        self._captcha_waits = 0
        self._first_captcha_action: int | None = None
        self._released_to_boundary = False

    def before_result_action(self, kind: SearchActionKind) -> None:
        now = float(self.clock())
        wait_seconds = 0.0

        if self._last_completed_at is not None:
            if (
                self._captcha_cooldown_until is not None
                and self._captcha_cooldown_until > now
            ):
                wait_seconds = round(self._captcha_cooldown_until - now, 6)
            else:
                elapsed = now - self._last_completed_at
                wait_seconds = round(
                    max(0.0, self.minimum_interval_seconds - elapsed),
                    6,
                )

        if wait_seconds > 0:
            self.sleep(wait_seconds)

        self._result_action_count += 1
        if kind == "keyword-submission":
            self._keyword_submissions += 1
        elif kind == "year-filter-switch":
            self._year_filter_switches += 1

    def after_result_action(self, completed_at: float | None = None) -> None:
        self._last_completed_at = (
            float(completed_at) if completed_at is not None else float(self.clock())
        )

    def note_captcha_cleared(self, observed_at: float | None = None) -> bool:
        self._captcha_waits += 1
        now = float(observed_at) if observed_at is not None else float(self.clock())
        self._captcha_cooldown_until = now + self.captcha_interval_seconds

        if self._first_captcha_action is None:
            self._first_captcha_action = self._result_action_count
            return False

        if (
            self._result_action_count - self._first_captcha_action
            <= self.captcha_window_actions
        ):
            self._released_to_boundary = True
            return True

        self._first_captcha_action = self._result_action_count
        return False

    def snapshot(self) -> dict[str, int | float | None]:
        return {
            "keyword_submissions": self._keyword_submissions,
            "year_filter_switches": self._year_filter_switches,
            "captcha_waits": self._captcha_waits,
            "released_to_boundary": self._released_to_boundary,
            "result_action_count": self._result_action_count,
            "last_completed_at": self._last_completed_at,
        }
