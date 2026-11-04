from __future__ import annotations

from typing import Any, Protocol


class SourceAdapter(Protocol):
    def search(
        self,
        *,
        keyword: str,
        library: str,
        year: str,
        province: str,
        timeliness: str,
        page_size: int,
    ) -> dict[str, Any]: ...

    def read_results(self) -> dict[str, Any]: ...

    def next_page(self, expected_page: int) -> dict[str, Any]: ...

    def open_detail(self, row: dict[str, Any]) -> dict[str, Any]: ...

    def click_word_download(self) -> dict[str, Any]: ...

    def return_to_results(self) -> None: ...
