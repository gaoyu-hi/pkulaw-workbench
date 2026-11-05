from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from ..browser_bridge import BrowserBridge
from ..logging_utils import JsonLineLogger
from ..policy import normalize_title, safe_filename
from .pkulaw import (
    InstitutionQuotaReached,
    LoginRequired,
    ManualActionRequired,
)


class GovernmentSourceUnavailable(RuntimeError):
    pass


def select_exact_official_candidate(
    candidates: list[dict[str, str]],
    title: str,
    *,
    fixture_mode: bool,
) -> dict[str, str]:
    expected = normalize_title(title)
    exact = [
        candidate
        for candidate in candidates
        if normalize_title(str(candidate.get("title") or "")) == expected
    ]
    unique: dict[str, dict[str, str]] = {}
    for candidate in exact:
        url = str(candidate.get("url") or "").strip()
        host = (urlparse(url).hostname or "").lower()
        marked_official = candidate.get("official") == "true"
        if not url or not (
            host == "gov.cn"
            or host.endswith(".gov.cn")
            or (fixture_mode and marked_official)
        ):
            continue
        unique.setdefault(url, candidate)
    if not unique:
        raise GovernmentSourceUnavailable(f"政府官网检索未找到可核验的同标题页面：{title}")
    if len(unique) > 1:
        raise GovernmentSourceUnavailable(f"政府官网存在多个同标题来源，需人工消歧：{title}")
    return next(iter(unique.values()))


class GovernmentProvider:
    """Finds and verifies one exact-title official government source."""

    def __init__(self, bridge: BrowserBridge, logger: JsonLineLogger) -> None:
        self.bridge = bridge
        self.logger = logger

    def resolve(
        self,
        *,
        title: str,
        search_template: str,
        evidence_directory: Path,
    ) -> dict[str, Any]:
        template = search_template.strip()
        if not template:
            raise GovernmentSourceUnavailable("未配置政府官网检索地址")
        encoded_title = quote(title, safe="")
        target = template.replace("{title}", encoded_title)
        target = urljoin(self.bridge.entry_url, target)
        entry_host = urlparse(self.bridge.entry_url).hostname or ""
        fixture_mode = entry_host in {"127.0.0.1", "localhost"}

        with self.bridge.transaction() as tab:
            tab.get(target)
        deadline = time.monotonic() + 25
        candidates: list[dict[str, str]] = []
        while time.monotonic() < deadline:
            time.sleep(0.35)
            with self.bridge.transaction() as tab:
                candidates = tab.run_js(
                    """
                    return (() => {
                      const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                      const officialUrl = anchor => {
                        const mdUrl = anchor.getAttribute('data-mdurl');
                        if (mdUrl) return mdUrl;
                        const card = anchor.closest('.res-list');
                        const direct = card?.getAttribute('data-pcurl');
                        if (direct) return direct;
                        try {
                          const url = new URL(anchor.href || '');
                          const decoded = url.searchParams.get('u');
                          if (
                            decoded &&
                            (url.hostname === 'so.com' ||
                              url.hostname.endsWith('.so.com'))
                          ) {
                            return decoded;
                          }
                        } catch (_) {}
                        return anchor.href || '';
                      };
                      const anchors = [...document.querySelectorAll(
                        '.gov-result a[data-official], li.b_algo h2 a, ' +
                        '#b_results h2 a, ' +
                        'li.res-list h3.res-title a[data-mdurl], ' +
                        'div.res-list a.alink'
                      )];
                      return anchors.map(anchor => {
                        const nestedTitle = anchor.querySelector(
                          '.res-title > em, h3.res-title > em, em'
                        );
                        return {
                          title: clean(
                            (nestedTitle || anchor).innerText ||
                            (nestedTitle || anchor).textContent
                          ),
                          url: officialUrl(anchor),
                          official: anchor.getAttribute('data-official') || ''
                        };
                      });
                    })()
                    """
                ) or []
            exact = [
                candidate
                for candidate in candidates
                if normalize_title(candidate.get("title", "")) == normalize_title(title)
            ]
            if exact:
                break
        else:
            raise GovernmentSourceUnavailable(
                f"政府官网检索未找到标题完全一致的结果：{title}"
            )

        candidate = select_exact_official_candidate(
            exact,
            title,
            fixture_mode=fixture_mode,
        )
        url = str(candidate.get("url") or "")
        with self.bridge.transaction() as tab:
            tab.get(url)
            final_host = (urlparse(str(tab.url or "")).hostname or "").lower()
            if not fixture_mode and not (
                final_host == "gov.cn"
                or final_host.endswith(".gov.cn")
            ):
                raise GovernmentSourceUnavailable(
                    "政府官网候选最终跳转到非政府域名："
                    f"{tab.url or '<missing>'}"
                )
        page = self._wait_for_detail(title)
        if not page:
            raise GovernmentSourceUnavailable(f"政府官网页面未通过标题核验：{title}")

        evidence_directory.mkdir(parents=True, exist_ok=True)
        stem = safe_filename(title, 80)
        html_path = evidence_directory / f"{stem}.html"
        screenshot_path = evidence_directory / f"{stem}.png"
        with self.bridge.transaction() as tab:
            page_html = str(tab.html or "")
            if len(page_html.strip()) < 80:
                raise GovernmentSourceUnavailable("政府官网 HTML 证据为空或过短")
            html_path.write_text(page_html, encoding="utf-8")
            try:
                captured = tab.run_cdp(
                    "Page.captureScreenshot",
                    format="png",
                    captureBeyondViewport=False,
                )
                encoded = captured.get("data") if isinstance(captured, dict) else ""
                if not encoded:
                    raise RuntimeError("CDP screenshot returned no data")
                screenshot_path.write_bytes(base64.b64decode(encoded))
            except Exception as exc:
                self.logger.write(
                    "warn",
                    "government.screenshot_failed",
                    "Official page screenshot failed",
                    {"title": title, "error": repr(exc)},
                )
                raise GovernmentSourceUnavailable(
                    "政府官网页面截图失败；请保持内置浏览器可见后重试"
                ) from exc
        if not screenshot_path.is_file() or screenshot_path.stat().st_size == 0:
            raise GovernmentSourceUnavailable("政府官网页面截图未能保存")
        return {
            **page,
            "searchUrl": target,
            "sourceUrl": url,
            "htmlPath": str(html_path),
            "htmlSha256": hashlib.sha256(html_path.read_bytes()).hexdigest(),
            "screenshotPath": str(screenshot_path),
            "screenshotSha256": hashlib.sha256(screenshot_path.read_bytes()).hexdigest(),
        }

    def _wait_for_detail(self, expected_title: str) -> dict[str, Any] | None:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            time.sleep(0.35)
            with self.bridge.transaction() as tab:
                state = tab.run_js(
                    """
                return (() => {
                  const firstText = selectors => {
                    for (const selector of selectors) {
                      const element = document.querySelector(selector);
                      const value = element
                        ? (element.innerText || element.textContent || '').trim()
                        : '';
                      if (value) return value;
                    }
                    return '';
                  };
                  const body = firstText([
                    '#gov-body', '.gov-body', '.article-content', 'article', 'main'
                  ]);
                  const anchors = [...document.querySelectorAll('a[href]')]
                    .map(anchor => ({
                      text: (anchor.innerText || anchor.textContent || '').trim(),
                      url: anchor.href || ''
                    }))
                    .filter(item => /\\.(?:docx?|pdf)(?:[?#]|$)/i.test(item.url));
                  const fields = Object.fromEntries(
                    [...document.querySelectorAll('[data-gov-field]')].map(element => [
                      element.getAttribute('data-gov-field'),
                      (element.innerText || element.textContent || '').trim()
                    ])
                  );
                  const visible = element => !!(element && element.getClientRects().length &&
                    getComputedStyle(element).visibility !== 'hidden');
                  const captcha = [...document.querySelectorAll('.drag_text, #captcha-overlay')]
                    .some(visible);
                  const quota = /(今日|当日|今天).{0,24}(下载|正文|访问).{0,20}(已用满|用完|达到上限|已达上限)/.test(
                    (document.body?.innerText || '').replace(/\\s+/g, ' ')
                  );
                  return {
                    title: firstText(['#gov-title', 'h1', '.article-title']),
                    body,
                    attachments: anchors,
                    fields,
                    captcha,
                    quota,
                    url: location.href
                  };
                })()
                    """
                ) or {}
            if state.get("quota"):
                raise InstitutionQuotaReached(f"政府官网出现访问额度限制：{state.get('url')}")
            if state.get("captcha"):
                raise ManualActionRequired("政府官网出现滑块或人机验证，请人工处理后继续")
            if normalize_title(str(state.get("title") or "")) == normalize_title(expected_title):
                if len(str(state.get("body") or "")) >= 20 or state.get("attachments"):
                    return state
            if "/login" in str(state.get("url") or "").lower():
                raise LoginRequired(f"政府官网需要登录：{state.get('url')}")
        return None

    def trigger_attachment(self, attachment_url: str) -> dict[str, Any]:
        with self.bridge.transaction() as tab:
            result = tab.run_js(
                f"""
                return (() => {{
                  const wanted = {json.dumps(attachment_url)};
                  const anchor = [...document.querySelectorAll('a[href]')].find(
                    item => item.href === wanted
                  );
                  if (!anchor) return {{ok:false, reason:'attachment link not found'}};
                  setTimeout(() => anchor.click(), 150);
                  return {{ok:true, href:anchor.href}};
                }})()
                """
            ) or {}
            if not result.get("ok"):
                raise GovernmentSourceUnavailable(f"政府官网附件入口无效：{result!r}")
            return result
