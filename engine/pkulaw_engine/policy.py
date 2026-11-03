from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).lower()
    normalized = normalized.replace("—", "-").replace("–", "-").replace("－", "-")
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)


_DETAIL_TITLE_STATUS_RE = re.compile(
    r"(?:\s*(?:已被修改|现行有效|已废止或失效|废止或失效|已废止|失效|"
    r"尚未生效|部分废止或失效)\s*|"
    r"\s*[\(（](?:已被修改|现行有效|已废止或失效|废止或失效|已废止|失效|"
    r"尚未生效|部分废止或失效)[\)）]\s*)$"
)


def normalize_detail_title(value: str) -> str:
    """Ignore status text appended by the result/detail page shell."""

    clean = _DETAIL_TITLE_STATUS_RE.sub("", str(value or ""))
    return normalize_title(clean)


def safe_filename(value: str, limit: int | None = 180) -> str:
    result = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(value or ""))
    result = re.sub(r"\s+", " ", result).strip(" ._") or "未命名"
    return result if limit is None else result[:limit].rstrip(" .")


def extract_policy_id(url: str) -> str:
    value = str(url or "")
    patterns = (
        r"/pkulaw/detail/([^/?#]+)\.html",
        r"/law/(?:chl|lar)/([^/?#]+)\.html",
        r"/read/law/(?:chl|lar)/([^/?#]+)",
        r"/(?:chl|lar)/([^/?#]+)\.html",
    )
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.I)
        if match:
            return match.group(1)
    return ""


def policy_identity(row: dict[str, Any]) -> str:
    policy_id = str(row.get("policyId") or "").strip() or extract_policy_id(
        str(row.get("url") or "")
    )
    if policy_id:
        return f"id:{policy_id.lower()}"
    clean_url = (
        str(row.get("url") or "")
        .strip()
        .split("#", 1)[0]
        .split("?", 1)[0]
        .rstrip("/")
    )
    return f"url:{clean_url.lower()}"


def stable_policy_id(row: dict[str, Any]) -> str:
    return str(row.get("policyId") or "").strip() or extract_policy_id(
        str(row.get("url") or "")
    )


def _merge_policy_rows(
    current: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        existing = merged.get(key)
        if existing in (None, "", [], {}):
            merged[key] = value
    for field, label in (
        ("query", "observedQueries"),
        ("library", "observedLibraries"),
        ("year", "observedYears"),
    ):
        values = list(merged.get(label) or [])
        for value in (
            current.get(field),
            incoming.get(field),
        ):
            clean = str(value or "").strip()
            if clean and clean not in values:
                values.append(clean)
        if values:
            merged[label] = values
    return merged


def pending_policy_identity(row: dict[str, Any]) -> str:
    fingerprint_payload = {
        "title": normalize_title(str(row.get("title") or "")),
        "number": str(row.get("number") or ""),
        "url": str(row.get("url") or "").strip(),
        "publicationDate": str(row.get("publicationDate") or ""),
    }
    digest = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"pending:{digest[:24]}"


def keyword_result_identity(row: dict[str, Any]) -> str:
    policy_id = stable_policy_id(row)
    return (
        f"id:{policy_id.lower()}"
        if policy_id
        else pending_policy_identity(row)
    )


def candidate_score(
    candidate: dict[str, Any],
    expected: dict[str, Any],
) -> int:
    score = 0
    if normalize_title(candidate.get("title", "")) == normalize_title(expected.get("title", "")):
        score += 100
    if expected.get("documentNumber") and candidate.get("documentNumber") == expected["documentNumber"]:
        score += 50
    if expected.get("publicationDate") and candidate.get("publicationDate") == expected["publicationDate"]:
        score += 30
    if expected.get("issuingAuthority") and candidate.get("issuingAuthority") == expected["issuingAuthority"]:
        score += 20
    return score


def policy_filename(
    *,
    library: str,
    title: str,
    publication_date: str,
    source_url: str,
    suffix: str,
) -> str:
    year_match = re.search(r"(?:19|20)\d{2}", str(publication_date or ""))
    year = year_match.group(0) if year_match else "年份未知"
    prefix = "中央政策" if library == "central" else "地方政策"
    policy_id = extract_policy_id(source_url)
    id_suffix = f"_{safe_filename(policy_id, 32)}" if policy_id else ""
    max_title_length = 110 if id_suffix else 140
    clean_title = safe_filename(title, max_title_length)
    clean_suffix = suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix or "") else ".docx"
    return f"{prefix}_{year}_{clean_title}{id_suffix}{clean_suffix.lower()}"


def group_results(
    rows: list[dict[str, Any]],
    *,
    identity_mode: str,
) -> list[dict[str, Any]]:
    if identity_mode not in {"keyword", "title"}:
        raise ValueError("identity_mode must be keyword or title")
    if identity_mode == "keyword":
        return _group_keyword_results(rows)

    by_policy_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = policy_identity(row)
        if identity not in by_policy_id:
            by_policy_id[identity] = dict(row)
        else:
            by_policy_id[identity] = _merge_policy_rows(
                by_policy_id[identity],
                row,
            )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in by_policy_id.values():
        key = normalize_title(row.get("title", ""))
        grouped.setdefault(key or policy_identity(row), []).append(row)

    items: list[dict[str, Any]] = []
    for normalized, candidates in grouped.items():
        candidates.sort(
            key=lambda value: (
                str(value.get("publicationDate") or ""),
                str(value.get("policyId") or ""),
            ),
            reverse=True,
        )
        if len(candidates) == 1:
            candidate = candidates[0]
            items.append(
                {
                    "policyKey": policy_identity(candidate),
                    "title": candidate.get("title", ""),
                    "candidates": candidates,
                }
            )
        else:
            items.append(
                {
                    "policyKey": f"title:{normalized}",
                    "title": candidates[0].get("title", ""),
                    "candidates": candidates,
                }
            )
    return items


def _group_keyword_results(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_policy_id: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    for row in rows:
        policy_id = stable_policy_id(row)
        if not policy_id:
            pending.append(
                {
                    "policyKey": pending_policy_identity(row),
                    "title": str(row.get("title") or ""),
                    "candidates": [],
                    "identityStatus": "pending_review",
                    "identityError": {
                        "code": "STABLE_POLICY_ID_MISSING",
                        "message": "结果行缺少稳定政策 ID，未自动猜测身份",
                    },
                    "sourceRow": dict(row),
                }
            )
            continue
        identity = f"id:{policy_id.lower()}"
        if identity not in by_policy_id:
            by_policy_id[identity] = dict(row)
        else:
            by_policy_id[identity] = _merge_policy_rows(
                by_policy_id[identity],
                row,
            )

    items = [
        {
            "policyKey": identity,
            "title": candidate.get("title", ""),
            "candidates": [candidate],
            "identityStatus": "verified",
        }
        for identity, candidate in by_policy_id.items()
    ]
    items.extend(pending)
    return items


def validate_docx(path: Path) -> tuple[bool, str]:
    import zipfile

    if not path.is_file() or path.stat().st_size == 0:
        return False, "文件不存在或为空"
    if path.suffix.lower() != ".docx":
        return False, f"下载文件的扩展名不是 .docx：{path.suffix}"
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            needed = {"[Content_Types].xml", "word/document.xml"}
            if not needed.issubset(names):
                return False, "DOCX 缺少必要内部文件"
            if archive.testzip() is not None:
                return False, "DOCX 内部 CRC 校验失败"
    except (zipfile.BadZipFile, OSError) as exc:
        return False, f"DOCX 无法读取：{exc}"
    return True, ""


def validate_word_attachment(
    path: Path,
    expected_suffix: str,
) -> tuple[bool, str]:
    suffix = expected_suffix.lower()
    if suffix == ".docx":
        return validate_docx(path)
    if suffix != ".doc":
        return False, f"不支持的 Word 附件扩展名：{expected_suffix}"
    if not path.is_file() or path.stat().st_size < 8:
        return False, "文件不存在或为空"
    try:
        with path.open("rb") as stream:
            header = stream.read(8)
    except OSError as exc:
        return False, f"Word 附件无法读取：{exc}"
    if header != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return False, "下载文件不是有效的 OLE Word 文档"
    return True, ""


def validate_downloaded_word(path: Path) -> tuple[bool, str]:
    """Validate a downloaded Word file using its actual extension."""
    if not path.is_file() or path.stat().st_size == 0:
        return False, "文件不存在或为空"
    suffix = path.suffix.lower()
    if suffix not in {".doc", ".docx"}:
        return False, f"不支持的 Word 文件扩展名：{path.suffix or '无扩展名'}"
    return validate_word_attachment(path, suffix)
