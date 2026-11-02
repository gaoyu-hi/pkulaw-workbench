from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


SCAN_JOURNAL_SCHEMA_VERSION = 1


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


class ScanJournal:
    def __init__(
        self,
        path: Path,
        *,
        task_id: str,
        config_fingerprint: str,
    ) -> None:
        self.path = Path(path)
        self.task_id = str(task_id)
        self.config_fingerprint = str(config_fingerprint)
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
    ) -> dict[str, Any] | None:
        for record in self.read():
            if (
                record["query"] == query
                and record["library"] == library
                and record["effectiveYear"] == year
                and int(record["page"]) == int(page)
            ):
                return record
        return None

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
                )
            )
            combination = combinations.setdefault(
                key,
                {
                    "query": record["query"],
                    "library": record["library"],
                    "year": record["effectiveYear"],
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
            "completedPages": completed_pages,
            "combinations": list(combinations.values()),
            "lastStableSession": (
                {
                    "query": last["query"],
                    "library": last["library"],
                    "year": last["effectiveYear"],
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
        page = int(record["page"])
        key = "|".join((query, library, year))
        combinations = list(self._summary_cache.get("combinations") or [])
        combination = next(
            (
                item
                for item in combinations
                if (
                    item.get("query") == query
                    and item.get("library") == library
                    and item.get("year") == year
                )
            ),
            None,
        )
        if combination is None:
            combination = {
                "query": query,
                "library": library,
                "year": year,
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
                    "page": page,
                    "searchId": record["searchId"],
                    "resultSignature": record["resultSignature"],
                    "observedAt": record["observedAt"],
                },
                "sealed": False,
            }
        )

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
        if (
            str(payload.get("configFingerprint") or "")
            != self.config_fingerprint
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
        search_ids: dict[tuple[str, str, str], str] = {}
        seen_pages: set[tuple[str, str, str, int]] = set()
        page_records: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        for record in records:
            key = (
                str(record["query"]),
                str(record["library"]),
                str(record["effectiveYear"]),
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
                    and str(previous_record["searchId"]) == resumed_from
                    and str(previous_record["resultSignature"])
                    == str(previous_page.get("resultSignature") or "")
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
