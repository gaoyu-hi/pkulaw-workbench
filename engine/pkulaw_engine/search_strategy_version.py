from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .selections.years import SearchCombination
from .storage import ValidationError


def parse_search_strategy_version(payload: Mapping[str, Any]) -> int:
    if "searchStrategyVersion" not in payload:
        return 1
    value = payload["searchStrategyVersion"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in {1, 2}:
        raise ValidationError(
            "INVALID_SEARCH_STRATEGY_VERSION",
            "searchStrategyVersion must be 1 or 2",
        )
    return value


def refresh_adaptive_progress_total(
    total_planned: int,
    combinations: Sequence[SearchCombination],
) -> int:
    return max(1, len(combinations))
