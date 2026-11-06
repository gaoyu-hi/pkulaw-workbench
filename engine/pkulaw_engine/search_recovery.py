from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .sources.search_transaction import SearchRequestIdentity


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
