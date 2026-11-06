from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from .sources.search_transaction import (
    SearchRequestIdentity,
    SearchSession,
    filter_values_match,
)


SearchActionKind = Literal[
    "keyword-submission",
    "year-filter-switch",
    "filter-change",
    "grouping-change",
    "page-size-change",
    "next-page",
]

SEARCH_ACTION_KINDS = frozenset(
    {
        "keyword-submission",
        "year-filter-switch",
        "filter-change",
        "grouping-change",
        "page-size-change",
        "next-page",
    }
)


@dataclass(frozen=True)
class PendingSearchAction:
    action_id: str
    kind: SearchActionKind
    request: SearchRequestIdentity
    expected_page: int
    previous_result_signature: str
    target: dict[str, Any]
    attempt_count: int
    created_at: str


class SearchRecoveryBoundaryRequired(RuntimeError):
    def __init__(
        self,
        action: PendingSearchAction,
        reason: str,
        *,
        governor: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.action = action
        self.reason = reason
        self.governor = dict(governor or {})
        self.code = "CAPTURE_BOUNDARY_REACHED"


class SearchRecoveryUnconfirmed(RuntimeError):
    code = "MANUAL_RECOVERY_UNCONFIRMED"

    def __init__(
        self,
        action: PendingSearchAction,
        reason: str,
    ) -> None:
        super().__init__(reason)
        self.action = action


def build_pending_search_action(
    *,
    task_id: str,
    kind: SearchActionKind,
    request: SearchRequestIdentity,
    expected_page: int,
    previous_result_signature: str,
    target: dict[str, Any],
    attempt_count: int = 0,
) -> PendingSearchAction:
    identity = {
        "taskId": str(task_id),
        "kind": kind,
        "request": request.to_dict(),
        "expectedPage": int(expected_page),
        "target": dict(target),
    }
    action_id = hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return PendingSearchAction(
        action_id=action_id,
        kind=kind,
        request=request,
        expected_page=int(expected_page),
        previous_result_signature=str(
            previous_result_signature or ""
        ),
        target=dict(target),
        attempt_count=max(0, int(attempt_count)),
        created_at=time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        ),
    )


def pending_action_to_checkpoint(
    action: PendingSearchAction,
) -> dict[str, Any]:
    return {
        "version": 1,
        "actionId": action.action_id,
        "kind": action.kind,
        "request": action.request.to_dict(),
        "expectedPage": int(action.expected_page),
        "previousResultSignature": action.previous_result_signature,
        "target": dict(action.target),
        "attemptCount": int(action.attempt_count),
        "createdAt": action.created_at,
    }


def pending_action_from_checkpoint(
    payload: Any,
) -> PendingSearchAction | None:
    if not isinstance(payload, dict):
        return None
    action_id = str(payload.get("actionId") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    request_value = payload.get("request")
    if (
        not action_id
        or kind not in SEARCH_ACTION_KINDS
        or not isinstance(request_value, dict)
    ):
        return None
    try:
        request = SearchRequestIdentity(
            query=str(request_value["query"]),
            match_mode=str(request_value["matchMode"]),
            library=str(request_value["library"]),
            year=str(request_value["year"]),
            province=str(request_value["province"]),
            timeliness=str(request_value["timeliness"]),
            requested_page_size=int(request_value["requestedPageSize"]),
        )
        expected_page = int(payload["expectedPage"])
        attempt_count = int(payload.get("attemptCount") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    target = payload.get("target")
    if not isinstance(target, dict):
        target = {}
    return PendingSearchAction(
        action_id=action_id,
        kind=kind,  # type: ignore[arg-type]
        request=request,
        expected_page=expected_page,
        previous_result_signature=str(
            payload.get("previousResultSignature") or ""
        ),
        target=dict(target),
        attempt_count=max(0, attempt_count),
        created_at=str(payload.get("createdAt") or ""),
    )


def pending_action_for_request(
    checkpoint: Any,
    *,
    request: SearchRequestIdentity,
    expected_page: int,
    target: Mapping[str, Any],
) -> PendingSearchAction | None:
    if not isinstance(checkpoint, dict):
        return None
    recovery = checkpoint.get("searchRecovery")
    if not isinstance(recovery, dict):
        return None
    action = pending_action_from_checkpoint(recovery.get("action"))
    if action is None:
        return None
    if action.kind not in {
        "keyword-submission",
        "year-filter-switch",
    }:
        return None
    if (
        action.request != request
        or action.expected_page != int(expected_page)
        or action.target != dict(target)
    ):
        return None
    return action


def restore_governor_state(
    governor: Any,
    snapshot: Any,
) -> Any:
    if not isinstance(snapshot, dict) or not snapshot:
        return governor
    restore = getattr(type(governor), "restore", None)
    if callable(restore):
        return restore(snapshot)
    return governor


def observe_adapter_action(
    adapter: Any,
    action: PendingSearchAction,
) -> dict[str, Any] | None:
    observer = getattr(adapter, "observe_pending_action", None)
    if callable(observer):
        value = observer(action)
        return dict(value) if isinstance(value, dict) else None
    try:
        state = adapter.read_results()
    except Exception:
        return None
    payload = state.get("searchSession")
    session = (
        SearchSession.from_dict(payload)
        if isinstance(payload, dict)
        else None
    )
    if session is None:
        return None
    request = action.request
    if (
        session.page != action.expected_page
        or session.query != request.query
        or session.match_mode != request.match_mode
        or session.library != request.library
    ):
        return None
    if action.kind == "year-filter-switch":
        return (
            state
            if filter_values_match(
                request.year,
                session.actual_year,
            )
            else None
        )
    if action.kind == "next-page":
        return (
            state
            if session.result_signature
            != action.previous_result_signature
            else None
        )
    for expected, actual in (
        (request.year, session.actual_year),
        (request.province, session.actual_province),
        (request.timeliness, session.actual_timeliness),
    ):
        if not filter_values_match(expected, actual):
            return None
    return dict(state)


class CheckpointedActionResultRunner:
    """Execute a result action once and verify it before every replay."""

    def __init__(self, store: Any, task_id: str, governor: Any) -> None:
        self.store = store
        self.task_id = str(task_id)
        self.governor = governor

    def run(
        self,
        action: PendingSearchAction,
        *,
        execute: Callable[[], dict[str, Any]],
        observe: Callable[[], dict[str, Any] | None],
        wait_for_manual: Callable[
            [str, str, str, PendingSearchAction],
            bool | None,
        ],
    ) -> dict[str, Any]:
        recovery = self._recovery()
        pending = pending_action_from_checkpoint(
            recovery.get("action")
        )
        phase = str(recovery.get("phase") or "")
        if pending is not None and pending.action_id == action.action_id:
            action = pending
            if phase == "boundary":
                self._finish_boundary(action)
            try:
                observed = observe()
            except Exception as exc:
                if not _is_access_error(exc):
                    raise
                if action.attempt_count >= 1 or phase in {
                    "completed",
                    "reused",
                }:
                    if _is_captcha(exc):
                        self._record_captcha(action.action_id)
                    self._finish_boundary(
                        action,
                        reason=(
                            "automatic-retry-already-attempted"
                            if action.attempt_count >= 1
                            else "completed-action-result-not-observed"
                        ),
                    )
                return self._recover_after_wait(
                    action,
                    first_error=exc,
                    execute=execute,
                    observe=observe,
                    wait_for_manual=wait_for_manual,
                )
            if observed is not None:
                self._record_completed(action.action_id)
                return self._finish(
                    action,
                    observed,
                    attempt_count=action.attempt_count,
                    reused=True,
                )
            if action.attempt_count >= 1:
                self._finish_boundary(
                    action,
                    reason="automatic-retry-already-attempted",
                )
            if phase in {"completed", "reused"}:
                self._finish_boundary(
                    action,
                    reason="completed-action-result-not-observed",
                )

        self._start(action)
        self._save(
            phase="prepared",
            action=action,
            event_id=f"recovery-{uuid.uuid4()}",
            attempt_count=action.attempt_count,
        )
        try:
            state = execute()
        except Exception as exc:
            if not _is_access_error(exc):
                raise
            return self._recover_after_wait(
                action,
                first_error=exc,
                execute=execute,
                observe=observe,
                wait_for_manual=wait_for_manual,
            )
        self._record_completed(action.action_id)
        return self._finish(
            action,
            state,
            attempt_count=action.attempt_count,
            reused=False,
        )

    def _recover_after_wait(
        self,
        action: PendingSearchAction,
        *,
        first_error: Exception,
        execute: Callable[[], dict[str, Any]],
        observe: Callable[[], dict[str, Any] | None],
        wait_for_manual: Callable[
            [str, str, str, PendingSearchAction],
            bool | None,
        ],
    ) -> dict[str, Any]:
        captcha = _is_captcha(first_error)
        released = (
            self._record_captcha(action.action_id)
            if captcha
            else False
        )
        event_id = str(self._recovery().get("eventId") or "")
        self._save(
            phase="waiting",
            action=action,
            event_id=event_id,
            attempt_count=0,
            breaker_reason=(
                str(
                    self._snapshot().get("breaker_reason") or ""
                )
                if released
                else ""
            ),
        )
        try:
            accepted = wait_for_manual(
                manual_wait_reason(action),
                _wait_status(first_error),
                event_id,
                action,
            )
        except Exception as exc:
            if _is_access_error(exc):
                self._save(
                    phase="waiting",
                    action=action,
                    event_id=event_id,
                    attempt_count=0,
                )
            raise
        if accepted is False:
            raise SearchRecoveryUnconfirmed(
                action,
                "人工恢复令牌已失效，不能唤醒当前验证码等待",
            )
        if released:
            self._finish_boundary(action)

        if captcha:
            self._clear_captcha(action.action_id)
        observed = self._observe_after_manual(
            action,
            observe,
        )
        if observed is not None:
            self._record_completed(action.action_id)
            return self._finish(
                action,
                observed,
                attempt_count=0,
                reused=True,
            )

        retry_action = PendingSearchAction(
            action_id=action.action_id,
            kind=action.kind,
            request=action.request,
            expected_page=action.expected_page,
            previous_result_signature=action.previous_result_signature,
            target=action.target,
            attempt_count=1,
            created_at=action.created_at,
        )
        self._start(retry_action)
        self._save(
            phase="retrying",
            action=retry_action,
            event_id=event_id,
            attempt_count=1,
        )
        try:
            state = execute()
        except Exception as exc:
            if not _is_access_error(exc):
                raise
            if _is_captcha(exc):
                self._record_captcha(retry_action.action_id)
            self._finish_boundary(retry_action)
            raise SearchRecoveryBoundaryRequired(
                retry_action,
                str(exc),
                governor=self._snapshot(),
            ) from exc
        self._record_completed(retry_action.action_id)
        return self._finish(
            retry_action,
            state,
            attempt_count=1,
            reused=False,
        )

    def _observe_after_manual(
        self,
        action: PendingSearchAction,
        observe: Callable[[], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        try:
            return observe()
        except Exception as exc:
            if not _is_access_error(exc):
                raise
            if _is_captcha(exc):
                self._record_captcha(action.action_id)
                self._finish_boundary(action)
            raise

    def _finish(
        self,
        action: PendingSearchAction,
        state: dict[str, Any],
        *,
        attempt_count: int,
        reused: bool,
    ) -> dict[str, Any]:
        self._save(
            phase="reused" if reused else "completed",
            action=action,
            event_id=str(self._recovery().get("eventId") or ""),
            attempt_count=attempt_count,
            reused=reused,
            last_outcome={
                "status": "reused" if reused else "completed",
                "actionId": action.action_id,
                "actionKind": action.kind,
            },
        )
        return state

    def _finish_boundary(
        self,
        action: PendingSearchAction,
        *,
        reason: str = "",
    ) -> None:
        snapshot = self._snapshot()
        reason = str(
            reason
            or snapshot.get("breaker_reason")
            or "repeated-captcha-boundary"
        )
        self._save(
            phase="boundary",
            action=action,
            event_id=str(self._recovery().get("eventId") or ""),
            attempt_count=action.attempt_count,
            breaker_reason=reason,
            reused=False,
            last_outcome={
                "status": "boundary",
                "reason": reason,
                "actionId": action.action_id,
                "actionKind": action.kind,
            },
        )
        raise SearchRecoveryBoundaryRequired(
            action,
            reason,
            governor=snapshot,
        )

    def _recovery(self) -> dict[str, Any]:
        task = self.store.get_task(self.task_id) or {}
        value = dict(task.get("checkpoint") or {}).get(
            "searchRecovery"
        )
        return dict(value) if isinstance(value, dict) else {}

    def _save(
        self,
        *,
        phase: str,
        action: PendingSearchAction,
        event_id: str,
        attempt_count: int,
        reused: bool = False,
        breaker_reason: str = "",
        last_outcome: dict[str, Any] | None = None,
    ) -> None:
        task = self.store.get_task(self.task_id)
        if task is None:
            raise RuntimeError(f"task {self.task_id} was not found")
        checkpoint = dict(task.get("checkpoint") or {})
        previous = dict(checkpoint.get("searchRecovery") or {})
        checkpoint["searchRecovery"] = {
            "version": 1,
            "eventId": event_id or f"recovery-{uuid.uuid4()}",
            "phase": phase,
            "action": pending_action_to_checkpoint(action),
            "governor": self._snapshot(),
            "attemptCount": int(attempt_count),
            "reusedResult": bool(reused),
            "breakerReason": str(breaker_reason),
            "lastOutcome": (
                dict(last_outcome)
                if last_outcome is not None
                else dict(previous.get("lastOutcome") or {})
            ),
        }
        self.store.update_task(
            self.task_id,
            checkpoint=checkpoint,
        )

    def _snapshot(self) -> dict[str, Any]:
        snapshot = getattr(self.governor, "snapshot", None)
        value = snapshot() if callable(snapshot) else {}
        return dict(value) if isinstance(value, dict) else {}

    def _start(self, action: PendingSearchAction) -> None:
        started = getattr(
            self.governor,
            "record_action_started",
            None,
        )
        if callable(started):
            started(
                action.kind,
                action.action_id,
                _scope_key(action),
            )
            return
        started = getattr(
            self.governor,
            "before_result_action",
            None,
        )
        if callable(started):
            started(action.kind)

    def _record_completed(self, action_id: str) -> None:
        completed = getattr(
            self.governor,
            "record_action_completed",
            None,
        )
        if callable(completed):
            completed(action_id)
            return
        completed = getattr(
            self.governor,
            "after_result_action",
            None,
        )
        if callable(completed):
            completed()

    def _record_captcha(self, action_id: str) -> bool:
        record = getattr(self.governor, "record_captcha", None)
        return bool(record(action_id)) if callable(record) else False

    def _clear_captcha(self, action_id: str) -> None:
        clear = getattr(
            self.governor,
            "record_captcha_cleared",
            None,
        )
        if callable(clear):
            clear(action_id)
            return
        clear = getattr(
            self.governor,
            "note_captcha_cleared",
            None,
        )
        if callable(clear):
            clear()


def manual_wait_reason(action: PendingSearchAction) -> str:
    return (
        f"检测到访问验证，等待后先核验原动作"
        f"（{action.kind}；查询={action.request.query}；"
        f"库={action.request.library}；"
        f"年份={action.request.year}；"
        f"第 {action.attempt_count + 1} 次尝试）"
    )


def _scope_key(action: PendingSearchAction) -> str:
    return f"{action.request.query}|{action.request.library}"


def _is_access_error(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "")
    name = exc.__class__.__name__
    return bool(
        code
        in {
            "MANUAL_ACTION_REQUIRED",
            "LOGIN_REQUIRED",
            "CAPTCHA_VISIBLE",
            "SEARCH_TRANSITION_UNCONFIRMED",
        }
        or name in {"ManualActionRequired", "LoginRequired"}
    )


def _is_captcha(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "")
    message = str(exc)
    return bool(
        code == "CAPTCHA_VISIBLE"
        or "验证码" in message
        or "滑块" in message
        or "captcha" in message.lower()
    )


def _wait_status(exc: Exception) -> str:
    code = str(getattr(exc, "code", "") or "")
    if code == "LOGIN_REQUIRED" or exc.__class__.__name__ == "LoginRequired":
        return "waiting_login"
    return "waiting_manual"
