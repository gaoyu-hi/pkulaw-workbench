from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from ..search_window import assess_result_window
from .search_mode import (
    is_exact_title_mode,
    resolve_search_mode,
    search_type_matches,
)


class SearchSessionError(RuntimeError):
    code = "RESULTS_STALE"


class SearchTransitionTimeout(RuntimeError):
    code = "SEARCH_TRANSITION_UNCONFIRMED"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code or "SEARCH_TRANSITION_UNCONFIRMED"


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def result_signature(rows: list[dict[str, Any]]) -> str:
    identities = [
        str(row.get("policyId") or row.get("id") or row.get("url") or "")
        for row in rows
    ]
    return hashlib.sha256("\x1f".join(identities).encode("utf-8")).hexdigest()


def clean_filter_label(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"\s*[\(（]\s*\d+\s*[\)）]\s*$", "", text).strip()
    text = re.sub(r"\s+\d+\s*$", "", text).strip()
    return text


def filter_values_match(requested: Any, actual: Any) -> bool:
    expected = clean_filter_label(requested)
    observed = clean_filter_label(actual)
    if expected.startswith("全部") and (not observed or observed.startswith("全部")):
        return True
    return bool(expected) and expected == observed


def page_size_shape_error(
    *,
    page: int,
    total_pages: int,
    result_count: int,
    row_count: int,
    page_size: int,
) -> str:
    if page < 1:
        return f"页码无效：{page}"
    if page_size < 1:
        return f"实际页大小无效：{page_size}"
    expected_rows = (
        page_size
        if page < total_pages
        else max(0, result_count - (page - 1) * page_size)
    )
    if row_count != expected_rows:
        return (
            f"当前页行数与页大小不一致：page={page}, totalPages={total_pages}, "
            f"resultCount={result_count}, pageSize={page_size}, "
            f"rowCount={row_count}, expected={expected_rows}"
        )
    return ""


@dataclass(frozen=True)
class SearchRequestIdentity:
    query: str
    match_mode: str
    library: str
    year: str
    province: str
    timeliness: str
    requested_page_size: int

    @property
    def expected_library_code(self) -> str:
        return {"central": "chl", "local": "lar"}[self.library]

    @property
    def expected_search_types(self) -> tuple[str, ...]:
        return (resolve_search_mode(self.match_mode).search_keyword_type,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "matchMode": self.match_mode,
            "library": self.library,
            "year": self.year,
            "province": self.province,
            "timeliness": self.timeliness,
            "requestedPageSize": self.requested_page_size,
        }


@dataclass(frozen=True)
class SearchSession:
    search_id: str
    query: str
    match_mode: str
    library: str
    year: str
    province: str
    timeliness: str
    requested_page_size: int
    actual_page_size: int
    page: int
    total_pages: int
    result_count: int
    row_count: int
    grouping_mode: str
    result_signature: str
    observed_at: str
    url: str
    actual_year: str = ""
    actual_province: str = ""
    actual_timeliness: str = ""
    reported_total_pages: int | None = None

    def is_verified_empty_exact_title(self) -> bool:
        """An exact-title zero-result page has no refinement controls.

        The base search transaction already verifies the query, exact-title
        mode, target library, page reset, and stable DOM.  Filters, page-size,
        and grouping cannot change a genuinely empty exact-title result and the
        live site removes those controls in this state.
        """
        return (
            is_exact_title_mode(self.match_mode)
            and self.page == 1
            and self.result_count == 0
            and self.row_count == 0
        )

    @classmethod
    def from_observation(
        cls,
        request: SearchRequestIdentity,
        observation: dict[str, Any],
        *,
        search_id: str,
        observed_at: str | None = None,
    ) -> "SearchSession":
        rows = list(observation.get("rows") or [])
        result_count = max(0, int(observation.get("resultCount") or 0))
        actual_page_size = int(observation.get("actualPageSize") or 0)
        reported_total_pages = max(
            1,
            int(
                observation.get("reportedTotalPages")
                or observation.get("totalPages")
                or 1
            ),
        )
        window = (
            assess_result_window(
                result_count=result_count,
                reported_total_pages=reported_total_pages,
                page_size=actual_page_size,
            )
            if actual_page_size > 0
            else None
        )
        return cls(
            search_id=search_id,
            query=request.query,
            match_mode=request.match_mode,
            library=request.library,
            year=request.year,
            province=request.province,
            timeliness=request.timeliness,
            requested_page_size=request.requested_page_size,
            actual_page_size=actual_page_size,
            page=int(observation.get("page") or 0),
            total_pages=(
                max(
                    reported_total_pages,
                    window.expected_total_pages,
                )
                if window is not None and result_count > 0
                else reported_total_pages
            ),
            result_count=result_count,
            row_count=len(rows),
            grouping_mode=str(observation.get("groupingMode") or "unknown"),
            result_signature=str(
                observation.get("resultSignature") or result_signature(rows)
            ),
            observed_at=observed_at
            or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            url=str(observation.get("url") or ""),
            actual_year=str(observation.get("actualYear") or ""),
            actual_province=str(observation.get("actualProvince") or ""),
            actual_timeliness=str(observation.get("actualTimeliness") or ""),
            reported_total_pages=reported_total_pages,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "searchId": self.search_id,
            "query": self.query,
            "matchMode": self.match_mode,
            "library": self.library,
            "year": self.year,
            "province": self.province,
            "timeliness": self.timeliness,
            "requestedPageSize": self.requested_page_size,
            "actualPageSize": self.actual_page_size,
            "page": self.page,
            "totalPages": self.total_pages,
            "resultCount": self.result_count,
            "rowCount": self.row_count,
            "groupingMode": self.grouping_mode,
            "resultSignature": self.result_signature,
            "observedAt": self.observed_at,
            "url": self.url,
            "actualYear": self.actual_year,
            "actualProvince": self.actual_province,
            "actualTimeliness": self.actual_timeliness,
            "reportedTotalPages": self.reported_total_pages,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SearchSession | None":
        try:
            return cls(
                search_id=str(payload["searchId"]),
                query=str(payload["query"]),
                match_mode=str(payload["matchMode"]),
                library=str(payload["library"]),
                year=str(payload["year"]),
                province=str(payload["province"]),
                timeliness=str(payload["timeliness"]),
                requested_page_size=int(payload["requestedPageSize"]),
                actual_page_size=int(payload["actualPageSize"]),
                page=int(payload["page"]),
                total_pages=int(payload["totalPages"]),
                result_count=int(payload["resultCount"]),
                row_count=int(payload["rowCount"]),
                grouping_mode=str(payload["groupingMode"]),
                result_signature=str(payload["resultSignature"]),
                observed_at=str(payload["observedAt"]),
                url=str(payload.get("url") or ""),
                actual_year=str(payload.get("actualYear") or ""),
                actual_province=str(payload.get("actualProvince") or ""),
                actual_timeliness=str(payload.get("actualTimeliness") or ""),
                reported_total_pages=(
                    int(payload["reportedTotalPages"])
                    if payload.get("reportedTotalPages") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def matches_request(
        self,
        request: SearchRequestIdentity,
        *,
        expected_page: int | None = None,
        require_refinements: bool = False,
    ) -> bool:
        return (
            _normalized_text(self.query) == _normalized_text(request.query)
            and self.match_mode == request.match_mode
            and self.library == request.library
            and self.year == request.year
            and self.province == request.province
            and self.timeliness == request.timeliness
            and self.requested_page_size == request.requested_page_size
            and (expected_page is None or self.page == expected_page)
            and (
                not require_refinements
                or self.is_verified_empty_exact_title()
                or (
                    filter_values_match(request.year, self.actual_year)
                    and filter_values_match(
                        request.province,
                        self.actual_province,
                    )
                    and filter_values_match(
                        request.timeliness,
                        self.actual_timeliness,
                    )
                    and self.actual_page_size == request.requested_page_size
                    and self.grouping_mode == "ungrouped"
                )
            )
        )

    def stability_key(self) -> tuple[Any, ...]:
        return (
            self.search_id,
            self.url,
            self.page,
            self.total_pages,
            self.result_count,
            self.row_count,
            self.actual_page_size,
            self.grouping_mode,
            self.result_signature,
            self.actual_year,
            self.actual_province,
            self.actual_timeliness,
            self.reported_total_pages,
        )


class SearchTransaction:
    """Confirms that one submitted search replaced the previous result DOM."""

    def __init__(
        self,
        request: SearchRequestIdentity,
        *,
        token: str,
        previous: dict[str, Any],
        stable_samples: int = 2,
        timeout_seconds: float = 25.0,
        search_id: str | None = None,
        required_filter_keys: tuple[str, ...] = (),
        require_page_size: bool = False,
        require_ungrouped: bool = False,
    ) -> None:
        self.request = request
        self.token = token
        self.previous = dict(previous)
        self.stable_samples = max(2, int(stable_samples))
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.search_id = search_id or str(uuid.uuid4())
        self.required_filter_keys = tuple(required_filter_keys)
        self.require_page_size = bool(require_page_size)
        self.require_ungrouped = bool(require_ungrouped)
        self.last_rejection = ""
        self.last_rejection_code = ""
        self.last_observation: dict[str, Any] = {}
        self.last_session: SearchSession | None = None
        self.session: SearchSession | None = None
        self._last_key: tuple[Any, ...] | None = None
        self._stable_count = 0

    def observe(self, observation: dict[str, Any]) -> SearchSession | None:
        rejection = self._rejection_reason(observation)
        if rejection:
            self.last_rejection = rejection
            self.last_rejection_code = self._rejection_code()
            self._last_key = None
            self._stable_count = 0
            return None
        session = SearchSession.from_observation(
            self.request,
            observation,
            search_id=self.search_id,
        )
        self.last_observation = dict(observation)
        key = session.stability_key()
        if key == self._last_key:
            self._stable_count += 1
        else:
            self._last_key = key
            self._stable_count = 1
        self.last_session = session
        if self._stable_count >= self.stable_samples:
            self.session = session
            return session
        return None

    def wait(
        self,
        observer: Callable[[], dict[str, Any]],
        *,
        interval_seconds: float = 0.35,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        tolerate: Callable[[Exception], bool] | None = None,
    ) -> SearchSession:
        deadline = clock() + self.timeout_seconds
        last_error = ""
        while clock() < deadline:
            sleep(interval_seconds)
            try:
                observation = observer()
            except Exception as exc:
                if tolerate and tolerate(exc):
                    last_error = str(exc)
                    self._last_key = None
                    self._stable_count = 0
                    continue
                raise
            session = self.observe(observation)
            if session is not None:
                return session
        detail = self.last_rejection or last_error or "没有观察到可验证的新检索页面"
        raise SearchTransitionTimeout(
            f"无法确认新检索已经替换旧结果：{detail}",
            code=self.last_rejection_code or "SEARCH_TRANSITION_UNCONFIRMED",
        )

    def _rejection_reason(self, observation: dict[str, Any]) -> str:
        if bool(observation.get("loading")):
            return "页面仍处于加载状态"
        if bool(self.previous.get("markerApplied")) and bool(
            observation.get("transitionMarkerPresent")
        ):
            return "旧结果容器的检索事务标记仍然存在"
        if not bool(observation.get("ready")):
            return "页面 DOM 尚未就绪"
        if not bool(observation.get("resultsRootPresent")):
            return "结果容器尚未出现"
        query_value = str(observation.get("queryValue") or "")
        if _normalized_text(query_value) != _normalized_text(self.request.query):
            return (
                f"搜索框值未更新：{query_value!r} != {self.request.query!r}"
            )
        condition_text = str(observation.get("conditionsText") or "")
        if condition_text and _normalized_text(self.request.query) not in _normalized_text(
            condition_text
        ):
            return "检索条件区域仍显示旧关键词"
        hidden_keywords = str(observation.get("hiddenKeywords") or "")
        if hidden_keywords and _normalized_text(
            hidden_keywords
        ) != _normalized_text(self.request.query):
            return (
                f"隐藏关键词未更新：{hidden_keywords!r} != {self.request.query!r}"
            )
        library = str(
            observation.get("currentLibrary")
            or observation.get("hiddenLibrary")
            or ""
        )
        if library != self.request.expected_library_code:
            return (
                f"检索子库未应用：{library or '<missing>'} != "
                f"{self.request.expected_library_code}"
            )
        search_type = str(observation.get("searchType") or "")
        if not search_type_matches(
            search_type,
            self.request.expected_search_types,
        ):
            return (
                f"检索字段未应用：{search_type or '<missing>'} 不属于 "
                f"{list(self.request.expected_search_types)}"
            )
        match_type = str(observation.get("matchType") or "")
        if match_type and _normalized_text(match_type) not in {
            "exact",
            "精确",
        }:
            return f"检索匹配方式未保持精确：{match_type}"
        if int(observation.get("page") or 0) != 1:
            return f"新检索页码未重置为 1：{observation.get('page')}"
        filter_values = {
            "year": (
                self.request.year,
                observation.get("actualYear"),
            ),
            "province": (
                self.request.province,
                observation.get("actualProvince"),
            ),
            "timeliness": (
                self.request.timeliness,
                observation.get("actualTimeliness"),
            ),
        }
        for key in self.required_filter_keys:
            expected, actual = filter_values[key]
            if not filter_values_match(expected, actual):
                return (
                    f"{key} 筛选未应用：{actual or '<missing>'} != {expected}"
                )
        if self.require_page_size:
            actual_page_size = int(observation.get("actualPageSize") or 0)
            if actual_page_size != self.request.requested_page_size:
                return (
                    f"每页条数未应用：{actual_page_size} != "
                    f"{self.request.requested_page_size}"
                )
            shape_error = page_size_shape_error(
                page=int(observation.get("page") or 0),
                total_pages=max(1, int(observation.get("totalPages") or 1)),
                result_count=max(0, int(observation.get("resultCount") or 0)),
                row_count=len(list(observation.get("rows") or [])),
                page_size=actual_page_size,
            )
            if shape_error:
                return shape_error
        if self.require_ungrouped and (
            str(observation.get("groupingMode") or "") != "ungrouped"
        ):
            return (
                "结果仍未切换为不分组："
                f"{observation.get('groupingMode') or '<missing>'}"
            )
        return ""

    def _rejection_code(self) -> str:
        if "检索子库未应用" in self.last_rejection:
            return "SEARCH_LIBRARY_MISMATCH"
        if self.require_ungrouped and "不分组" in self.last_rejection:
            return "RESULT_GROUPING_UNAVAILABLE"
        if self.require_page_size and (
            "每页条数" in self.last_rejection
            or "行数与页大小" in self.last_rejection
            or "页大小无效" in self.last_rejection
        ):
            return "PAGE_SIZE_NOT_APPLIED"
        if self.required_filter_keys and "筛选未应用" in self.last_rejection:
            return "FILTER_NOT_APPLIED"
        return "SEARCH_TRANSITION_UNCONFIRMED"


def require_search_session(
    state: dict[str, Any],
    request: SearchRequestIdentity,
    *,
    expected_page: int | None = None,
    require_refinements: bool = False,
) -> SearchSession:
    payload = state.get("searchSession")
    session = (
        SearchSession.from_dict(payload)
        if isinstance(payload, dict)
        else None
    )
    if session is None:
        raise SearchSessionError("当前页面缺少可验证的 SearchSession")
    if not session.matches_request(
        request,
        expected_page=expected_page,
        require_refinements=require_refinements,
    ):
        raise SearchSessionError(
            "当前结果与请求的查询、检索字段、子库或页码不一致"
        )
    return session


def checkpoint_requires_controlled_research(
    checkpoint: dict[str, Any],
    *,
    has_existing_items: bool = False,
    has_existing_batches: bool = False,
) -> bool:
    identity = checkpoint.get("searchIdentity")
    if isinstance(identity, dict) and SearchSession.from_dict(identity) is not None:
        return False
    previous_work = (
        has_existing_items
        or has_existing_batches
        or bool(checkpoint.get("scannedPages"))
        or bool(checkpoint.get("collectedRows"))
        or str(checkpoint.get("currentAction") or "")
        in {"search", "read-results", "next-page", "results-complete"}
    )
    return bool(previous_work)
