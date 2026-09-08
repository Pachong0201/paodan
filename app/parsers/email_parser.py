"""EML 邮件解析：header、plain/HTML 正文、附件提取（不解码附件内容，交由各附件 parser）。"""
from __future__ import annotations

import base64
import email
import hashlib
import logging
import re
import uuid
from email.header import decode_header
from email.policy import default as email_policy
from html.parser import HTMLParser
from pathlib import Path
from typing import List, Optional

from ..models import AttachmentDoc, EmailDocument
from ..preprocessing.normalization import TextCleaner

logger = logging.getLogger(__name__)


class _HTMLTextExtractor(HTMLParser):
    """把 HTML 转为近似可读文本：保留段落/换行，去脚本样式."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "blockquote", "table"):
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip > 0:
            self._skip -= 1
        if tag in ("p", "div", "tr", "li", "h1", "h2", "h3", "h4", "blockquote", "table"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip == 0:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.split("\n")]
        out = []
        for ln in lines:
            if ln:
                out.append(ln)
        return "\n".join(out)


def _decode_header_value(value) -> str:
    if not value:
        return ""
    out = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out).strip()


def _addr_list(value) -> List[str]:
    if not value:
        return []
    out = []
    for name, addr in email.utils.getaddresses([value]):
        if addr:
            out.append(f"{name} <{addr}>" if name else addr)
    return out


def parse_eml(path: str | Path) -> EmailDocument:
    """解析 .eml 文件到 EmailDocument（附件元数据+内容隔离缓存，正文文本化）。"""
    path = Path(path)
    raw_bytes = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    doc = EmailDocument(source_path=str(path), raw_sha256=raw_sha256)
    msg = email.message_from_bytes(raw_bytes, policy=email_policy)

    doc.message_id = _decode_header_value(msg.get("Message-ID", "")).strip("<>")
    doc.normalized_message_id = _normalize_message_id(doc.message_id)
    doc.subject = _decode_header_value(msg.get("Subject"))
    doc.sender = _decode_header_value(msg.get("From"))
    doc.recipients = _addr_list(msg.get("To"))
    doc.cc = _addr_list(msg.get("Cc"))
    doc.date = _decode_header_value(msg.get("Date"))

    plain_parts = []
    html_parts = []
    attach_specs = []   # [(filename, payload_bytes, content_type)]

    for part in msg.walk():
        ctype = part.get_content_type()
        fname_raw = part.get_filename()
        if fname_raw:
            fname = _decode_header_value(fname_raw)
            payload = part.get_payload(decode=True)
            if payload is not None:
                attach_specs.append((fname, payload, ctype))
            continue
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = None
        if ctype == "text/plain":
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            plain_parts.append(text)
        elif ctype == "text/html":
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            html_parts.append(text)

    body_text = TextCleaner.clean("\n\n".join(plain_parts))
    html_text = TextCleaner.clean("\n\n".join(_HTMLTextExtractor2(p) for p in html_parts))
    if not body_text and html_text:
        body_text = html_text
    doc.body_text = body_text
    doc.html_body = "\n\n".join(html_parts)

    # 附件落盘：按 email_source_hash / attachment_source_sha256 强隔离，同名不同内容绝不串件。
    cache_root = path.parent / ".attachments_cache"
    email_cache_dir = cache_root / raw_sha256
    try:
        email_cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        email_cache_dir = path.parent
    for fname, payload, ctype in attach_specs:
        source_sha256 = hashlib.sha256(payload).hexdigest()
        safe = _safe_filename(fname) or f"attach_{uuid.uuid4().hex[:8]}"
        att_path = email_cache_dir / f"{source_sha256}_{safe}"
        try:
            if not att_path.exists():
                att_path.write_bytes(payload)
        except OSError as e:
            logger.warning("附件写入失败 %s: %s", safe, e)
            continue
        att = AttachmentDoc(filename=fname, file_type=_ext_type(fname, ctype),
                            sha256=source_sha256, source_sha256=source_sha256,
                            metadata={"size": len(payload), "content_type": ctype,
                                      "cached_path": str(att_path),
                                      "email_source_hash": raw_sha256})
        doc.attachments.append(att)

    doc.body_hash = _hash(body_text)
    if doc.normalized_message_id:
        doc.email_id = "MID:" + hashlib.sha256(doc.normalized_message_id.encode("utf-8")).hexdigest()
    else:
        # 无 Message-ID：必须使用原始 EML bytes，禁止用正文哈希截断造成同正文不同发件人冲突。
        doc.email_id = "RAW:" + raw_sha256
    return doc


def _normalize_message_id(message_id: str) -> str:
    mid = _decode_header_value(message_id).strip()
    mid = mid.strip("<>").strip()
    return mid.lower()


def _safe_filename(name: str, max_len: int = 120) -> str:
    """跨 Windows/WSL/Linux 的附件安全文件名，保留扩展名并避免路径穿越。"""
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)
    name = name.strip("._") or ""
    if not name:
        return ""
    # Windows 保留名
    stem = Path(name).stem.upper()
    if stem in {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
                "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
                "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}:
        name = "_" + name
    if len(name) > max_len:
        ext = Path(name).suffix
        name = name[:max(1, max_len - len(ext) - 1)] + ext
    return name


def _HTMLTextExtractor2(html: str) -> str:
    p = _HTMLTextExtractor()
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001
        return ""
    return p.text()


def _ext_type(fname: str, ctype: str) -> str:
    ext = Path(fname).suffix.lower().lstrip(".")
    mapping = {
        "pdf": "pdf", "docx": "docx", "doc": "doc", "xlsx": "xlsx", "xls": "xls",
        "csv": "csv", "txt": "txt", "md": "txt", "jpg": "jpg", "jpeg": "jpg",
        "png": "png", "eml": "eml", "msg": "msg",
    }
    t = mapping.get(ext, "")
    if t:
        return t
    if ctype.startswith("image/"):
        return "image"
    if "pdf" in ctype:
        return "pdf"
    return "unknown"


def _hash(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()
