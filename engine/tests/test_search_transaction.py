from __future__ import annotations

import unittest

from pkulaw_engine.sources.search_transaction import (
    SearchRequestIdentity,
    SearchSession,
    SearchSessionError,
    SearchTransaction,
    SearchTransitionTimeout,
    checkpoint_requires_controlled_research,
    clean_filter_label,
    filter_values_match,
    page_size_shape_error,
    require_search_session,
    result_signature,
)


class SearchTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = SearchRequestIdentity(
            query="测试政策",
            match_mode="title",
            library="central",
            year="全部年份",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=20,
        )

    def _observation(self, **changes: object) -> dict[str, object]:
        rows = [
            {
                "policyId": "policy-1",
                "title": "测试政策",
                "url": "https://example.test/policy-1.html",
            }
        ]
        observation: dict[str, object] = {
            "ready": True,
            "loading": False,
            "resultsRootPresent": True,
            "transitionMarkerPresent": False,
            "queryValue": "测试政策",
            "conditionsText": "关键词：测试政策；检索字段：标题",
            "hiddenKeywords": "测试政策",
            "currentLibrary": "chl",
            "hiddenLibrary": "chl",
            "searchType": "Title",
            "matchType": "Exact",
            "page": 1,
            "totalPages": 1,
            "resultCount": 1,
            "actualPageSize": 20,
            "groupingMode": "ungrouped",
            "actualYear": "全部年份",
            "actualProvince": "全部省份",
            "actualTimeliness": "全部（仅记录元数据，不筛选）",
            "rows": rows,
            "url": "https://example.test/search?query=%E6%B5%8B%E8%AF%95",
            "resultSignature": result_signature(rows),  # type: ignore[arg-type]
        }
        observation.update(changes)
        return observation

    def test_fuzzy_match_is_never_accepted_as_exact(self) -> None:
        transaction = SearchTransaction(
            self.request,
            token="fuzzy-rejected",
            previous={"markerApplied": True},
        )

        self.assertIsNone(
            transaction.observe(self._observation(matchType="Fuzzy"))
        )
        self.assertIn("精确", transaction.last_rejection)

    def test_fulltext_field_accepts_site_mixed_case_value(self) -> None:
        request = SearchRequestIdentity(
            query="能源安全",
            match_mode="fulltext",
            library="central",
            year="2024",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=10,
        )
        transaction = SearchTransaction(
            request,
            token="fulltext-mixed-case",
            previous={"markerApplied": False},
        )
        observation = self._observation(
            queryValue="能源安全",
            conditionsText="关键词：能源安全",
            hiddenKeywords="能源安全",
            searchType="Fulltext",
            page=1,
            actualPageSize=10,
            actualYear="2024",
        )

        self.assertIsNone(transaction.observe(observation))
        session = transaction.observe(observation)

        self.assertIsNotNone(session)
        self.assertEqual(transaction.last_rejection, "")

    def test_old_page_five_is_never_accepted_as_new_search(self) -> None:
        transaction = SearchTransaction(
            self.request,
            token="token-1",
            previous={
                "markerApplied": True,
                "url": "https://example.test/search",
                "page": 5,
                "resultCount": 94,
                "resultIds": ["old-93", "old-94"],
                "pageSize": 20,
                "groupingMode": "grouped",
            },
        )
        old_rows = [
            {
                "policyId": "old-93",
                "title": "旧结果九十三",
                "url": "https://example.test/old-93.html",
            }
        ]
        self.assertIsNone(
            transaction.observe(
                self._observation(
                    transitionMarkerPresent=True,
                    page=5,
                    totalPages=5,
                    resultCount=94,
                    rows=old_rows,
                    resultSignature=result_signature(old_rows),
                )
            )
        )
        self.assertIn("旧结果容器", transaction.last_rejection)

        self.assertIsNone(
            transaction.observe(self._observation(transitionMarkerPresent=False))
        )
        session = transaction.observe(
            self._observation(transitionMarkerPresent=False)
        )
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.page, 1)
        self.assertEqual(session.total_pages, 1)
        self.assertEqual(session.result_count, 1)

    def test_same_url_same_query_and_same_results_use_token_replacement(self) -> None:
        previous = {
            "markerApplied": True,
            "url": "https://example.test/search?query=same",
            "page": 1,
            "resultCount": 1,
            "resultIds": ["policy-1"],
            "pageSize": 20,
            "groupingMode": "ungrouped",
        }
        transaction = SearchTransaction(
            self.request,
            token="same-query-token",
            previous=previous,
        )
        observation = self._observation(
            url=previous["url"],
            transitionMarkerPresent=True,
        )
        self.assertIsNone(transaction.observe(observation))
        self.assertIsNone(
            transaction.observe({**observation, "transitionMarkerPresent": False})
        )
        session = transaction.observe(
            {**observation, "transitionMarkerPresent": False}
        )
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.result_signature, result_signature(
            [{"policyId": "policy-1", "title": "测试政策", "url": "https://example.test/policy-1.html"}]
        ))

    def test_timeout_reports_unconfirmed_transition(self) -> None:
        transaction = SearchTransaction(
            self.request,
            token="never-replaced",
            previous={"markerApplied": True},
            timeout_seconds=1.0,
        )
        now = [0.0]

        def clock() -> float:
            return now[0]

        def sleep(seconds: float) -> None:
            now[0] += seconds

        observation = self._observation(transitionMarkerPresent=True)
        with self.assertRaises(SearchTransitionTimeout) as context:
            transaction.wait(
                lambda: observation,
                interval_seconds=0.35,
                clock=clock,
                sleep=sleep,
            )
        self.assertEqual(
            context.exception.code,
            "SEARCH_TRANSITION_UNCONFIRMED",
        )
        self.assertIn("旧结果容器", str(context.exception))

    def test_each_search_identity_condition_fails_closed(self) -> None:
        cases = [
            ({"loading": True}, "加载状态"),
            ({"ready": False}, "DOM"),
            ({"resultsRootPresent": False}, "结果容器"),
            ({"queryValue": "旧关键词"}, "搜索框"),
            ({"conditionsText": "关键词：旧关键词"}, "检索条件区域"),
            ({"hiddenKeywords": "旧关键词"}, "隐藏关键词"),
            ({"currentLibrary": "lar", "hiddenLibrary": "lar"}, "子库"),
            ({"searchType": "FullText"}, "检索字段"),
            ({"page": 5}, "页码"),
        ]
        for change, expected in cases:
            with self.subTest(change=change):
                transaction = SearchTransaction(
                    self.request,
                    token="token",
                    previous={"markerApplied": False},
                )
                self.assertIsNone(
                    transaction.observe(self._observation(**change))
                )
                self.assertIn(expected, transaction.last_rejection)

    def test_two_stable_samples_are_required(self) -> None:
        transaction = SearchTransaction(
            self.request,
            token="token",
            previous={"markerApplied": False},
        )
        self.assertIsNone(transaction.observe(self._observation()))
        changed = self._observation(resultCount=2)
        self.assertIsNone(transaction.observe(changed))
        self.assertIsNone(transaction.observe(self._observation()))
        session = transaction.observe(self._observation())
        self.assertIsNotNone(session)

    def test_checkpoint_round_trip_and_stale_page_rejection(self) -> None:
        transaction = SearchTransaction(
            self.request,
            token="token",
            previous={"markerApplied": False},
        )
        transaction.observe(self._observation())
        session = transaction.observe(self._observation())
        assert session is not None

        restored = SearchSession.from_dict(session.to_dict())
        self.assertEqual(restored, session)
        state = {"searchSession": session.to_dict()}
        self.assertEqual(
            require_search_session(state, self.request, expected_page=1),
            session,
        )
        with self.assertRaises(SearchSessionError) as stale_page:
            require_search_session(state, self.request, expected_page=2)
        self.assertEqual(stale_page.exception.code, "RESULTS_STALE")

    def test_search_session_keeps_reported_and_effective_page_counts(self) -> None:
        request = SearchRequestIdentity(
            query="碳中和",
            match_mode="keyword",
            library="local",
            year="全部年份",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=100,
        )
        observation = {
            "rows": [
                {
                    "policyId": f"policy-{index}",
                    "title": f"政策 {index}",
                    "url": f"https://example.test/policy-{index}.html",
                }
                for index in range(100)
            ],
            "page": 1,
            "totalPages": 20,
            "resultCount": 17224,
            "actualPageSize": 100,
            "groupingMode": "ungrouped",
        }

        session = SearchSession.from_observation(
            request,
            observation,
            search_id="search-local",
        )

        self.assertEqual(session.reported_total_pages, 20)
        self.assertEqual(session.total_pages, 173)
        restored = SearchSession.from_dict(session.to_dict())
        self.assertEqual(restored, session)

    def test_missing_search_session_requires_controlled_research(self) -> None:
        with self.assertRaises(SearchSessionError) as missing:
            require_search_session({}, self.request)
        self.assertEqual(missing.exception.code, "RESULTS_STALE")
        self.assertIn("SearchSession", str(missing.exception))

    def test_old_checkpoint_without_session_requires_research(self) -> None:
        self.assertTrue(
            checkpoint_requires_controlled_research(
                {
                    "scannedPages": 5,
                    "collectedRows": 94,
                    "currentAction": "results-complete",
                },
                has_existing_items=True,
            )
        )
        self.assertFalse(
            checkpoint_requires_controlled_research(
                {"currentAction": "preflight"},
            )
        )
        transaction = SearchTransaction(
            self.request,
            token="token",
            previous={"markerApplied": False},
        )
        transaction.observe(self._observation())
        session = transaction.observe(self._observation())
        assert session is not None
        self.assertFalse(
            checkpoint_requires_controlled_research(
                {"searchIdentity": session.to_dict(), "scannedPages": 1},
            )
        )

    def test_filter_labels_require_exact_clean_match(self) -> None:
        self.assertEqual(clean_filter_label("2024 (21)"), "2024")
        self.assertEqual(clean_filter_label("湖北省（3）"), "湖北省")
        self.assertTrue(filter_values_match("2024", "2024 (21)"))
        self.assertTrue(filter_values_match("全部省份", ""))
        self.assertFalse(filter_values_match("湖北省", "湖南省 (3)"))
        self.assertFalse(filter_values_match("现行有效", "已废止 (1)"))

    def test_filter_page_size_and_group_are_required(self) -> None:
        transaction = SearchTransaction(
            self.request,
            token="refine",
            previous={"markerApplied": False},
            required_filter_keys=("year", "province", "timeliness"),
            require_page_size=True,
            require_ungrouped=True,
        )
        invalid = self._observation(
            actualYear="2023",
            actualProvince="湖北省",
            actualTimeliness="现行有效",
            actualPageSize=10,
            groupingMode="grouped",
        )
        self.assertIsNone(transaction.observe(invalid))
        self.assertIn("year", transaction.last_rejection)

        valid = self._observation(
            actualYear="全部年份",
            actualProvince="全部省份",
            actualTimeliness="全部（仅记录元数据，不筛选）",
            actualPageSize=20,
            groupingMode="ungrouped",
        )
        self.assertIsNone(transaction.observe(valid))
        self.assertIsNotNone(transaction.observe(valid))

    def test_page_size_and_group_fail_with_specific_codes(self) -> None:
        page_size_transaction = SearchTransaction(
            self.request,
            token="page-size",
            previous={"markerApplied": False},
            require_page_size=True,
        )
        page_size_transaction.observe(
            self._observation(actualPageSize=10)
        )
        self.assertEqual(
            page_size_transaction.last_rejection_code,
            "PAGE_SIZE_NOT_APPLIED",
        )

        group_transaction = SearchTransaction(
            self.request,
            token="group",
            previous={"markerApplied": False},
            require_ungrouped=True,
        )
        group_transaction.observe(self._observation(groupingMode="grouped"))
        self.assertEqual(
            group_transaction.last_rejection_code,
            "RESULT_GROUPING_UNAVAILABLE",
        )

    def test_library_redirect_has_specific_code(self) -> None:
        request = SearchRequestIdentity(
            query="测试政策",
            match_mode="title",
            library="local",
            year="全部年份",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=20,
        )
        transaction = SearchTransaction(
            request,
            token="library-redirect",
            previous={"markerApplied": False},
        )
        transaction.observe(
            self._observation(
                currentLibrary="chl",
                hiddenLibrary="chl",
            )
        )
        self.assertEqual(
            transaction.last_rejection_code,
            "SEARCH_LIBRARY_MISMATCH",
        )

    def test_non_exact_filter_returns_filter_not_applied(self) -> None:
        request = SearchRequestIdentity(
            query="筛选测试",
            match_mode="title",
            library="local",
            year="2024",
            province="湖北省",
            timeliness="现行有效",
            requested_page_size=20,
        )
        transaction = SearchTransaction(
            request,
            token="filter-mismatch",
            previous={"markerApplied": False},
            required_filter_keys=("year", "province", "timeliness"),
        )
        transaction.observe(
            self._observation(
                queryValue="筛选测试",
                conditionsText="关键词：筛选测试；检索字段：标题",
                hiddenKeywords="筛选测试",
                actualYear="2023",
                actualProvince="湖南省",
                actualTimeliness="已废止",
                currentLibrary="lar",
                hiddenLibrary="lar",
            )
        )
        self.assertEqual(
            transaction.last_rejection_code,
            "FILTER_NOT_APPLIED",
        )

    def test_page_size_row_shape_for_non_tail_and_tail(self) -> None:
        self.assertEqual(
            page_size_shape_error(
                page=1,
                total_pages=5,
                result_count=94,
                row_count=20,
                page_size=20,
            ),
            "",
        )
        self.assertIn(
            "expected=14",
            page_size_shape_error(
                page=5,
                total_pages=5,
                result_count=94,
                row_count=10,
                page_size=20,
            ),
        )

    def test_supported_page_sizes_become_stable_sessions(self) -> None:
        for page_size in (10, 20, 50, 100):
            with self.subTest(page_size=page_size):
                request = SearchRequestIdentity(
                    query="测试政策",
                    match_mode="title",
                    library="central",
                    year="全部年份",
                    province="全部省份",
                    timeliness="全部（仅记录元数据，不筛选）",
                    requested_page_size=page_size,
                )
                rows = [
                    {
                        "policyId": f"policy-{index}",
                        "title": f"政策 {index}",
                        "url": f"https://example.test/{index}.html",
                    }
                    for index in range(1, page_size + 1)
                ]
                observation = self._observation(
                    actualPageSize=page_size,
                    totalPages=2,
                    resultCount=page_size * 2,
                    rows=rows,
                    resultSignature=result_signature(rows),
                )
                transaction = SearchTransaction(
                    request,
                    token=f"size-{page_size}",
                    previous={"markerApplied": False},
                    require_page_size=True,
                )
                self.assertIsNone(transaction.observe(observation))
                session = transaction.observe(observation)
                self.assertIsNotNone(session)
                assert session is not None
                self.assertEqual(session.actual_page_size, page_size)

    def test_require_refinements_checks_checkpoint_session(self) -> None:
        transaction = SearchTransaction(
            self.request,
            token="final",
            previous={"markerApplied": False},
            required_filter_keys=("year", "province", "timeliness"),
            require_page_size=True,
            require_ungrouped=True,
        )
        transaction.observe(self._observation())
        session = transaction.observe(self._observation())
        assert session is not None
        self.assertTrue(
            session.matches_request(
                self.request,
                expected_page=1,
                require_refinements=True,
            )
        )

    def test_empty_exact_title_session_is_valid_without_refinements(self) -> None:
        request = SearchRequestIdentity(
            query="某县公共数据管理办法",
            match_mode="title",
            library="local",
            year="全部年份",
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            requested_page_size=20,
        )
        session = SearchSession.from_observation(
            request,
            {
                "page": 1,
                "totalPages": 1,
                "resultCount": 0,
                "actualPageSize": 20,
                "groupingMode": "",
                "rows": [],
                "url": "https://example.test/search",
            },
            search_id="empty-exact-title",
        )
        self.assertTrue(session.is_verified_empty_exact_title())
        self.assertTrue(
            session.matches_request(
                request,
                expected_page=1,
                require_refinements=True,
            )
        )

        keyword = SearchRequestIdentity(
            query=request.query,
            match_mode="keyword",
            library=request.library,
            year=request.year,
            province=request.province,
            timeliness=request.timeliness,
            requested_page_size=request.requested_page_size,
        )
        keyword_session = SearchSession.from_observation(
            keyword,
            {
                "page": 1,
                "totalPages": 1,
                "resultCount": 0,
                "actualPageSize": 20,
                "groupingMode": "",
                "rows": [],
            },
            search_id="empty-keyword",
        )
        self.assertFalse(keyword_session.is_verified_empty_exact_title())
        self.assertFalse(
            keyword_session.matches_request(
                keyword,
                expected_page=1,
                require_refinements=True,
            )
        )

    def test_refinement_replaces_observation_under_same_search_id(self) -> None:
        base = SearchTransaction(
            self.request,
            token="base",
            previous={"markerApplied": False},
        )
        base.observe(self._observation())
        base_session = base.observe(self._observation())
        assert base_session is not None

        refined = SearchTransaction(
            self.request,
            token="refined",
            previous={"markerApplied": False},
            search_id=base_session.search_id,
            required_filter_keys=("year", "province", "timeliness"),
            require_page_size=True,
            require_ungrouped=True,
        )
        changed = self._observation(
            url="https://example.test/search?refined=1",
        )
        refined.observe(changed)
        refined_session = refined.observe(changed)
        assert refined_session is not None
        self.assertEqual(refined_session.search_id, base_session.search_id)
        self.assertNotEqual(refined_session, base_session)
        self.assertNotEqual(refined_session.url, base_session.url)


if __name__ == "__main__":
    unittest.main()
