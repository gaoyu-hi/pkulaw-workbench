from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCAN_JOURNAL_SCHEMA_VERSION = 1
SCAN_UNIVERSE_FINGERPRINT_VERSION = 2
_SCAN_NON_UNIVERSE_KEYS = frozenset(
    {
        "fingerprintVersion",
        "pageMode",
        "pageSelection",
        "maxPages",
        "outputDir",
        "catalogFormat",
        "fullFormat",
        "maxBatchSize",
        "searchStrategyVersion",
    }
)


class ScanJournalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def config_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def scan_universe_fingerprint(payload: dict[str, Any]) -> str:
    canonical = {
        key: value
        for key, value in payload.items()
        if key not in _SCAN_NON_UNIVERSE_KEYS
    }
    canonical["fingerprintVersion"] = (
        SCAN_UNIVERSE_FINGERPRINT_VERSION
    )
    return config_fingerprint(canonical)


def legacy_page_mode_fingerprints(
    payload: dict[str, Any],
    *,
    max_pages_values: Iterable[int] = (),
) -> frozenset[str]:
    candidates: set[str] = set()
    last = deepcopy(payload)
    last.update(
        {
            "pageMode": "last",
            "pageSelection": {"mode": "last"},
            "maxPages": None,
        }
    )
    candidates.add(config_fingerprint(last))
    limits = {
        int(value)
        for value in max_pages_values
        if value is not None and int(value) > 0
    }
    limits.add(1)
    for max_pages in limits:
        limited = deepcopy(payload)
        limited.update(
            {
                "pageMode": "limit",
                "pageSelection": {
                    "mode": "limit",
                    "maxPages": max_pages,
                },
                "maxPages": max_pages,
            }
        )
        candidates.add(config_fingerprint(limited))
    return frozenset(candidates)


class ScanJournal:
    def __init__(
        self,
        path: Path,
        *,
        task_id: str,
        config_fingerprint: str,
        accepted_fingerprints: Iterable[str] = (),
    ) -> None:
        self.path = Path(path)
        self.task_id = str(task_id)
        self.config_fingerprint = str(config_fingerprint)
        self.accepted_fingerprints = frozenset(
            {
                self.config_fingerprint,
                *(
                    str(value)
                    for value in accepted_fingerprints
                    if str(value)
                ),
            }
        )
        self._summary_cache: dict[str, Any] | None = None

    @property
    def marker_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".sealed")

    @property
    def sealed(self) -> bool:
        return self.marker_path.is_file()

    def append(self, record: dict[str, Any]) -> None:
        if self.sealed:
            raise ScanJournalError(
                "SCAN_JOURNAL_SEALED",
                "扫描日志已封存，不能继续追加",
            )
        payload = self._validated_record(record)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._repair_tail_before_append()
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if self._summary_cache is not None:
            self._update_summary_cache(payload)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        raw = self.path.read_bytes()
        lines = raw.splitlines()
        records: list[dict[str, Any]] = []
        last_index = len(lines) - 1
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                payload = json.loads(line.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("record is not an object")
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                if index == last_index:
                    break
                raise ScanJournalError(
                    "SCAN_JOURNAL_CORRUPT",
                    f"扫描日志中间行损坏（第 {index + 1} 行）：{exc}",
                ) from exc
            records.append(self._validated_record(payload))
        self._validate_sequence(records)
        return records

    def read_page(
        self,
        *,
        query: str,
        library: str,
        year: str,
        page: int,
        partition_key: str = "",
    ) -> dict[str, Any] | None:
        for record in self.read():
            if (
                record["query"] == query
                and record["library"] == library
                and record["effectiveYear"] == year
                and str(record.get("partitionKey") or "") == partition_key
                and int(record["page"]) == int(page)
            ):
                return record
        return None

    def quarantine(self, reason: str) -> Path:
        """Move a stale partial journal aside without discarding evidence."""

        records = self.read()
        if not self.path.is_file():
            raise ScanJournalError(
                "SCAN_JOURNAL_NOT_FOUND",
                "扫描日志不存在，无法隔离旧结果集",
            )
        archive_root = self.path.parent / "archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = (
            archive_root
            / f"{self.path.stem}-{stamp}{self.path.suffix}"
        )
        metadata_path = destination.with_suffix(
            destination.suffix + ".meta.json"
        )
        metadata = {
            "schemaVersion": SCAN_JOURNAL_SCHEMA_VERSION,
            "taskId": self.task_id,
            "configFingerprint": self.config_fingerprint,
            "acceptedLegacyFingerprints": sorted(
                {
                    str(record.get("configFingerprint") or "")
                    for record in records
                    if str(record.get("configFingerprint") or "")
                    and str(record.get("configFingerprint"))
                    != self.config_fingerprint
                }
            ),
            "reason": str(reason),
            "quarantinedAt": datetime.now(timezone.utc).isoformat(),
            "records": len(records),
            "pages": [
                int(record["page"])
                for record in records
            ],
            "lastStableSession": (
                {
                    "query": records[-1]["query"],
                    "library": records[-1]["library"],
                    "year": records[-1]["effectiveYear"],
                    "partitionKey": str(
                        records[-1].get("partitionKey") or ""
                    ),
                    "page": records[-1]["page"],
                    "searchId": records[-1]["searchId"],
                    "resultSignature": records[-1]["resultSignature"],
                    "observedAt": records[-1]["observedAt"],
                }
                if records
                else None
            ),
        }
        os.replace(self.path, destination)
        if self.marker_path.is_file():
            archived_marker = destination.with_suffix(
                destination.suffix + ".sealed"
            )
            os.replace(self.marker_path, archived_marker)
        temporary = metadata_path.with_suffix(
            metadata_path.suffix + ".tmp"
        )
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(
                metadata,
                stream,
                ensure_ascii=False,
                sort_keys=True,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, metadata_path)
        self._summary_cache = None
        return destination

    def checkpoint_summary(self) -> dict[str, Any]:
        if self._summary_cache is not None:
            return json.loads(
                json.dumps(self._summary_cache, ensure_ascii=False)
            )
        records = self.read()
        summary = self._summary_from_records(records)
        self._summary_cache = summary
        return json.loads(json.dumps(summary, ensure_ascii=False))

    def _summary_from_records(
        self,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        combinations: dict[str, dict[str, Any]] = {}
        for record in records:
            key = "|".join(
                (
                    str(record["query"]),
                    str(record["library"]),
                    str(record["effectiveYear"]),
                    str(record.get("partitionKey") or ""),
                )
            )
            combination = combinations.setdefault(
                key,
                {
                    "query": record["query"],
                    "library": record["library"],
                    "year": record["effectiveYear"],
                    "partitionKey": str(
                        record.get("partitionKey") or ""
                    ),
                    "pages": [],
                    "lastPage": 0,
                    "totalPages": 0,
                    "searchId": "",
                },
            )
            combination["pages"].append(int(record["page"]))
            combination["lastPage"] = max(
                int(combination["lastPage"]),
                int(record["page"]),
            )
            combination["totalPages"] = max(
                int(combination["totalPages"]),
                int(record["totalPages"]),
            )
            combination["searchId"] = str(record["searchId"])
        for combination in combinations.values():
            combination["pages"] = sorted(set(combination["pages"]))
        last = records[-1] if records else None
        completed_pages = sorted({int(record["page"]) for record in records})
        return {
            "schemaVersion": SCAN_JOURNAL_SCHEMA_VERSION,
            "path": str(self.path),
            "configFingerprint": self.config_fingerprint,
            "acceptedLegacyFingerprints": sorted(
                {
                    str(record.get("configFingerprint") or "")
                    for record in records
                    if str(record.get("configFingerprint") or "")
                    and str(record.get("configFingerprint"))
                    != self.config_fingerprint
                }
            ),
            "completedPages": completed_pages,
            "combinations": list(combinations.values()),
            "lastStableSession": (
                {
                    "query": last["query"],
                    "library": last["library"],
                    "year": last["effectiveYear"],
                    "partitionKey": str(last.get("partitionKey") or ""),
                    "page": last["page"],
                    "searchId": last["searchId"],
                    "resultSignature": last["resultSignature"],
                    "observedAt": last["observedAt"],
                }
                if last
                else None
            ),
            "sealed": self.sealed,
        }

    def _update_summary_cache(self, record: dict[str, Any]) -> None:
        assert self._summary_cache is not None
        query = str(record["query"])
        library = str(record["library"])
        year = str(record["effectiveYear"])
        partition_key = str(record.get("partitionKey") or "")
        page = int(record["page"])
        key = "|".join((query, library, year, partition_key))
        combinations = list(self._summary_cache.get("combinations") or [])
        combination = next(
            (
                item
                for item in combinations
                if (
                    item.get("query") == query
                    and item.get("library") == library
                    and item.get("year") == year
                    and str(item.get("partitionKey") or "")
                    == partition_key
                )
            ),
            None,
        )
        if combination is None:
            combination = {
                "query": query,
                "library": library,
                "year": year,
                "partitionKey": partition_key,
                "pages": [],
                "lastPage": 0,
                "totalPages": 0,
                "searchId": "",
            }
            combinations.append(combination)
        combination["pages"] = sorted(
            {
                *[int(value) for value in combination.get("pages") or []],
                page,
            }
        )
        combination["lastPage"] = max(
            int(combination.get("lastPage") or 0),
            page,
        )
        combination["totalPages"] = max(
            int(combination.get("totalPages") or 0),
            int(record["totalPages"]),
        )
        combination["searchId"] = str(record["searchId"])
        completed_pages = sorted(
            {
                *[
                    int(value)
                    for value in self._summary_cache.get(
                        "completedPages"
                    )
                    or []
                ],
                page,
            }
        )
        self._summary_cache.update(
            {
                "completedPages": completed_pages,
                "combinations": combinations,
                "lastStableSession": {
                    "query": query,
                    "library": library,
                    "year": year,
                    "partitionKey": partition_key,
                    "page": page,
                    "searchId": record["searchId"],
                    "resultSignature": record["resultSignature"],
                    "observedAt": record["observedAt"],
                },
                "sealed": False,
            }
        )
        fingerprint = str(record.get("configFingerprint") or "")
        if (
            fingerprint
            and fingerprint != self.config_fingerprint
        ):
            accepted = {
                *[
                    str(value)
                    for value in self._summary_cache.get(
                        "acceptedLegacyFingerprints"
                    )
                    or []
                ],
                fingerprint,
            }
            self._summary_cache[
                "acceptedLegacyFingerprints"
            ] = sorted(accepted)

    def seal(self) -> dict[str, Any]:
        self.read()
        if not self.path.is_file():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("wb") as stream:
                stream.flush()
                os.fsync(stream.fileno())
        if not self.sealed:
            self.marker_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.marker_path.with_suffix(
                self.marker_path.suffix + ".tmp"
            )
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "schemaVersion": SCAN_JOURNAL_SCHEMA_VERSION,
                            "taskId": self.task_id,
                            "configFingerprint": self.config_fingerprint,
                            "acceptedLegacyFingerprints": sorted(
                                self.accepted_fingerprints
                                - {self.config_fingerprint}
                            ),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.marker_path)
        if self._summary_cache is not None:
            self._summary_cache["sealed"] = True
        return self.checkpoint_summary()

    def _repair_tail_before_append(self) -> None:
        if not self.path.is_file():
            return
        raw = self.path.read_bytes()
        lines = raw.splitlines(keepends=True)
        valid_end = 0
        position = 0
        for index, line in enumerate(lines):
            line_end = position + len(line)
            content = line.rstrip(b"\r\n")
            if not line.endswith((b"\n", b"\r")):
                break
            try:
                payload = json.loads(content.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("record is not an object")
                self._validated_record(payload)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
                ScanJournalError,
            ) as exc:
                if index == len(lines) - 1:
                    break
                raise ScanJournalError(
                    "SCAN_JOURNAL_CORRUPT",
                    f"扫描日志中间行损坏（第 {index + 1} 行）：{exc}",
                ) from exc
            valid_end = line_end
            position = line_end
        if valid_end < len(raw):
            with self.path.open("r+b") as stream:
                stream.truncate(valid_end)
                stream.flush()
                os.fsync(stream.fileno())

    def _validated_record(self, record: dict[str, Any]) -> dict[str, Any]:
        payload = dict(record)
        if int(payload.get("schemaVersion") or 0) != SCAN_JOURNAL_SCHEMA_VERSION:
            raise ScanJournalError(
                "SCAN_JOURNAL_VERSION_MISMATCH",
                "扫描日志版本不兼容",
            )
        if str(payload.get("taskId") or "") != self.task_id:
            raise ScanJournalError(
                "SCAN_JOURNAL_TASK_MISMATCH",
                "扫描日志不属于当前任务",
            )
        if str(payload.get("configFingerprint") or "") not in (
            self.accepted_fingerprints
        ):
            raise ScanJournalError(
                "SCAN_JOURNAL_CONFIG_MISMATCH",
                "检索配置已变化，不能复用旧扫描日志",
            )
        required = (
            "query",
            "library",
            "effectiveYear",
            "page",
            "totalPages",
            "resultCount",
            "requestedPageSize",
            "actualPageSize",
            "groupingMode",
            "searchId",
            "resultSignature",
            "observedAt",
            "rows",
        )
        missing = [
            key
            for key in required
            if key not in payload or payload.get(key) in (None, "")
        ]
        if missing:
            raise ScanJournalError(
                "SCAN_JOURNAL_RECORD_INVALID",
                f"扫描日志记录缺字段：{', '.join(missing)}",
            )
        if not isinstance(payload["rows"], list):
            raise ScanJournalError(
                "SCAN_JOURNAL_RECORD_INVALID",
                "扫描日志 rows 必须是数组",
            )
        return payload

    @staticmethod
    def _validate_sequence(records: list[dict[str, Any]]) -> None:
        search_ids: dict[tuple[str, str, str, str], str] = {}
        seen_pages: set[tuple[str, str, str, str, int]] = set()
        page_records: dict[
            tuple[str, str, str, str, int],
            dict[str, Any],
        ] = {}
        for record in records:
            key = (
                str(record["query"]),
                str(record["library"]),
                str(record["effectiveYear"]),
                str(record.get("partitionKey") or ""),
            )
            current_search_id = str(record["searchId"])
            expected = search_ids.setdefault(key, current_search_id)
            if current_search_id != expected:
                resumed_from = str(record.get("resumedFrom") or "")
                evidence = record.get("resumeEvidence")
                previous_page = (
                    dict(evidence)
                    if isinstance(evidence, dict)
                    else {}
                )
                previous_record = page_records.get(
                    (
                        *key,
                        int(previous_page.get("page") or 0),
                    )
                )
                if not (
                    resumed_from == expected
                    and previous_record is not None
                    and str(previous_record["resultSignature"])
                    == str(previous_page.get("resultSignature") or "")
                    and str(
                        previous_page.get("searchId")
                        or previous_record["searchId"]
                    )
                    == str(previous_record["searchId"])
                ):
                    raise ScanJournalError(
                        "SCAN_JOURNAL_IDENTITY_MISMATCH",
                        "同一检索组合的 SearchSession 在扫描日志中发生变化",
                    )
                search_ids[key] = current_search_id
            if str(record["searchId"]) == expected:
                search_ids[key] = expected
            page_key = (*key, int(record["page"]))
            if page_key in seen_pages:
                raise ScanJournalError(
                    "SCAN_JOURNAL_DUPLICATE_PAGE",
                    f"扫描日志包含重复页：{page_key}",
                )
            seen_pages.add(page_key)
            page_records[page_key] = record
