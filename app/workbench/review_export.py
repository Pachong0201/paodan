"""人工审核通过邮件打包导出：ZIP 内包含原始 EML/正文回退 + manifest/CSV。

安全约束：
- 不把本机绝对路径写入 manifest/CSV/ZIP 条目名；
- ZIP 条目名使用安全 basename，自动去重；
- 只导出指定人工审核状态（默认 VERIFIED）。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


EXPORT_STATUS_LABELS = {
    "VERIFIED": "已核实",
    "PRIORITY": "重点跟进",
}
ALLOWED_EXPORT_STATUSES = tuple(EXPORT_STATUS_LABELS.keys())


class NoReviewedEmailsError(RuntimeError):
    pass


def normalize_export_statuses(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in values:
        for part in str(raw or "").split(","):
            value = part.strip().upper()
            if value in ALLOWED_EXPORT_STATUSES and value not in out:
                out.append(value)
    return out


def _safe_filename(name: str, fallback: str = "mail", max_len: int = 80) -> str:
    name = str(name or "").strip() or fallback
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name, flags=re.UNICODE).strip("._")
    return (name or fallback)[:max_len]


def _unique_arcname(base_name: str, used: set[str]) -> str:
    name = base_name
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix
    i = 1
    while name.lower() in used:
        name = f"{stem}_{i}{suffix}"
        i += 1
    used.add(name.lower())
    return name


def _query_export_rows(conn: sqlite3.Connection, statuses: Sequence[str]) -> List[dict]:
    placeholders = ",".join("?" for _ in statuses)
    sql = f"""
        SELECT e.email_id, e.subject, e.body_text, e.source_path, e.raw_sha256,
               s.priority, s.final_score, s.primary_track,
               dr.review_status, dr.editor_note, dr.reviewed_at, dr.updated_at
        FROM dashboard_reviews dr
        JOIN emails e ON e.email_id=dr.email_id
        LEFT JOIN scores s ON s.email_id=e.email_id
        WHERE dr.review_status IN ({placeholders})
        ORDER BY CASE COALESCE(s.priority,'D')
                   WHEN 'S' THEN 5 WHEN 'A' THEN 4 WHEN 'B' THEN 3 WHEN 'C' THEN 2 ELSE 1 END DESC,
                 COALESCE(s.final_score,0) DESC, e.processed_at DESC
    """
    return [dict(r) for r in conn.execute(sql, tuple(statuses)).fetchall()]


def count_exportable_emails(conn: sqlite3.Connection, statuses: Iterable[str]) -> int:
    normalized = normalize_export_statuses(statuses)
    if not normalized:
        return 0
    placeholders = ",".join("?" for _ in normalized)
    row = conn.execute(
        f"""SELECT COUNT(*) FROM dashboard_reviews dr
            JOIN emails e ON e.email_id=dr.email_id
            WHERE dr.review_status IN ({placeholders})""",
        tuple(normalized)).fetchone()
    return int(row[0] or 0) if row else 0


def _resolve_source_eml(row: dict, staging_root: Path, db_path: str | Path) -> Path | None:
    source = str(row.get("source_path") or "").strip()
    if source:
        p = Path(source)
        if p.exists() and p.is_file():
            return p
    email_id = str(row.get("email_id") or "")
    if not email_id:
        return None
    try:
        row2 = None
        with sqlite3.connect(str(db_path), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            row2 = conn.execute(
                """SELECT import_id, stored_filename, source_sha256
                   FROM import_files WHERE email_id=? LIMIT 1""", (email_id,)).fetchone()
    except sqlite3.Error:
        row2 = None
    if row2 is None:
        return None
    p = (staging_root / str(row2["import_id"]) / "files"
         / str(row2["source_sha256"] or "") / Path(str(row2["stored_filename"] or "")).name)
    return p if p.exists() and p.is_file() else None


def create_review_export_zip(db_path: str | Path,
                             staging_root: str | Path,
                             statuses: Iterable[str]) -> Tuple[str, int, str]:
    """生成审核通过邮件 ZIP，返回 (zip_path, item_count, download_filename)。"""
    normalized = normalize_export_statuses(statuses)
    if not normalized:
        raise ValueError("no valid review status")
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = _query_export_rows(conn, normalized)
    finally:
        conn.close()
    if not rows:
        raise NoReviewedEmailsError("没有符合条件的人工审核通过邮件")

    staging_root = Path(staging_root)
    fd, zip_path = tempfile.mkstemp(prefix="paodan_review_export_", suffix=".zip")
    os.close(fd)
    used_names: set[str] = set()
    manifest_items: List[dict] = []
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    writer.writerow(["email_id", "subject", "priority", "final_score", "primary_track",
                     "review_status", "reviewed_at", "editor_note", "archive_name"])

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, row in enumerate(rows, start=1):
                email_id = str(row.get("email_id") or "")
                subject = str(row.get("subject") or "")
                source_eml = _resolve_source_eml(row, staging_root, db_path)
                base_subject = _safe_filename(subject or f"email_{email_id}", fallback=f"email_{idx}")
                if source_eml is not None:
                    arc = _unique_arcname(f"{idx:04d}_{base_subject}.eml", used_names)
                    zf.write(source_eml, arc)
                    original_available = True
                else:
                    arc = _unique_arcname(f"{idx:04d}_{base_subject}.txt", used_names)
                    fallback_text = (
                        f"Email-ID: {email_id}\n"
                        f"Subject: {subject}\n\n"
                        "原始 EML 文件在本机已不可用；以下为数据库保存的邮件正文。\n\n"
                        f"{row.get('body_text') or ''}\n"
                    )
                    zf.writestr(arc, fallback_text.encode("utf-8", errors="replace"))
                    original_available = False
                manifest_items.append({
                    "email_id": email_id,
                    "subject": subject,
                    "priority": str(row.get("priority") or "D"),
                    "final_score": float(row.get("final_score") or 0.0),
                    "primary_track": str(row.get("primary_track") or "NONE"),
                    "review_status": str(row.get("review_status") or ""),
                    "reviewed_at": str(row.get("reviewed_at") or ""),
                    "editor_note": str(row.get("editor_note") or ""),
                    "archive_name": arc,
                    "original_eml_available": original_available,
                    "raw_sha256": str(row.get("raw_sha256") or ""),
                })
                writer.writerow([email_id, subject, row.get("priority") or "D",
                                 row.get("final_score") or 0.0, row.get("primary_track") or "NONE",
                                 row.get("review_status") or "", row.get("reviewed_at") or "",
                                 row.get("editor_note") or "", arc])

            manifest = {
                "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "statuses": normalized,
                "status_labels": [EXPORT_STATUS_LABELS[s] for s in normalized],
                "item_count": len(manifest_items),
                "items": manifest_items,
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            zf.writestr("reviewed_emails.csv", csv_buf.getvalue().encode("utf-8-sig"))
            zf.writestr(
                "README.txt",
                ("Paodan 人工审核通过邮件导出包\n"
                 "===============================\n\n"
                 "打包状态：" + "、".join(EXPORT_STATUS_LABELS[s] for s in normalized) + "\n"
                 f"邮件数量：{len(manifest_items)}\n\n"
                 "目录说明：\n"
                 "- manifest.json：审核与评分元数据\n"
                 "- reviewed_emails.csv：Excel 可读清单\n"
                 "- *.eml：原始邮件（含附件）；原始文件不可用时为 *.txt 正文回退\n").encode("utf-8"))
    except Exception:
        try:
            os.unlink(zip_path)
        except OSError:
            pass
        raise
    filename = f"paodan_review_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return zip_path, len(manifest_items), filename
