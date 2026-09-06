"""EML 邮件解析：header、plain/HTML 正文、附件提取（不解码附件内容，交由各附件 parser）。"""
from __future__ import annotations

import base64
import email
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
    """解析 .eml 文件到 EmailDocument（附件元数据+转储缓存，正文文本化）。"""
    path = Path(path)
    doc = EmailDocument(source_path=str(path))
    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email_policy)

    doc.message_id = _decode_header_value(msg.get("Message-ID", "")).strip("<>")
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
        disp = str(part.get("Content-Disposition", "")).lower()
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

    # 附件落盘缓存（不解析内容，由 AttachmentParser 按类型处理）
    cache_dir = path.parent / ".attachments_cache"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        cache_dir = path.parent
    for fname, payload, ctype in attach_specs:
        safe = re.sub(r"[^\w.\-]+", "_", fname) or f"attach_{uuid.uuid4().hex[:8]}"
        att_path = cache_dir / safe
        try:
            if not att_path.exists():
                att_path.write_bytes(payload)
        except OSError as e:
            logger.warning("附件写入失败 %s: %s", safe, e)
            continue
        att = AttachmentDoc(filename=fname, file_type=_ext_type(fname, ctype),
                            metadata={"size": len(payload), "content_type": ctype,
                                      "cached_path": str(att_path)})
        doc.attachments.append(att)

    doc.body_hash = _hash(body_text)
    doc.email_id = doc.message_id or doc.body_hash[:16]
    return doc


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
