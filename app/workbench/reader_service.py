"""V5.0 Sensitive Reader：只读原始邮件正文、来源 reveal、附件提取文本。

Reader 与 Safe Dashboard 严格分离：Reader 允许展示 raw body，普通 ViewModel 不允许。
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional


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
        """SELECT id, filename, file_type, extraction_status, warnings, LENGTH(COALESCE(text,'')) AS text_len
           FROM attachments WHERE email_id=? ORDER BY id""", (email_id,)).fetchall()
    return {
        "email_id": row["email_id"],
        "subject": row["subject"] or "",
        "body_text": row["body_text"] or "",
        "processed_at": row["processed_at"] or "",
        "priority": row["priority"] or "D",
        "final_score": float(row["final_score"] or 0.0),
        "primary_track": row["primary_track"] or "NONE",
        "attachments": [dict(a) for a in attachments],
    }


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
