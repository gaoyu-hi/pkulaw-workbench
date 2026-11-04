from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from typing import Iterable


class SanitizationError(ValueError):
    code = "PAGE_SNAPSHOT_UNSAFE"


_BLOCKED_TAGS = {
    "base",
    "canvas",
    "embed",
    "iframe",
    "img",
    "link",
    "meta",
    "noscript",
    "object",
    "script",
    "style",
    "svg",
    "video",
    "audio",
}
_VOID_TAGS = {
    "area",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}
_SAFE_HIDDEN_INPUT_NAMES = {
    "classflag",
    "isuserlogin",
    "library",
    "lislogin",
    "matchtype",
    "oldpageindex",
    "pageindex",
    "pager.pageindex",
    "pager.pagesize",
    "searchkeywordtype",
}
_ALLOWED_ATTRIBUTES = {
    "aria-disabled",
    "aria-label",
    "checked",
    "class",
    "data-page",
    "data-total",
    "disabled",
    "filetype",
    "for",
    "id",
    "library",
    "name",
    "placeholder",
    "readonly",
    "rel",
    "role",
    "searchname",
    "title",
    "type",
}
_SENSITIVE_MARKERS = (
    "access_token",
    "authorization",
    "cookie",
    "groupusername",
    "kcauth",
    "keycloak",
    "logout",
    "luserid",
    "luserip",
    "luserunitname",
    "lusername",
    "password",
    "session",
    "token",
)
_TOKEN_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+){1,2}\b")
_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")


def redact_text(value: str, redact_terms: Iterable[str] = ()) -> str:
    text = str(value or "")
    for term in sorted({item for item in redact_terms if item}, key=len, reverse=True):
        text = text.replace(term, "[REDACTED_INSTITUTION]")
    text = _TOKEN_PATTERN.sub("[REDACTED_TOKEN]", text)
    text = _UUID_PATTERN.sub("[REDACTED_ID]", text)
    text = _IPV4_PATTERN.sub("[REDACTED_IP]", text)
    text = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_PATTERN.sub("[REDACTED_PHONE]", text)
    return text


def _attribute_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name.lower(): value or "" for name, value in attrs}


def _is_sensitive(tag: str, attributes: dict[str, str]) -> bool:
    fingerprint = " ".join(
        (
            tag,
            attributes.get("id", ""),
            attributes.get("name", ""),
            attributes.get("class", ""),
            attributes.get("title", ""),
        )
    ).lower()
    if attributes.get("type", "").lower() == "hidden":
        return (
            attributes.get("name", "").lower() not in _SAFE_HIDDEN_INPUT_NAMES
            and attributes.get("id", "").lower() not in _SAFE_HIDDEN_INPUT_NAMES
        )
    return any(marker in fingerprint for marker in _SENSITIVE_MARKERS)


def _safe_href(value: str) -> str:
    candidate = str(value or "").strip()
    if re.match(r"^/(?:chl|lar)/[A-Za-z0-9_-]+\.html", candidate):
        return re.sub(r"\?.*$", "?fixture=1", candidate)
    if candidate.startswith("#"):
        return candidate
    return "#"


class _Sanitizer(HTMLParser):
    def __init__(self, redact_terms: Iterable[str]) -> None:
        super().__init__(convert_charrefs=False)
        self.redact_terms = tuple(redact_terms)
        self.parts: list[str] = []
        self.open_blocks: list[bool] = []
        self.blocked_depth = 0

    def handle_decl(self, decl: str) -> None:
        if self.blocked_depth:
            return
        if decl.lower().startswith("doctype"):
            self.parts.append("<!doctype html>")

    def handle_comment(self, data: str) -> None:
        return

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        attributes = _attribute_dict(attrs)
        blocked = (
            self.blocked_depth > 0
            or tag in _BLOCKED_TAGS
            or _is_sensitive(tag, attributes)
        )
        self.open_blocks.append(blocked)
        if blocked:
            self.blocked_depth += 1
            if tag in _VOID_TAGS:
                self.blocked_depth = max(0, self.blocked_depth - 1)
                self.open_blocks.pop()
            return

        rendered: list[str] = []
        for name, value in attrs:
            key = name.lower()
            raw = value or ""
            if key.startswith("data-pkulaw-"):
                rendered.append(f'{key}="{escape(redact_text(raw, self.redact_terms), quote=True)}"')
                continue
            if key == "value":
                if (
                    tag == "input"
                    and attributes.get("type", "").lower() == "hidden"
                    and attributes.get("name", "").lower() in _SAFE_HIDDEN_INPUT_NAMES
                ):
                    rendered.append(f'value="{escape(raw[:32], quote=True)}"')
                continue
            if key == "href":
                rendered.append(f'href="{escape(_safe_href(raw), quote=True)}"')
                continue
            if key.startswith("on") or key in {"src", "style", "action", "method"}:
                continue
            if key not in _ALLOWED_ATTRIBUTES:
                continue
            rendered.append(f'{key}="{escape(redact_text(raw, self.redact_terms), quote=True)}"')

        suffix = f" {' '.join(rendered)}" if rendered else ""
        self.parts.append(f"<{tag}{suffix}>")
        if tag in _VOID_TAGS:
            self._close_block()

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self.open_blocks:
            return
        blocked = self.open_blocks.pop()
        if blocked:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.blocked_depth:
            return
        self.parts.append(escape(redact_text(data, self.redact_terms), quote=False))

    def handle_entityref(self, name: str) -> None:
        if self.blocked_depth:
            return
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.blocked_depth:
            return
        self.parts.append(f"&#{name};")

    def _close_block(self) -> None:
        if self.open_blocks:
            self.open_blocks.pop()

    def result(self) -> str:
        return "".join(self.parts).strip()


def sanitize_html(html: str, redact_terms: Iterable[str] = ()) -> str:
    parser = _Sanitizer(redact_terms)
    parser.feed(str(html or ""))
    parser.close()
    sanitized = parser.result()
    assert_sanitized_html(sanitized, redact_terms)
    return sanitized


def assert_sanitized_html(
    html: str,
    redact_terms: Iterable[str] = (),
) -> None:
    value = str(html or "")
    lower = value.lower()
    for marker in _SENSITIVE_MARKERS:
        if marker in lower:
            raise SanitizationError(f"sanitized snapshot still contains {marker}")
    for term in sorted({item for item in redact_terms if item}, key=len, reverse=True):
        if term in value:
            raise SanitizationError("sanitized snapshot still contains an institution term")
    if _TOKEN_PATTERN.search(value) or _UUID_PATTERN.search(value):
        raise SanitizationError("sanitized snapshot still contains an opaque identifier")
    if _IPV4_PATTERN.search(value) or _EMAIL_PATTERN.search(value) or _PHONE_PATTERN.search(value):
        raise SanitizationError("sanitized snapshot still contains personal or network data")
    for match in re.finditer(r"<input\b[^>]*>", value, flags=re.IGNORECASE):
        tag = match.group(0)
        if re.search(r'type="hidden"', tag, flags=re.IGNORECASE):
            name_match = re.search(r'name="([^"]+)"', tag, flags=re.IGNORECASE)
            id_match = re.search(r'id="([^"]+)"', tag, flags=re.IGNORECASE)
            name = name_match.group(1).lower() if name_match else ""
            element_id = id_match.group(1).lower() if id_match else ""
            if (
                name not in _SAFE_HIDDEN_INPUT_NAMES
                and element_id not in _SAFE_HIDDEN_INPUT_NAMES
            ):
                raise SanitizationError("sanitized snapshot still contains an unknown hidden input")
