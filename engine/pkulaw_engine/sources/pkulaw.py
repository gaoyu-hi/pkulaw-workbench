from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ..body_quality import (
    BodyCompletenessError,
    assess_body_completeness,
    detail_body_extraction_js,
)
from ..browser_bridge import BrowserBridge
from ..logging_utils import JsonLineLogger
from ..policy import extract_policy_id
from ..storage import ValidationError
from .access_state import read_stable_access_state
from .page_navigation import PageNavigationError, transition_next_page
from .selector_profile import (
    SelectorProfile,
    SelectorProfileError,
    build_control_resolver_js,
    detect_selector_profile,
    diagnose_for_operation,
    diagnose_selector_profile,
    load_selector_profile,
    load_selector_profiles,
    operation_required_controls,
)
from .search_transaction import (
    SearchRequestIdentity,
    SearchSession,
    SearchTransaction,
    SearchTransitionTimeout,
    clean_filter_label,
    filter_values_match,
    result_signature,
)
from .search_mode import resolve_search_mode, search_type_matches


V6_FILTER_TREE_SELECTORS: dict[str, str] = {
    "year": (
        '#leftContent .recursion-tree:has(a.fold[searchname="IssueDate"])'
    ),
    "province": (
        "#leftContent .recursion-tree"
        ':has(a.fold[searchname="IssueDepartment"])'
    ),
    "timeliness": (
        "#leftContent .recursion-tree"
        ':has(a.fold[searchname="TimelinessDic"])'
    ),
}


class SourceAccessError(RuntimeError):
    code = "SOURCE_ACCESS_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class LoginRequired(SourceAccessError):
    code = "LOGIN_REQUIRED"


class ManualActionRequired(SourceAccessError):
    code = "MANUAL_ACTION_REQUIRED"


class InstitutionQuotaReached(SourceAccessError):
    code = "INSTITUTION_QUOTA_REACHED"


class PageStructureChanged(SourceAccessError):
    code = "PAGE_STRUCTURE_CHANGED"


class SelectorProfileMismatch(PageStructureChanged):
    code = "PAGE_SELECTOR_MISMATCH"


class SelectorUnstable(PageStructureChanged):
    code = "PAGE_SELECTOR_UNSTABLE"


class DownloadUnclear(SourceAccessError):
    code = "DOWNLOAD_UNCLEAR"


class DetailPageUnavailable(SourceAccessError):
    code = "DETAIL_PAGE_UNAVAILABLE"


class YearFilterUnavailable(SourceAccessError):
    code = "YEAR_FILTER_UNAVAILABLE"


class FilterNotApplied(SourceAccessError):
    code = "FILTER_NOT_APPLIED"


class PageSizeNotApplied(SourceAccessError):
    code = "PAGE_SIZE_NOT_APPLIED"


class ResultGroupingUnavailable(SourceAccessError):
    code = "RESULT_GROUPING_UNAVAILABLE"


class SearchTransitionUnconfirmed(ManualActionRequired):
    code = "SEARCH_TRANSITION_UNCONFIRMED"


class SearchLibraryMismatch(SourceAccessError):
    code = "SEARCH_LIBRARY_MISMATCH"


class ResultsStale(SourceAccessError):
    code = "RESULTS_STALE"


class ResultWindowTruncated(SourceAccessError):
    code = "RESULT_WINDOW_TRUNCATED"


def _is_page_context_transition(exc: Exception) -> bool:
    message = str(exc)
    return (
        exc.__class__.__name__ == "ContextLostError"
        or exc.__class__.__name__ == "PageDisconnectedError"
        or "页面被刷新" in message
        or "页面已刷新" in message
        or "与页面的连接已断开" in message
        or "context was destroyed" in message.lower()
    )


class PkulawAdapter:
    BATCH_ZIP_COOLDOWN_SECONDS = 65.0

    def __init__(
        self,
        bridge: BrowserBridge,
        logger: JsonLineLogger,
        selector_path: Path | None = None,
    ) -> None:
        self.bridge = bridge
        self.logger = logger
        self.profiles = (
            (load_selector_profile(selector_path),)
            if selector_path
            else load_selector_profiles()
        )
        self.selector_profile: SelectorProfile | None = None
        self.selector_diagnostics: dict[str, Any] = {}
        self.selectors: dict[str, Any] = {}
        if selector_path:
            self.selector_profile = self.profiles[0]
            self.selectors = dict(self.selector_profile.selectors)
        self.search_url = ""
        self.current_search_request: SearchRequestIdentity | None = None
        self.current_search_session: SearchSession | None = None
        self._last_batch_zip_request_at: float | None = None

    def diagnose_current_page(self) -> dict[str, Any]:
        with self.bridge.transaction() as tab:
            return self._diagnose_current_page(tab)

    def diagnose_current_page_stable(
        self,
        *,
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        diagnostics = self.diagnose_current_page()
        if (
            not diagnostics.get("detected")
            or diagnostics.get("pageKind") != "results"
            or diagnostics.get("ready")
        ):
            return diagnostics
        result_controls = {
            "resultLink",
            "resultNumber",
            "batchCheckbox",
            "resultCount",
        }
        if not result_controls.intersection(
            diagnostics.get("missingControls") or []
        ):
            return diagnostics
        try:
            return diagnose_for_operation(
                self.diagnose_current_page,
                "select-batch",
                timeout_seconds=timeout_seconds,
                interval_seconds=0.25,
                stable_samples=2,
            )
        except SelectorProfileError:
            return self.diagnose_current_page()

    def audit_current_page(self) -> dict[str, Any]:
        with self.bridge.transaction() as tab:
            diagnostics = self._diagnose_current_page(tab)
            access = self._read_access_state(tab)
            page_kind = str(diagnostics.get("pageKind") or "unknown")
            results = None
            detail = None
            if page_kind == "results":
                results = self._read_results(tab)
            elif page_kind == "detail":
                detail = self._read_detail(tab)
            controls = dict(diagnostics.get("controls") or {})
            collection_gate_ready = bool(diagnostics.get("ready"))
            return {
                "profileId": diagnostics.get("profileId", ""),
                "version": diagnostics.get("version", ""),
                "pageKind": page_kind,
                "pageReady": bool(diagnostics.get("pageReady")),
                "access": {
                    key: access.get(key)
                    for key in ("url", "captcha", "login", "loggedIn", "quota", "pageError")
                },
                "diagnostics": diagnostics,
                "results": results,
                "detail": detail,
                "downloadAvailable": bool(
                    dict(controls.get("downloadWord") or {}).get("ok")
                ),
                "collectionGateReady": collection_gate_ready,
                "collectionGateError": (
                    None
                    if collection_gate_ready
                    else {
                        "code": "PAGE_SELECTOR_MISMATCH",
                        "message": str(
                            diagnostics.get("message")
                            or "当前页面未通过采集启动门禁"
                        ),
                        "missingControls": diagnostics.get("missingControls") or [],
                    }
                ),
                "downloaded": False,
            }

    def _diagnose_current_page(self, tab) -> dict[str, Any]:
        try:
            profile, evidence = detect_selector_profile(tab, self.profiles)
        except SelectorProfileError as exc:
            return {
                "profileId": "",
                "version": "",
                "detected": False,
                "ready": False,
                "pageKind": "unknown",
                "pageReady": False,
                "requiredControls": [],
                "pageRequiredControls": [],
                "controls": {},
                "missingControls": ["profile"],
                "pageMissingControls": ["profile"],
                "groups": {},
                "detectionEvidence": [],
                "message": str(exc),
            }
        if profile is None and self.selector_profile is not None:
            profile = self.selector_profile
            evidence = ["explicit-selector-path"]
        diagnostics = diagnose_selector_profile(
            tab,
            profile,
            detection_evidence=evidence,
        )
        if profile is not None:
            self.selector_profile = profile
            self.selectors = dict(profile.selectors)
            self.selector_diagnostics = diagnostics
            self.logger.write(
                "info" if diagnostics.get("ready") else "warn",
                "source.selector_diagnostics",
                "Pkulaw selector diagnostics completed",
                diagnostics,
            )
        return diagnostics

    def _require_selector_ready(self, tab) -> SelectorProfile:
        diagnostics = self._diagnose_current_page(tab)
        if not diagnostics.get("ready"):
            missing = ", ".join(diagnostics.get("missingControls") or [])
            raise SelectorProfileMismatch(
                f"北大法宝页面选择器诊断未通过"
                f"（配置={diagnostics.get('profileId') or 'unknown'}，缺少={missing or 'unknown'}）"
            )
        if self.selector_profile is None:
            raise SelectorProfileMismatch("未能识别北大法宝页面选择器配置")
        return self.selector_profile

    def _ensure_operation_ready(self, operation: str) -> dict[str, Any]:
        try:
            diagnostics = diagnose_for_operation(
                self.diagnose_current_page,
                operation,
                timeout_seconds=5.0,
                interval_seconds=0.25,
                stable_samples=2,
            )
        except SelectorProfileError as exc:
            error_type = (
                SelectorUnstable
                if getattr(exc, "code", "") == "PAGE_SELECTOR_UNSTABLE"
                else SelectorProfileMismatch
            )
            raise error_type(str(exc)) from exc
        self.logger.write(
            "info",
            "source.operation_stability",
            "Selector operation reached a stable state",
            {
                "operation": operation,
                "profileId": diagnostics.get("profileId"),
                "version": diagnostics.get("version"),
                "stabilitySamples": diagnostics.get("stabilitySamples"),
                "controls": diagnostics.get("operationControls"),
            },
        )
        return diagnostics

    def search(
        self,
        *,
        keyword: str,
        match_mode: str = "keyword",
        library: str,
        year: str,
        province: str,
        timeliness: str,
        page_size: int,
    ) -> dict[str, Any]:
        resolve_search_mode(match_mode)
        request = SearchRequestIdentity(
            query=keyword,
            match_mode=match_mode,
            library=library,
            year=year,
            province=province,
            timeliness=timeliness,
            requested_page_size=page_size,
        )
        self.current_search_request = request
        self.current_search_session = None
        with self.bridge.transaction() as tab:
            self._ensure_search_page(tab)
        self._ensure_operation_ready("search")
        library_code = {"central": "chl", "local": "lar"}[library]
        self._ensure_library(library_code)
        token = f"search-{uuid.uuid4()}"
        with self.bridge.transaction() as tab:
            previous = self._mark_search_transition(tab, token)
        transaction = SearchTransaction(
            request,
            token=token,
            previous=previous,
            stable_samples=2,
            timeout_seconds=25,
        )
        with self.bridge.transaction() as tab:
            submitted = self._submit_search(
                tab,
                request=request,
                library_code=library_code,
            )
        if not submitted.get("ok"):
            if submitted.get("code") == "YEAR_FILTER_UNAVAILABLE":
                raise YearFilterUnavailable(
                    f"当前页面没有可用的公布年份筛选：{year}"
                )
            raise SearchTransitionUnconfirmed(
                f"无法提交可验证的检索：{submitted!r}"
            )
        session = self._wait_for_search_transaction(transaction)
        state = self._attach_search_session(
            dict(transaction.last_observation),
            session,
        )
        session, state = self._apply_search_refinements(
            request=request,
            session=session,
            state=state,
        )
        if not session.is_verified_empty_exact_title():
            self._ensure_operation_ready("read-results")
        self.search_url = str(session.url or state.get("url") or "")
        return state

    def select_year(self, *, year: str) -> dict[str, Any]:
        request = self.current_search_request
        session = self.current_search_session
        if request is None or session is None:
            raise YearFilterUnavailable(
                f"当前没有可复用的检索会话，无法切换公布年份：{year}"
            )
        if (
            session.query != request.query
            or session.library != request.library
            or session.match_mode != request.match_mode
        ):
            raise YearFilterUnavailable(
                "当前检索会话与目标年份筛选不兼容"
            )
        target_request = SearchRequestIdentity(
            query=request.query,
            match_mode=request.match_mode,
            library=request.library,
            year=year,
            province=request.province,
            timeliness=request.timeliness,
            requested_page_size=request.requested_page_size,
        )
        state = self.read_results()
        session = self.current_search_session or session
        session, state, _ = self._run_refinement(
            request=target_request,
            session=session,
            state=state,
            action=lambda: self._apply_filter_action("year", year),
            required_filter_keys=("year",),
            require_page_size=True,
            require_ungrouped=True,
        )
        self.current_search_request = target_request
        self.current_search_session = session
        self.search_url = str(session.url or state.get("url") or "")
        if int(state.get("page") or 0) != 1:
            raise YearFilterUnavailable(
                f"公布年份切换后未回到第 1 页：{state.get('page')}"
            )
        return state

    def read_results(self) -> dict[str, Any]:
        self._ensure_operation_ready("read-results")
        with self.bridge.transaction() as tab:
            return self._attach_current_session(self._read_results(tab))

    def next_page(self, expected_page: int) -> dict[str, Any]:
        self._ensure_operation_ready("read-results")
        with self.bridge.transaction() as tab:
            raw_before = self._read_results(tab)
            current = int(raw_before.get("page") or 0)
            session_page = (
                int(self.current_search_session.page or 0)
                if self.current_search_session is not None
                else 0
            )
            if current == expected_page and (
                self.current_search_session is None
                or session_page == expected_page
            ):
                return self._attach_current_session(raw_before)
            if (
                current == expected_page
                and self.current_search_session is not None
                and session_page != expected_page
            ):
                # The site can advance the visible page number before replacing
                # the previous result rows. Treat the old SearchSession as the
                # transition origin so the controlled form fallback must prove
                # a new page identity/signature.
                before = self._attach_search_session(
                    raw_before,
                    self.current_search_session,
                )
                before["page"] = session_page
            else:
                before = self._attach_current_session(raw_before)
            try:
                allow_direct_page_fallback = bool(
                    self.current_search_session
                    and expected_page
                    > int(
                        self.current_search_session.reported_total_pages
                        or self.current_search_session.total_pages
                    )
                    and expected_page
                    <= self.current_search_session.total_pages
                )
                transition = transition_next_page(
                    tab,
                    expected_page=expected_page,
                    selectors=self.selectors,
                    before_state=before,
                    read_state=lambda: self._read_results(tab),
                    ensure_ready=lambda: self._ensure_operation_ready(
                        "read-results"
                    ),
                    read_error_is_transition=_is_page_context_transition,
                    allow_direct_page_fallback=allow_direct_page_fallback,
                    clock=time.monotonic,
                    sleep=time.sleep,
                )
            except PageNavigationError as exc:
                if exc.code == "RESULT_WINDOW_TRUNCATED":
                    raise ResultWindowTruncated(str(exc)) from exc
                if exc.code == "PAGE_STRUCTURE_CHANGED":
                    raise PageStructureChanged(str(exc)) from exc
                raise ManualActionRequired(
                    f"未能核验第 {expected_page} 页；{exc}"
                ) from exc
            observed = self._attach_current_session(transition.state)
            if self.current_search_request and self.current_search_session:
                query_value = str(observed.get("queryValue") or "")
                if (
                    query_value
                    and re.sub(r"\s+", "", query_value)
                    != re.sub(r"\s+", "", self.current_search_request.query)
                ):
                    raise ResultsStale(
                        f"翻页后检索关键词不一致：{query_value}"
                    )
                search_type = str(observed.get("searchType") or "")
                if (
                    search_type
                    and not search_type_matches(
                        search_type,
                        self.current_search_request.expected_search_types,
                    )
                ):
                    raise ResultsStale(
                        f"翻页后检索字段不一致：{search_type}"
                    )
                match_type = str(observed.get("matchType") or "")
                if match_type and match_type.lower() not in {
                    "exact",
                    "精确",
                }:
                    raise ResultsStale(
                        f"翻页后匹配方式不再是精确：{match_type}"
                    )
                library_code = str(observed.get("library") or "")
                if (
                    library_code
                    and library_code
                    != self.current_search_request.expected_library_code
                ):
                    raise ResultsStale(
                        f"翻页后子库不一致：{library_code}"
                    )
                rows = list(observed.get("rows") or [])
                updated_session = replace(
                    self.current_search_session,
                    actual_page_size=int(
                        observed.get("actualPageSize")
                        or observed.get("pageSize")
                        or self.current_search_session.actual_page_size
                    ),
                    page=int(observed.get("page") or expected_page),
                    total_pages=max(
                        1,
                        int(
                            observed.get("totalPages")
                            or self.current_search_session.total_pages
                        ),
                    ),
                    result_count=max(
                        0,
                        int(
                            observed.get("resultCount")
                            or self.current_search_session.result_count
                        ),
                    ),
                    row_count=len(rows),
                    result_signature=result_signature(rows),
                    observed_at=time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(),
                    ),
                    url=str(observed.get("url") or ""),
                )
                if not updated_session.matches_request(
                    self.current_search_request,
                    expected_page=expected_page,
                ):
                    raise ResultsStale(
                        "翻页后的 SearchSession 与当前检索请求不一致"
                    )
                self.current_search_session = updated_session
                observed["searchSession"] = updated_session.to_dict()
                observed["searchId"] = updated_session.search_id
                observed["reportedTotalPages"] = (
                    updated_session.reported_total_pages
                )
                observed["totalPages"] = updated_session.total_pages
            return observed

    def go_to_page(self, target_page: int) -> dict[str, Any]:
        state = self.read_results()
        current = int(state.get("page") or 1)
        if current == target_page:
            return state
        if current > target_page:
            raise PageStructureChanged(
                f"当前在第 {current} 页，不能反向自动跳转到第 {target_page} 页"
            )
        while current < target_page:
            current += 1
            state = self.next_page(current)
        return state

    def select_batch(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        self._ensure_operation_ready("select-batch")
        with self.bridge.transaction() as tab:
            self._raise_for_access(self._read_access_state(tab))
            expected = [
                {
                    "number": int(row.get("number") or 0),
                    "id": str(row.get("policyId") or ""),
                    "title": str(row.get("title") or ""),
                }
                for row in batch
            ]
            expected_json = json.dumps(expected, ensure_ascii=False)
            apply_script = f"""
                return (() => {{
                  const expected = {expected_json};
                  const norm = value => (value || '').replace(/\\s+/g, '').trim();
                  const cards = [...document.querySelectorAll({json.dumps(self.selectors["resultLink"])})]
                    .map(anchor => anchor.closest('.t')).filter(Boolean);
                  const rows = cards.map(card => ({{
                    box: card.querySelector({json.dumps(self.selectors["batchCheckbox"])}),
                    label: card.querySelector({json.dumps(self.selectors["batchSort"])}),
                    anchor: card.querySelector('h4 a[href*=".html"]')
                  }})).filter(row => row.box && row.label && row.anchor);
                  if (rows.length !== cards.length) {{
                    return {{ok:false, reason:'batch row controls incomplete'}};
                  }}
                  const wanted = rows.map(row => expected.some(item =>
                    item.id === row.box.value &&
                    item.number === Number((row.label.innerText || row.label.textContent || '')
                      .replace(/[^0-9]/g, '')) &&
                    norm(item.title) === norm(row.anchor.innerText || row.anchor.textContent)
                  ));
                  if (wanted.filter(Boolean).length !== expected.length) {{
                    return {{ok:false, reason:'selected identities do not match page rows'}};
                  }}
                  rows.forEach((row, index) => {{
                    if (row.box.checked !== wanted[index]) row.box.click();
                  }});
                  const actual = rows.filter(row => row.box.checked).map(row => row.box.value);
                  return {{
                    ok: actual.length === expected.length &&
                      actual.every(value => expected.some(item => item.id === value)),
                    actual
                  }};
                }})()
                """
            observe_script = f"""
                return (() => {{
                  const expected = {expected_json};
                  const norm = value => (value || '').replace(/\\s+/g, '').trim();
                  const cards = [...document.querySelectorAll({json.dumps(self.selectors["resultLink"])})]
                    .map(anchor => anchor.closest('.t')).filter(Boolean);
                  const rows = cards.map(card => ({{
                    box: card.querySelector({json.dumps(self.selectors["batchCheckbox"])}),
                    label: card.querySelector({json.dumps(self.selectors["batchSort"])}),
                    anchor: card.querySelector('h4 a[href*=".html"]')
                  }})).filter(row => row.box && row.label && row.anchor);
                  const actual = rows
                    .filter(row => row.box.checked)
                    .map(row => String(row.box.value || ''));
                  const matches = rows.map(row => ({{
                    number:Number((row.label.innerText || row.label.textContent || '')
                      .replace(/[^0-9]/g, '')),
                    id:String(row.box.value || ''),
                    title:norm(row.anchor.innerText || row.anchor.textContent),
                    checked:!!row.box.checked
                  }}));
                  return {{
                    ok: matches.length >= expected.length &&
                      actual.length === expected.length &&
                      expected.every(item => matches.some(row =>
                        row.number === item.number &&
                        row.id === item.id &&
                        row.title === norm(item.title) &&
                        row.checked
                      )),
                    actual,
                    matches
                  }};
                }})()
                """
            last_observation: dict[str, Any] = {}
            for _correction in range(4):
                applied = tab.run_js(apply_script) or {}
                if not applied.get("ok"):
                    raise PageStructureChanged(
                        f"批量勾选核验失败：{applied!r}"
                    )
                stable_samples = 0
                for _sample in range(12):
                    time.sleep(0.25)
                    last_observation = tab.run_js(observe_script) or {}
                    if not last_observation.get("ok"):
                        break
                    stable_samples += 1
                    if stable_samples >= 12:
                        return {
                            **applied,
                            "stableSamples": stable_samples,
                            "verification": last_observation,
                        }
            raise PageStructureChanged(
                "批量勾选未能保持稳定："
                f"{last_observation!r}"
            )

    def verify_batch_selection(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        self._ensure_operation_ready("select-batch")
        with self.bridge.transaction() as tab:
            self._raise_for_access(self._read_access_state(tab))
            page_state = self._read_results(tab)
            expected = [
                {
                    "number": int(row.get("number") or 0),
                    "id": str(row.get("policyId") or ""),
                    "title": str(row.get("title") or ""),
                }
                for row in batch
            ]
            result = tab.run_js(
                f"""
                return (() => {{
                  const expected = {json.dumps(expected, ensure_ascii=False)};
                  const norm = value => (value || '').replace(/\\s+/g, '').trim();
                  const cards = [...document.querySelectorAll({json.dumps(self.selectors["resultLink"])})]
                    .map(anchor => anchor.closest('.t')).filter(Boolean);
                  const rows = cards.map(card => ({{
                    box: card.querySelector({json.dumps(self.selectors["batchCheckbox"])}),
                    label: card.querySelector({json.dumps(self.selectors["batchSort"])}),
                    anchor: card.querySelector('h4 a[href*=".html"]')
                  }})).filter(row => row.box && row.label && row.anchor);
                  const matches = rows.map(row => ({{
                    number:Number((row.label.innerText || row.label.textContent || '')
                      .replace(/[^0-9]/g, '')),
                    id:String(row.box.value || ''),
                    title:norm(row.anchor.innerText || row.anchor.textContent),
                    checked:!!row.box.checked
                  }}));
                  const exactlyExpected = matches.length >= expected.length &&
                    expected.every(item => matches.some(row =>
                      row.number === item.number &&
                      row.id === item.id &&
                      row.title === norm(item.title)
                    ));
                  const checked = matches.filter(row => row.checked).map(row => row.id);
                  return {{
                    ok: exactlyExpected &&
                      checked.length === expected.length &&
                      expected.every(item => checked.includes(item.id)),
                    matches,
                    checked,
                    expectedCount:expected.length,
                    actualCount:matches.length,
                    page:{int(page_state.get("page") or 0)}
                  }};
                }})()
                """
            ) or {}
            matches = list(result.get("matches") or [])
            if matches:
                normalized_expected = {
                    (
                        int(item["number"]),
                        str(item["id"]),
                        "".join(str(item["title"]).split()),
                    )
                    for item in expected
                }
                normalized_matches = {
                    (
                        int(row.get("number") or 0),
                        str(row.get("id") or ""),
                        "".join(str(row.get("title") or "").split()),
                    )
                    for row in matches
                }
                checked = [
                    str(row.get("id") or "")
                    for row in matches
                    if bool(row.get("checked"))
                ]
                result = {
                    **result,
                    "ok": (
                        len(matches) >= len(expected)
                        and normalized_expected.issubset(normalized_matches)
                        and len(checked) == len(expected)
                        and set(checked) == {
                            str(item["id"]) for item in expected
                        }
                    ),
                    "matches": matches,
                    "checked": checked,
                    "expectedCount": len(expected),
                    "actualCount": len(matches),
                }
            if not result.get("ok"):
                raise PageStructureChanged(
                    f"批次行或勾选身份核验失败：{result!r}"
                )
            return result

    def download_batch_file(
        self,
        *,
        kind: str,
        catalog_format: str = "xls",
        full_format: str = "docx",
    ) -> dict[str, Any]:
        if kind not in {"catalog", "zip"}:
            raise ValidationError("INVALID_BATCH_KIND", "kind must be catalog or zip")
        if (
            kind == "zip"
            and self._last_batch_zip_request_at is not None
        ):
            elapsed = time.monotonic() - self._last_batch_zip_request_at
            remaining = self.BATCH_ZIP_COOLDOWN_SECONDS - elapsed
            if remaining > 0:
                self.logger.write(
                    "info",
                    "source.batch_zip_cooldown",
                    "Waiting before the next batch ZIP request",
                    {
                        "elapsedSeconds": round(elapsed, 2),
                        "waitSeconds": round(remaining, 2),
                    },
                )
                time.sleep(remaining)
        source_catalog_format = "xls"
        self._ensure_operation_ready(f"download-{kind}-trigger")
        with self.bridge.transaction() as tab:
            self._raise_for_access(self._read_access_state(tab))
            button = tab.run_js(
                f"""
                return (() => {{
                  const visible = element => !!(element && element.getClientRects().length &&
                    getComputedStyle(element).visibility !== 'hidden' &&
                    getComputedStyle(element).display !== 'none');
                  const buttons = [...document.querySelectorAll({json.dumps(self.selectors["batchButton"])})];
                  const button = buttons.find(visible) || buttons[0];
                  if (!button) return {{ok:false, reason:'batch download button not found'}};
                  setTimeout(() => button.click(), 100);
                  return {{ok:true}};
                }})()
                """
            ) or {}
            if not button.get("ok"):
                raise PageStructureChanged(f"无法打开批量下载弹窗：{button!r}")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                time.sleep(0.25)
                ready = tab.run_js(
                    f"""
                    return (() => {{
                      const visible = element => !!(element && element.getClientRects().length &&
                        getComputedStyle(element).visibility !== 'hidden' &&
                        getComputedStyle(element).display !== 'none');
                      const dialog = [...document.querySelectorAll({json.dumps(self.selectors["batchDialog"])})]
                        .find(visible);
                      const radios = [...document.querySelectorAll({json.dumps(self.selectors["batchFormat"])})];
                      return {{dialog:!!dialog, radios:radios.length}};
                    }})()
                    """
                ) or {}
                if ready.get("dialog") and int(ready.get("radios") or 0) > 0:
                    break
            else:
                raise ManualActionRequired("批量下载设置弹窗未出现，请检查页面后继续")

            self._ensure_operation_ready(f"download-{kind}-submit")
            target_value = "catalogExcel" if kind == "catalog" else "fullWord"
            submitted = tab.run_js(
                f"""
                return (() => {{
                  const radio = document.querySelector(
                    {json.dumps(self.selectors["batchFormat"])} + '[value="' + {json.dumps(target_value)} + '"]'
                  );
                  if (!radio) return {{ok:false, reason:'download format radio not found'}};
                  if (!radio.checked) radio.click();
                  const catalogInput = document.querySelector({json.dumps(self.selectors["batchCatalogFormatInput"])});
                  if (catalogInput) catalogInput.value = {json.dumps(source_catalog_format)};
                  const confirm = document.querySelector({json.dumps(self.selectors["batchConfirm"])});
                  if (!confirm) return {{ok:false, reason:'batch confirm button not found'}};
                  const checked = [...document.querySelectorAll({json.dumps(self.selectors["batchFormat"])})]
                    .filter(item => item.checked).map(item => item.value);
                  setTimeout(() => confirm.click(), 180);
                  return {{ok:true, checked, catalogFormat:{json.dumps(source_catalog_format)}, fullFormat:{json.dumps(full_format)}}};
                }})()
                """
            ) or {}
            if not submitted.get("ok"):
                raise DownloadUnclear(f"批量下载提交失败：{submitted!r}")
            if kind == "zip":
                self._last_batch_zip_request_at = time.monotonic()
            return {
                **submitted,
                "catalogFormat": source_catalog_format,
                "fullFormat": full_format,
            }

    def open_detail(self, row: dict[str, Any]) -> dict[str, Any]:
        with self.bridge.transaction() as tab:
            url = str(row.get("url") or "").strip()
            if not url:
                raise PageStructureChanged("检索结果没有详情 URL")
            tab.get(url)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                time.sleep(0.35)
                state = self._read_access_state(tab)
                self._raise_for_access(state)
                server_error = tab.run_js(
                    """
                    const text = document.body?.innerText || '';
                    return text.includes('服务器无法处理您的请求') ||
                      text.includes('您访问的页面不存在');
                    """
                )
                if server_error:
                    raise DetailPageUnavailable(
                        "详情页返回服务器错误或页面不存在"
                    )
                ready = tab.run_js(
                    f"return !!document.querySelector({json.dumps(self.selectors['detailTitle'])});"
                )
                if ready:
                    self._ensure_operation_ready("read-detail")
                    return self.read_detail()
            raise ManualActionRequired("详情页未完成加载；请检查页面后继续")

    def read_detail(self) -> dict[str, Any]:
        self._ensure_operation_ready("read-detail")
        with self.bridge.transaction() as tab:
            return self._read_detail(tab)

    def _read_detail(self, tab) -> dict[str, Any]:
        access = self._read_access_state(tab)
        self._raise_for_access(access)
        details = tab.run_js(
            f"""
            return (() => {{
              const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
              const query = selector => {{
                if (!selector) return null;
                try {{
                  return document.querySelector(selector);
                }} catch (_) {{
                  return null;
                }}
              }};
              const titleElement = query({json.dumps(self.selectors["detailTitle"])});
              let title = '';
              if (titleElement) {{
                const clone = titleElement.cloneNode(true);
                for (const extra of clone.querySelectorAll(
                  '.en, .btnAudio, .tips, [class*=english], [class*=English]'
                )) extra.remove();
                title = clean(clone.innerText || clone.textContent);
              }}
              const fields = [...document.querySelectorAll(
                {json.dumps(self.selectors["detailFields"])}
              )];
              const field = label => {{
                const item = fields.find(element => {{
                  const strong = element.querySelector('strong');
                  return strong && clean(strong.innerText || strong.textContent)
                    .replace(/[：:]\\s*$/, '').trim() === label;
                }});
                if (!item) return '';
                const box = item.querySelector('.box') || item;
                const valueBox = box.cloneNode(true);
                for (const extra of valueBox.querySelectorAll(
                  'strong, a.subscription, i, .subscription'
                )) extra.remove();
                const visibleValue = clean(valueBox.innerText || valueBox.textContent)
                  .replace(/^[:：\\s]+/, '').trim();
                if (visibleValue) return visibleValue;
                const titled = box.querySelector('[title]');
                if (titled) return clean(titled.getAttribute('title'));
                return clean(item.getAttribute('title'));
              }};
              {detail_body_extraction_js(str(self.selectors["detailBody"]))}
              return {{
                title,
                body,
                bodySegments,
                issuingAuthority: field('制定机关'),
                documentNumber: field('发文字号'),
                publicationDate: field('公布日期'),
                effectiveDate: field('施行日期'),
                validity: field('时效性'),
                effectLevel: field('效力位阶'),
                url: location.href,
                downloadAvailable: !!query({json.dumps(self.selectors["downloadWord"])})
              }};
            }})()
            """
        ) or {}
        if not details.get("title"):
            raise PageStructureChanged("详情页标题为空")
        assessment = assess_body_completeness(
            str(details.get("body") or ""),
        )
        details["bodyCompleteness"] = assessment.to_dict()
        if assessment.status != "complete":
            raise BodyCompletenessError(assessment)
        return details

    def click_word_download(self) -> dict[str, Any]:
        self._ensure_operation_ready("download-word")
        with self.bridge.transaction() as tab:
            access = self._read_access_state(tab)
            self._raise_for_access(access)
            result = tab.run_js(
                f"""
                return (() => {{
                  const visible = element => !!(element && element.getClientRects().length &&
                    getComputedStyle(element).visibility !== 'hidden' &&
                    getComputedStyle(element).display !== 'none');
                  let element = document.querySelector({json.dumps(self.selectors["downloadWord"])});
                  if (element) {{
                    const menu = element.closest('.tab-list');
                    const trigger = menu && menu.querySelector(':scope > span.text');
                    if (trigger && !visible(element)) trigger.click();
                    element.click();
                    return JSON.stringify({{ok:true, method:'fullWord'}});
                  }}
                  const buttons = [...document.querySelectorAll('a,button,[role="button"]')];
                  element = buttons.find(item => (item.innerText || item.textContent || '')
                    .replace(/\\s+/g, '').includes('下载WORD'));
                  if (element) {{
                    element.click();
                    return JSON.stringify({{
                      ok:true,
                      method:'download-word-text'
                    }});
                  }}
                  return JSON.stringify({{
                    ok:false,
                    reason:'word download control not found'
                  }});
                }})()
                """
            ) or {}
            if isinstance(result, str):
                try:
                    decoded = json.loads(result)
                except json.JSONDecodeError as exc:
                    raise DownloadUnclear(
                        f"Word 下载点击结果无法解析：{result[:200]}"
                    ) from exc
                result = decoded if isinstance(decoded, dict) else {}
            if not result.get("ok"):
                raise DownloadUnclear(f"未找到 Word 下载入口：{result!r}")
            return result

    def return_to_results(self) -> None:
        if not self.search_url:
            raise PageStructureChanged("缺少当前检索结果地址")
        with self.bridge.transaction() as tab:
            tab.get(self.search_url)
            time.sleep(0.5)
            self._raise_for_access(self._read_access_state(tab))

    def _ensure_library(self, library_code: str) -> None:
        attribute = str(self.selectors.get("libraryAttribute") or "library")
        root_selector = str(self.selectors.get("libraryRoot") or "")
        library_home_url = str(self.selectors.get("libraryHomeUrl") or "").strip()
        routes = self.selectors.get("libraryRoutes")
        library_routes = routes if isinstance(routes, dict) else {}
        library_route = str(library_routes.get(library_code) or "").strip()
        navigation_attempted = False
        stable_samples = 0
        deadline = time.monotonic() + 8
        last: dict[str, Any] = {}
        hidden_values: list[str] = []
        while time.monotonic() < deadline:
            try:
                with self.bridge.transaction() as tab:
                    last = tab.run_js(
                        f"""
                    return (() => {{
                      const root = document.querySelector({json.dumps(root_selector)});
                      if (!root) return {{ok:false, reason:'library root missing'}};
                      const link = root.querySelector(
                        'a[' + {json.dumps(attribute)} + '="' + {json.dumps(library_code)} + '"]'
                      );
                      if (!link) return {{ok:false, reason:'target library missing'}};
                      const item = link.closest('li');
                      const current = !!(
                        link.classList.contains('current') ||
                        (item && item.classList.contains('current'))
                      );
                      const hidden = document.querySelector(
                        'input[name="Library"], input[name="library"]'
                      );
                      const hiddenValues = [...document.querySelectorAll(
                        'input[name="Library"], input[name="library"]'
                      )].map(input => input.value).filter(Boolean);
                      if (!current) {{
                        setTimeout(() => link.click(), 0);
                        return {{
                          ok:true,
                          changed:true,
                          library:{json.dumps(library_code)},
                          hiddenLibrary:hidden?.value || '',
                          hiddenValues
                        }};
                      }}
                      return {{
                        ok:true,
                        changed:false,
                        library:{json.dumps(library_code)},
                        hiddenLibrary:hidden?.value || '',
                        hiddenValues
                      }};
                    }})()
                    """
                    ) or {}
                    hidden_values = [
                        str(value)
                        for value in (last.get("hiddenValues") or [])
                        if str(value)
                    ]
            except Exception as exc:
                if not _is_page_context_transition(exc):
                    raise
                time.sleep(0.35)
                continue
            if last.get("ok") and not last.get("changed"):
                hidden = str(last.get("hiddenLibrary") or "")
                mismatched_hidden = [
                    value for value in hidden_values if value != library_code
                ]
                if mismatched_hidden or (hidden and hidden != library_code):
                    raise SearchTransitionUnconfirmed(
                        f"子库隐藏字段未切换："
                        f"{mismatched_hidden or [hidden]} != {library_code}"
                    )
                stable_samples += 1
                if stable_samples >= 2:
                    return
                time.sleep(0.25)
                continue
            stable_samples = 0
            navigation_url = library_home_url or library_route
            if navigation_url and not navigation_attempted:
                navigation_attempted = True
                with self.bridge.transaction() as tab:
                    tab.get(navigation_url)
                    time.sleep(0.5)
                    try:
                        link = tab.ele(
                            f"css:a[{attribute}=\"{library_code}\"]",
                            timeout=3,
                        )
                        link.click()
                    except Exception as exc:
                        self.logger.write(
                            "warn",
                            "source.library_switch_click_failed",
                            str(exc),
                            {
                                "library": library_code,
                                "navigationUrl": navigation_url,
                            },
                        )
                time.sleep(0.5)
                continue
            time.sleep(0.35)
        raise SearchTransitionUnconfirmed(
            f"提交检索前无法确认目标子库：{library_code}；最后状态={last!r}"
        )

    def _mark_search_transition(
        self,
        tab,
        token: str,
    ) -> dict[str, Any]:
        results_root = str(self.selectors.get("resultsRoot") or "")
        result_link = str(self.selectors.get("resultLink") or "")
        result_count = str(self.selectors.get("resultCount") or "")
        group_control = str(self.selectors.get("groupUngroup") or "")
        return tab.run_js(
            f"""
            return (() => {{
              const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
              const query = selector => {{
                if (!selector) return null;
                try {{
                  return document.querySelector(selector);
                }} catch (_) {{
                  return null;
                }}
              }};
              const root = document.querySelector({json.dumps(results_root)});
              const markerId = 'pkulaw-search-transition-' + {json.dumps(token)};
              if (root) {{
                document.querySelectorAll('[data-pkulaw-search-transition]')
                  .forEach(marker => marker.remove());
                const marker = document.createElement('span');
                marker.id = markerId;
                marker.setAttribute('data-pkulaw-search-transition', {json.dumps(token)});
                marker.hidden = true;
                marker.style.display = 'none';
                root.appendChild(marker);
              }}
              const links = [...document.querySelectorAll({json.dumps(result_link)})];
              const total = document.querySelector({json.dumps(result_count)});
              const pageIndex = document.querySelector(
                '#right_form input[name="Pager.PageIndex"], input[name="Pager.PageIndex"]'
              );
              const pageSize = document.querySelector(
                'input[name="Pager.PageSize"], select[name="pageSize"], .articleSelect'
              );
              const group = query({json.dumps(group_control)});
              return {{
                markerApplied: !!root,
                markerId,
                url: location.href,
                page: Number(pageIndex?.value || 0) + 1,
                resultCount: total
                  ? Number((total.innerText || total.textContent || '').replace(/[^0-9]/g, ''))
                  : links.length,
                resultIds: links.map(link =>
                  link.getAttribute('data-policy-id') ||
                  link.closest('.t')?.querySelector('input[name="recordList"]')?.value ||
                  link.href
                ),
                pageSize: Number(pageSize?.value || 0) || null,
                groupingMode: group?.checked ? 'ungrouped' : 'grouped'
              }};
            }})()
            """
        ) or {
            "markerApplied": False,
            "markerId": "",
            "url": str(tab.url or ""),
            "page": 0,
            "resultCount": 0,
            "resultIds": [],
            "pageSize": None,
            "groupingMode": "unknown",
        }

    def _submit_search(
        self,
        tab,
        *,
        request: SearchRequestIdentity,
        library_code: str,
    ) -> dict[str, Any]:
        mode = resolve_search_mode(request.match_mode)
        title_field = mode.search_keyword_type == "Title"
        mode_result = self._ensure_search_mode(
            tab,
            match_mode=request.match_mode,
            expected_search_types=request.expected_search_types,
        )
        if not mode_result.get("ok"):
            return mode_result
        actions = self.selectors.get("librarySearchActions")
        action_map = actions if isinstance(actions, dict) else {}
        expected_library_action = str(
            action_map.get(library_code) or ""
        ).strip()
        resolver_js = build_control_resolver_js(self.selector_profile)
        return tab.run_js(
            f"""
            return (() => {{
              {resolver_js}
              const inputResult = resolveControl("searchInput");
              const submitResult = resolveControl("searchSubmit");
              const input = inputResult.element;
              const submit = submitResult.element;
              if (!input || !submit) return {{
                ok:false,
                reason:'search controls not found',
                missing: [
                  ...(input ? [] : ["searchInput"]),
                  ...(submit ? [] : ["searchSubmit"]),
                ]
              }};
              const setControl = (selector, value) => {{
                if (!selector) return false;
                let control = null;
                try {{
                  control = document.querySelector(selector);
                }} catch (_) {{
                  return false;
                }}
                if (!control) return false;
                if (control.tagName === 'SELECT') {{
                  const option = [...control.options].find(item =>
                    item.value === String(value) ||
                    (item.textContent || '').trim() === String(value)
                  );
                  if (!option) return false;
                  control.value = option.value;
                  control.dispatchEvent(new Event('change', {{bubbles:true}}));
                  return true;
                }}
                if (control.type === 'radio' || control.type === 'checkbox') {{
                  if (!control.checked) control.click();
                  return control.checked;
                }}
                control.value = value;
                control.dispatchEvent(new Event('change', {{bubbles:true}}));
                return true;
              }};
              document.querySelectorAll('input[name="Library"], input[name="library"]')
                .forEach(control => {{
                  control.value = {json.dumps(library_code)};
                  control.dispatchEvent(new Event('change', {{bubbles:true}}));
                }});
              const libraryStateFields = new Set([
                'ClassFlag',
                'PreviousLib',
                'LastLibForChangeColumn'
              ]);
              document.querySelectorAll('input[name]')
                .forEach(control => {{
                  if (!libraryStateFields.has(control.name)) return;
                  control.value = {json.dumps(library_code)};
                  control.dispatchEvent(new Event('change', {{bubbles:true}}));
                }});
              const mainForm = document.querySelector('#main_form');
              const expectedLibraryAction = {json.dumps(expected_library_action)};
              if (expectedLibraryAction) {{
                if (!mainForm) return {{
                  ok:false,
                  code:'SEARCH_FORM_UNAVAILABLE',
                  reason:'main search form is unavailable'
                }};
                mainForm.setAttribute('action', expectedLibraryAction);
              }}
              document.querySelectorAll('input[name="Keywords"]')
                .forEach(control => {{ control.value = {json.dumps(request.query, ensure_ascii=False)}; }});
              document.querySelectorAll('input[name="PreKeywords"]')
                .forEach(control => {{ control.value = ''; }});
              document.querySelectorAll('input[name="MatchType"]')
                .forEach(control => {{ control.value = 'Exact'; }});
              const titleKeywords = {json.dumps(title_field)};
              document.querySelectorAll('input[name="TitleKeywords"]')
                .forEach(control => {{
                  control.value = titleKeywords
                    ? {json.dumps(request.query, ensure_ascii=False)}
                    : '';
                }});
              document.querySelectorAll('input[name="FullTextKeywords"]')
                .forEach(control => {{
                  control.value = titleKeywords
                    ? ''
                    : {json.dumps(request.query, ensure_ascii=False)};
                }});
              document.querySelectorAll('input[name="Pager.PageIndex"]')
                .forEach(control => {{ control.value = '0'; }});
              const typeControls = [...document.querySelectorAll(
                'input[name="SearchKeywordType"], select[name="SearchKeywordType"]'
              )];
              const allowedTypes = {json.dumps(list(request.expected_search_types))};
              const sameSearchType = value =>
                allowedTypes.some(item =>
                  String(item || '').toLowerCase() ===
                  String(value || '').toLowerCase()
                );
              const selectedType = typeControls.find(control =>
                sameSearchType(control.value) &&
                (control.type !== 'radio' || control.checked)
              ) || typeControls.find(control => sameSearchType(control.value));
              let resolvedType = selectedType || null;
              if (!resolvedType) {{
                for (const control of typeControls) {{
                  if (control.tagName !== 'SELECT') continue;
                  const targetOption = [...control.options].find(option =>
                    sameSearchType(option.value)
                  );
                  if (!targetOption) continue;
                  control.value = targetOption.value;
                  control.dispatchEvent(new Event('change', {{bubbles:true}}));
                  resolvedType = control;
                  break;
                }}
              }}
              if (!resolvedType) return {{
                ok:false,
                code:'SEARCH_FIELD_UNAVAILABLE',
                reason:'target search keyword type is unavailable',
                available:typeControls.map(control => String(control.value || ''))
              }};
              if (resolvedType.type === 'radio' || resolvedType.type === 'checkbox') {{
                if (!resolvedType.checked) resolvedType.click();
              }} else {{
                if (!sameSearchType(resolvedType.value)) {{
                  resolvedType.value = allowedTypes[0];
                  resolvedType.dispatchEvent(new Event('change', {{bubbles:true}}));
                }}
              }}
              document.querySelectorAll('input[name="SearchKeywordType"]')
                .forEach(control => {{ control.value = resolvedType.value; }});
              input.value = {json.dumps(request.query, ensure_ascii=False)};
              input.dispatchEvent(new Event('input', {{bubbles:true}}));
              input.dispatchEvent(new Event('change', {{bubbles:true}}));
              setTimeout(() => {{
                if (mainForm) mainForm.submit();
                else submit.click();
              }}, 150);
              return {{
                ok:true,
                keyword:{json.dumps(request.query, ensure_ascii=False)},
                library:{json.dumps(request.library)},
                matchMode:{json.dumps(request.match_mode)},
                searchType:resolvedType.value,
                matchType:'Exact',
                searchInputStrategy:inputResult.strategy,
                searchSubmitStrategy:submitResult.strategy,
                exactMode:true,
                formAction:mainForm?.action || '',
                expectedLibraryAction,
                formData:mainForm
                  ? Object.fromEntries(new FormData(mainForm).entries())
                  : {{}}
              }};
            }})()
            """
        ) or {}

    def _ensure_search_mode(
        self,
        tab,
        *,
        match_mode: str,
        expected_search_types: tuple[str, ...],
        timeout_seconds: float = 5.0,
    ) -> dict[str, Any]:
        mode = resolve_search_mode(match_mode)
        target_selector = str(self.selectors.get("exactMode") or "").strip()
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = tab.run_js(
                f"""
                return (() => {{
                  let target = null;
                  try {{
                    target = document.querySelector({
                      json.dumps(target_selector)
                    });
                  }} catch (_) {{
                    target = null;
                  }}
                  const requireTarget = {json.dumps(bool(target_selector))};
                  if (requireTarget && !target) return {{
                    ok:false,
                    code:'SEARCH_MODE_UNAVAILABLE',
                    reason:'target search mode is unavailable',
                    matchMode:{json.dumps(match_mode)}
                  }};
                  if (target && !target.checked) target.click();
                  const typeControls = [...document.querySelectorAll(
                    'input[name="SearchKeywordType"], select[name="SearchKeywordType"]'
                  )];
                  const allowedTypes = {json.dumps(
                      list(expected_search_types)
                  )};
                  const sameSearchType = value =>
                    allowedTypes.some(item =>
                      String(item || '').toLowerCase() ===
                      String(value || '').toLowerCase()
                    );
                  let resolvedType = typeControls.find(control =>
                    sameSearchType(control.value) &&
                    (
                      !['radio', 'checkbox'].includes(control.type) ||
                      control.checked
                    )
                  ) || null;
                  if (!resolvedType) {{
                    for (const control of typeControls) {{
                      if (control.tagName === 'SELECT') {{
                        const option = [...control.options].find(item =>
                          sameSearchType(item.value)
                        );
                        if (!option) continue;
                        control.value = option.value;
                        control.dispatchEvent(new Event('change', {{bubbles:true}}));
                        resolvedType = control;
                        break;
                      }}
                      if (!sameSearchType(control.value)) continue;
                      if (
                        ['radio', 'checkbox'].includes(control.type) &&
                        !control.checked
                      ) {{
                        control.click();
                      }}
                      resolvedType = control;
                      break;
                    }}
                  }}
                  const areaRoot = document.querySelector('.areajSelect');
                  const areaLinks = areaRoot
                    ? [...areaRoot.querySelectorAll('a[areacode]')]
                    : [];
                  const areaLink = areaLinks.find(link =>
                    sameSearchType(link.getAttribute('areacode'))
                  ) || areaLinks.find(link =>
                    {json.dumps(
                        list(
                            (
                                "标 题",
                                "标题",
                            )
                            if mode.search_keyword_type == "Title"
                            else (
                                "全 文",
                                "全文",
                            )
                        )
                    )}.includes(
                      String(link.innerText || link.textContent || '')
                        .replace(/\\s+/g, '')
                    )
                  );
                  if (areaLink) {{
                    const areaItem = areaLink.closest('li');
                    const areaCurrent = !!(
                      areaLink.classList.contains('current') ||
                      (areaItem && areaItem.classList.contains('current'))
                    );
                    if (!areaCurrent) areaLink.click();
                  }}
                  const available = typeControls.flatMap(control =>
                    control.tagName === 'SELECT'
                      ? [...control.options].map(option => String(option.value || ''))
                      : [String(control.value || '')]
                  );
                  const modeReady = !requireTarget || !!target?.checked;
                  const fieldReady = !!resolvedType;
                  return {{
                    ok:modeReady && fieldReady,
                    checked:target ? target.checked : true,
                    matchMode:{json.dumps(match_mode)},
                    areaCode:areaLink?.getAttribute('areacode') || '',
                    available,
                    selectedType:resolvedType?.value || '',
                    code:modeReady && fieldReady
                      ? 'SEARCH_FIELD_UNAVAILABLE'
                      : 'SEARCH_MODE_UNAVAILABLE',
                    reason:modeReady && fieldReady
                      ? 'search mode selected but field is not ready'
                      : modeReady
                        ? 'target search field is unavailable'
                        : 'target search mode did not stay checked'
                  }};
                }})()
                """
            ) or {}
            if last.get("ok"):
                return last
            time.sleep(0.2)
        return last or {
            "ok": False,
            "code": "SEARCH_MODE_UNAVAILABLE",
            "reason": "search mode did not become stable",
            "matchMode": match_mode,
        }

    def _observe_search_transition(
        self,
        tab,
        token: str,
    ) -> dict[str, Any]:
        state = self._read_results(tab)
        identity = tab.run_js(
            f"""
            return (() => {{
              const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
              const query = selector => {{
                if (!selector) return null;
                try {{
                  return document.querySelector(selector);
                }} catch (_) {{
                  return null;
                }}
              }};
              const input = query({json.dumps(self.selectors.get("searchInput") or "")});
              const resultsRoot = query({json.dumps(self.selectors.get("resultsRoot") or "")});
              const libraryRoot = query({json.dumps(self.selectors.get("libraryRoot") or "")});
              const currentLibraryLink = libraryRoot
                ? [...libraryRoot.querySelectorAll(
                    'a[' + {json.dumps(str(self.selectors.get("libraryAttribute") or "library"))} + ']'
                  )].find(link => {{
                    const item = link.closest('li');
                    return link.classList.contains('current') ||
                      (item && item.classList.contains('current'));
                  }})
                : null;
              const hiddenLibrary = document.querySelector(
                'input[name="Library"], input[name="library"]'
              );
              const hiddenKeywords = document.querySelector(
                'input[name="Keywords"], input[name="Keyword"]'
              );
              const typeControls = [...document.querySelectorAll(
                'input[name="SearchKeywordType"], select[name="SearchKeywordType"]'
              )];
              const selectedType = typeControls.find(control =>
                control.type !== 'radio' || control.checked
              );
              const selectedMatchType = [
                ...document.querySelectorAll(
                  'input[name="MatchType"], select[name="MatchType"]'
                )
              ].find(control => control.type !== 'radio' || control.checked);
              const conditionSelectors = [
                '#searchCondition',
                '.search-condition',
                '.condition-list',
                '.search-result-condition',
                '.result-condition'
              ];
              const condition = conditionSelectors.map(query).find(Boolean);
              const pageIndex = document.querySelector(
                '#right_form input[name="Pager.PageIndex"], input[name="Pager.PageIndex"]'
              );
              const visiblePageSizeElement = document.querySelector('.articleSelect');
              const hiddenPageSize = document.querySelector(
                'input[name="Pager.PageSize"], select[name="pageSize"]'
              );
              const visiblePageSize = Number(
                visiblePageSizeElement?.getAttribute('data-current-size') ||
                (visiblePageSizeElement?.innerText || visiblePageSizeElement?.textContent || '')
                  .match(/(\\d+)\\s*条\\s*\\/\\s*页/)?.[1] ||
                0
              );
              const group = query({json.dumps(self.selectors.get("groupUngroup") or "")});
              const filters = {json.dumps(self.selectors.get("filters") or {}, ensure_ascii=False)};
              const filterItemSelector = {json.dumps(str(self.selectors.get("filterItem") or "a"))};
              const params = new URLSearchParams(location.search);
              const readFilter = (kind, rootSelector, urlNames, allValue) => {{
                const root = query(rootSelector);
                if (!root) return '';
                const select = root.matches?.('select')
                  ? root
                  : root.querySelector?.('select');
                if (select?.value) {{
                  return clean(
                    select.selectedOptions?.[0]?.textContent ||
                    select.value
                  );
                }}
                const activeItem = [...root.querySelectorAll(filterItemSelector)]
                  .find(item => {{
                    const nested = item.closest('li') || item;
                    return (
                      item.classList.contains('current') ||
                      item.classList.contains('selected') ||
                      item.classList.contains('active') ||
                      item.getAttribute('aria-selected') === 'true' ||
                      item.dataset.selected === 'true' ||
                      nested.classList.contains('current') ||
                      nested.classList.contains('selected') ||
                      nested.classList.contains('active')
                    );
                  }});
                if (activeItem) {{
                  return clean(
                    activeItem.getAttribute('title') ||
                    activeItem.querySelector?.('.node_name')?.textContent ||
                    activeItem.textContent
                  );
                }}
                for (const name of urlNames) {{
                  const value = params.get(name);
                  if (value) return decodeURIComponent(value);
                }}
                return allValue;
              }};
              const body = document.body;
              const transitionState = String(
                resultsRoot?.getAttribute('data-transition-state') ||
                body?.getAttribute('data-transition-state') ||
                ''
              );
              const loading = !!(
                document.querySelector(
                  '.loading, .is-loading, [aria-busy="true"], .pkulaw-loading'
                ) ||
                /request-pending|loading|searching/i.test(transitionState)
              );
              return {{
                ready: document.readyState !== 'loading',
                loading,
                resultsRootPresent: !!resultsRoot,
                transitionMarkerPresent: !!document.querySelector(
                  '[data-pkulaw-search-transition="' + {json.dumps(token)} + '"]'
                ),
                queryValue: String(input?.value || ''),
                conditionsText: condition
                  ? clean(condition.innerText || condition.textContent)
                  : '',
                hiddenKeywords: String(hiddenKeywords?.value || ''),
                currentLibrary: String(
                  currentLibraryLink?.getAttribute(
                    {json.dumps(str(self.selectors.get("libraryAttribute") or "library"))}
                  ) || ''
                ),
                hiddenLibrary: String(hiddenLibrary?.value || ''),
                searchType: String(selectedType?.value || ''),
                matchType: String(selectedMatchType?.value || ''),
                pageIndex: pageIndex ? Number(pageIndex.value || 0) + 1 : 0,
                visiblePageSize: Number(visiblePageSize?.value || 0) || 0,
                hiddenPageSize: Number(hiddenPageSize?.value || 0) || 0,
                groupingMode: group?.checked ? 'ungrouped' : 'grouped',
                actualYear: readFilter(
                  'year',
                  filters.year || '',
                  ['IssueDate', 'year', 'Year'],
                  '全部年份'
                ),
                actualProvince: readFilter(
                  'province',
                  filters.province || '',
                  ['IssueDepartment', 'province', 'Province'],
                  '全部省份'
                ),
                actualTimeliness: readFilter(
                  'timeliness',
                  filters.timeliness || '',
                  ['TimelinessDic', 'timeliness', 'Timeliness'],
                  '全部（仅记录元数据，不筛选）'
                ),
                url: location.href
              }};
            }})()
            """
        ) or {}
        expected_page = int(state.get("page") or 0)
        observed_page = int(identity.get("pageIndex") or 0)
        if observed_page and observed_page != expected_page:
            identity["ready"] = False
        identity.update(
            {
                "page": expected_page,
                "totalPages": max(1, int(state.get("totalPages") or 1)),
                "resultCount": int(state.get("resultCount") or 0),
                "actualPageSize": int(
                    identity.get("visiblePageSize")
                    or identity.get("hiddenPageSize")
                    or state.get("pageSize")
                    or 0
                ),
                "rows": list(state.get("rows") or []),
                "resultSignature": result_signature(list(state.get("rows") or [])),
                "url": str(state.get("url") or identity.get("url") or ""),
            }
        )
        return identity

    def _wait_for_search_transaction(
        self,
        transaction: SearchTransaction,
    ) -> SearchSession:
        def observe() -> dict[str, Any]:
            with self.bridge.transaction() as tab:
                return self._observe_search_transition(tab, transaction.token)

        try:
            return transaction.wait(
                observe,
                interval_seconds=0.35,
                tolerate=_is_page_context_transition,
            )
        except SearchTransitionTimeout as exc:
            if exc.code == "SEARCH_LIBRARY_MISMATCH":
                raise SearchLibraryMismatch(str(exc)) from exc
            error = SearchTransitionUnconfirmed(
                str(exc),
                details=dict(getattr(exc, "details", {}) or {}),
            )
            error.code = exc.code
            raise error from exc

    def _apply_search_refinements(
        self,
        *,
        request: SearchRequestIdentity,
        session: SearchSession,
        state: dict[str, Any],
    ) -> tuple[SearchSession, dict[str, Any]]:
        if session.is_verified_empty_exact_title():
            # The live V6 empty-result page removes page-size, grouping, and
            # sometimes filter controls.  The stable base SearchTransaction has
            # already verified the exact query, mode, library, page, and DOM.
            # Returning here lets the single-task layer emit NO_EXACT_MATCH
            # instead of misreporting a refinement-control failure.
            return session, state

        applied_filters: list[str] = []
        for key, value in (
            ("year", request.year),
            ("province", request.province),
            ("timeliness", request.timeliness),
        ):
            session, state, action = self._run_refinement(
                request=request,
                session=session,
                state=state,
                action=lambda key=key, value=value: self._apply_filter_action(
                    key,
                    value,
                ),
                required_filter_keys=tuple(applied_filters + [key]),
            )
            if action.get("changed"):
                applied_filters.append(key)
            else:
                actual = {
                    "year": session.actual_year,
                    "province": session.actual_province,
                    "timeliness": session.actual_timeliness,
                }[key]
                if not filter_values_match(value, actual):
                    if key == "year" and clean_filter_label(actual) == "":
                        raise YearFilterUnavailable(
                            f"当前页面没有可用的公布年份筛选：{value}",
                            details={
                                "filterKey": key,
                                "expected": value,
                                "actual": str(actual or ""),
                            },
                        )
                    raise FilterNotApplied(
                        f"{key} 筛选未生效：{actual or '<missing>'} != {value}",
                        details={
                            "filterKey": key,
                            "expected": value,
                            "actual": str(actual or ""),
                        },
                    )

        session, state, group_action = self._run_refinement(
            request=request,
            session=session,
            state=state,
            action=self._ensure_ungrouped_action,
            required_filter_keys=tuple(applied_filters),
            require_ungrouped=True,
        )
        if group_action.get("changed"):
            pass
        elif session.grouping_mode != "ungrouped":
            raise ResultGroupingUnavailable(
                f"结果仍未切换为不分组：{session.grouping_mode}"
            )

        session, state, page_size_action = self._run_refinement(
            request=request,
            session=session,
            state=state,
            action=lambda: self._apply_page_size_action(
                request.requested_page_size
            ),
            required_filter_keys=tuple(applied_filters),
            require_page_size=True,
            require_ungrouped=True,
        )
        if page_size_action.get("changed"):
            pass
        elif (
            session.actual_page_size != request.requested_page_size
            or not self._page_size_rows_match(
                state,
                expected_page_size=request.requested_page_size,
            )
        ):
            raise PageSizeNotApplied(
                f"每页条数未生效：{session.actual_page_size} != "
                f"{request.requested_page_size}"
            )
        for key, requested, actual in (
            ("year", request.year, session.actual_year),
            ("province", request.province, session.actual_province),
            ("timeliness", request.timeliness, session.actual_timeliness),
        ):
            if not filter_values_match(requested, actual):
                if key == "year":
                    raise YearFilterUnavailable(
                        f"公布年份筛选未生效：{actual or '<missing>'} != {requested}",
                        details={
                            "filterKey": key,
                            "expected": str(requested or ""),
                            "actual": str(actual or ""),
                        },
                    )
                raise FilterNotApplied(
                    f"{key} 筛选未生效：{actual or '<missing>'} != {requested}",
                    details={
                        "filterKey": key,
                        "expected": str(requested or ""),
                        "actual": str(actual or ""),
                    },
                )
        if session.actual_page_size != request.requested_page_size:
            raise PageSizeNotApplied(
                f"每页条数未生效：{session.actual_page_size} != "
                f"{request.requested_page_size}"
            )
        if not self._page_size_rows_match(
            state,
            expected_page_size=request.requested_page_size,
        ):
            raise PageSizeNotApplied("当前页行数与每页条数不一致")
        if session.grouping_mode != "ungrouped":
            raise ResultGroupingUnavailable(
                f"结果仍未切换为不分组：{session.grouping_mode}"
            )
        return session, state

    def _run_refinement(
        self,
        *,
        request: SearchRequestIdentity,
        session: SearchSession,
        state: dict[str, Any],
        action: Any,
        required_filter_keys: tuple[str, ...],
        require_page_size: bool = False,
        require_ungrouped: bool = False,
    ) -> tuple[SearchSession, dict[str, Any], dict[str, Any]]:
        token = f"refine-{uuid.uuid4()}"
        with self.bridge.transaction() as tab:
            previous = self._mark_search_transition(tab, token)
        try:
            result = action()
        except Exception:
            self._clear_search_transition_marker(token)
            raise
        if not result.get("ok"):
            self._clear_search_transition_marker(token)
            raise FilterNotApplied(f"筛选操作无法执行：{result!r}")
        if not result.get("changed"):
            self._clear_search_transition_marker(token)
            return session, state, result
        transaction = SearchTransaction(
            request,
            token=token,
            previous=previous,
            stable_samples=2,
            timeout_seconds=25,
            search_id=session.search_id,
            required_filter_keys=required_filter_keys,
            require_page_size=require_page_size,
            require_ungrouped=require_ungrouped,
        )
        try:
            refined = self._wait_for_search_transaction(transaction)
        except SearchTransitionUnconfirmed as exc:
            code = getattr(exc, "code", "")
            if code == "YEAR_FILTER_UNAVAILABLE" or (
                "year 筛选未应用" in str(exc)
            ):
                raise YearFilterUnavailable(
                    str(exc),
                    details=dict(getattr(exc, "details", {}) or {}),
                ) from exc
            if code == "PAGE_SIZE_NOT_APPLIED":
                raise PageSizeNotApplied(
                    str(exc),
                    details=dict(getattr(exc, "details", {}) or {}),
                ) from exc
            if code == "RESULT_GROUPING_UNAVAILABLE":
                raise ResultGroupingUnavailable(
                    str(exc),
                    details=dict(getattr(exc, "details", {}) or {}),
                ) from exc
            if code == "FILTER_NOT_APPLIED":
                raise FilterNotApplied(
                    str(exc),
                    details=dict(getattr(exc, "details", {}) or {}),
                ) from exc
            raise
        state = self._attach_search_session(
            dict(transaction.last_observation),
            refined,
        )
        return refined, state, result

    def _clear_search_transition_marker(self, token: str) -> None:
        try:
            with self.bridge.transaction() as tab:
                tab.run_js(
                    f"""
                    document.querySelectorAll(
                      '[data-pkulaw-search-transition={json.dumps(token)}]'
                    ).forEach(marker => marker.remove());
                    return true;
                    """
                )
        except Exception:
            return

    def _apply_filter_action(
        self,
        key: str,
        value: str,
    ) -> dict[str, Any]:
        root_selector = str(
            (self.selectors.get("filters") or {}).get(key) or ""
        )
        if str(value).startswith("全部"):
            return {"ok": True, "changed": False}
        if not root_selector:
            code = "YEAR_FILTER_UNAVAILABLE" if key == "year" else "FILTER_NOT_APPLIED"
            return {
                "ok": False,
                "changed": False,
                "code": code,
                "reason": f"{key} filter selector is missing",
            }
        availability = self._wait_for_filter_option(
            key=key,
            value=value,
            root_selector=root_selector,
        )
        if not availability.get("ok"):
            if key == "year":
                raise YearFilterUnavailable(
                    f"当前页面没有可用的公布年份筛选：{value}；"
                    f"{availability!r}",
                    details={
                        "filterKey": key,
                        "expected": value,
                        "available": list(
                            availability.get("available") or []
                        ),
                        "reason": str(
                            availability.get("reason") or ""
                        ),
                    },
                )
            return availability
        with self.bridge.transaction() as tab:
            result = tab.run_js(
                f"""
                return (() => {{
                  const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                  const label = value => clean(value)
                    .replace(/\\s*\\(\\s*\\d+\\s*\\)\\s*$/, '')
                    .replace(/\\s+\\d+\\s*$/, '')
                    .trim();
                  const expected = label({json.dumps(value, ensure_ascii=False)});
                  const root = document.querySelector({json.dumps(root_selector)});
                  if (!root) return {{
                    ok:false,
                    changed:false,
                    code:{json.dumps("YEAR_FILTER_UNAVAILABLE" if key == "year" else "FILTER_NOT_APPLIED")},
                    reason:'filter root missing'
                  }};
                  const select = root.matches?.('select')
                    ? root
                    : root.querySelector?.('select');
                  if (select) {{
                    const option = [...select.options].find(item =>
                      label(item.textContent) === expected ||
                      label(item.value) === expected
                    );
                    if (!option) return {{
                      ok:false,
                      changed:false,
                      code:{json.dumps("YEAR_FILTER_UNAVAILABLE" if key == "year" else "FILTER_NOT_APPLIED")},
                      reason:'exact filter option missing',
                      expected
                    }};
                    const changed = select.value !== option.value;
                    if (changed) {{
                      select.value = option.value;
                      select.dispatchEvent(new Event('change', {{bubbles:true}}));
                      const submit = document.querySelector(
                        {json.dumps(str(self.selectors.get("filterSubmit") or ""))}
                      );
                      if (submit) setTimeout(() => submit.click(), 80);
                    }}
                    return {{ok:true, changed, actual:label(option.textContent)}};
                  }}
                  const items = [...root.querySelectorAll(
                    {json.dumps(str(self.selectors.get("filterItem") or "a"))}
                  )];
                  const item = items.find(candidate => {{
                    const text = label(
                      candidate.getAttribute('title') ||
                      candidate.querySelector?.('.node_name')?.textContent ||
                      candidate.textContent
                    );
                    return text === expected;
                  }});
                  if (!item) return {{
                    ok:false,
                    changed:false,
                    code:{json.dumps("YEAR_FILTER_UNAVAILABLE" if key == "year" else "FILTER_NOT_APPLIED")},
                    reason:'exact filter option missing',
                    expected,
                    available:items.map(candidate => label(
                      candidate.getAttribute('title') ||
                      candidate.querySelector?.('.node_name')?.textContent ||
                      candidate.textContent
                    ))
                  }};
                  const nested = item.closest('li') || item;
                  const clickTarget = (
                    item.matches?.('.node_name')
                      ? item
                      : item.querySelector?.('.node_name')
                  ) || item;
                  const current = !!(
                    item.classList.contains('current') ||
                    item.classList.contains('selected') ||
                    item.classList.contains('active') ||
                    item.getAttribute('aria-selected') === 'true' ||
                    item.dataset.selected === 'true' ||
                    nested.classList.contains('current') ||
                    nested.classList.contains('selected') ||
                    nested.classList.contains('active')
                  );
                  if (!current) {{
                    for (const type of [
                      'pointerdown',
                      'mousedown',
                      'pointerup',
                      'mouseup'
                    ]) {{
                      const EventCtor = (
                        type.startsWith('pointer') && window.PointerEvent
                          ? window.PointerEvent
                          : window.MouseEvent
                      );
                      clickTarget.dispatchEvent(new EventCtor(type, {{
                        bubbles:true,
                        cancelable:true,
                        view:window,
                        button:0
                      }}));
                    }}
                    clickTarget.click();
                  }}
                  return {{
                    ok:true,
                    changed:!current,
                    previous:current ? expected : 'not-selected',
                    actual:expected
                  }};
                }})()
                """
            ) or {}
        if not result.get("ok") and key == "year":
            raise YearFilterUnavailable(
                f"当前页面没有可用的公布年份筛选：{value}；{result!r}",
                details={
                    "filterKey": key,
                    "expected": value,
                    "available": list(result.get("available") or []),
                    "reason": str(result.get("reason") or ""),
                },
            )
        if key == "year" and result.get("changed"):
            selection = self._wait_for_filter_selection(
                key=key,
                value=value,
                root_selector=root_selector,
            )
            if not selection.get("ok"):
                actual = str(selection.get("actual") or "")
                raise YearFilterUnavailable(
                    f"公布年份选择未确认：{actual or '<missing>'} != {value}",
                    details={
                        "filterKey": key,
                        "expected": value,
                        "actual": actual,
                    },
                )
            result.update(
                {
                    "actual": str(selection.get("actual") or value),
                }
            )
        return result

    def _wait_for_filter_selection(
        self,
        *,
        key: str,
        value: str,
        root_selector: str,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        item_selector = str(self.selectors.get("filterItem") or "a")
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            with self.bridge.transaction() as tab:
                last = tab.run_js(
                    f"""
                    return (() => {{
                      const clean = value => (value || '')
                        .replace(/\\s+/g, ' ').trim();
                      const label = value => clean(value)
                        .replace(/\\s*\\(\\s*\\d+\\s*\\)\\s*$/, '')
                        .replace(/\\s+\\d+\\s*$/, '')
                        .trim();
                      const expected = label({
                        json.dumps(value, ensure_ascii=False)
                      });
                      const root = document.querySelector({
                        json.dumps(root_selector)
                      });
                      if (!root) return {{
                        ok:false,
                        expected,
                        actual:'',
                        reason:'filter root missing'
                      }};
                      const select = root.matches?.('select')
                        ? root
                        : root.querySelector?.('select');
                      if (select) {{
                        const actual = label(
                          select.selectedOptions?.[0]?.textContent ||
                          select.value
                        );
                        return {{ok:actual === expected, expected, actual}};
                      }}
                      const items = [...root.querySelectorAll({
                        json.dumps(item_selector)
                      })];
                      const selected = items.find(candidate => {{
                        const nested = candidate.closest('li') || candidate;
                        return (
                          candidate.classList.contains('current') ||
                          candidate.classList.contains('selected') ||
                          candidate.classList.contains('active') ||
                          candidate.getAttribute('aria-selected') === 'true' ||
                          candidate.dataset.selected === 'true' ||
                          nested.classList.contains('current') ||
                          nested.classList.contains('selected') ||
                          nested.classList.contains('active')
                        );
                      }});
                      const actual = selected ? label(
                        selected.getAttribute('title') ||
                        selected.querySelector?.('.node_name')?.textContent ||
                        selected.textContent
                      ) : '全部年份';
                      return {{ok:actual === expected, expected, actual}};
                    }})()
                    """
                ) or {}
            if last.get("ok"):
                return last
            if last.get("actual") not in {"", "全部年份"}:
                return last
            time.sleep(0.25)
        return last or {
            "ok": False,
            "expected": value,
            "actual": "全部年份",
        }

    def _wait_for_filter_option(
        self,
        *,
        key: str,
        value: str,
        root_selector: str,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        if str(value).startswith("全部"):
            return {"ok": True, "changed": False}
        item_selector = str(self.selectors.get("filterItem") or "a")
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            with self.bridge.transaction() as tab:
                last = tab.run_js(
                    f"""
                    return (() => {{
                      const clean = value => (value || '')
                        .replace(/\\s+/g, ' ').trim();
                      const label = value => clean(value)
                        .replace(/\\s*\\(\\s*\\d+\\s*\\)\\s*$/, '')
                        .replace(/\\s+\\d+\\s*$/, '')
                        .trim();
                      const expected = label({
                        json.dumps(value, ensure_ascii=False)
                      });
                      const root = document.querySelector({
                        json.dumps(root_selector)
                      });
                      if (!root) return {{
                        ok:false,
                        changed:false,
                        code:{json.dumps(
                            "YEAR_FILTER_UNAVAILABLE"
                            if key == "year"
                            else "FILTER_NOT_APPLIED"
                        )},
                        reason:'filter root missing',
                        expected
                      }};
                      const select = root.matches?.('select')
                        ? root
                        : root.querySelector?.('select');
                      if (select) {{
                        const option = [...select.options].find(item =>
                          label(item.textContent) === expected ||
                          label(item.value) === expected
                        );
                        return option
                          ? {{ok:true, changed:false, expected}}
                          : {{
                              ok:false,
                              changed:false,
                              code:{json.dumps(
                                  "YEAR_FILTER_UNAVAILABLE"
                                  if key == "year"
                                  else "FILTER_NOT_APPLIED"
                              )},
                              reason:'exact filter option missing',
                              expected,
                              available:[...select.options].map(item =>
                                label(item.textContent)
                              )
                            }};
                      }}
                      const items = [...root.querySelectorAll({
                        json.dumps(item_selector)
                      })];
                      const item = items.find(candidate => {{
                        const text = label(
                          candidate.getAttribute('title') ||
                          candidate.querySelector?.('.node_name')?.textContent ||
                          candidate.textContent
                        );
                        return text === expected;
                      }});
                      return item
                        ? {{ok:true, changed:false, expected}}
                        : {{
                            ok:false,
                            changed:false,
                            code:{json.dumps(
                                "YEAR_FILTER_UNAVAILABLE"
                                if key == "year"
                                else "FILTER_NOT_APPLIED"
                            )},
                            reason:'exact filter option missing',
                            expected,
                            available:items.map(candidate => label(
                              candidate.getAttribute('title') ||
                              candidate.querySelector?.('.node_name')?.textContent ||
                              candidate.textContent
                            ))
                          }};
                    }})()
                    """
                ) or {}
            if last.get("ok"):
                return last
            time.sleep(0.25)
        return last or {
            "ok": False,
            "changed": False,
            "code": (
                "YEAR_FILTER_UNAVAILABLE"
                if key == "year"
                else "FILTER_NOT_APPLIED"
            ),
            "reason": "filter option did not become available",
            "expected": value,
        }

    def _apply_page_size_action(self, page_size: int) -> dict[str, Any]:
        trigger_selector = str(
            self.selectors.get("pageSizeTrigger")
            or self.selectors.get("pageSize")
            or ".articleSelect"
        )
        item_selector = str(
            self.selectors.get("pageSizeItem") or "#recordPage dd"
        )
        with self.bridge.transaction() as tab:
            result = tab.run_js(
                f"""
                return (() => {{
                  const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                  const trigger = document.querySelector({json.dumps(trigger_selector)});
                  const hidden = document.querySelector(
                    {json.dumps(str(self.selectors.get("pageSizeHidden") or "input[name='Pager.PageSize']"))}
                  );
                  const currentVisible = Number(
                    trigger?.getAttribute('data-current-size') ||
                    (trigger?.innerText || trigger?.textContent || '')
                      .match(/(\\d+)\\s*条\\s*\\/\\s*页/)?.[1] ||
                    0
                  );
                  const currentHidden = Number(hidden?.value || 0);
                  if (currentVisible === {page_size} && currentHidden === {page_size}) {{
                    return {{
                      ok:true,
                      changed:false,
                      visible:{page_size},
                      hidden:{page_size}
                    }};
                  }}
                  const items = [...document.querySelectorAll({json.dumps(item_selector)})];
                  const item = items.find(candidate =>
                    Number((candidate.innerText || candidate.textContent || '')
                      .match(/(\\d+)\\s*条\\s*\\/\\s*页/)?.[1] || 0) === {page_size}
                  );
                  if (!item) return {{
                    ok:false,
                    changed:false,
                    reason:'exact page size option missing',
                    available:items.map(candidate =>
                      clean(candidate.innerText || candidate.textContent)
                    )
                  }};
                  if (trigger && !items.some(candidate =>
                    candidate.getClientRects().length > 0
                  )) trigger.click();
                  item.click();
                  return {{ok:true, changed:true, target:{page_size}}};
                }})()
                """
            ) or {}
        if not result.get("ok"):
            raise PageSizeNotApplied(
                f"每页条数控件无法应用 {page_size}：{result!r}"
            )
        return result

    def ensure_ungrouped_results(self) -> dict[str, Any]:
        """Public operation used by diagnostics and task gates."""
        return self._ensure_ungrouped_action()

    def available_filter_entries(self, key: str) -> list[dict[str, Any]]:
        filter_selectors = dict(self.selectors.get("filters") or {})
        root_selector = str(
            filter_selectors.get(key)
            or self.selectors.get(f"{key}Filter")
            or V6_FILTER_TREE_SELECTORS.get(key)
            or ""
        )
        if not root_selector:
            raise YearFilterUnavailable(f"{key} 筛选器不存在")
        item_selector = str(self.selectors.get("filterItem") or "a")
        deadline = time.monotonic() + 8.0
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            with self.bridge.transaction() as tab:
                last = tab.run_js(
                    f"""
                return (() => {{
                  const clean = value => String(value || '')
                    .replace(/\\s+/g, ' ').trim();
                  let root = null;
                  try {{
                    root = document.querySelector({
                      json.dumps(root_selector)
                    });
                  }} catch (_) {{
                    return {{
                      ok:false,
                      values:[],
                      entries:[],
                      reason:"invalid filter selector"
                    }};
                  }}
                  if (!root) return {{ok:false, values:[]}};
                  const select = root.matches?.('select')
                    ? root
                    : root.querySelector?.('select');
                  const raw = select
                    ? [...select.options].map(item =>
                        item.textContent || item.value
                      )
                    : [...root.querySelectorAll({
                        json.dumps(item_selector)
                      })].map(item =>
                        item.getAttribute('title') ||
                        item.querySelector?.('.node_name')?.textContent ||
                        item.textContent
                      );
                  const entries = raw.map(clean).filter(Boolean).map(text => {{
                    const counted = text.match(
                      /^(.*?)\\s*[\\(（](\\d+)[\\)）]\\s*$/
                    );
                    return {{
                      value:(counted ? counted[1] : text).trim(),
                      label:text,
                      count:counted ? Number(counted[2]) : null
                    }};
                  }}).filter(entry =>
                    entry.value && !entry.value.startsWith('全部')
                  );
                  const values = entries
                    .map(entry => (
                      entry.value.match(/(?:19|20)\\d{{2}}/)?.[0] || ''
                    ))
                    .filter(value => value && !value.startsWith('全部'));
                  if (!values.length) {{
                    const fold = root.querySelector('a.fold');
                    if (fold && !root.dataset.codexYearPolling) {{
                      root.dataset.codexYearPolling = '1';
                      fold.click();
                    }}
                  }}
                  return {{
                    ok:entries.length > 0,
                    values:[...new Set(values)].sort().reverse(),
                    entries
                  }};
                }})()
                """
                ) or {}
            if last.get("ok") and (
                last.get("entries") or last.get("values")
            ):
                break
            time.sleep(0.25)
        entries = [
            {
                "value": str(entry.get("value") or "").strip(),
                "label": str(entry.get("label") or "").strip(),
                "count": (
                    int(entry["count"])
                    if entry.get("count") is not None
                    else None
                ),
            }
            for entry in last.get("entries") or []
            if isinstance(entry, dict)
            and str(entry.get("value") or "").strip()
        ]
        if not entries:
            entries = [
                {
                    "value": str(value).strip(),
                    "label": str(value).strip(),
                    "count": None,
                }
                for value in last.get("values") or []
                if str(value).strip()
            ]
        if not last.get("ok") or not entries:
            raise YearFilterUnavailable(
                f"无法读取真实筛选选项（{key}）：{last!r}"
            )
        return entries

    def available_filter_values(self, key: str) -> list[str]:
        entries = self.available_filter_entries(key)
        values = [
            str(entry["value"]).strip()
            for entry in entries
            if str(entry.get("value") or "").strip()
        ]
        if key == "year":
            values = [
                match.group(0)
                for value in values
                if (match := re.search(r"(?:19|20)\d{2}", value))
            ]
        return list(dict.fromkeys(values))

    def _ensure_ungrouped_action(self) -> dict[str, Any]:
        selector = str(self.selectors.get("groupUngroup") or "")
        if not selector:
            return {"ok": False, "changed": False, "reason": "ungroup control missing"}
        with self.bridge.transaction() as tab:
            result = tab.run_js(
                f"""
                return (() => {{
                  let group = null;
                  try {{
                    group = document.querySelector({json.dumps(selector)});
                  }} catch (_) {{
                    return {{ok:false, changed:false, reason:'invalid ungroup selector'}};
                  }}
                  if (!group) return {{
                    ok:false,
                    changed:false,
                    reason:'ungroup control missing'
                  }};
                  if (group.checked) return {{ok:true, changed:false}};
                  group.click();
                  return {{ok:true, changed:true}};
                }})()
                """
            ) or {}
        if not result.get("ok"):
            raise ResultGroupingUnavailable(
                f"无法切换不分组：{result!r}"
            )
        return result

    @staticmethod
    def _page_size_rows_match(
        state: dict[str, Any],
        *,
        expected_page_size: int,
    ) -> bool:
        page = max(1, int(state.get("page") or 1))
        total_pages = max(1, int(state.get("totalPages") or 1))
        result_count = max(0, int(state.get("resultCount") or 0))
        row_count = len(list(state.get("rows") or []))
        expected = (
            expected_page_size
            if page < total_pages
            else max(0, result_count - (page - 1) * expected_page_size)
        )
        return row_count == expected

    def _attach_search_session(
        self,
        state: dict[str, Any],
        session: SearchSession,
    ) -> dict[str, Any]:
        self.current_search_session = session
        state = dict(state)
        state["reportedTotalPages"] = session.reported_total_pages
        state["totalPages"] = session.total_pages
        state["searchSession"] = session.to_dict()
        state["searchId"] = session.search_id
        return state

    def _attach_current_session(self, state: dict[str, Any]) -> dict[str, Any]:
        if self.current_search_request is None or self.current_search_session is None:
            return state
        observation = dict(state)
        observation["actualPageSize"] = int(
            state.get("actualPageSize") or state.get("pageSize") or 0
        )
        observation["groupingMode"] = str(
            state.get("groupingMode")
            or self.current_search_session.grouping_mode
            or "unknown"
        )
        observation["actualYear"] = self.current_search_session.actual_year
        observation["actualProvince"] = self.current_search_session.actual_province
        observation["actualTimeliness"] = (
            self.current_search_session.actual_timeliness
        )
        session = SearchSession.from_observation(
            self.current_search_request,
            observation,
            search_id=self.current_search_session.search_id,
        )
        return self._attach_search_session(state, session)

    def _ensure_search_page(self, tab) -> None:
        search_required = operation_required_controls("search")

        def search_controls_ready(diagnostics: dict[str, Any]) -> bool:
            controls = dict(diagnostics.get("controls") or {})
            return all(
                bool(dict(controls.get(control) or {}).get("ok"))
                for control in search_required
            )

        try:
            profile, evidence = detect_selector_profile(tab, self.profiles)
        except SelectorProfileError as exc:
            raise SelectorProfileMismatch(str(exc)) from exc
        state = self._read_access_state(tab)
        self._raise_for_access(state)

        if profile is None and self.selector_profile is not None:
            profile = self.selector_profile
            evidence = ["cached-profile"]

        if profile is None:
            entry_url = str(self.bridge.entry_url or "").strip()
            if not entry_url:
                raise SelectorProfileMismatch(
                    "未能识别当前北大法宝页面，且缺少机构入口地址"
                )
            if str(tab.url or "").rstrip("/") != entry_url.rstrip("/"):
                tab.get(entry_url)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    time.sleep(0.3)
                    state = self._read_access_state(tab)
                    self._raise_for_access(state)
                    try:
                        profile, evidence = detect_selector_profile(tab, self.profiles)
                    except SelectorProfileError as exc:
                        raise SelectorProfileMismatch(str(exc)) from exc
                    if profile is not None:
                        break
            if profile is None:
                raise SelectorProfileMismatch(
                    "未能识别机构入口页面的选择器配置，"
                    "请确认已打开北大法宝检索首页并完成登录"
                )

        self.selector_profile = profile
        self.selectors = dict(profile.selectors)
        self.selector_diagnostics = diagnose_selector_profile(
            tab,
            profile,
            detection_evidence=evidence,
        )
        self.logger.write(
            "info" if self.selector_diagnostics.get("ready") else "warn",
            "source.selector_diagnostics",
            "Pkulaw selector diagnostics completed",
            self.selector_diagnostics,
        )
        diagnostics = self.selector_diagnostics
        if diagnostics.get("pageReady") and search_controls_ready(diagnostics):
            return
        try:
            stable = diagnose_for_operation(
                lambda: diagnose_selector_profile(
                    tab,
                    profile,
                    detection_evidence=evidence,
                ),
                "search",
                timeout_seconds=5.0,
                interval_seconds=0.25,
                stable_samples=2,
            )
            self.selector_diagnostics = stable
            if search_controls_ready(stable):
                return
        except SelectorProfileError as exc:
            if getattr(exc, "code", "") == "PAGE_SELECTOR_UNSTABLE":
                raise SelectorUnstable(str(exc)) from exc
        base = str(self.bridge.entry_url or "").strip()
        if urlparse(base).scheme:
            target = urljoin(base, self.selectors.get("searchPath") or "")
        else:
            current = urlparse(str(tab.url or ""))
            origin = (
                f"{current.scheme}://{current.netloc}/"
                if current.scheme and current.netloc
                else "http://127.0.0.1/"
            )
            target = urljoin(
                origin,
                base or self.selectors.get("searchPath") or "/",
            )
        if target.rstrip("/") == str(tab.url or "").rstrip("/"):
            missing = ", ".join(diagnostics.get("missingControls") or [])
            raise SelectorProfileMismatch(
                f"北大法宝页面缺少关键选择器：{missing or 'unknown'}"
            )
        tab.get(target)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            time.sleep(0.3)
            state = self._read_access_state(tab)
            self._raise_for_access(state)
            diagnostics = self._diagnose_current_page(tab)
            if diagnostics.get("pageReady") and search_controls_ready(diagnostics):
                return
        raise SelectorProfileMismatch(
            "未找到北大法宝检索控件，页面结构可能已变化"
        )

    def _wait_for_results(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_state: dict[str, Any] = {}
        stable_signature = None
        stable_count = 0
        while time.monotonic() < deadline:
            time.sleep(0.35)
            with self.bridge.transaction() as tab:
                try:
                    last_state = self._read_results(tab)
                except Exception as exc:
                    if not _is_page_context_transition(exc):
                        raise
                    stable_signature = None
                    stable_count = 0
                    continue
            signature = (
                last_state.get("url"),
                last_state.get("page"),
                tuple(row.get("policyId") or row.get("url") for row in last_state.get("rows", [])),
                last_state.get("empty"),
            )
            if (last_state.get("rows") or last_state.get("empty")) and signature == stable_signature:
                stable_count += 1
            else:
                stable_count = 1 if (last_state.get("rows") or last_state.get("empty")) else 0
            stable_signature = signature
            if stable_count >= 2:
                return last_state
        raise ManualActionRequired(
            f"检索结果未完成稳定更新；最后状态={last_state!r}。请处理后继续"
        )

    def _read_results(self, tab) -> dict[str, Any]:
        access = self._read_access_state(tab)
        self._raise_for_access(access)
        state = tab.run_js(
            f"""
            return (() => {{
              const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
              const query = selector => {{
                if (!selector) return null;
                try {{
                  return document.querySelector(selector);
                }} catch (_) {{
                  return null;
                }}
              }};
              const links = [...document.querySelectorAll({json.dumps(self.selectors["resultLink"])})];
              const rows = links.map(anchor => {{
                const item = anchor.closest('.item') || anchor.closest('.col') ||
                  anchor.parentElement?.parentElement?.parentElement;
                const card = anchor.closest('.t') || item;
                const checkbox = card ? card.querySelector({json.dumps(self.selectors["batchCheckbox"])}) : null;
                const sort = card ? card.querySelector({json.dumps(self.selectors["batchSort"])}) : null;
                const info = item ? item.querySelector('.info') : null;
                const infoLinks = info ? [...info.querySelectorAll('a')] : [];
                const texts = info ? [...info.querySelectorAll('span.text')].map(
                  element => clean(element.innerText || element.textContent)
                ) : [];
                const byParam = parameter => {{
                  const found = infoLinks.find(element =>
                    (element.getAttribute('href') || '').includes(parameter)
                  );
                  return found ? clean(found.innerText || found.textContent) : '';
                }};
                const byLog = label => {{
                  const found = infoLinks.find(element =>
                    clean(element.getAttribute('logfunc')) === label
                  );
                  return found ? clean(found.innerText || found.textContent) : '';
                }};
                return {{
                  title: clean(anchor.innerText || anchor.textContent),
                  url: new URL(anchor.getAttribute('href') || '', location.href).href,
                  policyId: anchor.getAttribute('data-policy-id') || '',
                  number: sort
                    ? Number((sort.innerText || sort.textContent || '').replace(/[^0-9]/g, ''))
                    : null,
                  hasCheck: !!checkbox && !!checkbox.value,
                  selected: !!checkbox && checkbox.checked,
                  validity: byParam('Aggs.TimelinessDic=') || byLog('题录-时效性'),
                  issuingAuthority: byParam('Aggs.IssueDepartment=') || byLog('题录-制定机关'),
                  documentNumber: texts.find(value => /〔\\d{4}〕|令第|文号|字第|\\d+号/.test(value)) || '',
                  publicationDate: (texts.find(value => value.includes('公布')) || '')
                    .replace('公布', '').trim(),
                  effectiveDate: (texts.find(value => value.includes('施行')) || '')
                    .replace('施行', '').trim(),
                  province: anchor.getAttribute('data-province') || '',
                  library: anchor.getAttribute('data-library') || ''
                }};
              }}).filter(row => row.title && row.url && !row.url.includes('/en_chl/'));
              const pagination = query({json.dumps(self.selectors.get("pagination") or "")});
              const paginationText = pagination
                ? clean(pagination.innerText || pagination.textContent)
                : '';
              const expandedMatch = paginationText.match(
                /(?:页数|第)\\s*(\\d+)\\s*(?:\\/|页\\s*\\/\\s*共)\\s*(\\d+)/
              );
              const compactMatch = paginationText.match(/(\\d+)\\s*\\/\\s*(\\d+)\\s*页/);
              const totalOnlyMatch = paginationText.match(/共\\s*(\\d+)\\s*页/);
              const totalElement = query({json.dumps(self.selectors.get("resultCount") or "")});
              const pageSizeElement = query(
                {json.dumps(str(self.selectors.get("pageSizeHidden") or self.selectors.get("pageSize") or ""))}
              );
              const pageSizeTrigger = query(
                {json.dumps(str(self.selectors.get("pageSizeTrigger") or ""))}
              );
              const pageIndexElement = query(
                '#right_form input[name="Pager.PageIndex"], input[name="Pager.PageIndex"]'
              );
              const resultCount = totalElement
                ? Number((totalElement.innerText || totalElement.textContent || '')
                  .replace(/[^0-9]/g, ''))
                : rows.length;
              const pageSize = Number(
                pageSizeElement?.value ||
                pageSizeTrigger?.getAttribute('data-current-size') ||
                (pageSizeTrigger?.innerText || pageSizeTrigger?.textContent || '')
                  .match(/(\\d+)\\s*条\\s*\\/\\s*页/)?.[1] ||
                20
              ) || 20;
              const page = expandedMatch
                ? Number(expandedMatch[1])
                : compactMatch
                  ? Number(compactMatch[1])
                  : Number(pagination?.dataset.page || 0) ||
                    Number(pageIndexElement?.value || 0) + 1;
              const totalPages = expandedMatch
                ? Number(expandedMatch[2])
                : compactMatch
                  ? Number(compactMatch[2])
                  : totalOnlyMatch
                    ? Number(totalOnlyMatch[1])
                    : Number(pagination?.dataset.total || 0) ||
                      Math.max(1, Math.ceil(resultCount / pageSize));
              const nextSelector = {json.dumps(self.selectors.get("nextPage") or "")};
              const nextControl = query(nextSelector) || (
                pagination
                  ? [...pagination.querySelectorAll('a,button')].find(
                    element => clean(element.innerText || element.textContent) === '下一页'
                  )
                  : null
              );
              const nextItem = nextControl ? nextControl.closest('li') : null;
              const nextAvailable = !!(
                nextControl &&
                !nextControl.disabled &&
                !nextControl.classList.contains('disabled') &&
                nextControl.getAttribute('aria-disabled') !== 'true' &&
                !(nextItem && nextItem.classList.contains('disabled'))
              );
              const searchInput = query({json.dumps(self.selectors.get("searchInput") or "")});
              const hiddenKeywords = document.querySelector(
                'input[name="Keywords"], input[name="Keyword"]'
              );
              const selectedSearchType = [
                ...document.querySelectorAll(
                  'input[name="SearchKeywordType"], select[name="SearchKeywordType"]'
                )
              ].find(control => control.type !== 'radio' || control.checked);
              const selectedMatchType = [
                ...document.querySelectorAll(
                  'input[name="MatchType"], select[name="MatchType"]'
                )
              ].find(control => control.type !== 'radio' || control.checked);
              const libraryRoot = query({json.dumps(self.selectors.get("libraryRoot") or "")});
              const libraryAttribute = {json.dumps(str(self.selectors.get("libraryAttribute") or "library"))};
              const currentLibrary = libraryRoot
                ? [...libraryRoot.querySelectorAll('a[' + libraryAttribute + ']')].find(link => {{
                    const item = link.closest('li');
                    return link.classList.contains('current') ||
                      (item && item.classList.contains('current'));
                  }})
                : null;
              const body = (document.body?.innerText || '').replace(/\\s+/g, ' ');
              return {{
                url: location.href,
                page,
                totalPages,
                resultCount,
                pageSize,
                rows,
                pagination: paginationText,
                nextAvailable,
                queryValue: clean(searchInput?.value || hiddenKeywords?.value || ''),
                searchType: String(selectedSearchType?.value || ''),
                matchType: String(selectedMatchType?.value || ''),
                library: String(
                  currentLibrary?.getAttribute(libraryAttribute) || ''
                ),
                empty: {json.dumps(self.selectors["emptyMarkers"], ensure_ascii=False)}.some(
                  marker => body.includes(marker)
                )
              }};
            }})()
            """
        ) or {}
        for row in state.get("rows", []):
            if not row.get("policyId"):
                row["policyId"] = extract_policy_id(str(row.get("url") or ""))
        return state

    def _read_access_state(self, tab) -> dict[str, Any]:
        return read_stable_access_state(
            tab,
            self.selectors,
        ).as_task_state()

    @staticmethod
    def _raise_for_access(state: dict[str, Any]) -> None:
        if state.get("quota"):
            raise InstitutionQuotaReached(
                f"检测到机构访问或下载额度限制：{state.get('url')}"
            )
        if state.get("login"):
            raise LoginRequired(f"登录会话失效或需要机构登录：{state.get('url')}")
        if state.get("captcha"):
            raise ManualActionRequired("检测到滑块或人机验证，请人工处理后继续")
        if state.get("pageError"):
            raise PageStructureChanged("页面返回异常提示，已安全停止")
