from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from DrissionPage import Chromium


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from pkulaw_engine.body_quality import (  # noqa: E402
    _CLAUSE_LINE_RE,
    _SECTION_LINE_RE,
    assess_body_completeness,
    detail_body_extraction_js,
)
from pkulaw_engine.logging_utils import JsonLineLogger  # noqa: E402
from pkulaw_engine.sources.live_page_diagnostics import sanitize_html  # noqa: E402
from pkulaw_engine.sources.pkulaw import PkulawAdapter  # noqa: E402
from pkulaw_engine.sources.selector_profile import load_selector_profile  # noqa: E402


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", required=True, type=int)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--redact-term", action="append", default=[])
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    profile_path = (
        PROJECT_ROOT
        / "engine"
        / "pkulaw_engine"
        / "sources"
        / "profiles"
        / "pkulaw-v6.json"
    )
    profile = load_selector_profile(profile_path)
    selectors = dict(profile.selectors)
    body_js = detail_body_extraction_js(str(selectors["detailBody"]))

    browser = Chromium(f"127.0.0.1:{args.debug_port}")
    candidates = [
        tab
        for tab in browser.get_tabs()
        if str(tab.url).startswith("https://www.pkulaw.com/")
        and "/Download/DownloadCatalog" not in str(tab.url)
        and "/BatchDownload.html" not in str(tab.url)
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected exactly one pkulaw tab, found {len(candidates)}"
        )
    tab = candidates[0]
    tab.get(args.url)
    tab.wait.doc_loaded(timeout=30)
    tab.wait(1)

    payload: dict[str, Any] = tab.run_js(
        f"""
        return (() => {{
          const query = selector => document.querySelector(selector);
          const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
          const titleElement = query({json.dumps(selectors["detailTitle"])});
          const title = clean(titleElement?.innerText || titleElement?.textContent)
            .replace(/\\s*点击标题可复制\\s*$/, '');
          {body_js}
          const html = document.documentElement.outerHTML;
          return {{
            title,
            body,
            bodySegments,
            url: location.href,
            documentTitle: document.title,
            downloadAvailable: !!query({json.dumps(selectors["downloadWord"])}),
            html
          }};
        }})()
        """
    ) or {}
    body = str(payload.get("body") or "")
    assessment = assess_body_completeness(body)
    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in body.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        if line.strip()
    ]
    sections = [
        {"lineIndex": index, "text": line}
        for index, line in enumerate(lines)
        if _SECTION_LINE_RE.match(line)
    ]
    clauses = [
        {"lineIndex": index, "text": line}
        for index, line in enumerate(lines)
        if _CLAUSE_LINE_RE.match(line)
    ]
    empty_sections: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        start = int(section["lineIndex"])
        end = (
            int(sections[index + 1]["lineIndex"])
            if index + 1 < len(sections)
            else len(lines)
        )
        has_clause = any(start < int(item["lineIndex"]) < end for item in clauses)
        if not has_clause:
            empty_sections.append(
                {
                    "section": section,
                    "nextLines": lines[start : min(end, start + 12)],
                }
            )

    sanitized = sanitize_html(
        str(payload.get("html") or ""),
        tuple(args.redact_term),
    )
    sanitized_bytes = sanitized.encode("utf-8")
    (args.output_dir / "详情页脱敏快照.html").write_bytes(sanitized_bytes)
    (args.output_dir / "正文提取.txt").write_text(body + "\n", encoding="utf-8")
    screenshot_path = args.output_dir / "详情页截图.png"
    tab.get_screenshot(path=str(screenshot_path))

    evidence = {
        "capturedAt": utc_now(),
        "url": payload.get("url"),
        "documentTitle": payload.get("documentTitle"),
        "title": payload.get("title"),
        "downloadClicked": False,
        "downloadAvailable": payload.get("downloadAvailable"),
        "assessment": assessment.to_dict(),
        "bodySha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "sanitizedHtml": {
            "path": str(args.output_dir / "详情页脱敏快照.html"),
            "sha256": hashlib.sha256(sanitized_bytes).hexdigest(),
        },
        "sections": sections,
        "clauses": clauses,
        "emptySections": empty_sections,
        "segments": payload.get("bodySegments") or [],
    }
    evidence_path = args.output_dir / "正文诊断.json"
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
