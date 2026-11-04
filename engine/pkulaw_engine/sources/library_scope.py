from __future__ import annotations

ALL_PROVINCES = "全部省份"


def province_for_library(
    library: str,
    selected_province: str,
) -> str:
    if library == "central":
        return ALL_PROVINCES
    return selected_province or ALL_PROVINCES
