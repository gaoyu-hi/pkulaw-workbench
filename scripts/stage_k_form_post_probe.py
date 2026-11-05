from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from DrissionPage import Chromium


def library_state(tab: Any) -> dict[str, Any]:
    return tab.run_js(
        """
        return {
          url: location.href,
          readyState: document.readyState,
          current: [...document.querySelectorAll(
            '#subLibraries li.current a'
          )].map(link => link.getAttribute('library') || ''),
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


def wait_until(
    tab: Any,
    predicate: Any,
    *,
    timeout: float,
    interval: float = 0.25,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            last = library_state(tab)
        except Exception as exc:
            last = {
                "waitingAfterReload": True,
                "errorType": type(exc).__name__,
            }
            time.sleep(interval)
            continue
        if predicate(last):
            return last
        time.sleep(interval)
    raise RuntimeError(f"condition timed out: {last!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--output", type=Path, required=True)
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

    tab.get("https://www.pkulaw.com/law?way=listCrumbs")
    wait_until(
        tab,
        lambda state: "chl" in state.get("libraries", []),
        timeout=15,
    )
    local_link = tab.ele('css:a[library="lar"]', timeout=5)
    if not local_link:
        raise RuntimeError("local library link was not found")
    local_link.click()
    before_submit = wait_until(
        tab,
        lambda state: state.get("current") == ["lar"]
        and "chl" in state.get("libraries", []),
        timeout=15,
    )

    form_data = tab.run_js(
        """
        (() => {
          const query = arguments[0];
          const form = document.querySelector('#main_form');
          document.querySelectorAll(
            'input[name="Library"], input[name="library"]'
          ).forEach(control => {
            control.value = 'lar';
            control.dispatchEvent(new Event('change', {bubbles:true}));
          });
          for (const name of [
            'ClassFlag',
            'PreviousLib',
            'LastLibForChangeColumn'
          ]) {
            document.querySelectorAll(`input[name=${name}]`).forEach(control => {
              control.value = 'lar';
              control.dispatchEvent(new Event('change', {bubbles:true}));
            });
          }
          form.setAttribute('action', 'https://www.pkulaw.com/law/lar');
          document.querySelectorAll('input[name="Keywords"]').forEach(
            control => { control.value = query; }
          );
          document.querySelectorAll('input[name="PreKeywords"]').forEach(
            control => { control.value = ''; }
          );
          document.querySelectorAll('input[name="MatchType"]').forEach(
            control => { control.value = 'Exact'; }
          );
          document.querySelectorAll('input[name="SearchKeywordType"]').forEach(
            control => { control.value = 'Title'; }
          );
          document.querySelectorAll('input[name="TitleKeywords"]').forEach(
            control => { control.value = query; }
          );
          document.querySelectorAll('input[name="FullTextKeywords"]').forEach(
            control => { control.value = ''; }
          );
          const input = document.querySelector('#txtSearch');
          input.value = query;
          input.dispatchEvent(new Event('input', {bubbles:true}));
          input.dispatchEvent(new Event('change', {bubbles:true}));
          return {
            action: form.action,
            data: Object.fromEntries(new FormData(form).entries())
          };
        })()
        """,
        args.query,
    )
    tab.run_js("document.querySelector('#main_form').submit();")
    after_submit = wait_until(
        tab,
        lambda state: state.get("current") == ["lar"]
        and state.get("query") == args.query,
        timeout=20,
    )

    result = {
        "beforeSubmit": before_submit,
        "form": form_data,
        "afterSubmit": after_submit,
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
