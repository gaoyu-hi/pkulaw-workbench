from __future__ import annotations

import argparse
import json
import sys

from DrissionPage import Chromium


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", required=True, type=int)
    parser.add_argument("--close-empty", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    payload = []
    for tab in browser.get_tabs():
        try:
            body_text = str(
                tab.run_js(
                    "return document.body ? document.body.innerText : ''"
                )
                or ""
            )
            html = str(
                tab.run_js(
                    "return document.documentElement "
                    "? document.documentElement.outerHTML : ''"
                )
                or ""
            )
            selector_counts = tab.run_js(
                """
                const selectors = {
                  rightContent: '#rightContent',
                  rightContentListWrap: '#rightContent .list-wrap',
                  listWrap: '.list-wrap',
                  accompanyingWrap: '.accompanying-wrap',
                  listToolWrap: '.list-tool-wrap',
                  pagination: '.pagination, .page-index, .pager',
                  recordSearch: 'form[action*="RecordSearch"]'
                };
                return Object.fromEntries(
                  Object.entries(selectors).map(([key, selector]) => [
                    key,
                    document.querySelectorAll(selector).length
                  ])
                );
                """
            )
        except Exception as error:  # noqa: BLE001
            body_text = f"<inspect failed: {error}>"
            html = ""
            selector_counts = {}
        payload.append(
            {
                "id": tab.tab_id,
                "url": tab.url,
                "title": tab.title,
                "bodyText": body_text[:500],
                "bodyTextLength": len(body_text),
                "htmlLength": len(html),
                "selectorCounts": selector_counts,
            }
        )
    closed_empty = []
    if args.close_empty:
        for item in payload:
            if (
                not item["url"]
                and item["bodyTextLength"] == 0
                and item["htmlLength"] <= 64
            ):
                tab = next(
                    candidate
                    for candidate in browser.get_tabs()
                    if candidate.tab_id == item["id"]
                )
                tab.close()
                closed_empty.append(item["id"])
    print(
        json.dumps(
            {"tabs": payload, "closedEmptyTabs": closed_empty},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
