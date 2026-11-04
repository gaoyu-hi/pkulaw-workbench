from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from DrissionPage import Chromium


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from pkulaw_engine.sources.live_page_diagnostics import (  # noqa: E402
    sanitize_html,
)


SEARCH_RESULTS_SCRIPT = r"""
const clean = value => (value || '').replace(/\s+/g, ' ').trim();
const wantedTitle = arguments[0];
const exactAnchor = [...document.querySelectorAll('a')].find(
  anchor => clean(anchor.innerText || anchor.textContent) === wantedTitle
);
const searchInput = document.querySelector('#txtSearch');
const search = document.querySelector('.search-rect');
const libraries = document.querySelector('#subLibContent');
const left = document.querySelector('#leftContent');
const rightRoot = document.querySelector('#rightContent');
if (
  !search ||
  !libraries ||
  !left ||
  !rightRoot ||
  !exactAnchor ||
  !searchInput ||
  clean(searchInput.value) !== wantedTitle
) return null;
const exactCard = exactAnchor.closest('.item');
const cardText = clean(exactCard?.innerText || exactCard?.textContent);
const publicationMatch = cardText.match(/(\d{4}\.\d{2}\.\d{2})公布/);
const effectiveMatch = cardText.match(/(\d{4}\.\d{2}\.\d{2})施行/);
const resultNumber = clean(
  exactCard?.querySelector('label[name="sortNum"]')?.innerText ||
  exactCard?.querySelector('b[name="sortNum"]')?.innerText
).replace(/[^0-9]/g, '');
const resultAnchor = exactCard?.querySelector('h4 a[href*=".html"]') || exactAnchor;
const resultHref = resultAnchor.getAttribute('href') || '';
const policyIdMatch = resultHref.match(/\/chl\/([^./]+)\.html/);
const infoLinks = exactCard
  ? [...exactCard.querySelectorAll('.info a')].map(anchor => ({
      href: anchor.getAttribute('href') || '',
      text: clean(anchor.innerText || anchor.textContent)
    }))
  : [];
const infoTexts = exactCard
  ? [...exactCard.querySelectorAll('.info .text')].map(node => clean(node.innerText || node.textContent))
  : [];
const metadata = {
  resultNumber,
  title: clean(exactAnchor.innerText || exactAnchor.textContent),
  policyId: policyIdMatch ? policyIdMatch[1] : '',
  resultUrl: resultHref,
  validity: infoLinks.find(item => item.href.includes('Aggs.TimelinessDic'))?.text || '',
  effectLevel: infoLinks.find(item => item.href.includes('Aggs.EffectivenessDic'))?.text || '',
  issuingAuthority: infoLinks.find(item => item.href.includes('Aggs.IssueDepartment'))?.text || '',
  documentNumber: infoTexts.find(text => /(?:令第\d+号|〔\d{4}〕\d+号|\[\d{4}\]\d+号)/.test(text)) || '',
  publicationDate: publicationMatch ? publicationMatch[1] : '',
  effectiveDate: effectiveMatch ? effectiveMatch[1] : ''
};
const right = rightRoot.cloneNode(true);
if (exactAnchor) {
  const exactCard = exactAnchor.closest('.item');
  for (const card of right.querySelectorAll('.accompanying-wrap > .item')) {
    if (exactCard && card.outerHTML !== exactCard.outerHTML) card.remove();
  }
}
for (const node of right.querySelectorAll(
  '.grid-right > :not(.list-wrap), .list-wrap > :not(.list-tool-wrap):not(.accompanying-wrap):not(.pagination)'
)) node.remove();
return {
  url: location.href,
  title: document.title,
  search: search.outerHTML,
  libraries: libraries.outerHTML,
  left: left.outerHTML,
  right: right.outerHTML,
  resultCount: clean(document.querySelector('.list-tool-wrap .filtrater-box .total')?.innerText),
  metadata
};
"""

DETAIL_SCRIPT = r"""
return (() => {
  const root = document.querySelector('.fulltext-wrap');
  if (!root) return null;
  const clone = root.cloneNode(true);
  for (const node of clone.querySelectorAll(
    '.qr_code_container, .tool-position-bottom > :not(:has(#downloadword)), script, style'
  )) node.remove();
  return {url: location.href, title: document.title, body: clone.outerHTML};
})()
"""


def wrap_search_snapshot(payload: dict[str, str]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN" data-pkulaw-profile="pkulaw-v6" data-pkulaw-fixture="live-sanitized">
<head><meta charset="utf-8"><title>北大法宝 V6 搜索页脱敏快照</title></head>
<body>
  <input id="lIsLogin" type="hidden" value="1">
  <input id="IsUserLogin" type="hidden" value="true">
  <div id="mainBody">
    <form id="main_form" action="/law/chl" method="post">{payload["search"]}</form>
    {payload["libraries"]}
  </div>
</body>
</html>
"""


def wrap_results_snapshot(payload: dict[str, str]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN" data-pkulaw-profile="pkulaw-v6" data-pkulaw-fixture="live-sanitized">
<head><meta charset="utf-8"><title>北大法宝 V6 第一页脱敏快照</title></head>
<body>
  <input id="lIsLogin" type="hidden" value="1">
  <input id="IsUserLogin" type="hidden" value="true">
  <div id="mainBody">
    <form id="main_form" action="/law/chl" method="post">{payload["search"]}</form>
    {payload["libraries"]}
    <div class="container">
      {payload["left"]}
      {payload["right"]}
    </div>
  </div>
</body>
</html>
"""


def wrap_detail_snapshot(payload: dict[str, str]) -> str:
    return f"""<!doctype html>
<html lang="zh-CN" data-pkulaw-profile="pkulaw-v6" data-pkulaw-fixture="live-sanitized">
<head><meta charset="utf-8"><title>北大法宝 V6 详情页脱敏快照</title></head>
<body>
  <input id="lIsLogin" type="hidden" value="1">
  <div id="body1" class="container fulltext-container">{payload["body"]}</div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--mode", choices=("search-results", "detail"), required=True)
    parser.add_argument("--title", default="中华人民共和国国防动员法(2026修订)")
    parser.add_argument("--redact-term", action="append", default=[])
    args = parser.parse_args()

    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    tab = next(
        item
        for item in browser.get_tabs()
        if str(item.url).startswith("https://www.pkulaw.com/")
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    terms = tuple(args.redact_term)

    if args.mode == "search-results":
        payload = tab.run_js(SEARCH_RESULTS_SCRIPT, args.title)
        if not payload:
            raise RuntimeError(
                "current V6 page is not the approved exact-title first-page result"
            )
        search = sanitize_html(wrap_search_snapshot(payload), terms)
        results = sanitize_html(wrap_results_snapshot(payload), terms)
        search_path = args.output_dir / "搜索页脱敏快照.html"
        results_path = args.output_dir / "结果第一页脱敏快照.html"
        search_bytes = search.encode("utf-8")
        results_bytes = results.encode("utf-8")
        search_path.write_bytes(search_bytes)
        results_path.write_bytes(results_bytes)
        evidence = {
            "testedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "mode": "exact-title-first-page-read-only",
            "requestedTitle": args.title,
            "resultCount": payload.get("resultCount", ""),
            "metadata": payload.get("metadata", {}),
            "downloadClicked": False,
            "detailOpened": False,
            "sanitizedSearchHtml": {
                "path": search_path.name,
                "sha256": hashlib.sha256(search_bytes).hexdigest(),
            },
            "sanitizedResultsHtml": {
                "path": results_path.name,
                "sha256": hashlib.sha256(results_bytes).hexdigest(),
            },
        }
        (args.output_dir / "真实只读适配证据.json").write_bytes(
            (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            )
        )
        print(
            f"search={len(search)} results={len(results)} "
            f"resultCount={payload.get('resultCount', '')} "
            f"policyId={evidence['metadata'].get('policyId', '')}"
        )
    else:
        payload = tab.run_js(DETAIL_SCRIPT)
        if not payload:
            raise RuntimeError("required V6 detail root was not found")
        detail = sanitize_html(wrap_detail_snapshot(payload), terms)
        (args.output_dir / "详情页脱敏快照.html").write_text(detail, encoding="utf-8")
        print(f"detail={len(detail)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
