from __future__ import annotations

import argparse
import json
from pathlib import Path

from DrissionPage import Chromium


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--close-child", action="store_true")
    parser.add_argument("--close-extra-pkulaw", action="store_true")
    parser.add_argument("--close-non-main", action="store_true")
    parser.add_argument("--open-dialog", action="store_true")
    parser.add_argument("--capture-submit", action="store_true")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    tabs = browser.get_tabs()
    result: dict[str, object] = {
        "tabs": [
            {
                "url": str(tab.url),
                "title": str(tab.title or ""),
            }
            for tab in tabs
        ]
    }
    main_tabs = [
        tab
        for tab in tabs
        if str(tab.url).startswith("https://www.pkulaw.com/")
        and "/Download/DownloadCatalog" not in str(tab.url)
        and "/BatchDownload.html" not in str(tab.url)
    ]
    child_tabs = [
        tab
        for tab in tabs
        if "/Download/DownloadCatalog" in str(tab.url)
    ]
    if args.close_extra_pkulaw:
        for extra in [
            tab
            for tab in tabs
            if str(tab.url).startswith("https://www.pkulaw.com/")
            and tab not in main_tabs[:1]
        ]:
            extra.close()
    if args.close_non_main:
        keep_main = main_tabs[0] if main_tabs else None
        for extra in [
            tab
            for tab in tabs
            if tab is not keep_main
            and not str(tab.url).startswith("file://")
        ]:
            extra.close()
    if main_tabs:
        main = main_tabs[0]
        if args.open_dialog:
            main.run_js(
                r"""
                return (() => {
                  const checkbox = document.querySelector(
                    'input[type="checkbox"][name="recordList"]'
                  );
                  if (checkbox && !checkbox.checked) checkbox.click();
                  const button = [...document.querySelectorAll(
                    '.list-tool-wrap a.down-all'
                  )].find(item => item.getClientRects().length);
                  if (button) button.click();
                  return {
                    checkboxChecked: Boolean(checkbox?.checked),
                    buttonClicked: Boolean(button)
                  };
                })()
                """
            )
            import time

            time.sleep(2)
        if args.capture_submit:
            main.run_js(
                r"""
                return (() => {
                  window.__pkulawCapturedCatalogSubmission = null;
                  if (!window.__pkulawNativeFormSubmit) {
                    window.__pkulawNativeFormSubmit =
                      HTMLFormElement.prototype.submit;
                  }
                  HTMLFormElement.prototype.submit = function () {
                    const entries = {};
                    for (const [key, value] of new FormData(this).entries()) {
                      entries[key] = key === 'Authorization'
                        ? '<redacted>'
                        : String(value);
                    }
                    window.__pkulawCapturedCatalogSubmission = {
                      action: this.getAttribute('action') || '',
                      method: this.getAttribute('method') || '',
                      target: this.getAttribute('target') || '',
                      entries
                    };
                  };
                  if (typeof recordBatchDownload === 'function') {
                    recordBatchDownload();
                  }
                  return true;
                })()
                """
            )
            import time

            time.sleep(3)
            result["capturedSubmission"] = main.run_js(
                "return window.__pkulawCapturedCatalogSubmission"
            )
        result["main"] = main.run_js(
            r"""
            return (() => {
              const clean = value => (value || '').replace(/\s+/g, ' ').trim();
              const dialog = document.querySelector(
                '[name="bathDownloadContainer"], .layer-down-content'
              );
              const formats = [...document.querySelectorAll(
                'input[name="batchDownload"]'
              )];
              const confirm = document.querySelector('#batchDownload');
              const catalog = document.querySelector('#catalog-format');
              const formControls = dialog
                ? [...dialog.querySelectorAll('input,select,textarea')].map(control => ({
                    tag: control.tagName,
                    type: control.type || '',
                    name: control.name || '',
                    id: control.id || '',
                    value: control.value || '',
                    checked: Boolean(control.checked),
                    disabled: Boolean(control.disabled),
                    outerHTML: control.outerHTML
                  }))
                : [];
              return {
                url: location.href,
                dialogText: clean(dialog?.innerText || dialog?.textContent),
                dialogHtml: dialog?.outerHTML.slice(0, 60000) || '',
                formats: formats.map(control => ({
                  value: control.value || '',
                  checked: Boolean(control.checked),
                  outerHTML: control.outerHTML
                })),
                confirmHtml: confirm?.outerHTML || '',
                onclicks: {
                  confirm: confirm?.getAttribute('onclick') || '',
                  catalog: catalog?.outerHTML || '',
                  functionSource:
                    typeof recordBatchDownload === 'function'
                      ? recordBatchDownload.toString()
                      : '',
                  catalogFunctionSource:
                    typeof downloadRecordExcel === 'function'
                      ? downloadRecordExcel.toString()
                      : ''
                },
                hiddenState: {
                  accessTokenPresent: Boolean(
                    document.querySelector('#access_token')?.value
                  ),
                  library: document.querySelector(
                    'input[name="Library"], input[name="library"]'
                  )?.value || '',
                  showType: document.querySelector(
                    'input[name="ShowType"]'
                  )?.value || '',
                  orderByIndex: document.querySelector(
                    'input[name="OrderByIndex"]'
                  )?.value || '',
                  viewType: document.querySelector(
                    'input[name="viewType"]:checked'
                  )?.value || '',
                  originalListCount: document.querySelectorAll(
                    'div[name="OriginalList"] input[name="recordList"]'
                  ).length,
                  originalListChecked: document.querySelectorAll(
                    'div[name="OriginalList"] input[name="recordList"]:checked'
                  ).length,
                  allRecordChecked: document.querySelectorAll(
                    'input[name="recordList"]:checked'
                  ).length
                },
                scripts: [...document.scripts].map(script => ({
                  src: script.getAttribute('src') || '',
                  inlineLength: (script.textContent || '').length,
                  inlineHasBatchDownload: (script.textContent || '')
                    .includes('recordBatchDownload')
                })).filter(item => item.src || item.inlineHasBatchDownload),
                formControls
              };
            })()
            """
        )
    if child_tabs:
        child = child_tabs[0]
        result["child"] = child.run_js(
            r"""
            return {
              url: location.href,
              title: document.title,
              bodyText: document.body?.innerText || '',
              bodyHtml: document.documentElement?.outerHTML.slice(0, 60000) || ''
            }
            """
        )
        if args.close_child:
            child.close()
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
