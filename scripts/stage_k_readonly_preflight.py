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


def summarize_session(state: dict[str, Any]) -> dict[str, Any]:
    session = dict(state.get("searchSession") or {})
    rows = list(state.get("rows") or [])
    return {
        "searchId": session.get("searchId"),
        "query": session.get("query"),
        "matchMode": session.get("matchMode"),
        "library": session.get("library"),
        "year": session.get("year"),
        "province": session.get("province"),
        "timeliness": session.get("timeliness"),
        "requestedPageSize": session.get("requestedPageSize"),
        "actualPageSize": session.get("actualPageSize"),
        "page": session.get("page"),
        "totalPages": session.get("totalPages"),
        "resultCount": session.get("resultCount"),
        "rowCount": session.get("rowCount"),
        "groupingMode": session.get("groupingMode"),
        "resultSignature": session.get("resultSignature"),
        "url": session.get("url"),
        "actualYear": session.get("actualYear"),
        "actualProvince": session.get("actualProvince"),
        "actualTimeliness": session.get("actualTimeliness"),
        "firstRowTitle": str(rows[0].get("title") or "") if rows else "",
        "firstRowPolicyId": (
            str(rows[0].get("policyId") or "") if rows else ""
        ),
    }


def capture_snapshot(
    tab: Any,
    output_path: Path,
    redactions: tuple[str, ...],
) -> dict[str, str]:
    raw = str(
        tab.run_js("return document.documentElement.outerHTML") or ""
    )
    sanitized = sanitize_html(raw, redactions)
    data = sanitized.encode("utf-8")
    output_path.write_bytes(data)
    return {
        "path": str(output_path),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--query",
        default="中华人民共和国国防动员法(2026修订)",
    )
    parser.add_argument(
        "--match-mode",
        choices=("title", "keyword"),
        default="title",
    )
    parser.add_argument(
        "--library",
        choices=("central", "local"),
        default="central",
    )
    parser.add_argument("--year", default="2026")
    parser.add_argument("--province", default="全部省份")
    parser.add_argument(
        "--timeliness",
        default="现行有效",
    )
    parser.add_argument("--first-page-size", type=int, default=10)
    parser.add_argument("--second-page-size", type=int, default=20)
    parser.add_argument("--redact-term", action="append", default=[])
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
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
    bridge = BrowserBridge(
        JsonLineLogger(args.output_dir / "k1-readonly-engine.jsonl")
    )
    bridge.browser = browser
    bridge.main_tab = tab
    bridge.entry_url = "https://www.pkulaw.com/"
    bridge.origin = "https://www.pkulaw.com"
    bridge.target_url = str(tab.url)
    adapter = PkulawAdapter(bridge, bridge.logger)

    started_at = utc_now()
    diagnostics = adapter.diagnose_current_page()
    access = adapter._read_access_state(tab)
    if not diagnostics.get("ready"):
        raise RuntimeError(
            "selector diagnostics failed: "
            + json.dumps(diagnostics, ensure_ascii=False)
        )
    if (
        not access.get("loggedIn")
        or access.get("captcha")
        or access.get("quota")
    ):
        raise RuntimeError(
            "authentication gate failed: "
            + json.dumps(access, ensure_ascii=False)
        )

    first = adapter.search(
        keyword=args.query,
        match_mode=args.match_mode,
        library=args.library,
        year=args.year,
        province=args.province,
        timeliness=args.timeliness,
        page_size=args.first_page_size,
    )
    first_summary = summarize_session(first)
    first_snapshot = capture_snapshot(
        tab,
        args.output_dir / "K1_第一次检索_脱敏页面.html",
        tuple(args.redact_term),
    )

    second = adapter.search(
        keyword=args.query,
        match_mode=args.match_mode,
        library=args.library,
        year=args.year,
        province=args.province,
        timeliness=args.timeliness,
        page_size=args.second_page_size,
    )
    second_summary = summarize_session(second)
    second_snapshot = capture_snapshot(
        tab,
        args.output_dir / "K1_相同查询再检索_脱敏页面.html",
        tuple(args.redact_term),
    )

    if not first_summary["searchId"] or not second_summary["searchId"]:
        raise RuntimeError("one or both searches returned no SearchSession")
    if first_summary["searchId"] == second_summary["searchId"]:
        raise RuntimeError("same-query rerun reused the previous searchId")
    if first_summary["actualPageSize"] != args.first_page_size:
        raise RuntimeError(
            f"first page size mismatch: {first_summary['actualPageSize']}"
        )
    if second_summary["actualPageSize"] != args.second_page_size:
        raise RuntimeError(
            f"second page size mismatch: {second_summary['actualPageSize']}"
        )
    if first_summary["groupingMode"] != "ungrouped":
        raise RuntimeError(
            f"first search did not switch to ungrouped: "
            f"{first_summary['groupingMode']}"
        )
    if second_summary["groupingMode"] != "ungrouped":
        raise RuntimeError(
            f"second search did not switch to ungrouped: "
            f"{second_summary['groupingMode']}"
        )

    evidence = {
        "scenario": "K1",
        "mode": "real-site-read-only",
        "startedAt": started_at,
        "finishedAt": utc_now(),
        "query": args.query,
        "matchMode": args.match_mode,
        "library": args.library,
        "year": args.year,
        "province": args.province,
        "timeliness": args.timeliness,
        "access": {
            key: access.get(key)
            for key in (
                "url",
                "loggedIn",
                "login",
                "captcha",
                "quota",
                "pageError",
            )
        },
        "selector": {
            "profileId": diagnostics.get("profileId"),
            "version": diagnostics.get("version"),
            "ready": diagnostics.get("ready"),
            "pageKind": diagnostics.get("pageKind"),
            "missingControls": diagnostics.get("missingControls") or [],
        },
        "firstSearch": first_summary,
        "secondSearch": second_summary,
        "pageSizeSwitch": {
            "from": first_summary["actualPageSize"],
            "to": second_summary["actualPageSize"],
            "verified": True,
        },
        "sameQueryRerun": {
            "oldSearchId": first_summary["searchId"],
            "newSearchId": second_summary["searchId"],
            "changed": True,
        },
        "downloadsStarted": 0,
        "downloadClicked": False,
        "snapshots": {
            "first": first_snapshot,
            "second": second_snapshot,
        },
    }
    path = args.output_dir / "K1_真实只读预检.json"
    path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
