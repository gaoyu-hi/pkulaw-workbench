from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .years import (
    MIN_YEAR,
    SearchCombination,
    YearSelection,
    current_year,
)


@dataclass(frozen=True)
class AdaptiveYearPlan:
    requested_selection: YearSelection
    available_counts: dict[str, int]
    collected_counts: dict[str, int]
    completed_years: tuple[str, ...]
    missing_years: tuple[str, ...]
    unresolved_years: tuple[str, ...]
    query: str = ""
    library: str = ""


def _valid_year(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    if len(text) != 4 or not text.isdigit():
        return None
    year = int(text)
    if year < MIN_YEAR or year > current_year():
        return None
    return text


def _requested_years(selection: YearSelection, entry_years: set[str]) -> set[str]:
    if selection.mode == "all":
        return set(entry_years)
    if selection.mode == "single":
        requested = {str(selection.year)}
    else:
        assert selection.start_year is not None and selection.end_year is not None
        requested = {
            str(year)
            for year in range(selection.start_year, selection.end_year + 1)
        }
    return requested & entry_years


def plan_adaptive_years(
    selection: YearSelection,
    entries: Iterable[Mapping[str, Any]],
    collected_counts: Mapping[str, Any],
    *,
    query: str = "",
    library: str = "",
) -> AdaptiveYearPlan:
    raw_counts: dict[str, Any] = {}
    for entry in entries:
        year = _valid_year(entry.get("value"))
        if year is None:
            continue
        raw_counts[year] = entry.get("count")

    requested = _requested_years(selection, set(raw_counts))
    available_counts: dict[str, int] = {}
    unresolved_years: list[str] = []
    for year in sorted(requested, key=int):
        raw = raw_counts.get(year)
        if isinstance(raw, bool):
            unresolved_years.append(year)
            continue
        try:
            count = int(raw)
        except (TypeError, ValueError):
            unresolved_years.append(year)
            continue
        if count < 0:
            unresolved_years.append(year)
            continue
        if count == 0:
            continue
        available_counts[year] = count

    normalized_collected: dict[str, int] = {}
    for year, raw in collected_counts.items():
        year_key = _valid_year(year)
        if year_key is None or isinstance(raw, bool):
            continue
        try:
            count = int(raw)
        except (TypeError, ValueError):
            continue
        if count < 0:
            continue
        normalized_collected[year_key] = count

    completed_years: list[str] = []
    missing_years: list[str] = []
    for year in sorted(available_counts, key=int):
        if normalized_collected.get(year, 0) >= available_counts[year]:
            completed_years.append(year)
        else:
            missing_years.append(year)

    return AdaptiveYearPlan(
        requested_selection=selection,
        available_counts=available_counts,
        collected_counts=normalized_collected,
        completed_years=tuple(completed_years),
        missing_years=tuple(missing_years),
        unresolved_years=tuple(unresolved_years),
        query=query,
        library=library,
    )


def publication_year_from_row(row: Mapping[str, Any]) -> int | None:
    raw = row.get("publicationDate") or row.get("date")
    if raw is None:
        return None
    text = str(raw)
    match = re.search(r"(?<!\d)(19|20|21)\d{2}(?!\d)", text)
    if match is None:
        return None
    year = int(match.group(0))
    if year < MIN_YEAR or year > current_year():
        return None
    return year


def row_matches_year_selection(
    row: Mapping[str, Any],
    selection: YearSelection,
) -> bool:
    year = publication_year_from_row(row)
    if year is None:
        return False
    if selection.mode == "all":
        return True
    if selection.mode == "single":
        return year == selection.year
    assert selection.start_year is not None and selection.end_year is not None
    return selection.start_year <= year <= selection.end_year


def build_year_switch_combinations(
    plan: AdaptiveYearPlan,
    query_index: int,
    library_index: int,
    start_index: int = 0,
) -> list[SearchCombination]:
    combinations: list[SearchCombination] = []
    for year_index, year in enumerate(plan.missing_years):
        combinations.append(
            SearchCombination(
                query=plan.query,
                library=plan.library,
                year=year,
                query_index=query_index,
                library_index=library_index,
                year_index=year_index,
                combination_index=start_index + len(combinations) + 1,
                action_kind="year-filter-switch",
            )
        )
    return combinations
