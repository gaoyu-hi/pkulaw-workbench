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
_SECTION_COMPACT_HEADING_RE = re.compile(
    r"^第([0-9〇零一二两三四五六七八九十百千]+)章"
    r"[^\s，。；：]{2,50}$"
)
_CLAUSE_LINE_RE = re.compile(
    r"^第([0-9〇零一二两三四五六七八九十百千]+)条(?:\s|$)"
)
_CLAUSE_COMPACT_HEADING_RE = re.compile(
    r"^第([0-9〇零一二两三四五六七八九十百千]+)条"
    r"[\u4e00-\u9fff]{2,24}$"
)
_CLAUSE_ONLY_RE = re.compile(
    r"^第[0-9〇零一二两三四五六七八九十百千]+条$"
)
_OUTLINE_HEADING_RE = re.compile(
    r"^第[0-9〇零一二两三四五六七八九十百千]+"
    r"(?:分编|编|章|节)(?:\s|$)"
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


def _is_placeholder_ellipsis(line: str) -> bool:
    compact = str(line or "").strip()
    if re.fullmatch(r"(?:…+|\.{3,})", compact):
        return True
    if "|" not in compact:
        return False
    final_cell = compact.rsplit("|", 1)[-1].strip()
    return re.fullmatch(r"(?:…+|\.{3,})", final_cell) is not None


def _section_has_content(
    lines: list[str],
    start: int,
    end: int,
) -> bool:
    return any(
        line.strip() and not _SECTION_LINE_RE.match(line)
        for line in lines[start + 1 : end]
    )


def _only_outline_headings(
    lines: list[str],
    start: int,
    end: int,
) -> bool:
    return all(
        not line.strip()
        or _OUTLINE_HEADING_RE.match(line)
        or re.sub(r"\s+", "", line) in {"目录", "目次"}
        for line in lines[start:end]
    )


def _is_case_compilation_reference(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return (
        all(
            label in compact
            for label in ("基本案情", "裁判结果", "典型意义")
        )
        and re.search(
            r"[（(][〇零一二两三四五六七八九十百千]+[）)]"
            r"民法典条文指引",
            compact,
        )
        is not None
    )


def _repeated_front_matter_start(
    lines: list[str],
    toc_index: int,
) -> int | None:
    positions = [
        index
        for index in range(toc_index + 1, len(lines))
        if re.sub(r"\s+", "", lines[index]) in {
            "前言",
            "引言",
            "绪论",
        }
    ]
    return positions[1] if len(positions) >= 2 else None


def _content_sections(
    lines: list[str],
    sections: list[tuple[int, int]],
    clauses: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    toc_index: int | None = None
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        split_marker = ""
        if compact.endswith("目") and index + 1 < len(lines):
            following = re.sub(r"\s+", "", lines[index + 1])
            if following in {"录", "次"}:
                split_marker = compact + following
        if (
            compact in {"目录", "目次"}
            or compact.endswith(("目录", "目次"))
            or split_marker.endswith(("目录", "目次"))
        ):
            toc_index = index
            break
    if toc_index is not None:
        body_start = _repeated_front_matter_start(lines, toc_index)
        if body_start is not None:
            sections = [
                section
                for section in sections
                if section[0] > body_start
            ]
            return sections
        toc_sections = [
            section
            for section in sections
            if section[0] > toc_index
        ]
        if len(toc_sections) >= 4:
            first_number = toc_sections[0][1]
            best_section: tuple[int, int] | None = None
            best_length = 1
            for repeat_index in range(1, len(toc_sections)):
                if toc_sections[repeat_index][1] != first_number:
                    continue
                prefix = toc_sections[:repeat_index]
                repeated = toc_sections[
                    repeat_index : repeat_index + len(prefix)
                ]
                if (
                    len(prefix) >= 2
                    and [number for _, number in repeated]
                    == [number for _, number in prefix]
                ):
                    if len(prefix) > best_length:
                        best_section = toc_sections[repeat_index]
                        best_length = len(prefix)
            if best_section is not None:
                sections = sections[sections.index(best_section) :]
    if toc_index is not None and clauses and len(sections) > 1:
        first_clause_index = clauses[0][0]
        before_first_clause = [
            section
            for section in sections
            if section[0] < first_clause_index
        ]
        if len(before_first_clause) > 1 and all(
            _only_outline_headings(
                lines,
                current[0] + 1,
                following[0],
            )
            for current, following in zip(
                before_first_clause,
                before_first_clause[1:],
            )
        ):
            sections = sections[
                sections.index(before_first_clause[-1]) :
            ]
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
        section_match = (
            _SECTION_LINE_RE.match(line)
            or _SECTION_COMPACT_HEADING_RE.match(line)
        )
        if section_match:
            number = _parse_number(section_match.group(1))
            if number is not None:
                sections.append((index, number))
        clause_match = (
            _CLAUSE_LINE_RE.match(line)
            or _CLAUSE_COMPACT_HEADING_RE.match(line)
        )
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
        if missing and not _is_case_compilation_reference(joined):
            _append_issue(issues, "MISSING_CLAUSE_NUMBERS")

    for index, line in enumerate(lines):
        if _CLAUSE_ONLY_RE.fullmatch(line):
            if index == len(lines) - 1:
                _append_issue(issues, "TRUNCATED_TAIL")
            elif (
                _CLAUSE_ONLY_RE.fullmatch(lines[index + 1])
                or _SECTION_LINE_RE.match(lines[index + 1])
                or re.sub(r"\s+", "", lines[index + 1])
                in {"目录", "目次"}
            ):
                _append_issue(issues, "EMPTY_CLAUSE_BODY")

    tail = lines[-1]
    if (
        not _is_placeholder_ellipsis(tail)
        and re.search(r"(?:…+|\.{3,}|未完待续|续下页)$", tail)
    ):
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
      let pendingInline = "";
      const appendBody = value => {{
        const text = clean(value);
        if (text) bodySegments.push(text);
      }};
      const flushInline = () => {{
        if (!pendingInline) return;
        appendBody(pendingInline);
        pendingInline = "";
      }};
      const isBlockElement = element => {{
        if (element.matches('br, hr')) return true;
        const display = typeof getComputedStyle === 'function'
          ? getComputedStyle(element).display
          : '';
        if (display) return !display.startsWith('inline') && display !== 'contents';
        return /^(?:ADDRESS|ARTICLE|ASIDE|BLOCKQUOTE|DIV|DL|FIELDSET|FIGCAPTION|FIGURE|FOOTER|FORM|H[1-6]|HEADER|HR|LI|MAIN|NAV|OL|P|PRE|SECTION|TABLE|UL)$/.test(
          element.tagName || ''
        );
      }};
      const collectBody = node => {{
        if (!node) return;
        if (node.nodeType === Node.TEXT_NODE) {{
          pendingInline += node.textContent || '';
          return;
        }}
        if (node.nodeType !== Node.ELEMENT_NODE) return;
        const element = node;
        if (element.matches(
          'script,style,noscript,.TiaoYinV2,.fbWindow-btn,.law-ai-full,button'
        )) return;
        if (element.matches('br')) {{
          flushInline();
          return;
        }}
        if (element.matches('p.navzhang, .kuan-content')) {{
          flushInline();
          appendBody(element.textContent);
          return;
        }}
        if (element.matches('p')) {{
          flushInline();
          appendBody(element.textContent);
          return;
        }}
        if (element.matches('table')) {{
          flushInline();
          for (const row of [...element.querySelectorAll('tr')]) {{
            const cells = [...row.querySelectorAll(':scope > th, :scope > td')]
              .map(cell => clean(cell.textContent))
              .filter(Boolean);
            if (cells.length) bodySegments.push(cells.join(' | '));
          }}
          return;
        }}
        const block = isBlockElement(element);
        if (block) flushInline();
        for (const child of element.childNodes) collectBody(child);
        if (block) flushInline();
      }};
      if (bodyRoot) {{
        for (const child of bodyRoot.childNodes) collectBody(child);
      }}
      flushInline();
      const body = bodySegments.join('\\n');
    """
