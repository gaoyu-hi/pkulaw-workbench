from __future__ import annotations

from dataclasses import dataclass

from ..storage import SEARCH_SEMANTICS_VERSION, ValidationError


@dataclass(frozen=True)
class SearchModeSpec:
    match_mode: str
    search_keyword_type: str
    match_type: str
    identity_mode: str


_SEARCH_MODES = {
    "keyword": SearchModeSpec(
        match_mode="keyword",
        search_keyword_type="Title",
        match_type="Exact",
        identity_mode="keyword",
    ),
    "fulltext": SearchModeSpec(
        match_mode="fulltext",
        search_keyword_type="FullText",
        match_type="Exact",
        identity_mode="keyword",
    ),
    "title": SearchModeSpec(
        match_mode="title",
        search_keyword_type="Title",
        match_type="Exact",
        identity_mode="title",
    ),
}


def resolve_search_mode(match_mode: str) -> SearchModeSpec:
    clean = str(match_mode or "").strip()
    spec = _SEARCH_MODES.get(clean)
    if spec is None:
        raise ValidationError(
            "INVALID_MATCH_MODE",
            "match_mode must be keyword, fulltext or title",
        )
    return spec


def is_exact_title_mode(match_mode: str) -> bool:
    return resolve_search_mode(match_mode).identity_mode == "title"


def search_type_matches(
    actual: object,
    expected: tuple[str, ...],
) -> bool:
    normalized_actual = str(actual or "").casefold()
    return normalized_actual in {
        str(value or "").casefold()
        for value in expected
    }


def ensure_current_search_semantics(task_input: dict[str, object]) -> None:
    try:
        version = int(
            task_input.get("searchSemanticsVersion") or 0
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "SEARCH_SEMANTICS_UPGRADE_REQUIRED",
            "任务检索语义版本无效，请重新应用任务设置",
        ) from exc
    if version != SEARCH_SEMANTICS_VERSION:
        raise ValidationError(
            "SEARCH_SEMANTICS_UPGRADE_REQUIRED",
            "旧任务的检索字段含义已变化，请先重新应用任务设置",
        )
