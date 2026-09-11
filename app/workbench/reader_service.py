"""V5.0 Sensitive Reader：只读原始邮件正文、来源 reveal、附件提取文本。

Reader 与 Safe Dashboard 严格分离：Reader 允许展示 raw body，普通 ViewModel 不允许。

图片附件内联展示的安全前提：
- 只读取本地 `.attachments_cache` 中已落盘的附件字节，绝不发起任何网络请求；
- 只按 **magic bytes** 判定类型，且白名单只含位图格式，**明确排除 SVG**
  （SVG 可内嵌脚本，以内联方式同源返回等于 XSS）；
- 任何不匹配白名单的内容一律拒绝，不依赖扩展名或 DB 里记录的 file_type。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# 与 Dashboard 保持一致的排序（优先级 → 评分 → 处理时间）。
# 这里刻意本地定义，避免 workbench 层反向依赖 dashboard 层。
_PRIORITY_CASE = ("CASE sc.priority WHEN 'S' THEN 5 WHEN 'A' THEN 4 "
                  "WHEN 'B' THEN 3 WHEN 'C' THEN 2 ELSE 1 END")

# 允许内联展示的位图格式（magic bytes）。SVG 不在其中，且永远不会被匹配。
_IMAGE_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

# 内联展示的附件的扩展名白名单（仅用于模板判断是否有图可看，不用于放行字节）。
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}

# 单张图片内联展示上限，避免超大附件拖垮浏览器。
MAX_INLINE_IMAGE_BYTES = 50 * 1024 * 1024


def _sniff_image_media_type(head: bytes) -> Optional[str]:
    """按 magic bytes 判定图片类型；非白名单位图一律返回 None。"""
    for magic, media_type in _IMAGE_MAGIC:
        if head.startswith(magic):
            return media_type
    # WebP: RIFF....WEBP
    if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def _cached_image_path(cached_path: str) -> Optional[Path]:
    if not cached_path:
        return None
    p = Path(str(cached_path))
    try:
        if not p.is_file():
            return None
    except OSError:
        return None
    if p.suffix.lower() not in _IMAGE_EXTS:
        return None
    return p


def _probe_image_file(path: Path) -> Optional[str]:
    """校验本地文件确实是白名单内的位图，返回其 media type。

    只读取文件头 16 字节，因此模板渲染时可以逐附件安全调用。
    这样 `image_available` 与实际 raw 路由的判定完全一致，
    不会出现「页面渲染 <img> 但路由返回 404」的错位。
    """
    try:
        if path.stat().st_size > MAX_INLINE_IMAGE_BYTES:
            return None
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return None
    return _sniff_image_media_type(head)


def get_reader_view(conn: sqlite3.Connection, email_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT e.email_id, e.subject, e.body_text, e.processed_at,
                  s.priority, s.final_score, s.primary_track
           FROM emails e
           LEFT JOIN scores s ON s.email_id=e.email_id
           WHERE e.email_id=?""", (email_id,)).fetchone()
    if row is None:
        return None
    attachments = conn.execute(
        """SELECT id, filename, file_type, extraction_status, warnings,
                  cached_path, LENGTH(COALESCE(text,'')) AS text_len
           FROM attachments WHERE email_id=? ORDER BY id""", (email_id,)).fetchall()
    attachment_views = []
    for a in attachments:
        d = dict(a)
        # 只暴露布尔值：绝不在模板/JSON 中泄漏本地文件系统路径。
        path = _cached_image_path(d.pop("cached_path", "") or "")
        d["image_available"] = bool(path and _probe_image_file(path))
        attachment_views.append(d)

    prev_id, next_id = get_reader_neighbors(conn, email_id)
    return {
        "email_id": row["email_id"],
        "subject": row["subject"] or "",
        "body_text": row["body_text"] or "",
        "processed_at": row["processed_at"] or "",
        "priority": row["priority"] or "D",
        "final_score": float(row["final_score"] or 0.0),
        "primary_track": row["primary_track"] or "NONE",
        "attachments": attachment_views,
        "prev_email_id": prev_id,
        "next_email_id": next_id,
    }


def get_reader_neighbors(conn: sqlite3.Connection,
                         email_id: str) -> Tuple[Optional[str], Optional[str]]:
    """按「优先级 → 评分 → 处理时间」排序返回 (上一封, 下一封)。

    与 `/leads` 的默认排序一致，因此 Reader 的翻页顺序与线索列表一致。
    本地 Workbench 数据量有界，直接取有序 id 列表求相邻项即可，逻辑最不易出错。
    """
    rows = conn.execute(
        f"""SELECT e.email_id AS email_id
            FROM emails e
            LEFT JOIN scores sc ON sc.email_id = e.email_id
            ORDER BY {_PRIORITY_CASE} DESC, sc.final_score DESC, e.processed_at DESC"""
    ).fetchall()
    ids = [r["email_id"] for r in rows]
    try:
        i = ids.index(email_id)
    except ValueError:
        return None, None
    prev_id = ids[i - 1] if i > 0 else None
    next_id = ids[i + 1] if i + 1 < len(ids) else None
    return prev_id, next_id


def get_source(conn: sqlite3.Connection, email_id: str) -> Optional[Dict[str, str]]:
    row = conn.execute("SELECT sender FROM emails WHERE email_id=?", (email_id,)).fetchone()
    if row is None:
        return None
    return {"sender": str(row["sender"] or "")}


def get_attachment_text(conn: sqlite3.Connection, email_id: str,
                        attachment_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT id, filename, file_type, extraction_status, text, metadata, warnings
           FROM attachments WHERE id=? AND email_id=?""",
        (int(attachment_id), email_id)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"] or "",
        "file_type": row["file_type"] or "",
        "extraction_status": row["extraction_status"] or "",
        "text": row["text"] or "",
        "warnings": row["warnings"] or "[]",
    }


def get_attachment_image(conn: sqlite3.Connection, email_id: str,
                         attachment_id: int) -> Optional[Dict[str, Any]]:
    """读取本地图片附件字节用于内联展示。

    返回 None 表示「不可展示」：不存在、路径失效、超限，或 magic bytes 不属于
    白名单位图格式（例如伪装成 .png 的 HTML / SVG）。
    """
    row = conn.execute(
        """SELECT id, filename, cached_path FROM attachments
           WHERE id=? AND email_id=?""",
        (int(attachment_id), email_id)).fetchone()
    if row is None:
        return None
    path = _cached_image_path(row["cached_path"] or "")
    if path is None:
        return None
    media_type = _probe_image_file(path)
    if media_type is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return {
        "id": int(row["id"]),
        "filename": os.path.basename(str(row["filename"] or "")) or "attachment",
        "media_type": media_type,
        "data": data,
    }
