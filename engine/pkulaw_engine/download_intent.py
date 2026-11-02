from __future__ import annotations

import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from urllib.parse import urlparse

from .storage import ValidationError


class DownloadIntentExhausted(ValidationError):
    def __init__(self, message: str) -> None:
        super().__init__("DOWNLOAD_INTENT_EXHAUSTED", message)


@dataclass(frozen=True)
class DownloadIntent:
    intent_id: str
    task_id: str
    kind: str
    expected_suffixes: tuple[str, ...]
    expected_source_hosts: tuple[str, ...]
    max_downloads: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "intentId": self.intent_id,
            "taskId": self.task_id,
            "kind": self.kind,
            "expectedSuffixes": list(self.expected_suffixes),
            "expectedSourceHosts": list(self.expected_source_hosts),
            "maxDownloads": self.max_downloads,
            "createdAt": self.created_at,
        }


class DownloadIntentManager:
    """Matches Electron download events to one explicit user action."""

    def __init__(
        self,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._lock = RLock()
        self._event_sink = event_sink
        self._intents: dict[str, DownloadIntent] = {}
        self._states: dict[str, dict[str, Any]] = {}

    def create(
        self,
        *,
        task_id: str,
        kind: str,
        source_url: str,
        expected_suffixes: tuple[str, ...],
        max_downloads: int = 1,
    ) -> dict[str, Any]:
        clean_task_id = str(task_id or "").strip()
        clean_kind = str(kind or "").strip()
        suffixes = tuple(
            suffix if suffix.startswith(".") else f".{suffix}"
            for suffix in (
                str(value or "").strip().lower()
                for value in expected_suffixes
            )
            if suffix
        )
        if not clean_task_id:
            raise ValidationError("TASK_ID_REQUIRED", "download intent requires taskId")
        if not clean_kind:
            raise ValidationError("DOWNLOAD_INTENT_KIND_REQUIRED", "download intent kind is required")
        if not suffixes:
            raise ValidationError(
                "DOWNLOAD_INTENT_SUFFIX_REQUIRED",
                "download intent requires expected suffixes",
            )
        source_host = urlparse(str(source_url or "")).hostname or ""
        if not source_host:
            raise ValidationError(
                "DOWNLOAD_INTENT_SOURCE_REQUIRED",
                "download intent requires an expected source URL",
            )
        intent = DownloadIntent(
            intent_id=str(uuid.uuid4()),
            task_id=clean_task_id,
            kind=clean_kind,
            expected_suffixes=suffixes,
            expected_source_hosts=(source_host.lower(),),
            max_downloads=max(1, int(max_downloads)),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        with self._lock:
            self._intents[intent.intent_id] = intent
            self._states[intent.intent_id] = {
                "acceptedDownloadId": "",
                "status": "pending",
                "matchedDownloads": 0,
                "startedEvent": None,
                "terminalEvent": None,
                "validation": None,
            }
        return intent.to_dict()

    def observe(self, intent_id: str, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            intent = self._intents.get(str(intent_id))
            state = self._states.get(str(intent_id))
            if intent is None or state is None:
                return {"accepted": False, "outcome": "ignored", "reason": "unknown intent"}
            event_name = str(event.get("event") or "").split(".")[-1]
            download_id = str(event.get("downloadId") or "")
            matched, reason = self._event_matches(intent, event)
            if not matched:
                self._emit_mismatch(intent, event, reason)
                if self._is_immediate_catalog_mismatch(intent, event, reason):
                    state["status"] = "mismatch"
                    filename = str(event.get("filename") or "")
                    path = str(event.get("path") or "")
                    suffix = Path(
                        filename or urlparse(str(event.get("url") or "")).path
                    ).suffix.lower()
                    return {
                        "accepted": False,
                        "outcome": "mismatch",
                        "downloadId": download_id,
                        "reason": reason,
                        "errorCode": "CATALOG_NATIVE_FORMAT_MISMATCH",
                        "actualFile": filename,
                        "actualSuffix": suffix,
                        "actualSignature": self._file_signature(path),
                        "actualPath": path,
                        "expectedSuffixes": list(intent.expected_suffixes),
                        "expectedSourceHosts": list(intent.expected_source_hosts),
                        "recovery": (
                            "重新触发网站原生目录下载并确认 catalogExcel "
                            "只返回 BIFF .xls；若网站格式变化，停止任务并更新兼容契约"
                        ),
                        "event": deepcopy(event),
                        "intent": intent.to_dict(),
                    }
                return {"accepted": False, "outcome": "ignored", "reason": reason}

            if event_name == "started":
                accepted = str(state.get("acceptedDownloadId") or "")
                if accepted and accepted != download_id:
                    raise DownloadIntentExhausted(
                        f"下载意图 {intent_id} 已绑定 downloadId={accepted}，"
                        f"又收到匹配下载 {download_id}"
                    )
                if not accepted:
                    state["acceptedDownloadId"] = download_id
                    state["matchedDownloads"] = int(
                        state.get("matchedDownloads") or 0
                    ) + 1
                    state["startedEvent"] = deepcopy(event)
                    state["status"] = "started"
                return {
                    "accepted": True,
                    "outcome": "started",
                    "downloadId": download_id,
                    "intent": intent.to_dict(),
                }

            accepted = str(state.get("acceptedDownloadId") or "")
            if event_name == "progress":
                if accepted == download_id:
                    state["status"] = "progressing"
                    return {
                        "accepted": True,
                        "outcome": "progress",
                        "downloadId": download_id,
                    }
                return {
                    "accepted": False,
                    "outcome": "ignored",
                    "reason": "progress belongs to another download",
                }

            if event_name in {"completed", "failed"}:
                if not accepted or accepted != download_id:
                    self._emit_mismatch(
                        intent,
                        event,
                        "terminal event does not match accepted downloadId",
                    )
                    return {
                        "accepted": False,
                        "outcome": "ignored",
                        "reason": "terminal event has no matching started download",
                    }
                state["status"] = event_name
                state["terminalEvent"] = deepcopy(event)
                return {
                    "accepted": True,
                    "outcome": event_name,
                    "downloadId": download_id,
                    "intent": intent.to_dict(),
                }
            return {"accepted": False, "outcome": "ignored", "reason": "unknown event"}

    def record_validation(
        self,
        intent_id: str,
        *,
        valid: bool,
        actual_kind: str,
        detail: str = "",
    ) -> None:
        with self._lock:
            state = self._states.get(str(intent_id))
            if state is None:
                return
            state["validation"] = {
                "valid": bool(valid),
                "actualKind": str(actual_kind),
                "detail": str(detail),
            }
            if not valid:
                state["status"] = "validation_failed"

    def state(self, intent_id: str) -> dict[str, Any] | None:
        with self._lock:
            intent = self._intents.get(str(intent_id))
            state = self._states.get(str(intent_id))
            if intent is None or state is None:
                return None
            return {
                **intent.to_dict(),
                **deepcopy(state),
            }

    def _event_matches(
        self,
        intent: DownloadIntent,
        event: dict[str, Any],
    ) -> tuple[bool, str]:
        filename = str(event.get("filename") or "")
        url = str(event.get("url") or "")
        suffix = Path(filename or urlparse(url).path).suffix.lower()
        if suffix not in intent.expected_suffixes:
            return (
                False,
                f"unexpected suffix {suffix or '<missing>'}; "
                f"expected={list(intent.expected_suffixes)}",
            )
        host = (urlparse(url).hostname or "").lower()
        if not self._host_matches(host, intent.expected_source_hosts):
            return (
                False,
                f"unexpected source host {host or '<missing>'}; "
                f"expected={list(intent.expected_source_hosts)}",
            )
        return True, ""

    @staticmethod
    def _host_matches(host: str, expected_hosts: tuple[str, ...]) -> bool:
        if not host:
            return False
        for expected in expected_hosts:
            if host == expected or host.endswith(f".{expected}"):
                return True
        return False

    def _is_immediate_catalog_mismatch(
        self,
        intent: DownloadIntent,
        event: dict[str, Any],
        reason: str,
    ) -> bool:
        if intent.kind != "batch-catalog":
            return False
        if not reason.startswith("unexpected suffix"):
            return False
        host = (urlparse(str(event.get("url") or "")).hostname or "").lower()
        return self._host_matches(host, intent.expected_source_hosts)

    @staticmethod
    def _file_signature(path: str) -> str:
        if not path:
            return ""
        try:
            with Path(path).open("rb") as handle:
                return handle.read(16).hex()
        except OSError:
            return ""

    def _emit_mismatch(
        self,
        intent: DownloadIntent,
        event: dict[str, Any],
        reason: str,
    ) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(
                "DOWNLOAD_EVENT_MISMATCH",
                {
                    "intent": intent.to_dict(),
                    "event": deepcopy(event),
                    "reason": reason,
                },
            )
        except Exception:
            return
