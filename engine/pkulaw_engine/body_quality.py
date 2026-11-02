from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

BodyStatus = Literal["complete", "suspicious", "failed"]

_CHINESE_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {
    "十": 10,
    "百": 100,
    "千": 1000,
}
_SECTION_LINE_RE = re.compile(
    r"^第([0-9〇零一二两三四五六七八九十百千]+)章(?:\s|$)"
)
_CLAUSE_LINE_RE = re.compile(
    r"^第([0-9〇零一二两三四五六七八九十百千]+)条(?:\s|$)"
)
_CLAUSE_ONLY_RE = re.compile(
    r"^第[0-9〇零一二两三四五六七八九十百千]+条$"
)


@dataclass(frozen=True)
class BodyCompleteness:
    status: BodyStatus
    text_length: int
    segment_count: int
    section_count: int
    clause_count: int
    clause_numbers: list[int]
    missing_clause_numbers: list[int]
    continuous_clause_numbers: bool
    issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "textLength": self.text_length,
            "segmentCount": self.segment_count,
            "sectionCount": self.section_count,
            "clauseCount": self.clause_count,
            "clauseNumbers": list(self.clause_numbers),
            "missingClauseNumbers": list(self.missing_clause_numbers),
            "continuousClauseNumbers": self.continuous_clause_numbers,
            "issues": list(self.issues),
        }


class BodyCompletenessError(RuntimeError):
    code = "BODY_INCOMPLETE"

    def __init__(self, assessment: BodyCompleteness) -> None:
        self.assessment = assessment
        self.details = assessment.to_dict()
        super().__init__(
            "详情正文未通过完整性门禁："
            f"状态={assessment.status}，"
            f"章节={assessment.section_count}，"
            f"条文={assessment.clause_count}，"
            f"问题={', '.join(assessment.issues) or '<unknown>'}"
        )


def _parse_number(value: str) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    total = 0
    current = 0
    for character in raw:
        if character in _CHINESE_DIGITS:
            current = _CHINESE_DIGITS[character]
            continue
        unit = _CHINESE_UNITS.get(character)
        if unit is None:
            return None
        total += (current or 1) * unit
        current = 0
    return total + current


def _append_issue(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)


def _section_has_content(
    lines: list[str],
    start: int,
    end: int,
) -> bool:
    return any(
        line.strip() and not _SECTION_LINE_RE.match(line)
        for line in lines[start + 1 : end]
    )


def _content_sections(
    lines: list[str],
    sections: list[tuple[int, int]],
    clauses: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    if not sections or not clauses:
        return sections
    first_clause_index = clauses[0][0]
    pre_clause_count = sum(
        1 for line_index, _number in sections if line_index < first_clause_index
    )
    if pre_clause_count < 3:
        return sections

    for outline_count in range(pre_clause_count - 1, 1, -1):
        prefix = sections[:outline_count]
        remaining_numbers = [
            number for _line_index, number in sections[outline_count:]
        ]
        outline_numbers = [number for _line_index, number in prefix]
        if remaining_numbers[:outline_count] != outline_numbers:
            continue
        if any(
            _section_has_content(
                lines,
                line_index,
                (
                    sections[index + 1][0]
                    if index + 1 < len(sections)
                    else len(lines)
                ),
            )
            for index, (line_index, _number) in enumerate(prefix)
        ):
            continue
        return sections[outline_count:]
    return sections


def assess_body_completeness(
    text: str,
    metadata: dict[str, Any] | None = None,
) -> BodyCompleteness:
    normalized = (
        str(text or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u3000", " ")
    )
    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in normalized.splitlines()
        if line.strip()
    ]
    joined = "\n".join(lines)
    issues: list[str] = []
    if not joined:
        return BodyCompleteness(
            status="failed",
            text_length=0,
            segment_count=0,
            section_count=0,
            clause_count=0,
            clause_numbers=[],
            missing_clause_numbers=[],
            continuous_clause_numbers=False,
            issues=["BODY_EMPTY"],
        )
    if len(joined) < 20:
        _append_issue(issues, "BODY_TOO_SHORT")

    sections: list[tuple[int, int]] = []
    clauses: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        section_match = _SECTION_LINE_RE.match(line)
        if section_match:
            number = _parse_number(section_match.group(1))
            if number is not None:
                sections.append((index, number))
        clause_match = _CLAUSE_LINE_RE.match(line)
        if clause_match:
            number = _parse_number(clause_match.group(1))
            if number is not None:
                clauses.append((index, number))

    clause_numbers = [number for _, number in clauses]
    sections = _content_sections(lines, sections, clauses)
    section_count = len(sections)
    clause_count = len(clauses)

    for section_index, (_line_index, _number) in enumerate(sections):
        start = sections[section_index][0]
        end = (
            sections[section_index + 1][0]
            if section_index + 1 < len(sections)
            else len(lines)
        )
        if not _section_has_content(lines, start, end):
            _append_issue(issues, "EMPTY_SECTION")

    missing: list[int] = []
    continuous = not clause_numbers
    if clause_numbers:
        unique_numbers = sorted(set(clause_numbers))
        missing = sorted(set(range(1, max(unique_numbers) + 1)) - set(unique_numbers))
        continuous = unique_numbers[0] == 1 and not missing
        if missing:
            _append_issue(issues, "MISSING_CLAUSE_NUMBERS")

    for index, line in enumerate(lines):
        if _CLAUSE_ONLY_RE.fullmatch(line):
            if index == len(lines) - 1:
                _append_issue(issues, "TRUNCATED_TAIL")
            else:
                _append_issue(issues, "EMPTY_CLAUSE_BODY")

    tail = lines[-1]
    if re.search(r"(?:…+|\.{3,}|未完待续|续下页)$", tail):
        _append_issue(issues, "TRUNCATED_TAIL")
    if _SECTION_LINE_RE.match(tail):
        _append_issue(issues, "TRUNCATED_TAIL")

    if metadata:
        expected_sections = metadata.get("expectedSections")
        expected_clauses = metadata.get("expectedClauses")
        if expected_sections is not None and int(expected_sections) != section_count:
            _append_issue(issues, "EXPECTED_SECTION_COUNT_MISMATCH")
        if expected_clauses is not None and int(expected_clauses) != clause_count:
            _append_issue(issues, "EXPECTED_CLAUSE_COUNT_MISMATCH")

    return BodyCompleteness(
        status="suspicious" if issues else "complete",
        text_length=len(joined),
        segment_count=len(lines),
        section_count=section_count,
        clause_count=clause_count,
        clause_numbers=clause_numbers,
        missing_clause_numbers=missing,
        continuous_clause_numbers=continuous,
        issues=issues,
    )


def detail_body_extraction_js(root_selector: str) -> str:
    return f"""
      const bodyRoot = query({json.dumps(root_selector)});
      const bodySegments = [];
      const appendBody = value => {{
        const text = clean(value);
        if (text) bodySegments.push(text);
      }};
      const collectBody = node => {{
        if (!node) return;
        if (node.nodeType === Node.TEXT_NODE) {{
          appendBody(node.textContent);
          return;
        }}
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const element = node;
        if (element.matches(
          'script,style,noscript,.TiaoYinV2,.fbWindow-btn,.law-ai-full,button'
        )) return;
        if (element.matches('p.navzhang, .kuan-content')) {{
          appendBody(element.textContent);
          return;
        }}
        if (element.matches('p')) {{
          appendBody(element.textContent);
          return;
        }}
        if (element.matches('table')) {{
          for (const row of [...element.querySelectorAll('tr')]) {{
            const cells = [...row.querySelectorAll(':scope > th, :scope > td')]
              .map(cell => clean(cell.textContent))
              .filter(Boolean);
            if (cells.length) bodySegments.push(cells.join(' | '));
          }}
          return;
        }}
        for (const child of element.childNodes) collectBody(child);
      }};
      if (bodyRoot) {{
        for (const child of bodyRoot.childNodes) collectBody(child);
      }}
      const body = bodySegments.join('\\n');
    """
