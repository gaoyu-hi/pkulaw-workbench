from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from DrissionPage import Chromium


def write_result(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: probe_drission.py <debug_port> <fixture_url> <result_path>")

    debug_port = int(sys.argv[1])
    fixture_url = sys.argv[2]
    result_path = Path(sys.argv[3])
    address = f"127.0.0.1:{debug_port}"
    started_at = time.time()

    result = {
        "success": False,
        "address": address,
        "fixtureUrl": fixture_url,
        "steps": [],
    }

    try:
        deadline = time.time() + 25
        last_error = None
        browser = None
        while time.time() < deadline:
            try:
                browser = Chromium(address)
                break
            except Exception as exc:
                last_error = repr(exc)
                time.sleep(1)

        if browser is None:
            raise RuntimeError(f"DrissionPage could not attach: {last_error}")

        result["steps"].append("attached")
        result["browserVersion"] = getattr(browser, "version", "")

        deadline = time.time() + 15
        tab = None
        observed_urls = []
        while time.time() < deadline:
            tabs = browser.get_tabs()
            observed_urls = [str(item.url) for item in tabs]
            for item in tabs:
                if str(item.url).rstrip("/") == fixture_url.rstrip("/"):
                    tab = item
                    break
            if tab is not None:
                break
            time.sleep(0.5)

        result["observedUrls"] = observed_urls
        if tab is None:
            raise RuntimeError(f"fixture WebContentsView not found; urls={observed_urls!r}")

        result["steps"].append("target_found")
        result["tabUrl"] = tab.url
        result["tabTitle"] = tab.title

        heading = tab.ele("#heading", timeout=8)
        status = tab.ele("#status", timeout=8)
        download_button = tab.ele("#download", timeout=8)
        if not heading or not status or not download_button:
            raise RuntimeError("fixture DOM controls were not available through DrissionPage")

        result["heading"] = heading.text
        result["status"] = status.text
        result["sessionCookie"] = tab.run_js(
            "return document.cookie.match(/(?:^|; )pkulaw_spike_session=([^;]+)/)?.[1] || '';"
        )
        result["steps"].append("dom_read")

        tab.run_js(
            "document.querySelector('#status').textContent='changed-by-drissionpage';"
        )
        result["changedStatus"] = tab.ele("#status").text
        result["steps"].append("dom_changed")

        download_button.click()
        now = time.time()
        while time.time() - now < 15:
            try:
                completed = json.loads(
                    (result_path.parent / "spike_result.json").read_text(encoding="utf-8")
                )
                if completed.get("downloadState"):
                    result["downloadState"] = completed["downloadState"]
                    result["downloadPath"] = completed.get("downloadPath", "")
                    result["downloadContent"] = completed.get("downloadContent", "")
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.5)

        result["steps"].append("download_clicked")

        child_button = tab.ele("#open-child", timeout=8)
        if not child_button:
            raise RuntimeError("child-window button was unavailable")
        child_button.click()
        child_deadline = time.time() + 10
        while time.time() < child_deadline:
            child_urls = [str(item.url) for item in browser.get_tabs()]
            if any(url.rstrip("/").endswith("/child") for url in child_urls):
                result["childWindowDetected"] = True
                result["observedChildUrls"] = child_urls
                break
            time.sleep(0.25)
        else:
            result["childWindowDetected"] = False

        result["steps"].append("child_window_checked")
        result["success"] = (
            result.get("downloadState") == "completed"
            and "ELECTRON_DRISSION_SPIKE_OK" in result.get("downloadContent", "")
            and result.get("childWindowDetected") is True
            and result.get("sessionCookie") == "persisted"
        )
        result["elapsedSeconds"] = round(time.time() - started_at, 2)
    except Exception as exc:
        result["error"] = repr(exc)
        result["elapsedSeconds"] = round(time.time() - started_at, 2)

    write_result(result_path, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
