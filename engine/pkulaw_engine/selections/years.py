from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from ..storage import ValidationError

MIN_YEAR = 2000
ALL_YEARS_LABEL = "全部年份"


def current_year() -> int:
    return date.today().year


def _parse_year(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValidationError("INVALID_YEAR", f"{field} must be a four-digit year")
    text = str(value or "").strip()
    if len(text) != 4 or not text.isdigit():
        raise ValidationError("INVALID_YEAR", f"{field} must be a four-digit year")
    parsed = int(text)
    if parsed < MIN_YEAR or parsed > current_year():
        raise ValidationError(
            "YEAR_OUT_OF_RANGE",
            f"{field} must be between {MIN_YEAR} and {current_year()}",
        )
    return parsed


@dataclass(frozen=True)
class YearSelection:
    mode: str
    year: int | None = None
    start_year: int | None = None
    end_year: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"all", "single", "range"}:
            raise ValidationError(
                "INVALID_YEAR_MODE",
                "year mode must be all, single or range",
            )
        if self.mode == "single":
            if self.year is None:
                raise ValidationError("YEAR_REQUIRED", "单一年份模式必须指定年份")
            parsed_year = _parse_year(self.year, "year")
            if parsed_year != self.year:
                object.__setattr__(self, "year", parsed_year)
        elif self.mode == "range":
            if self.start_year is None or self.end_year is None:
                raise ValidationError(
                    "YEAR_RANGE_REQUIRED",
                    "年份区间模式必须指定起始和结束年份",
                )
            parsed_start = _parse_year(self.start_year, "yearStart")
            parsed_end = _parse_year(self.end_year, "yearEnd")
            if parsed_start > parsed_end:
                raise ValidationError(
                    "INVALID_YEAR_RANGE",
                    "起始年份不能晚于结束年份",
                )
            object.__setattr__(self, "start_year", parsed_start)
            object.__setattr__(self, "end_year", parsed_end)

    @property
    def years(self) -> tuple[str, ...]:
        if self.mode == "all":
            return (ALL_YEARS_LABEL,)
        if self.mode == "single":
            return (str(self.year),)
        assert self.start_year is not None and self.end_year is not None
        return tuple(
            str(year)
            for year in range(self.start_year, self.end_year + 1)
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "yearMode": self.mode,
            "year": str(self.year) if self.year is not None else (
                ALL_YEARS_LABEL if self.mode == "all" else ""
            ),
            "yearStart": self.start_year,
            "yearEnd": self.end_year,
            "yearSelection": {
                "mode": self.mode,
                "year": self.year,
                "startYear": self.start_year,
                "endYear": self.end_year,
            },
        }


def parse_year_selection(payload: dict[str, Any]) -> YearSelection:
    nested = payload.get("yearSelection")
    if isinstance(nested, dict):
        mode = str(nested.get("mode") or nested.get("yearMode") or "").strip()
        return YearSelection(
            mode=mode,
            year=_optional_year(nested.get("year")),
            start_year=_optional_year(nested.get("startYear")),
            end_year=_optional_year(nested.get("endYear")),
        )

    mode = str(payload.get("yearMode") or "").strip()
    if mode:
        return YearSelection(
            mode=mode,
            year=_optional_year(payload.get("year")),
            start_year=_optional_year(payload.get("yearStart")),
            end_year=_optional_year(payload.get("yearEnd")),
        )

    legacy = payload.get("year")
    if legacy is None or str(legacy).strip() in {"", ALL_YEARS_LABEL}:
        return YearSelection(mode="all")
    return YearSelection(
        mode="single",
        year=_optional_year(legacy),
    )


def _optional_year(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return _parse_year(value, "year")


@dataclass(frozen=True)
class SearchCombination:
    query: str
    library: str
    year: str
    query_index: int
    library_index: int
    year_index: int
    combination_index: int
    province: str = ""
    timeliness: str = ""
    partition_key: str = ""

    def checkpoint(self) -> dict[str, Any]:
        return {
            "queryIndex": self.query_index,
            "libraryIndex": self.library_index,
            "yearIndex": self.year_index,
            "combinationIndex": self.combination_index,
            "query": self.query,
            "library": self.library,
            "year": self.year,
            "currentYear": self.year,
            "province": self.province,
            "timeliness": self.timeliness,
            "partitionKey": self.partition_key,
            "page": 1,
            "currentAction": "search",
        }


def expand_search_combinations(
    queries: Iterable[str],
    libraries: Iterable[str],
    selection: YearSelection,
) -> list[SearchCombination]:
    combinations: list[SearchCombination] = []
    years = selection.years
    for query_index, query in enumerate(queries):
        for library_index, library in enumerate(libraries):
            for year_index, year in enumerate(years):
                combinations.append(
                    SearchCombination(
                        query=query,
                        library=library,
                        year=year,
                        query_index=query_index,
                        library_index=library_index,
                        year_index=year_index,
                        combination_index=len(combinations) + 1,
                    )
                )
    return combinations


def mark_current_year_status(
    checkpoint: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    current_year = str(
        checkpoint.get("currentYear")
        or checkpoint.get("year")
        or ""
    )
    current_query = str(
        checkpoint.get("currentQuery")
        or checkpoint.get("query")
        or ""
    )
    current_library = str(
        checkpoint.get("currentLibrary")
        or checkpoint.get("library")
        or ""
    )
    current_partition_key = str(
        checkpoint.get("partitionKey") or ""
    )
    updated: list[dict[str, Any]] = []
    for raw in checkpoint.get("yearStats") or []:
        item = dict(raw)
        if (
            str(item.get("year") or "") == current_year
            and str(item.get("query") or "") == current_query
            and str(item.get("library") or "") == current_library
            and (
                not current_partition_key
                or str(item.get("partitionKey") or "")
                == current_partition_key
            )
        ):
            item["status"] = status
        updated.append(item)
    checkpoint["yearStats"] = updated
    return checkpoint


def finalize_year_stats(checkpoint: dict[str, Any]) -> dict[str, Any]:
    updated: list[dict[str, Any]] = []
    for raw in checkpoint.get("yearStats") or []:
        item = dict(raw)
        if item.get("status") in {"running", "paused"}:
            item["status"] = (
                "no_result"
                if int(item.get("resultCount") or 0) == 0
                else "completed"
            )
        updated.append(item)
    checkpoint["yearStats"] = updated
    return checkpoint
