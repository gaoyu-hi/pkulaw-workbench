from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from DrissionPage import Chromium


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    candidates = [
        tab
        for tab in browser.get_tabs()
        if str(tab.url).startswith("https://www.pkulaw.com/")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one pkulaw tab, found {len(candidates)}"
        )
    tab = candidates[0]

    result = tab.run_js(
        r"""
        return (() => {
          const clean = value => (value || '').replace(/\s+/g, ' ').trim();
          const roots = [...document.querySelectorAll('#leftContent .recursion-tree')]
            .map((root, index) => {
              const rootLink = root.querySelector('a.fold, a[searchname], .tree-title');
              return {
                index,
                searchName: rootLink?.getAttribute('searchname') || '',
                title: clean(rootLink?.innerText || rootLink?.textContent),
                className: String(root.className || ''),
                dataId: root.getAttribute('data-id') || '',
                rootHtml: root.outerHTML.slice(0, 12000),
                links: [...root.querySelectorAll('a')].slice(0, 80).map(anchor => ({
                  text: clean(anchor.innerText || anchor.textContent),
                  title: anchor.getAttribute('title') || '',
                  className: String(anchor.className || ''),
                  searchName: anchor.getAttribute('searchname') || '',
                  href: anchor.getAttribute('href') || '',
                  dataId: anchor.getAttribute('data-id') || ''
                }))
              };
            });
          const yearFold = document.querySelector(
            '#leftContent .recursion-tree a.fold[searchname="IssueDate"]'
          );
          const timelinessFold = document.querySelector(
            '#leftContent .recursion-tree a.fold[searchname="TimelinessDic"]'
          );
          return {
            url: location.href,
            title: document.title,
            yearFold: yearFold ? yearFold.outerHTML : '',
            timelinessFold: timelinessFold ? timelinessFold.outerHTML : '',
            pageSize: (() => {
              const trigger = document.querySelector('.articleSelect');
              const hidden = document.querySelector(
                '#right_form input[name="Pager.PageSize"], input[name="Pager.PageSize"]'
              );
              const items = [...document.querySelectorAll('#recordPage dd')];
              return {
                trigger: trigger ? trigger.outerHTML : '',
                triggerText: clean(trigger?.innerText || trigger?.textContent),
                hiddenValue: hidden?.value || '',
                itemCount: items.length,
                items: items.map(item => clean(item.innerText || item.textContent))
              };
            })(),
            searchMode: {
              exactChecked: Boolean(document.querySelector('#accurate')?.checked),
              exactOuterHtml: document.querySelector('#accurate')?.outerHTML || '',
              searchRectHtml:
                document.querySelector('.search-rect')?.outerHTML.slice(0, 30000) || '',
              areaSelect: (() => {
                const root = document.querySelector('.areajSelect');
                return {
                  html: root?.outerHTML.slice(0, 20000) || '',
                  text: clean(root?.innerText || root?.textContent),
                  items: [...(root?.querySelectorAll('li,a,span') || [])].map(item => ({
                    tag: item.tagName,
                    text: clean(item.innerText || item.textContent),
                    className: String(item.className || ''),
                    value: item.getAttribute('value') || '',
                    dataValue: item.getAttribute('data-value') || '',
                    href: item.getAttribute('href') || ''
                  }))
                };
              })(),
              modes: [...document.querySelectorAll(
                'input[name="Search_Mode"], select[name="Search_Mode"]'
              )].map(control => ({
                type: control.type || '',
                value: control.value || '',
                checked: Boolean(control.checked),
                outerHTML: control.outerHTML
              })),
              types: [...document.querySelectorAll(
                'input[name="SearchKeywordType"], select[name="SearchKeywordType"]'
              )].map(control => ({
                tag: control.tagName,
                type: control.type || '',
                value: control.value || '',
                checked: Boolean(control.checked),
                disabled: Boolean(control.disabled),
                outerHTML: control.outerHTML
              }))
            },
            roots
          };
        })()
        """
    )
    path = args.output_dir / "K1_真实筛选树探针.json"
    data = (
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    path.write_bytes(data)
    print(
        json.dumps(
            {
                "path": str(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "url": result.get("url"),
                "rootCount": len(result.get("roots") or []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
