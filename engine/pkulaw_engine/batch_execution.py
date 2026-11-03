from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .sources.search_transaction import SearchSession


class BatchExecutionContextError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RetrievalContext:
    query: str
    library: str
    year: str
    scope_key: str
    search_id: str
    pages: dict[int, dict[str, Any]]
    batch_keys: tuple[str, ...]
    last_page: int

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RetrievalContext":
        return cls(
            query=str(payload.get("query") or ""),
            library=str(payload.get("library") or ""),
            year=str(payload.get("year") or ""),
            scope_key=str(
                payload.get("scopeKey")
                or payload.get("year")
                or ""
            ),
            search_id=str(payload.get("searchId") or ""),
            pages={
                int(page["page"]): dict(page)
                for page in payload.get("pages", [])
                if isinstance(page, dict) and page.get("page") is not None
            },
            batch_keys=tuple(
                str(value)
                for value in payload.get("batchKeys", [])
                if str(value)
            ),
            last_page=max(1, int(payload.get("lastPage") or 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "library": self.library,
            "year": self.year,
            "scopeKey": self.scope_key,
            "searchId": self.search_id,
            "pages": [
                self.pages[page]
                for page in sorted(self.pages)
            ],
            "batchKeys": list(self.batch_keys),
            "lastPage": self.last_page,
        }


@dataclass(frozen=True)
class NavigationDecision:
    action: str
    reason: str
    current_page: int
    target_page: int


class BatchExecutionContext:
    def __init__(self, contexts: list[dict[str, Any]]) -> None:
        self.contexts = {}
        for item in contexts:
            context = RetrievalContext.from_dict(item)
            self.contexts[
                (context.query, context.library, context.scope_key)
            ] = context
        self.active_key: tuple[str, str, str] | None = None
        self.active_search_id = ""
        self.current_session: SearchSession | None = None

    def bind_current(
        self,
        key: tuple[str, str, str],
        session: SearchSession,
    ) -> None:
        context = self._context(key)
        if not self._session_matches_context(context, session):
            raise BatchExecutionContextError(
                "RESULTS_STALE",
                f"SearchSession 与检索组合不一致：{key}",
            )
        self._validate_page_signature(context, session)
        self.active_key = key
        self.active_search_id = session.search_id
        self.current_session = session

    def accept_forward_page(
        self,
        key: tuple[str, str, str],
        session: SearchSession,
    ) -> None:
        if self.active_key != key or session.search_id != self.active_search_id:
            raise BatchExecutionContextError(
                "RESULTS_STALE",
                "向前翻页后 SearchSession 身份发生变化",
            )
        self._validate_page_signature(self._context(key), session)
        self.current_session = session

    def decide(
        self,
        key: tuple[str, str, str],
        target_page: int,
    ) -> NavigationDecision:
        context = self._context(key)
        session = self.current_session
        current_page = session.page if session is not None else 0
        if (
            session is None
            or self.active_key != key
            or session.search_id != self.active_search_id
            or not self._session_matches_context(context, session)
        ):
            return NavigationDecision(
                "research",
                "当前页面不是目标检索组合",
                current_page,
                target_page,
            )
        try:
            self._validate_page_signature(context, session)
        except BatchExecutionContextError:
            return NavigationDecision(
                "research",
                "当前页结果签名与扫描断点不一致",
                current_page,
                target_page,
            )
        if current_page > target_page:
            return NavigationDecision(
                "research",
                "当前页在目标页之后",
                current_page,
                target_page,
            )
        if current_page == target_page:
            return NavigationDecision(
                "reuse",
                "当前页可直接复用",
                current_page,
                target_page,
            )
        return NavigationDecision(
            "forward",
            "当前页在目标页之前",
            current_page,
            target_page,
        )

    def checkpoint(self) -> dict[str, Any]:
        return {
            "contexts": [
                self.contexts[key].to_dict()
                for key in sorted(self.contexts)
            ],
            "activeQueryLibraryYear": list(self.active_key) if self.active_key else [],
            "activeSearchId": self.active_search_id,
            "currentPage": self.current_session.page if self.current_session else 0,
            "currentResultSignature": (
                self.current_session.result_signature
                if self.current_session
                else ""
            ),
        }

    def _context(self, key: tuple[str, str, str]) -> RetrievalContext:
        context = self.contexts.get(key)
        if context is None:
            raise BatchExecutionContextError(
                "BATCH_CONTEXT_NOT_FOUND",
                f"缺少检索组合执行上下文：{key}",
            )
        return context

    @staticmethod
    def _session_matches_context(
        context: RetrievalContext,
        session: SearchSession,
    ) -> bool:
        return (
            session.query == context.query
            and session.library == context.library
            and session.year == context.year
        )

    @staticmethod
    def _validate_page_signature(
        context: RetrievalContext,
        session: SearchSession,
    ) -> None:
        expected = context.pages.get(session.page)
        if expected is None:
            raise BatchExecutionContextError(
                "RESULTS_STALE",
                f"扫描上下文没有第 {session.page} 页",
            )
        if str(expected.get("resultSignature") or "") != session.result_signature:
            raise BatchExecutionContextError(
                "BATCH_RESULTS_CHANGED",
                f"第 {session.page} 页结果签名与扫描断点不一致",
            )
