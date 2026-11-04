from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from docx import Document

EXPECTED_ANTIWORD_VERSION = "0.37"
EXPECTED_ANTIWORD_SHA256 = (
    "1dde37af1bb049c03e71c9d20eb99a2a3e011fe9170306b3a154be5c8515788f"
)


class DocumentExtractionError(RuntimeError):
    def __init__(
        self,
        code_or_message: str,
        message: str | None = None,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        if message is None:
            self.code = "DOC_TEXT_EXTRACTION_FAILED"
            actual_message = str(code_or_message)
        else:
            self.code = str(code_or_message or "DOC_TEXT_EXTRACTION_FAILED")
            actual_message = str(message)
        self.details = dict(details or {})
        super().__init__(actual_message)


@dataclass(frozen=True)
class DocumentExtractionResult:
    text: str
    artifact_method: str
    text_source_type: str
    converter_name: str
    converter_version: str
    converter_sha256: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "artifactMethod": self.artifact_method,
            "textSourceType": self.text_source_type,
            "converterName": self.converter_name,
            "converterVersion": self.converter_version,
            "converterSha256": self.converter_sha256,
        }


def normalize_text(value: str) -> str:
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_document(
    path: Path,
    runtime_root: Path | None = None,
) -> DocumentExtractionResult:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        raw_text = _extract_docx(path)
        try:
            converter_version = package_version("python-docx")
        except PackageNotFoundError:
            converter_version = "unknown"
        result = DocumentExtractionResult(
            text="",
            artifact_method="python-docx",
            text_source_type="word-text",
            converter_name="python-docx",
            converter_version=converter_version,
        )
    elif suffix == ".doc":
        raw_text, result = _extract_legacy_doc(path, runtime_root)
    else:
        raise DocumentExtractionError(
            "DOC_TEXT_EXTRACTION_FAILED",
            f"不支持的 Word 扩展名：{path.suffix}",
        )
    normalized = normalize_text(raw_text)
    if len(normalized) < 20:
        raise DocumentExtractionError(
            "DOC_TEXT_EXTRACTION_FAILED",
            "Word 正文过短或未能提取有效文本",
            details={
                "file": str(path),
                "method": result.artifact_method,
                "textLength": len(normalized),
            },
        )
    return DocumentExtractionResult(
        text=normalized,
        artifact_method=result.artifact_method,
        text_source_type=result.text_source_type,
        converter_name=result.converter_name,
        converter_version=result.converter_version,
        converter_sha256=result.converter_sha256,
    )


def extract_text(
    path: Path,
    runtime_root: Path | None = None,
) -> tuple[str, str]:
    result = extract_document(path, runtime_root)
    return result.text, result.artifact_method


def create_docx_from_text(title: str, body: str, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_heading(title, level=1)
    for paragraph in normalize_text(body).split("\n\n"):
        document.add_paragraph(paragraph)
    temporary = output.with_suffix(output.suffix + ".tmp")
    document.save(temporary)
    temporary.replace(output)
    return output


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(normalize_text(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def _extract_docx(path: Path) -> str:
    try:
        document = Document(path)
        sections = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table in document.tables:
            for row in table.rows:
                values = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if values:
                    sections.append(" | ".join(values))
        return "\n".join(sections)
    except Exception as exc:
        raise DocumentExtractionError(
            "DOC_TEXT_EXTRACTION_FAILED",
            f"DOCX 正文读取失败：{exc}",
            details={"file": str(path), "converter": "python-docx"},
        ) from exc


def _default_runtime_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(str(sys._MEIPASS))  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def _bundled_antiword_paths(runtime_root: Path | None) -> tuple[Path, Path]:
    root = Path(runtime_root) if runtime_root is not None else _default_runtime_root()
    binary_root = root / "vendor" / "antiword" / "win-x64"
    return binary_root / "antiword.exe", binary_root / "share" / "antiword"


def _antiword_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["ANTIWORDHOME"] = str(home)
    return environment


def _decode_process_output(value: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            return value.decode(encoding)
        except UnicodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _validate_bundled_antiword(
    binary: Path,
) -> None:
    if not binary.is_file():
        raise DocumentExtractionError(
            "DOC_CONVERTER_UNAVAILABLE",
            f"内置 antiword 不存在：{binary}",
            details={"path": str(binary), "reason": "missing"},
        )
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if digest.lower() != EXPECTED_ANTIWORD_SHA256:
        raise DocumentExtractionError(
            "DOC_CONVERTER_UNAVAILABLE",
            "内置 antiword 的 SHA-256 与锁定版本不一致",
            details={
                "path": str(binary),
                "expectedSha256": EXPECTED_ANTIWORD_SHA256,
                "actualSha256": digest,
            },
        )
    home = binary.parent / "share" / "antiword"
    if not home.is_dir() or not (home / "UTF-8.txt").is_file():
        raise DocumentExtractionError(
            "DOC_CONVERTER_UNAVAILABLE",
            "内置 antiword 缺少 UTF-8 资源文件",
            details={"path": str(binary), "antiwordHome": str(home)},
        )
    try:
        result = subprocess.run(
            [str(binary)],
            capture_output=True,
            timeout=10,
            check=False,
            env=_antiword_environment(home),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DocumentExtractionError(
            "DOC_CONVERTER_UNAVAILABLE",
            f"内置 antiword 无法启动：{exc}",
            details={"path": str(binary)},
        ) from exc
    output = "\n".join(
        (
            _decode_process_output(result.stdout),
            _decode_process_output(result.stderr),
        )
    )
    match = re.search(r"Version:\s*([0-9.]+)", output)
    actual_version = match.group(1) if match else ""
    if result.returncode != 0 or actual_version != EXPECTED_ANTIWORD_VERSION:
        raise DocumentExtractionError(
            "DOC_CONVERTER_UNAVAILABLE",
            "内置 antiword 版本不符合锁定值",
            details={
                "path": str(binary),
                "expectedVersion": EXPECTED_ANTIWORD_VERSION,
                "actualVersion": actual_version or "<unknown>",
                "returnCode": result.returncode,
            },
        )


def inspect_bundled_antiword(
    runtime_root: Path | None = None,
) -> dict[str, str]:
    binary, _home = _bundled_antiword_paths(runtime_root)
    try:
        _validate_bundled_antiword(binary)
    except DocumentExtractionError as exc:
        return {
            "status": "unavailable",
            "code": exc.code,
            "path": str(binary),
            "version": "",
            "sha256": (
                hashlib.sha256(binary.read_bytes()).hexdigest()
                if binary.is_file()
                else ""
            ),
            "error": str(exc),
        }
    return {
        "status": "available",
        "code": "",
        "path": str(binary),
        "version": EXPECTED_ANTIWORD_VERSION,
        "sha256": EXPECTED_ANTIWORD_SHA256,
        "error": "",
    }


def _run_bundled_antiword(path: Path, binary: Path) -> str:
    home = binary.parent / "share" / "antiword"
    try:
        result = subprocess.run(
            [str(binary), "-m", "UTF-8.txt", "-w", "0", str(path)],
            capture_output=True,
            timeout=60,
            check=False,
            env=_antiword_environment(home),
        )
    except subprocess.TimeoutExpired as exc:
        raise DocumentExtractionError(
            "DOC_TEXT_EXTRACTION_FAILED",
            "内置 antiword 转换超时",
            details={"file": str(path), "converter": "bundled-antiword"},
        ) from exc
    except OSError as exc:
        raise DocumentExtractionError(
            "DOC_CONVERTER_UNAVAILABLE",
            f"内置 antiword 启动失败：{exc}",
            details={"file": str(path), "converter": "bundled-antiword"},
        ) from exc
    text = _decode_process_output(result.stdout)
    if result.returncode != 0 or not text.strip():
        raise DocumentExtractionError(
            "DOC_TEXT_EXTRACTION_FAILED",
            "内置 antiword 未输出有效正文",
            details={
                "file": str(path),
                "returnCode": result.returncode,
                "stderr": _decode_process_output(result.stderr)[-1000:],
            },
        )
    return text


def _run_system_antiword(path: Path, executable: str) -> str:
    result = subprocess.run(
        [executable, "-w", "0", str(path)],
        capture_output=True,
        timeout=60,
        check=False,
    )
    text = _decode_process_output(result.stdout)
    if result.returncode != 0 or not text.strip():
        raise DocumentExtractionError(
            "DOC_TEXT_EXTRACTION_FAILED",
            "系统 antiword 未输出有效正文",
            details={"file": str(path), "returnCode": result.returncode},
        )
    return text


def _run_libreoffice(path: Path, executable: str) -> str:
    with tempfile.TemporaryDirectory(prefix="pkulaw-doc-") as directory:
        result = subprocess.run(
            [
                executable,
                "--headless",
                "--convert-to",
                "txt:Text (encoded):UTF8",
                "--outdir",
                directory,
                str(path),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        converted = Path(directory) / f"{path.stem}.txt"
        if result.returncode == 0 and converted.is_file():
            text = converted.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                return text
    raise DocumentExtractionError(
        "DOC_TEXT_EXTRACTION_FAILED",
        "LibreOffice 未输出有效正文",
        details={"file": str(path), "returnCode": result.returncode},
    )


def _run_word_com(path: Path) -> str:
    try:
        import win32com.client  # type: ignore
        import pythoncom  # type: ignore
    except Exception as exc:
        raise DocumentExtractionError(
            "DOC_CONVERTER_UNAVAILABLE",
            f"本机 Microsoft Word COM 不可用：{exc}",
            details={"file": str(path), "converter": "word-com"},
        ) from exc

    errors: list[str] = []
    pythoncom.CoInitialize()
    try:
        for attempt in range(3):
            word = None
            document = None
            try:
                word = win32com.client.DispatchEx("Word.Application")
                word.Visible = False
                word.DisplayAlerts = 0
                word.Options.SaveNormalPrompt = False
                word.AutomationSecurity = 3
                document = word.Documents.Open(
                    str(path.resolve()),
                    ReadOnly=True,
                    AddToRecentFiles=False,
                    ConfirmConversions=False,
                )
                try:
                    text = str(document.Content.Text or "")
                except Exception:
                    text = ""
                if not text.strip():
                    with tempfile.TemporaryDirectory(
                        prefix="pkulaw-word-com-"
                    ) as directory:
                        converted = Path(directory) / "converted.txt"
                        document.SaveAs2(
                            str(converted),
                            FileFormat=2,
                            Encoding=65001,
                            AddToRecentFiles=False,
                        )
                        if converted.is_file():
                            text = converted.read_text(
                                encoding="utf-8",
                                errors="replace",
                            )
                if text.strip():
                    return text
                errors.append(f"attempt {attempt + 1}: empty text")
            except Exception as exc:
                errors.append(f"attempt {attempt + 1}: {exc}")
            finally:
                if document is not None:
                    try:
                        document.Close(False)
                    except Exception:
                        pass
                if word is not None:
                    try:
                        word.Quit()
                    except Exception:
                        pass
            if attempt < 2:
                time.sleep(1.0)
    finally:
        pythoncom.CoUninitialize()
    raise DocumentExtractionError(
        "DOC_TEXT_EXTRACTION_FAILED",
        "Microsoft Word COM 未提取到有效正文",
        details={
            "file": str(path),
            "converter": "word-com",
            "errors": errors,
        },
    )


def _extract_legacy_doc(
    path: Path,
    runtime_root: Path | None,
) -> tuple[str, DocumentExtractionResult]:
    if len(str(path)) >= 240:
        with tempfile.TemporaryDirectory(prefix="pkd-") as directory:
            short_path = Path(directory) / "input.doc"
            shutil.copy2(path, short_path)
            try:
                return _extract_legacy_doc(short_path, runtime_root)
            except DocumentExtractionError as exc:
                exc.details.setdefault("sourceFile", str(path))
                raise
    binary, _home = _bundled_antiword_paths(runtime_root)
    errors: list[dict[str, object]] = []
    try:
        _validate_bundled_antiword(binary)
        text = _run_bundled_antiword(path, binary)
        return text, DocumentExtractionResult(
            text="",
            artifact_method="bundled-antiword",
            text_source_type="word-text",
            converter_name="antiword",
            converter_version=EXPECTED_ANTIWORD_VERSION,
            converter_sha256=EXPECTED_ANTIWORD_SHA256,
        )
    except DocumentExtractionError as exc:
        errors.append(
            {
                "method": "bundled-antiword",
                "code": exc.code,
                "message": str(exc),
                **exc.details,
            }
        )

    system_antiword = shutil.which("antiword")
    if system_antiword:
        try:
            text = _run_system_antiword(path, system_antiword)
            return text, DocumentExtractionResult(
                text="",
                artifact_method="system-antiword",
                text_source_type="word-text",
                converter_name="antiword",
                converter_version="system",
            )
        except (DocumentExtractionError, subprocess.TimeoutExpired, OSError) as exc:
            errors.append(
                {
                    "method": "system-antiword",
                    "code": getattr(
                        exc,
                        "code",
                        "DOC_TEXT_EXTRACTION_FAILED",
                    ),
                    "message": str(exc),
                }
            )

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            text = _run_libreoffice(path, soffice)
            return text, DocumentExtractionResult(
                text="",
                artifact_method="libreoffice",
                text_source_type="word-text",
                converter_name="libreoffice",
                converter_version="system",
            )
        except (DocumentExtractionError, subprocess.TimeoutExpired, OSError) as exc:
            errors.append(
                {
                    "method": "libreoffice",
                    "code": getattr(
                        exc,
                        "code",
                        "DOC_TEXT_EXTRACTION_FAILED",
                    ),
                    "message": str(exc),
                }
            )

    try:
        text = _run_word_com(path)
        return text, DocumentExtractionResult(
            text="",
            artifact_method="word-com",
            text_source_type="word-text",
            converter_name="microsoft-word-com",
            converter_version="system",
        )
    except DocumentExtractionError as exc:
        errors.append(
            {
                "method": "word-com",
                "code": exc.code,
                "message": str(exc),
                **exc.details,
            }
        )

    unavailable = [error for error in errors if error.get("code") == "DOC_CONVERTER_UNAVAILABLE"]
    code = (
        "DOC_CONVERTER_UNAVAILABLE"
        if errors and len(unavailable) == len(errors)
        else "DOC_TEXT_EXTRACTION_FAILED"
    )
    raise DocumentExtractionError(
        code,
        "旧版 .doc 无法提取正文；所有离线与可选后备转换器均失败",
        details={"file": str(path), "attempts": errors},
    )
