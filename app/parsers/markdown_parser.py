"""独立 Markdown 文稿解析：.md -> EmailDocument（无附件、无邮件头）。

用于报社内部未发布稿件等纯文本来源；不改变 EML 解析逻辑。
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from ..models import EmailDocument
from ..preprocessing.normalization import TextCleaner

logger = logging.getLogger(__name__)

SOURCE_KIND_INTERNAL_DRAFT = "internal_draft"
MARKDOWN_SUFFIXES = {".md", ".markdown"}
DEFAULT_MAX_MARKDOWN_BYTES = 5 * 1024 * 1024
_ENCODINGS = ("utf-8-sig", "gb18030", "big5")

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)\n(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.DOTALL)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
_LIST_RE = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?")
_QUOTE_RE = re.compile(r"^\s{0,3}(>\s?)+")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_REF_DEF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s+\S+.*$")
_AUTOLINK_RE = re.compile(r"<((?:https?|mailto):[^>\s]+)>")
_FOOTNOTE_RE = re.compile(r"\[\^[^\]]+\]")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`+([^`]+)`+")
_EMPHASIS_RES = (
    re.compile(r"\*\*\*(.+?)\*\*\*"), re.compile(r"\*\*(.+?)\*\*"), re.compile(r"__(.+?)__"),
    re.compile(r"~~(.+?)~~"), re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"),
)
_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")


def decode_markdown_bytes(raw: bytes) -> tuple[str, str]:
    """返回 (text, encoding)；依次尝试 UTF-8/GB18030/Big5，最后 UTF-8 替换解码。"""
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def split_front_matter(text: str) -> tuple[dict, str]:
    """剥离 YAML front-matter，避免元数据词误触发规则。"""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    try:
        import yaml
        loaded = yaml.safe_load(m.group(1))
        if isinstance(loaded, dict):
            meta = loaded
    except Exception as exc:  # noqa: BLE001
        logger.warning("Markdown front-matter 解析失败，按无元数据处理: %s", exc)
    return meta, text[m.end():]


def _inline(line: str) -> str:
    line = _IMAGE_RE.sub(r"\1", line)
    line = _LINK_RE.sub(r"\1", line)
    line = _REF_LINK_RE.sub(r"\1", line)
    line = _AUTOLINK_RE.sub(r"\1", line)
    line = _FOOTNOTE_RE.sub("", line)
    line = _INLINE_CODE_RE.sub(r"\1", line)
    for rx in _EMPHASIS_RES:
        line = rx.sub(r"\1", line)
    line = _HTML_TAG_RE.sub("", line)
    return _ESCAPE_RE.sub(r"\1", line)


def markdown_to_text(md: str) -> str:
    """Markdown -> 纯文本：去标记、保留标题/列表/表格/代码块中的文字。"""
    md = md.replace("\r\n", "\n").replace("\r", "\n")
    md = _HTML_COMMENT_RE.sub("", md)
    md = _BR_RE.sub("\n", md)
    out: list[str] = []
    in_fence = False
    for line in md.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            out.append(line)
            continue
        if _REF_DEF_RE.match(line):
            continue
        if _SETEXT_RE.match(line) and out and out[-1].strip():
            continue
        if _HR_RE.match(line) or (_TABLE_SEP_RE.match(line) and "|" in line):
            continue
        h = _HEADING_RE.match(line)
        if h:
            out.append(_inline(h.group(2)))
            continue
        line = _QUOTE_RE.sub("", line)
        line = _LIST_RE.sub(r"\1", line)
        stripped = line.strip()
        if stripped.startswith("|") or (stripped.endswith("|") and stripped.count("|") >= 2):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            line = " | ".join(_inline(c) for c in cells if c)
        else:
            line = _inline(line)
        out.append(line)
    return TextCleaner.clean("\n".join(out))


def _first_heading(md: str, level: int = 1) -> str:
    in_fence = False
    for line in md.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        h = None if in_fence else _HEADING_RE.match(line)
        if h and len(h.group(1)) == level:
            return _inline(h.group(2)).strip()
    return ""


def _split_sections(md: str) -> list[tuple[str, str]]:
    """按二级标题切段；返回 [(heading, markdown)]，标题前的导语为 ("", ...)。"""
    sections: list[tuple[str, str]] = []
    heading, buf, in_fence = "", [], False
    for line in md.split("\n"):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        h = None if in_fence else _HEADING_RE.match(line)
        if h and len(h.group(1)) == 2:
            sections.append((heading, "\n".join(buf)))
            heading, buf = _inline(h.group(2)).strip(), []
            continue
        buf.append(line)
    sections.append((heading, "\n".join(buf)))
    return [(hd, body) for hd, body in sections if markdown_to_text(body)]


def _meta_str(meta: dict, *keys: str) -> str:
    for k in keys:
        v = meta.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _build_doc(path: Path, email_id: str, raw_sha256: str, subject: str,
               date: str, body: str) -> EmailDocument:
    return EmailDocument(
        email_id=email_id,
        subject=subject,
        date=date,
        body_text=body,
        body_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        raw_sha256=raw_sha256,
        attachments=[],
        source_path=str(path),
        source_kind=SOURCE_KIND_INTERNAL_DRAFT,
    )


def parse_markdown_documents(path: str | Path, split_sections: bool = False,
                             max_bytes: int | None = DEFAULT_MAX_MARKDOWN_BYTES) -> list[EmailDocument]:
    """解析一个 .md 文件；split_sections=True 时按 ``##`` 切段，每段一个文档。

    身份：整篇 ``MD:<文件 bytes SHA256>``；分段 ``MD:<文件SHA256>#S<n>``。
    """
    path = Path(path)
    raw = path.read_bytes()
    if max_bytes and len(raw) > max_bytes:
        raise ValueError(f"Markdown 文件超过大小上限 {max_bytes} bytes: {len(raw)}")
    file_sha = hashlib.sha256(raw).hexdigest()
    text, encoding = decode_markdown_bytes(raw)
    if encoding == "utf-8-replace":
        logger.warning("Markdown 编码无法识别，已按 UTF-8 替换解码: %s", path)
    meta, md = split_front_matter(text.replace("\r\n", "\n").replace("\r", "\n"))
    title = _meta_str(meta, "title", "标题", "標題") or _first_heading(md) or path.stem
    date = _meta_str(meta, "date", "日期", "time", "时间")
    body = markdown_to_text(md)
    if title and title not in body[:len(title) + 200]:
        body = TextCleaner.clean(title + "\n\n" + body)

    sections = _split_sections(md) if split_sections else []
    if len(sections) <= 1:
        return [_build_doc(path, f"MD:{file_sha}", file_sha, title, date, body)]

    docs: list[EmailDocument] = []
    for idx, (heading, sec_md) in enumerate(sections, 1):
        sec_body = TextCleaner.clean(title + "\n\n" + (heading + "\n" if heading else "")
                                     + markdown_to_text(sec_md))
        sec_sha = hashlib.sha256(f"{file_sha}#S{idx}\n{sec_body}".encode()).hexdigest()
        subject = f"{title} / {heading}" if heading else f"{title} / 导语"
        docs.append(_build_doc(path, f"MD:{file_sha}#S{idx}", sec_sha, subject, date, sec_body))
    return docs


def parse_markdown(path: str | Path, max_bytes: int | None = DEFAULT_MAX_MARKDOWN_BYTES) -> EmailDocument:
    return parse_markdown_documents(path, split_sections=False, max_bytes=max_bytes)[0]
