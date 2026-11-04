from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from DrissionPage import Chromium


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from pkulaw_engine.browser_bridge import BrowserBridge  # noqa: E402
from pkulaw_engine.logging_utils import JsonLineLogger  # noqa: E402
from pkulaw_engine.sources.pkulaw import PkulawAdapter  # noqa: E402


def browser_target(tab: Any) -> dict[str, Any]:
    return tab.run_js(
        """
        return {
          url: location.href,
          current: [...document.querySelectorAll(
            '#subLibraries li.current a'
          )].map(link => ({
            library: link.getAttribute('library') || '',
            text: (link.textContent || '').replace(/\\s+/g, ' ').trim()
          })),
          libraries: [...document.querySelectorAll(
            '#subLibraries a[library]'
          )].map(link => link.getAttribute('library') || ''),
          hiddenLibraries: [...document.querySelectorAll(
            'input[name="Library"], input[name="library"]'
          )].map(input => input.value),
          query: document.querySelector('#txtSearch')?.value || '',
          total: (document.querySelector(
            '.list-tool-wrap .filtrater-box .total, .list-tool-wrap .total'
          )?.textContent || '').replace(/\\s+/g, ' ').trim()
        }
        """
    ) or {}


def session_summary(state: dict[str, Any]) -> dict[str, Any]:
    session = dict(state.get("searchSession") or {})
    return {
        "searchId": session.get("searchId"),
        "query": session.get("query"),
        "library": session.get("library"),
        "year": session.get("year"),
        "page": session.get("page"),
        "totalPages": session.get("totalPages"),
        "resultCount": session.get("resultCount"),
        "rowCount": session.get("rowCount"),
        "url": session.get("url"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--query",
        default="中华人民共和国国防动员法(2026修订)",
    )
    args = parser.parse_args()

    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    tabs = [
        tab
        for tab in browser.get_tabs()
        if str(tab.url).startswith("https://www.pkulaw.com/")
        and "/BatchDownload.html" not in str(tab.url)
        and "/Download/DownloadCatalog" not in str(tab.url)
    ]
    if len(tabs) != 1:
        raise RuntimeError(f"expected one Pkulaw main tab, found {len(tabs)}")
    tab = tabs[0]
    bridge = BrowserBridge(
        JsonLineLogger(args.output.with_suffix(".jsonl"))
    )
    bridge.browser = browser
    bridge.main_tab = tab
    bridge.entry_url = "https://www.pkulaw.com/"
    bridge.origin = "https://www.pkulaw.com"
    bridge.target_url = str(tab.url)
    adapter = PkulawAdapter(bridge, bridge.logger)

    ensure_calls: list[dict[str, Any]] = []
    mode_calls: list[dict[str, Any]] = []
    submit_calls: list[dict[str, Any]] = []
    original_ensure = adapter._ensure_library
    original_mode = adapter._ensure_search_mode
    original_submit = adapter._submit_search

    def observed_ensure(library_code: str) -> None:
        before = browser_target(tab)
        original_ensure(library_code)
        after = browser_target(tab)
        ensure_calls.append(
            {
                "library": library_code,
                "before": before,
                "after": after,
            }
        )

    adapter._ensure_library = observed_ensure  # type: ignore[method-assign]

    def observed_mode(*args: Any, **kwargs: Any) -> dict[str, Any]:
        before = browser_target(tab)
        result = original_mode(*args, **kwargs)
        after = browser_target(tab)
        mode_calls.append(
            {
                "before": before,
                "after": after,
                "result": result,
            }
        )
        return result

    adapter._ensure_search_mode = observed_mode  # type: ignore[method-assign]

    def observed_submit(*args: Any, **kwargs: Any) -> dict[str, Any]:
        before = browser_target(tab)
        result = original_submit(*args, **kwargs)
        after = browser_target(tab)
        submit_calls.append(
            {
                "before": before,
                "after": after,
                "result": result,
            }
        )
        return result

    adapter._submit_search = observed_submit  # type: ignore[method-assign]

    central = adapter.search(
        keyword=args.query,
        match_mode="title",
        library="central",
        year="全部年份",
        province="全部省份",
        timeliness="现行有效",
        page_size=20,
    )
    time.sleep(1)
    after_central = browser_target(tab)
    local: dict[str, Any] | None = None
    local_error: dict[str, str] | None = None
    try:
        local = adapter.search(
            keyword=args.query,
            match_mode="title",
            library="local",
            year="全部年份",
            province="全部省份",
            timeliness="现行有效",
            page_size=20,
        )
    except Exception as exc:
        local_error = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    after_local = browser_target(tab)

    result = {
        "ensureCalls": ensure_calls,
        "modeCalls": mode_calls,
        "submitCalls": submit_calls,
        "afterCentral": after_central,
        "central": session_summary(central),
        "local": session_summary(local) if local else None,
        "localError": local_error,
        "afterLocal": after_local,
        "downloadsStarted": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
