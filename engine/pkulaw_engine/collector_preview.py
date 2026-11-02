from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import Any, Callable

from .browser_bridge import BrowserBridge
from .logging_utils import JsonLineLogger
from .search_window import assess_result_window
from .selections.years import (
    YearSelection,
    expand_search_combinations,
    parse_year_selection,
)
from .sources.pkulaw import (
    PkulawAdapter,
    SearchLibraryMismatch,
    SourceAccessError,
)
from .sources.search_transaction import (
    SearchRequestIdentity,
    SearchSession,
    SearchSessionError,
)
from .storage import ValidationError


@dataclass(frozen=True)
class PreviewConfig:
    mode: str
    queries: tuple[str, ...]
    match_mode: str
    libraries: tuple[str, ...]
    library: str
    year_selection: YearSelection
    province: str
    timeliness: str
    page_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "queries": list(self.queries),
            "matchMode": self.match_mode,
            "library": self.library,
            "libraries": list(self.libraries),
            "yearSelection": self.year_selection.to_payload(),
            "province": self.province,
            "timeliness": self.timeliness,
            "pageSize": self.page_size,
        }

    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def parse_preview_config(mode: str, payload: dict[str, Any]) -> PreviewConfig:
    clean_mode = str(mode or "").strip()
    if clean_mode not in {"single", "batch"}:
        raise ValidationError(
            "INVALID_TASK_MODE",
            "preview mode must be single or batch",
        )
    if not isinstance(payload, dict):
        raise ValidationError("INVALID_TASK_INPUT", "preview input must be an object")
    raw_queries = payload.get("queries") or payload.get("keywords") or []
    if isinstance(raw_queries, str):
        raw_queries = [raw_queries]
    if not isinstance(raw_queries, (list, tuple)):
        raise ValidationError("QUERIES_REQUIRED", "queries must be a list")
    queries = tuple(
        str(item).strip()
        for item in raw_queries
        if str(item).strip()
    )
    if not queries:
        raise ValidationError("QUERIES_REQUIRED", "请至少输入一个检索词")
    match_mode = str(payload.get("matchMode") or "keyword").strip()
    if match_mode not in {"keyword", "title"}:
        raise ValidationError(
            "INVALID_MATCH_MODE",
            "matchMode must be keyword or title",
        )
    if clean_mode == "batch" and match_mode == "title":
        raise ValidationError(
            "BATCH_EXACT_TITLE_UNSUPPORTED",
            "精确完整标题只适用于逐篇任务；批量任务必须使用关键词模式",
        )
    library = str(payload.get("library") or "central").strip()
    if library not in {"central", "local", "both"}:
        raise ValidationError(
            "INVALID_LIBRARY",
            "library must be central, local or both",
        )
    libraries = (
        ("central", "local")
        if library == "both"
        else (library,)
    )
    try:
        page_size = int(payload.get("pageSize") or (50 if clean_mode == "batch" else 20))
    except (TypeError, ValueError) as exc:
        raise ValidationError("INVALID_PAGING", "pageSize must be an integer") from exc
    if page_size not in {10, 20, 50, 100}:
        raise ValidationError(
            "INVALID_PAGE_SIZE",
            "pageSize must be 10, 20, 50 or 100",
        )
    return PreviewConfig(
        mode=clean_mode,
        queries=queries,
        match_mode=match_mode,
        libraries=libraries,
        library=library,
        year_selection=parse_year_selection(payload),
        province=str(payload.get("province") or "全部省份"),
        timeliness=str(
            payload.get("timeliness")
            or "全部（仅记录元数据，不筛选）"
        ),
        page_size=page_size,
    )


def checkpoint_preview_snapshot(
    task_input: dict[str, Any],
) -> dict[str, Any] | None:
    raw = task_input.get("previewSnapshot")
    if not isinstance(raw, dict):
        return None
    fingerprint = str(raw.get("previewFingerprint") or "").strip()
    if not fingerprint:
        return None
    return {
        "formFingerprint": str(raw.get("formFingerprint") or ""),
        "previewFingerprint": fingerprint,
        "generatedAt": str(raw.get("generatedAt") or ""),
        "combinationCount": max(0, int(raw.get("combinationCount") or 0)),
        "totalResults": max(0, int(raw.get("totalResults") or 0)),
        "totalPages": max(0, int(raw.get("totalPages") or 0)),
        "estimatedBatches50": max(
            0,
            int(raw.get("estimatedBatches50") or 0),
        ),
        "windowStatus": str(raw.get("windowStatus") or "unknown"),
        "downloadsStarted": 0,
        "downloadIntentsCreated": 0,
        "tasksCreated": 0,
    }


class CollectorPreviewService:
    def __init__(
        self,
        browser_bridge: BrowserBridge,
        logger: JsonLineLogger,
        adapter_factory: Callable[[BrowserBridge, JsonLineLogger], PkulawAdapter]
        | None = None,
    ) -> None:
        self.browser_bridge = browser_bridge
        self.logger = logger
        self.adapter_factory = adapter_factory or PkulawAdapter

    def preview(self, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        config = parse_preview_config(mode, payload)
        combinations = expand_search_combinations(
            config.queries,
            config.libraries,
            config.year_selection,
        )
        adapter = self.adapter_factory(self.browser_bridge, self.logger)
        started = time.monotonic()
        records: list[dict[str, Any]] = []
        for combination in combinations:
            request = SearchRequestIdentity(
                query=combination.query,
                match_mode=config.match_mode,
                library=combination.library,
                year=combination.year,
                province=config.province,
                timeliness=config.timeliness,
                requested_page_size=config.page_size,
            )
            try:
                state = adapter.search(
                    keyword=request.query,
                    match_mode=request.match_mode,
                    library=request.library,
                    year=request.year,
                    province=request.province,
                    timeliness=request.timeliness,
                    page_size=request.requested_page_size,
                )
            except SearchLibraryMismatch as exc:
                if config.library != "both":
                    raise ValidationError(exc.code, str(exc)) from exc
                records.append(
                    {
                        "query": request.query,
                        "matchMode": request.match_mode,
                        "library": request.library,
                        "year": request.year,
                        "status": "skipped",
                        "resultCount": 0,
                        "totalPages": 0,
                        "actualPageSize": 0,
                        "groupingMode": "",
                        "searchId": "",
                        "resultSignature": "",
                        "actualYear": "",
                        "actualProvince": "",
                        "actualTimeliness": "",
                        "skipReason": str(exc),
                    }
                )
                continue
            except SourceAccessError as exc:
                raise ValidationError(
                    getattr(exc, "code", "PREVIEW_SEARCH_FAILED"),
                    str(exc),
                ) from exc

            raw_session = state.get("searchSession")
            session = (
                SearchSession.from_dict(raw_session)
                if isinstance(raw_session, dict)
                else None
            )
            if (
                session is None
                or not session.matches_request(
                    request,
                    expected_page=1,
                    require_refinements=True,
                )
            ):
                raise ValidationError(
                    "SEARCH_TRANSITION_UNCONFIRMED",
                    "预览没有取得与当前请求匹配的稳定 SearchSession",
                )
            try:
                window = assess_result_window(
                    result_count=session.result_count,
                    reported_total_pages=int(
                        session.reported_total_pages
                        or session.total_pages
                        or 0
                    ),
                    page_size=session.actual_page_size,
                )
                records.append(
                    {
                        "query": session.query,
                        "matchMode": session.match_mode,
                        "library": session.library,
                        "year": session.year,
                        "status": (
                            "no_result"
                            if session.result_count == 0
                            else "ready"
                        ),
                        "resultCount": session.result_count,
                        "totalPages": (
                            0
                            if session.result_count == 0
                            else session.total_pages
                        ),
                        "actualPageSize": session.actual_page_size,
                        "groupingMode": session.grouping_mode,
                        "searchId": session.search_id,
                        "resultSignature": session.result_signature,
                        "actualYear": session.actual_year,
                        "actualProvince": session.actual_province,
                        "actualTimeliness": session.actual_timeliness,
                        "reportedTotalPages": window.reported_total_pages,
                        "expectedTotalPages": window.expected_total_pages,
                        "accessibleCapacity": window.accessible_capacity,
                        "windowStatus": window.status,
                        "windowCode": window.code,
                    }
                )
            except SearchSessionError as exc:
                raise ValidationError(
                    getattr(exc, "code", "RESULTS_STALE"),
                    str(exc),
                ) from exc

        total_results = sum(
            int(record["resultCount"])
            for record in records
        )
        total_pages = sum(
            int(record["totalPages"])
            for record in records
        )
        form_fingerprint = config.fingerprint()
        observation_payload = {
            "formFingerprint": form_fingerprint,
            "combinations": [
                {
                    "searchId": record["searchId"],
                    "resultSignature": record["resultSignature"],
                    "resultCount": record["resultCount"],
                    "totalPages": record["totalPages"],
                }
                for record in records
            ],
        }
        preview_fingerprint = hashlib.sha256(
            json.dumps(
                observation_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        generated_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(),
        )
        window_status = (
            "probe_required"
            if any(
                record["windowStatus"] == "truncated"
                for record in records
            )
            else "complete"
        )
        result = {
            "formFingerprint": form_fingerprint,
            "previewFingerprint": preview_fingerprint,
            "generatedAt": generated_at,
            "mode": config.mode,
            "queryCount": len(config.queries),
            "libraryCount": len(config.libraries),
            "yearCount": len(config.year_selection.years),
            "combinationCount": len(combinations),
            "totalResults": total_results,
            "totalPages": total_pages,
            "estimatedBatches50": math.ceil(total_results / 50)
            if total_results
            else 0,
            "windowStatus": window_status,
            "downloadsStarted": 0,
            "downloadIntentsCreated": 0,
            "tasksCreated": 0,
            "combinations": records,
            "durationMs": round((time.monotonic() - started) * 1000),
        }
        self.logger.write(
            "info",
            "task.preview.completed",
            "Collector preview completed without downloads",
            {
                "mode": config.mode,
                "combinationCount": len(combinations),
                "totalResults": total_results,
                "downloadsStarted": 0,
            },
        )
        return result
