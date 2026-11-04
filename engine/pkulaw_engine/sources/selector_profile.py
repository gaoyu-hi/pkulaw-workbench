from __future__ import annotations

import json
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


class SelectorProfileError(RuntimeError):
    code = "PAGE_SELECTOR_MISMATCH"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code or "PAGE_SELECTOR_MISMATCH"


@dataclass(frozen=True)
class SelectorProfile:
    profile_id: str
    version: str
    detection: dict[str, list[str]]
    required_controls: tuple[str, ...]
    diagnostic_controls: tuple[str, ...]
    selectors: dict[str, Any]
    fallbacks: dict[str, list[dict[str, Any]]]
    selector_groups: dict[str, tuple[str, ...]]
    selector_group_optional: dict[str, tuple[str, ...]]
    required_by_page: dict[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SelectorProfile":
        profile_id = str(payload.get("profileId") or "").strip()
        version = str(payload.get("version") or "").strip()
        selectors = payload.get("selectors")
        detection = payload.get("detection")
        if not profile_id or not version or not isinstance(selectors, dict):
            raise ValueError("selector profile is missing profileId, version, or selectors")
        if not isinstance(detection, dict):
            raise ValueError(f"selector profile {profile_id} has no detection markers")
        raw_selector_groups = dict(payload.get("selectorGroups") or {})
        selector_groups = {
            str(key): tuple(
                str(item)
                for item in (
                    value if isinstance(value, list) else value.get("controls", [])
                )
            )
            for key, value in raw_selector_groups.items()
            if isinstance(value, (list, dict))
        }
        selector_group_optional = {
            str(key): tuple(
                str(item)
                for item in (
                    value.get("optional", [])
                    if isinstance(value, dict)
                    else []
                )
            )
            for key, value in raw_selector_groups.items()
            if isinstance(value, (list, dict))
        }
        return cls(
            profile_id=profile_id,
            version=version,
            detection={
                "any": [str(value) for value in detection.get("any", []) if value],
            },
            required_controls=tuple(
                str(value) for value in payload.get("requiredControls", [])
            ),
            diagnostic_controls=tuple(
                str(value) for value in payload.get("diagnosticControls", [])
            ),
            selectors=selectors,
            fallbacks={
                str(key): [
                    dict(item)
                    for item in value
                    if isinstance(item, dict)
                ]
                for key, value in dict(payload.get("fallbacks") or {}).items()
                if isinstance(value, list)
            },
            selector_groups=selector_groups,
            selector_group_optional=selector_group_optional,
            required_by_page={
                str(key): tuple(str(item) for item in value)
                for key, value in dict(payload.get("requiredByPage") or {}).items()
                if isinstance(value, list)
            },
        )


def profile_directory() -> Path:
    return Path(__file__).with_name("profiles")


@lru_cache(maxsize=4)
def load_selector_profiles(directory: str | None = None) -> tuple[SelectorProfile, ...]:
    root = Path(directory) if directory else profile_directory()
    profiles = []
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        profiles.append(SelectorProfile.from_dict(payload))
    if not profiles:
        raise SelectorProfileError(f"没有可用的选择器配置：{root}")
    return tuple(profiles)


def load_selector_profile(path: Path) -> SelectorProfile:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "selectors" not in payload:
        payload = {
            "profileId": path.stem,
            "version": str(payload.get("version") or f"{path.stem}-custom"),
            "detection": {"any": []},
            "requiredControls": ["searchInput", "searchSubmit"],
            "diagnosticControls": ["searchInput", "searchSubmit"],
            "selectors": payload,
            "fallbacks": {},
        }
    return SelectorProfile.from_dict(payload)


def profile_from_markers(
    marker_state: dict[str, Any],
    profiles: tuple[SelectorProfile, ...],
) -> tuple[SelectorProfile | None, list[str]]:
    matches = []
    for profile in profiles:
        if any(bool(marker_state.get(marker)) for marker in profile.detection.get("any", [])):
            matches.append(profile)
    if len(matches) > 1:
        ids = ", ".join(profile.profile_id for profile in matches)
        raise SelectorProfileError(f"页面同时匹配多个选择器配置：{ids}")
    if not matches:
        return None, []
    profile = matches[0]
    evidence = [
        marker
        for marker in profile.detection.get("any", [])
        if marker_state.get(marker)
    ]
    return profile, evidence


OPERATION_REQUIRED_CONTROLS: dict[str, tuple[str, ...]] = {
    "search": ("searchInput", "searchSubmit"),
    "read-results": ("resultsRoot", "resultCount"),
    "select-batch": (
        "resultsRoot",
        "resultLink",
        "resultNumber",
        "batchCheckbox",
        "batchSort",
    ),
    "download-catalog-trigger": ("batchButton",),
    "download-zip-trigger": ("batchButton",),
    "download-catalog-submit": ("batchDialog", "batchFormat", "batchConfirm"),
    "download-zip-submit": ("batchDialog", "batchFormat", "batchConfirm"),
    "read-detail": ("detailTitle", "detailFields", "detailBody"),
    "download-word": ("detailTitle", "downloadWord"),
}


def operation_required_controls(operation: str) -> tuple[str, ...]:
    try:
        return OPERATION_REQUIRED_CONTROLS[operation]
    except KeyError as exc:
        raise ValueError(f"unsupported selector operation: {operation}") from exc


def diagnose_for_operation(
    observer: Any,
    operation: str,
    *,
    timeout_seconds: float = 5.0,
    interval_seconds: float = 0.25,
    stable_samples: int = 2,
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Wait for consecutive stable samples for one page operation."""
    required = operation_required_controls(operation)
    deadline = clock() + max(0.0, float(timeout_seconds))
    consecutive = 0
    ever_ready = False
    missing_snapshots: set[tuple[str, ...]] = set()
    history: list[dict[str, Any]] = []
    last: dict[str, Any] = {}
    while clock() <= deadline:
        last = dict(observer() or {})
        controls = dict(last.get("controls") or {})
        missing = tuple(
            key
            for key in required
            if not dict(controls.get(key) or {}).get("ok")
        )
        missing_snapshots.add(missing)
        history.append(
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "missingControls": list(missing),
            }
        )
        if missing:
            consecutive = 0
        else:
            ever_ready = True
            consecutive += 1
            if consecutive >= max(2, int(stable_samples)):
                return {
                    **last,
                    "operation": operation,
                    "operationReady": True,
                    "operationControls": list(required),
                    "operationMissingControls": [],
                    "stabilitySamples": consecutive,
                    "stabilityHistory": history,
                }
        if clock() >= deadline:
            break
        sleep(max(0.01, float(interval_seconds)))

    last_missing = list(
        key
        for key in required
        if not dict((last.get("controls") or {}).get(key) or {}).get("ok")
    )
    page_known = bool(last.get("detected")) and str(
        last.get("pageKind") or "unknown"
    ) != "unknown"
    unstable = ever_ready or len(missing_snapshots) > 1
    code = (
        "PAGE_SELECTOR_UNSTABLE"
        if page_known and unstable
        else "PAGE_SELECTOR_MISMATCH"
    )
    reason = (
        "关键控件在等待窗口内反复出现和消失"
        if code == "PAGE_SELECTOR_UNSTABLE"
        else f"关键控件持续缺失：{', '.join(last_missing) or '页面无法识别'}"
    )
    raise SelectorProfileError(
        f"{reason}（operation={operation}）",
        code=code,
    )


def detect_selector_profile(
    tab: Any,
    profiles: tuple[SelectorProfile, ...] | None = None,
) -> tuple[SelectorProfile | None, list[str]]:
    loaded_profiles = profiles or load_selector_profiles()
    markers = sorted(
        {
            marker
            for profile in loaded_profiles
            for marker in profile.detection.get("any", [])
        }
    )
    state = tab.run_js(
        f"""
        return (() => {{
          const markers = {json.dumps(markers, ensure_ascii=False)};
          return Object.fromEntries(markers.map(marker => {{
            try {{
              return [marker, !!document.querySelector(marker)];
            }} catch (_) {{
              return [marker, false];
            }}
          }}));
        }})()
        """
    ) or {}
    if not isinstance(state, dict):
        raise SelectorProfileError("无法读取页面选择器特征")
    return profile_from_markers(state, loaded_profiles)


def build_control_resolver_js(profile: SelectorProfile) -> str:
    specs = {
        name: {
            "stable": profile.selectors.get(name) or "",
            "fallbacks": profile.fallbacks.get(name, []),
        }
        for name in sorted(
            set(profile.required_controls)
            | set(profile.diagnostic_controls)
            | {
                control
                for controls in profile.selector_groups.values()
                for control in controls
            }
            | {
                control
                for controls in profile.required_by_page.values()
                for control in controls
            }
            | {
                control
                for controls in OPERATION_REQUIRED_CONTROLS.values()
                for control in controls
            }
        )
    }
    return f"""
    const controlSpecs = {json.dumps(specs, ensure_ascii=False)};
    const safeQuery = (selector, root = document) => {{
      if (!selector) return null;
      try {{
        return root.querySelector(selector);
      }} catch (_) {{
        return null;
      }}
    }};
    const resolveControl = key => {{
      const spec = controlSpecs[key] || {{stable: "", fallbacks: []}};
      const stableElement = safeQuery(spec.stable);
      if (stableElement) {{
        return {{element: stableElement, strategy: "stable", selector: spec.stable}};
      }}
      for (const fallback of spec.fallbacks || []) {{
        let element = null;
        if (fallback.strategy === "css") {{
          element = safeQuery(fallback.value);
        }} else if (fallback.strategy === "attribute") {{
          const candidates = [...document.querySelectorAll(
            fallback.tags || "input,textarea,[contenteditable='true']"
          )];
          const attribute = String(fallback.attribute || "");
          const expected = String(fallback.contains || "");
          element = candidates.find(item =>
            String(item.getAttribute(attribute) || "").includes(expected)
          ) || null;
        }} else if (fallback.strategy === "text") {{
          const candidates = [...document.querySelectorAll(
            fallback.tags || "button,a,[role='button']"
          )];
          const expected = String(fallback.text || "").replace(/\\s+/g, "");
          element = candidates.find(item =>
            (item.innerText || item.textContent || "").replace(/\\s+/g, "") === expected
          ) || null;
        }} else if (fallback.strategy === "form-child") {{
          const root = safeQuery(fallback.root);
          element = root ? safeQuery(fallback.value, root) : null;
        }}
        if (element) {{
          return {{
            element,
            strategy: "fallback:" + fallback.strategy,
            selector: fallback.value || fallback.attribute || fallback.text || ""
          }};
        }}
      }}
      return {{element: null, strategy: "missing", selector: ""}};
    }};
    """


def diagnose_selector_profile(
    tab: Any,
    profile: SelectorProfile | None,
    *,
    detection_evidence: list[str] | None = None,
) -> dict[str, Any]:
    if profile is None:
        return {
            "profileId": "",
            "version": "",
            "detected": False,
            "ready": False,
            "pageKind": "unknown",
            "pageReady": False,
            "requiredControls": [],
            "pageRequiredControls": [],
            "controls": {},
            "missingControls": ["profile"],
            "pageMissingControls": ["profile"],
            "groups": {},
            "detectionEvidence": detection_evidence or [],
            "message": "无法识别当前页面的选择器配置",
        }
    diagnostics = tab.run_js(
        f"""
        return (() => {{
          {build_control_resolver_js(profile)}
          const controls = {{}};
          for (const key of Object.keys(controlSpecs)) {{
            const resolved = resolveControl(key);
            const element = resolved.element;
            const style = element ? getComputedStyle(element) : null;
            const visible = !!(
              element &&
              element.getClientRects().length &&
              style &&
              style.visibility !== "hidden" &&
              style.display !== "none"
            );
            controls[key] = {{
              ok: !!element,
              strategy: resolved.strategy,
              matched: resolved.selector,
              visible,
              enabled: element ? !element.disabled : false,
              readOnly: element ? Boolean(element.readOnly) : false,
              text: element
                ? (element.innerText || element.textContent || "")
                    .replace(/\\s+/g, " ").trim().slice(0, 80)
                : ""
            }};
          }}
          const has = key => Boolean(controls[key]?.ok);
          const pageKind = has("detailTitle") || has("detailBody")
            ? "detail"
            : has("resultLink") || has("resultsRoot")
              ? "results"
              : has("searchInput") || has("searchSubmit")
                ? "search"
                : has("login")
                  ? "login"
                  : "unknown";
          return {{
            url: location.href,
            title: document.title,
            pageKind,
            controls
          }};
        }})()
        """
    ) or {}
    controls = dict(diagnostics.get("controls") or {})
    page_kind = str(diagnostics.get("pageKind") or "unknown")
    page_required = list(
        profile.required_by_page.get(page_kind)
        or profile.required_controls
    )
    page_missing = [
        key
        for key in page_required
        if not dict(controls.get(key) or {}).get("ok")
    ]
    missing = page_missing
    groups: dict[str, dict[str, Any]] = {}
    for group_name, members in profile.selector_groups.items():
        optional = set(profile.selector_group_optional.get(group_name, ()))
        group_missing = [
            key
            for key in members
            if key not in optional and not dict(controls.get(key) or {}).get("ok")
        ]
        groups[group_name] = {
            "ready": not group_missing,
            "controls": list(members),
            "missingControls": group_missing,
            "optionalControls": sorted(optional),
        }
    return {
        "profileId": profile.profile_id,
        "version": profile.version,
        "detected": True,
        "ready": not missing,
        "pageKind": page_kind,
        "pageReady": not page_missing,
        "requiredControls": list(profile.required_controls),
        "pageRequiredControls": page_required,
        "controls": controls,
        "missingControls": missing,
        "pageMissingControls": page_missing,
        "groups": groups,
        "detectionEvidence": detection_evidence or [],
        "url": str(diagnostics.get("url") or ""),
        "title": str(diagnostics.get("title") or ""),
        "message": (
            "选择器诊断通过"
            if not missing
            else f"缺少关键选择器：{', '.join(missing)}"
        ),
    }
