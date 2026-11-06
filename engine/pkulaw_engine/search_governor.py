from __future__ import annotations

import random
import time
from typing import Any, Callable, Mapping

from .search_recovery import SearchActionKind


class SearchActionGovernor:
    """Paces result-affecting actions and signals repeated CAPTCHA pressure."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        minimum_interval_seconds: float = 1.5,
        maximum_interval_seconds: float = 2.5,
        scope_interval_seconds: tuple[float, float] = (3.0, 4.5),
        captcha_interval_seconds: float = 10.0,
        captcha_window_actions: int = 5,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        self.clock = clock
        self.sleep = sleep
        self.minimum_interval_seconds = minimum_interval_seconds
        self.maximum_interval_seconds = maximum_interval_seconds
        self.scope_interval_seconds = scope_interval_seconds
        self.captcha_interval_seconds = captcha_interval_seconds
        self.captcha_window_actions = captcha_window_actions
        self.random_fn = random_fn

        self._last_completed_at: float | None = None
        self._last_action_at: float | None = None
        self._captcha_cooldown_until: float | None = None
        self._result_action_count = 0
        self._keyword_submissions = 0
        self._year_filter_switches = 0
        self._captcha_waits = 0
        self._first_captcha_action: int | None = None
        self._captcha_counts: dict[str, int] = {}
        self._action_kinds: dict[str, SearchActionKind] = {}
        self._completed_action_ids: set[str] = set()
        self._last_scope_key = ""
        self._next_wait_ms = 0.0
        self._released_to_boundary = False
        self._breaker_reason = ""
        self._legacy_action_sequence = 0

    @classmethod
    def restore(
        cls,
        snapshot: Mapping[str, Any] | None,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        minimum_interval_seconds: float = 1.5,
        maximum_interval_seconds: float = 2.5,
        scope_interval_seconds: tuple[float, float] = (3.0, 4.5),
        captcha_interval_seconds: float = 10.0,
        captcha_window_actions: int = 5,
        random_fn: Callable[[], float] = random.random,
    ) -> "SearchActionGovernor":
        governor = cls(
            clock=clock,
            sleep=sleep,
            minimum_interval_seconds=minimum_interval_seconds,
            maximum_interval_seconds=maximum_interval_seconds,
            scope_interval_seconds=scope_interval_seconds,
            captcha_interval_seconds=captcha_interval_seconds,
            captcha_window_actions=captcha_window_actions,
            random_fn=random_fn,
        )
        payload = dict(snapshot or {})
        if not payload:
            return governor
        try:
            governor._last_completed_at = _optional_float(
                payload.get("last_completed_at")
            )
            governor._last_action_at = _optional_float(
                payload.get("last_action_at")
            )
            governor._captcha_cooldown_until = _optional_float(
                payload.get("captcha_cooldown_until")
            )
            governor._result_action_count = max(
                0, int(payload.get("result_action_count") or 0)
            )
            governor._keyword_submissions = max(
                0, int(payload.get("keyword_submissions") or 0)
            )
            governor._year_filter_switches = max(
                0, int(payload.get("year_filter_switches") or 0)
            )
            governor._captcha_waits = max(
                0, int(payload.get("captcha_waits") or 0)
            )
            governor._first_captcha_action = _optional_int(
                payload.get("first_captcha_action")
            )
            governor._released_to_boundary = bool(
                payload.get("released_to_boundary")
            )
            governor._breaker_reason = str(
                payload.get("breaker_reason") or ""
            )
            governor._last_scope_key = str(
                payload.get("last_scope_key") or ""
            )
            governor._next_wait_ms = float(
                payload.get("next_wait_ms") or 0
            )
            raw_counts = payload.get("captcha_counts")
            if isinstance(raw_counts, dict):
                governor._captcha_counts = {
                    str(key): max(0, int(value))
                    for key, value in raw_counts.items()
                }
            raw_kinds = payload.get("action_kinds")
            if isinstance(raw_kinds, dict):
                governor._action_kinds = {
                    str(key): str(value)  # type: ignore[dict-item]
                    for key, value in raw_kinds.items()
                    if str(value)
                    in {
                        "keyword-submission",
                        "year-filter-switch",
                        "filter-change",
                        "grouping-change",
                        "page-size-change",
                        "next-page",
                    }
                }
        except (TypeError, ValueError):
            return cls(
                clock=clock,
                sleep=sleep,
                minimum_interval_seconds=minimum_interval_seconds,
                maximum_interval_seconds=maximum_interval_seconds,
                scope_interval_seconds=scope_interval_seconds,
                captcha_interval_seconds=captcha_interval_seconds,
                captcha_window_actions=captcha_window_actions,
                random_fn=random_fn,
            )
        return governor

    def record_action_started(
        self,
        kind: SearchActionKind,
        action_id: str,
        scope_key: str,
    ) -> None:
        wait_seconds = self.required_delay_for_next_action(scope_key)

        if wait_seconds > 0:
            self.sleep(wait_seconds)

        self._next_wait_ms = round(wait_seconds * 1000)
        now = float(self.clock())
        self._result_action_count += 1
        self._last_action_at = now
        self._last_scope_key = str(scope_key or "")
        self._action_kinds[str(action_id)] = kind
        if kind == "keyword-submission":
            self._keyword_submissions += 1
        elif kind == "year-filter-switch":
            self._year_filter_switches += 1

    def required_delay_for_next_action(
        self,
        scope_key: str = "",
    ) -> float:
        now = float(self.clock())
        if (
            self._captcha_cooldown_until is not None
            and self._captcha_cooldown_until > now
        ):
            return round(
                self._captcha_cooldown_until - now,
                6,
            )
        if self._last_completed_at is None:
            return 0.0
        scope_changed = bool(
            self._last_scope_key
            and scope_key
            and self._last_scope_key != scope_key
        )
        minimum, maximum = (
            self.scope_interval_seconds
            if scope_changed
            else (
                self.minimum_interval_seconds,
                self.maximum_interval_seconds,
            )
        )
        random_value = min(
            1.0,
            max(0.0, float(self.random_fn())),
        )
        target = minimum + (maximum - minimum) * random_value
        elapsed = now - self._last_completed_at
        return round(max(0.0, target - elapsed), 6)

    def record_action_completed(
        self,
        action_id: str,
        completed_at: float | None = None,
    ) -> None:
        self._last_completed_at = (
            float(completed_at)
            if completed_at is not None
            else float(self.clock())
        )
        self._completed_action_ids.add(str(action_id))

    def record_captcha(self, action_id: str) -> bool:
        action_id = str(action_id)
        self._captcha_waits += 1
        count = self._captcha_counts.get(action_id, 0) + 1
        self._captcha_counts[action_id] = count
        if count >= 2:
            return self._release_to_boundary("same-action-captcha")
        current_action = self._result_action_count
        if self._first_captcha_action is None:
            self._first_captcha_action = current_action
            return False
        if (
            current_action - self._first_captcha_action
            <= self.captcha_window_actions
        ):
            return self._release_to_boundary(
                "captcha-within-five-actions"
            )
        self._first_captcha_action = current_action
        return False

    def record_captcha_cleared(
        self,
        action_id: str | None = None,
        observed_at: float | None = None,
    ) -> None:
        del action_id
        now = (
            float(observed_at)
            if observed_at is not None
            else float(self.clock())
        )
        self._captcha_cooldown_until = (
            now + self.captcha_interval_seconds
        )

    def before_result_action(self, kind: SearchActionKind) -> None:
        self._legacy_action_sequence += 1
        self.record_action_started(
            kind,
            f"legacy-{self._legacy_action_sequence}",
            self._last_scope_key,
        )

    def after_result_action(self, completed_at: float | None = None) -> None:
        self.record_action_completed(
            f"legacy-{self._legacy_action_sequence}",
            completed_at,
        )

    def note_captcha_cleared(self, observed_at: float | None = None) -> bool:
        action_id = f"legacy-captcha-{self._captcha_waits + 1}"
        released = self.record_captcha(action_id)
        self.record_captcha_cleared(action_id, observed_at)
        return released

    def _release_to_boundary(self, reason: str) -> bool:
        self._released_to_boundary = True
        self._breaker_reason = reason
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "keyword_submissions": self._keyword_submissions,
            "year_filter_switches": self._year_filter_switches,
            "captcha_waits": self._captcha_waits,
            "released_to_boundary": self._released_to_boundary,
            "breaker_reason": self._breaker_reason,
            "result_action_count": self._result_action_count,
            "last_completed_at": self._last_completed_at,
            "last_action_at": self._last_action_at,
            "captcha_cooldown_until": self._captcha_cooldown_until,
            "first_captcha_action": self._first_captcha_action,
            "captcha_counts": dict(self._captcha_counts),
            "action_kinds": dict(self._action_kinds),
            "last_scope_key": self._last_scope_key,
            "next_wait_ms": self._next_wait_ms,
            "minimum_interval_seconds": self.minimum_interval_seconds,
            "maximum_interval_seconds": self.maximum_interval_seconds,
            "scope_interval_seconds": list(
                self.scope_interval_seconds
            ),
        }


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
