from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..storage import ValidationError


@dataclass(frozen=True)
class PageSelection:
    mode: str = "last"
    max_pages: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"last", "limit"}:
            raise ValidationError(
                "INVALID_PAGE_MODE",
                "page mode must be last or limit",
            )
        if self.mode == "limit":
            if self.max_pages is None:
                raise ValidationError(
                    "MAX_PAGES_REQUIRED",
                    "指定页数上限时必须提供 maxPages",
                )
            if isinstance(self.max_pages, bool):
                raise ValidationError(
                    "INVALID_MAX_PAGES",
                    "maxPages must be an integer",
                )
            try:
                parsed = int(self.max_pages)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    "INVALID_MAX_PAGES",
                    "maxPages must be an integer",
                ) from exc
            if parsed < 1:
                raise ValidationError(
                    "INVALID_MAX_PAGES",
                    "maxPages must be at least 1",
                )
            if parsed != self.max_pages:
                object.__setattr__(self, "max_pages", parsed)

    def to_payload(self) -> dict[str, Any]:
        return {
            "pageMode": self.mode,
            "pageSelection": {
                "mode": self.mode,
                **(
                    {"maxPages": self.max_pages}
                    if self.mode == "limit"
                    else {}
                ),
            },
            "maxPages": self.max_pages if self.mode == "limit" else None,
        }


def parse_page_selection(payload: dict[str, Any]) -> PageSelection:
    nested = payload.get("pageSelection")
    if isinstance(nested, dict):
        mode = str(nested.get("mode") or nested.get("pageMode") or "").strip()
        if mode == "last":
            return PageSelection(mode="last")
        if mode == "limit":
            return PageSelection(
                mode="limit",
                max_pages=nested.get("maxPages"),
            )
        if mode:
            raise ValidationError(
                "INVALID_PAGE_MODE",
                "page mode must be last or limit",
            )
    mode = str(payload.get("pageMode") or "").strip()
    if mode == "last":
        return PageSelection(mode="last")
    if mode == "limit":
        return PageSelection(mode="limit", max_pages=payload.get("maxPages"))
    if mode:
        raise ValidationError(
            "INVALID_PAGE_MODE",
            "page mode must be last or limit",
        )
    if payload.get("maxPages") is not None:
        return PageSelection(mode="limit", max_pages=payload.get("maxPages"))
    return PageSelection(mode="last")


def restore_page_selection(
    selection: PageSelection,
    checkpoint: dict[str, Any],
) -> PageSelection:
    """Keep a user's task-wide continue-to-last decision across restarts."""
    if (
        str(checkpoint.get("pageMode") or "") == "last"
        and str(checkpoint.get("pageLimitDecision") or "")
        == "continue_to_last"
    ):
        return PageSelection(mode="last")
    return selection


def should_pause_for_page_limit(
    *,
    page: int,
    total_pages: int,
    max_pages: int,
) -> bool:
    """Pause after the allowed pages when the real result set is larger."""
    return total_pages > max_pages and page >= max_pages


def page_signature(rows: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(
        str(
            row.get("policyId")
            or row.get("url")
            or row.get("title")
            or ""
        )
        for row in rows
    )


def pagination_transition_error(
    *,
    previous_page: int,
    next_page: int,
    previous_total_pages: int,
    next_total_pages: int,
    previous_signature: tuple[str, ...],
    next_signature: tuple[str, ...],
) -> str | None:
    if next_page <= previous_page:
        return (
            f"分页未前进：第 {previous_page} 页之后仍为第 {next_page} 页"
        )
    if next_page < 1:
        return f"下一页页码无效：{next_page}"
    if next_total_pages < 1:
        return f"下一页总页数无效：{next_total_pages}"
    if next_total_pages < previous_total_pages:
        return (
            f"总页数异常下降：{previous_total_pages} → {next_total_pages}"
        )
    if next_page > next_total_pages:
        return (
            f"页码超出总页数：第 {next_page} 页 / 共 {next_total_pages} 页"
        )
    if previous_signature and previous_signature == next_signature:
        return "下一页结果签名与上一页相同，已暂停以避免重复政策"
    return None
