from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from DrissionPage import Chromium


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from pkulaw_engine.browser_bridge import BrowserBridge  # noqa: E402
from pkulaw_engine.logging_utils import JsonLineLogger  # noqa: E402
from pkulaw_engine.sources.live_page_diagnostics import sanitize_html  # noqa: E402
from pkulaw_engine.sources.pkulaw import PkulawAdapter  # noqa: E402


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def page_summary(state: dict[str, Any]) -> dict[str, Any]:
    rows = list(state.get("rows") or [])
    identities = [
        str(row.get("policyId") or row.get("url") or row.get("title") or "")
        for row in rows
    ]
    signature = hashlib.sha256(
        "\n".join(identities).encode("utf-8")
    ).hexdigest()
    return {
        "page": int(state.get("page") or 0),
        "totalPages": int(state.get("totalPages") or 0),
        "resultCount": int(state.get("resultCount") or 0),
        "rowCount": len(rows),
        "firstTitle": str(rows[0].get("title") or "") if rows else "",
        "lastTitle": str(rows[-1].get("title") or "") if rows else "",
        "firstPolicyId": str(rows[0].get("policyId") or "") if rows else "",
        "lastPolicyId": str(rows[-1].get("policyId") or "") if rows else "",
        "resultSignatureSha256": signature,
        "nextAvailable": bool(state.get("nextAvailable")),
    }


def capture_sanitized_snapshot(tab: Any, output_path: Path) -> str:
    html = tab.run_js("return document.documentElement.outerHTML") or ""
    sanitized = sanitize_html(str(html))
    output_path.write_text(sanitized, encoding="utf-8")
    return hashlib.sha256(sanitized.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--year", required=True)
    parser.add_argument("--library", choices=("central", "local"), default="central")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--skip-search", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    candidates = [
        item
        for item in browser.get_tabs()
        if str(item.url).startswith("https://www.pkulaw.com/")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one pkulaw tab, found {len(candidates)}"
        )
    tab = candidates[0]

    bridge = BrowserBridge(JsonLineLogger(args.output_dir / "tail-scan-engine.jsonl"))
    bridge.browser = browser
    bridge.main_tab = tab
    bridge.entry_url = "https://www.pkulaw.com/"
    bridge.origin = "https://www.pkulaw.com"
    bridge.target_url = str(tab.url)
    adapter = PkulawAdapter(bridge, bridge.logger)

    diagnostics = adapter.diagnose_current_page()
    if not diagnostics.get("ready"):
        raise RuntimeError(
            "V6 selector diagnostics failed before read-only tail scan: "
            + json.dumps(diagnostics, ensure_ascii=False)
        )

    access = adapter._read_access_state(tab)
    if not access.get("loggedIn") or access.get("captcha") or access.get("quota"):
        raise RuntimeError(
            "login, captcha, or quota is not safe for read-only tail scan: "
            + json.dumps(access, ensure_ascii=False)
        )

    started_at = utc_now()
    if args.skip_search:
        first = adapter.read_results()
    else:
        first = adapter.search(
            keyword=args.keyword,
            library=args.library,
            year=args.year,
            province="全部省份",
            timeliness="全部（仅记录元数据，不筛选）",
            page_size=args.page_size,
        )
        # The V6 result page can preserve the old DOM briefly while the new
        # query is still in flight. Wait until the queried state is stable.
        time.sleep(2.5)
        first = adapter.read_results()

    ungroup = tab.run_js(
        """
        return (() => {
          const radio = document.querySelector(
            '#rightContent input[name="group"][value="2"]'
          );
          if (!radio) return {ok:false, reason:'ungroup control not found'};
          if (!radio.checked) radio.click();
          return {ok:true, checked:radio.checked};
        })()
        """
    ) or {}
    if not ungroup.get("ok"):
        raise RuntimeError(f"cannot switch real results to ungrouped view: {ungroup!r}")
    time.sleep(2)
    first = adapter.read_results()
    first_summary = page_summary(first)
    total_pages = int(first_summary["totalPages"])
    if total_pages < 2:
        raise RuntimeError(
            f"query is not multi-page: totalPages={total_pages}; choose another narrow query"
        )
    if total_pages > args.max_pages:
        raise RuntimeError(
            f"query is too large for this acceptance run: totalPages={total_pages}, "
            f"maxPages={args.max_pages}; no tail scan was performed"
        )

    pages = [first_summary]
    page_hashes = {
        "page1SanitizedHtmlSha256": capture_sanitized_snapshot(
            tab, args.output_dir / "第1页脱敏快照.html"
        )
    }
    current = first
    while int(current.get("page") or 0) < total_pages:
        expected = int(current.get("page") or 0) + 1
        next_state = adapter.next_page(expected)
        next_summary = page_summary(next_state)
        if next_summary["page"] != expected:
            raise RuntimeError(
                f"pagination did not advance: expected {expected}, "
                f"got {next_summary['page']}"
            )
        if next_summary["resultSignatureSha256"] == pages[-1]["resultSignatureSha256"]:
            raise RuntimeError(
                f"adjacent pages have the same result signature at page {expected}"
            )
        pages.append(next_summary)
        current = next_state

    last_hash = capture_sanitized_snapshot(
        tab, args.output_dir / "尾页脱敏快照.html"
    )
    final_state = adapter.read_results()
    final_summary = page_summary(final_state)
    if final_summary["page"] != total_pages:
        raise RuntimeError(
            f"tail verification failed: expected page {total_pages}, "
            f"got {final_summary['page']}"
        )

    evidence = {
        "startedAt": started_at,
        "finishedAt": utc_now(),
        "mode": "real-site-read-only-tail-scan",
        "keyword": args.keyword,
        "year": args.year,
        "library": args.library,
        "pageSize": int(first.get("pageSize") or args.page_size),
        "selectorProfile": diagnostics.get("version"),
        "resultCount": total_pages and first_summary["resultCount"],
        "totalPages": total_pages,
        "pagesScanned": len(pages),
        "pages": pages,
        "firstPageSanitizedHtmlSha256": page_hashes[
            "page1SanitizedHtmlSha256"
        ],
        "lastPageSanitizedHtmlSha256": last_hash,
        "detailOpened": False,
        "downloadClicked": False,
        "downloadEventsStarted": 0,
    }
    evidence_path = args.output_dir / "真实多页尾页只读验收.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
