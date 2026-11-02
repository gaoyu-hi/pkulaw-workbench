from __future__ import annotations

from pathlib import Path
import unittest

from pkulaw_engine.sources.live_page_diagnostics import (
    SanitizationError,
    assert_sanitized_html,
    sanitize_html,
)


class LivePageDiagnosticsTests(unittest.TestCase):
    def test_sanitizer_removes_secrets_identity_and_active_content(self) -> None:
        source = """
        <!doctype html>
        <html>
          <head>
            <script>window.secret = 'unsafe';</script>
            <style>body { display:none }</style>
          </head>
          <body>
            <input id="access_token" type="hidden" value="eyJhbGciOiJIUzI1NiJ9.payload.signature">
            <input id="lUserId" type="hidden" value="1947cab4-3afa-ef11-b000-000000000000">
            <input id="lUserIP" type="hidden" value="113.57.5.250">
            <input id="Pager.PageSize" name="Pager.PageSize" type="hidden" value="20">
            <p>中国地质大学（武汉） 联系 test@example.com 13800138000</p>
            <img src="https://tracker.invalid/pixel">
            <a href="javascript:alert(1)" onclick="bad()">检索</a>
          </body>
        </html>
        """

        result = sanitize_html(source, redact_terms=("中国地质大学（武汉）",))

        self.assertIn('name="Pager.PageSize"', result)
        self.assertIn("检索", result)
        self.assertNotIn("script", result.lower())
        self.assertNotIn("style", result.lower())
        self.assertNotIn("access_token", result.lower())
        self.assertNotIn("luserid", result.lower())
        self.assertNotIn("113.57.5.250", result)
        self.assertNotIn("test@example.com", result)
        self.assertNotIn("13800138000", result)
        self.assertNotIn("中国地质大学", result)
        self.assertNotIn("javascript:", result)
        assert_sanitized_html(result, redact_terms=("中国地质大学（武汉）",))

    def test_unsafe_snapshot_is_rejected(self) -> None:
        with self.assertRaises(SanitizationError):
            assert_sanitized_html(
                '<input type="hidden" id="access_token" value="secret">'
            )

    def test_checked_in_v6_fixtures_are_sanitized(self) -> None:
        fixture_root = Path(__file__).with_name("fixtures") / "pkulaw-v6"
        expected = {
            "batch-modal-real-shape.html",
            "custom-page-size.html",
            "search.html",
            "search-missing-submit.html",
            "selector-flicker.html",
            "selector-missing.html",
            "same-query-rerun.html",
            "same-url-slow-search.html",
            "stale-page5.html",
            "download-out-of-order.html",
            "filter-tree.html",
            "grouped-results.html",
            "results.html",
            "multipage.html",
            "detail.html",
            "record-search-results.html",
            "pagination-dead-click.html",
            "captcha-dragtext-visible.html",
            "captcha-overlay-hidden.html",
            "detail-fulltext-clauses.html",
        }

        self.assertEqual(
            {path.name for path in fixture_root.glob("*.html")},
            expected,
        )
        for path in fixture_root.glob("*.html"):
            value = path.read_text(encoding="utf-8")
            assert_sanitized_html(value)
            self.assertIn('data-pkulaw-profile="pkulaw-v6"', value)
            self.assertNotIn("<script", value.lower())
            self.assertNotIn("access_token", value.lower())


if __name__ == "__main__":
    unittest.main()
