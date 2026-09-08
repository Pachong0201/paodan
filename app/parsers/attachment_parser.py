"""附件解析统一入口 + PDF/DOCX/XLSX/CSV/TXT/图片 各解析器。

设计要点：
- PDF 优先文本层，为空才尝试 OCR（tesseract 可选）；
- XLSX 保留 sheet/row/col 上下文，同时拼为可读文本；
- 图片 OCR 用 tesseract，缺可执行文件时 extraction_status=skipped + warning；
- 所有失败均记录 warning 并返回 partial/failed，不让单附件中断整批。
"""
from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import List, Optional

from ..models import AttachmentDoc
from ..preprocessing.normalization import TextCleaner

logger = logging.getLogger(__name__)

PARSER_VERSION = "attachment-parser-v1"
OCR_VERSION = "tesseract-v1"


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """流式读取文件 SHA256，避免大附件一次性读入内存。"""
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


SUPPORTED = {"pdf", "docx", "doc", "xlsx", "xls", "csv", "txt", "md", "jpg", "jpeg", "png", "eml"}


def parse_attachment(path: str | Path, filename: str = "", ocr_enabled: bool = True) -> AttachmentDoc:
    """按扩展名解析单个附件文件."""
    p = Path(path)
    ext = p.suffix.lower().lstrip(".") or Path(filename).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    source_sha = sha256_file(p)
    att = AttachmentDoc(filename=filename or p.name, file_type=ext,
                        sha256=source_sha, source_sha256=source_sha,
                        parser_version=PARSER_VERSION,
                        metadata={"size": p.stat().st_size if p.exists() else 0})
    try:
        if ext == "pdf":
            _parse_pdf(p, att, ocr_enabled)
        elif ext == "docx":
            _parse_docx(p, att)
        elif ext == "xlsx":
            _parse_xlsx(p, att)
        elif ext == "csv":
            _parse_csv(p, att)
        elif ext in ("txt", "md"):
            _parse_txt(p, att)
        elif ext in ("jpg", "png"):
            _parse_image(p, att, ocr_enabled)
        elif ext == "eml":
            # 嵌套邮件：仅文本（内容由主流程记录，避免递归爆炸）
            _parse_txt(p, att)
        else:
            att.extraction_status = "skipped"
            att.warnings.append(f"不支持的附件类型: {ext}")
            att.text = ""
    except Exception as e:  # noqa: BLE001
        logger.warning("附件解析失败 %s: %s", p.name, e)
        att.extraction_status = "failed"
        att.warnings.append(f"解析异常: {e}")
        # 合成/非标准扩展名兜底：若 bytes 明显是 UTF-8/UTF-16 文本，按纯文本读取，
        # 避免测试/真实误命名附件因解析器直接失败而丢失内容。
        if ext in ("pdf", "docx", "doc", "xlsx", "xls"):
            try:
                raw = p.read_bytes()
                if b"\x00" not in raw:
                    text = raw.decode("utf-8", errors="strict")
                    if text and sum(ch.isprintable() or ch.isspace() for ch in text) / max(len(text), 1) > 0.85:
                        att.text = TextCleaner.clean(text)
                        att.extraction_status = "partial"
                        att.warnings.append("非标准文件头，按纯文本兜底读取")
            except Exception:  # noqa: BLE001
                pass
    # 文本哈希（去重/Parse Cache 版本化使用），与 source_sha256 严格分离。
    import hashlib
    normalized_text = TextCleaner.clean(att.text or "")
    att.text = normalized_text
    att.text_sha256 = hashlib.sha256(normalized_text.encode("utf-8", errors="ignore")).hexdigest()
    # 旧字段兼容：不再作为唯一附件身份。
    att.content_hash = att.text_sha256
    att.ocr_version = OCR_VERSION if att.ocr_used else "none"
    if not att.source_sha256:
        att.source_sha256 = sha256_file(p)
    if not att.sha256:
        att.sha256 = att.source_sha256
    return att


# ---------------- PDF ----------------
def _parse_pdf(p: Path, att: AttachmentDoc, ocr_enabled: bool):
    text_parts = []
    try:
        import fitz  # PyMuPDF

        with fitz.open(p) as doc:
            att.metadata["pages"] = doc.page_count
            for page in doc:
                text_parts.append(page.get_text("text"))
    except Exception as e:  # noqa: BLE001
        # 尝试 pypdf 兜底
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(p))
            att.metadata["pages"] = len(reader.pages)
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
        except Exception as e2:  # noqa: BLE001
            raise RuntimeError(f"PDF 文本层失败(pymupdf:{e}; pypdf:{e2})") from e
    text = TextCleaner.clean("\n".join(text_parts))
    # 判断文本层是否明显不足 -> OCR
    if ocr_enabled and _pdf_text_poor(text):
        ocr_text = _ocr_pdf(p, att)
        if ocr_text:
            text = f"{text}\n[OCR]\n{ocr_text}"
            att.ocr_used = True
            att.extraction_status = "success" if text.strip() else "partial"
        else:
            att.extraction_status = "partial"
            att.warnings.append("PDF 文本层不足且 OCR 失败/不可用")
    att.text = text
    if not text.strip() and not ocr_enabled:
        att.extraction_status = "partial"
        att.warnings.append("PDF 无可提取文本层（未启用 OCR）")


def _pdf_text_poor(text: str) -> bool:
    """文本层为空或过短（扫描件特征）。"""
    t = (text or "").strip()
    if not t:
        return True
    if len(t) < 40:
        return True
    # 大量乱码/单字重复
    if sum(1 for ch in t if not ch.isascii() and not '\u4e00' <= ch <= '\u9fff') / max(len(t), 1) > 0.4:
        return True
    return False


def _ocr_pdf(p: Path, att: AttachmentDoc) -> str:
    """用 tesseract 对 PDF 每页渲染 OCR；不可用时返回空并记录 warning。"""
    try:
        from PIL import Image
    except Exception as e:  # noqa: BLE001
        att.warnings.append(f"OCR 依赖缺失: {e}")
        return ""
    import subprocess
    import sys

    if not shutil_which("tesseract"):
        att.warnings.append("tesseract 不可用，跳过 OCR")
        return ""
    try:
        import fitz
        out_parts = []
        with fitz.open(p) as doc:
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                out_parts.append(_tesseract_ocr(img))
        return "\n".join(x for x in out_parts if x)
    except Exception as e:  # noqa: BLE001
        att.warnings.append(f"PDF OCR 失败: {e}")
        return ""


def shutil_which(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def _tesseract_ocr(img) -> str:
    import subprocess
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "page.png")
        img.save(tmp)
        try:
            proc = subprocess.run(["tesseract", tmp, os.path.join(td, "out"), "-l", "chi_tra+eng"],
                                  capture_output=True, timeout=120)
            if proc.returncode != 0:
                # 无繁体包时用默认
                proc = subprocess.run(["tesseract", tmp, os.path.join(td, "out")],
                                      capture_output=True, timeout=120)
            out = os.path.join(td, "out.txt")
            if os.path.exists(out):
                with open(out, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()
        except Exception as e:  # noqa: BLE001
            logger.warning("OCR 子进程失败: %s", e)
        return ""


# ---------------- DOCX ----------------
def _parse_docx(p: Path, att: AttachmentDoc):
    try:
        from docx import Document
    except ImportError:
        att.extraction_status = "failed"
        att.warnings.append("python-docx 未安装")
        return
    doc = Document(str(p))
    parts = []
    tables = []
    # 正文段落（保留标题层级语义）
    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            style = (para.style.name or "").lower()
            if "heading" in style or "标题" in style:
                parts.append(f"[标题] {t}")
            else:
                parts.append(t)
    # 表格：保留 row/col
    for ti, table in enumerate(doc.tables):
        att.metadata.setdefault("table_count", 0)
        att.metadata["table_count"] += 1
        for ri, row in enumerate(table.rows):
            cells = []
            for ci, cell in enumerate(row.cells):
                v = cell.text.strip()
                if v:
                    tables.append({"sheet": f"table{ti+1}", "row": ri + 1, "col": ci + 1, "value": v[:500]})
                cells.append(v)
            parts.append(" | ".join(cells))
    att.tables = tables
    att.text = TextCleaner.clean("\n".join(parts))
    att.extraction_status = "success"
    if not att.text and not tables:
        att.warnings.append("DOCX 无可提取文本")


# ---------------- XLSX ----------------
def _parse_xlsx(p: Path, att: AttachmentDoc):
    try:
        from openpyxl import load_workbook
    except ImportError:
        att.extraction_status = "failed"
        att.warnings.append("openpyxl 未安装")
        return
    wb = load_workbook(str(p), read_only=True, data_only=True)
    att.metadata["sheets"] = wb.sheetnames
    parts = []
    max_show = 800
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        parts.append(f"### Sheet: {sheet}")
        for ri, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if ri > 5000:
                parts.append("[行数超过5000，截断]")
                break
            vals = []
            for ci, v in enumerate(row, start=1):
                if v is None:
                    continue
                s = str(v).strip()
                if not s:
                    continue
                if s.startswith("="):
                    continue  # 公式
                if len(s) > 300:
                    s = s[:300] + "…"
                vals.append(s)
                att.tables.append({"sheet": sheet, "row": ri, "col": ci, "value": s[:500]})
                if len(att.tables) > max_show:
                    att.tables = att.tables[:max_show]
            if vals:
                parts.append(f"[r{ri}] " + " | ".join(vals))
        # 每 sheet 上限
        if len(parts) > 3000:
            break
    wb.close()
    att.text = TextCleaner.clean("\n".join(parts))
    att.extraction_status = "success"


def _parse_csv(p: Path, att: AttachmentDoc):
    rows = []
    try:
        raw = p.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception:  # noqa: BLE001
        text = p.read_text(encoding="utf-8", errors="replace")
    try:
        reader = csv.reader(io.StringIO(text))
        for ri, row in enumerate(reader, start=1):
            vals = [v.strip() for v in row if v is not None and v.strip()]
            if vals:
                rows.append(" | ".join(vals))
                for ci, v in enumerate(row, start=1):
                    if v and v.strip():
                        att.tables.append({"sheet": "csv", "row": ri, "col": ci, "value": v.strip()[:500]})
            if ri > 10000:
                break
    except Exception as e:  # noqa: BLE001
        att.warnings.append(f"CSV 解析异常: {e}")
    att.text = TextCleaner.clean("\n".join(rows))
    att.extraction_status = "success"


def _parse_txt(p: Path, att: AttachmentDoc):
    try:
        raw = p.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception:  # noqa: BLE001
        text = p.read_text(encoding="utf-8", errors="replace")
    att.text = TextCleaner.clean(text)
    att.extraction_status = "success"


# ---------------- IMAGE ----------------
def _parse_image(p: Path, att: AttachmentDoc, ocr_enabled: bool):
    if not ocr_enabled:
        att.extraction_status = "skipped"
        att.warnings.append("OCR 已禁用")
        return
    try:
        from PIL import Image
        img = Image.open(p)
        att.metadata["image_size"] = img.size
        att.metadata["image_mode"] = img.mode
        img = _prep_image(img)
    except Exception as e:  # noqa: BLE001
        att.extraction_status = "failed"
        att.warnings.append(f"图片读取失败: {e}")
        return
    if not shutil_which("tesseract"):
        att.extraction_status = "skipped"
        att.warnings.append("tesseract 不可用，图片 OCR 跳过")
        return
    try:
        text = _tesseract_ocr(img)
        if text and text.strip():
            att.text = TextCleaner.clean(text)
            att.ocr_used = True
            att.extraction_status = "success"
        else:
            att.extraction_status = "partial"
            att.warnings.append("OCR 未返回文本（可能为纯图形/照片）")
    except Exception as e:  # noqa: BLE001
        att.extraction_status = "failed"
        att.warnings.append(f"OCR 异常: {e}")


def _prep_image(img):
    from PIL import Image
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    # 放大提高 OCR 小字识别率
    w, h = img.size
    if max(w, h) < 1600:
        scale = 1600 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img
