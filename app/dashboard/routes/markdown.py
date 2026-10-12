"""Markdown 内部稿筛选页面（独立于邮件 Import Center / 结果库）。

上传 .md / .markdown / .zip(内含 md)，同步执行 ScreeningPipeline.process_markdown_file，
结果写入独立的 md 结果库（app.state.md_db_path），不进入邮件 Workbench 数据。
页面不展示稿件原文，只展示评分、类别、摘要与评分依据。
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from ...parsers.markdown_parser import DEFAULT_MAX_MARKDOWN_BYTES, MARKDOWN_SUFFIXES
from ...security.spreadsheet import spreadsheet_safe
from ..security_utils import verify_csrf

router = APIRouter()
UPLOAD_SUFFIXES = MARKDOWN_SUFFIXES | {".zip"}
MAX_MD_FILE_BYTES = DEFAULT_MAX_MARKDOWN_BYTES
MAX_MD_BATCH_BYTES = 200 * 1024 * 1024
MAX_ZIP_MEMBERS = 5000
READ_CHUNK_BYTES = 1024 * 1024
PRIORITY_FILTERS = {"": None, "SAB": ("S", "A", "B"), "S": ("S",), "A": ("A",),
                    "B": ("B",), "C": ("C",), "D": ("D",)}

_SCHEMA = """CREATE TABLE IF NOT EXISTS md_results (
    email_id TEXT PRIMARY KEY,
    file_name TEXT,
    subject TEXT,
    priority TEXT,
    final_score REAL,
    categories TEXT,
    patterns TEXT,
    summary TEXT,
    detail_json TEXT,
    batch_id TEXT,
    processed_at TEXT
)"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def _list_results(db_path: Path, priority: str = "") -> list[dict]:
    wanted = PRIORITY_FILTERS.get(priority)
    sql = "SELECT * FROM md_results"
    params: tuple = ()
    if wanted:
        sql += " WHERE priority IN (%s)" % ",".join("?" * len(wanted))
        params = tuple(wanted)
    sql += " ORDER BY final_score DESC, processed_at DESC"
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _safe_name(name: str) -> str:
    base = Path(name.replace("\\", "/")).name or "upload.md"
    return re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", base)[:120]


def _record_row(rec, file_name: str, batch_id: str) -> dict:
    rule, llm, score = rec.rule, rec.llm, rec.score
    cats = list((llm.categories if llm else []) or (rule.matched_categories if rule else []) or [])
    pats = [p.pattern_id for p in (rule.matched_patterns if rule else [])]
    negatives = [n.get("rule_id") if isinstance(n, dict) else getattr(n, "rule_id", "")
                 for n in (rule.negative_matches if rule else [])]
    detail = {
        "dimension_details": score.dimension_details,
        "dimension_scores": score.dimension_scores,
        "stage_adjustment": score.stage_adjustment,
        "negative_adjustment": score.negative_adjustment,
        "evidence_stage": score.evidence_stage,
        "primary_track": score.primary_track,
        "rule_score": rule.rule_score if rule else 0.0,
        "negative_rules": [n for n in negatives if n],
        "target_persons": list((llm.target_persons if llm else []) or
                               (rule.target_persons_found if rule else []) or []),
        "target_orgs": list((llm.target_organizations if llm else []) or
                            (rule.target_orgs_found if rule else []) or []),
        "money_count": len(rule.money) if rule else 0,
        "verification_targets": [str(v) for v in (rec.verification_targets or [])][:10],
        "date": rec.email.date if rec.email else "",
    }
    return {
        "email_id": rec.email_id or (rec.email.email_id if rec.email else ""),
        "file_name": file_name,
        "subject": rec.email.subject if rec.email else file_name,
        "priority": score.priority,
        "final_score": float(score.final_score),
        "categories": "、".join(cats),
        "patterns": "/".join(pats),
        "summary": ((llm.one_sentence_summary if llm else "") or rec.summary_zh or "")[:500],
        "detail_json": json.dumps(detail, ensure_ascii=False, default=str),
        "batch_id": batch_id,
        "processed_at": datetime.now().isoformat(timespec="seconds"),
    }


def _run_batch(md_db_path: Path, files: list[tuple[str, Path]], batch_id: str,
               split_sections: bool, use_llm: bool, reprocess: bool) -> dict:
    from ...main import _load_config
    from ...runtime.pipeline_factory import build_cli_pipeline
    from ...storage.database import Database

    summary = {"batch_id": batch_id, "files": len(files), "processed": 0,
               "skipped": 0, "errors": [], "rows": []}
    db = Database(md_db_path)
    try:
        args = SimpleNamespace(llm_mode=None, no_llm=not use_llm, llm_trigger_score=None,
                               enable_release_advisor=False, enable_named_advisor=False,
                               reprocess=reprocess, rescore=False, reanalyze=False)
        pipeline = build_cli_pipeline(_load_config(), db, args)
        for file_name, path in files:
            recs = pipeline.process_markdown_file(path, reprocess=reprocess,
                                                  split_sections=split_sections)
            if not recs:
                summary["skipped"] += 1
                continue
            for rec in recs:
                if rec.score is None:
                    summary["errors"].append({"file_name": file_name,
                                              "reason": (rec.error or "未知错误")[:200]})
                    continue
                summary["rows"].append(_record_row(rec, file_name, batch_id))
            summary["processed"] += 1
    finally:
        db.close()
    if summary["rows"]:
        cols = list(summary["rows"][0].keys())
        with _connect(md_db_path) as conn:
            conn.executemany(
                f"INSERT OR REPLACE INTO md_results ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [tuple(r[c] for c in cols) for r in summary["rows"]])
    summary["rows"].sort(key=lambda r: r["final_score"], reverse=True)
    return summary


def _render(request: Request, result=None, error: str = "", priority: str = "", status_code: int = 200):
    md_db = Path(request.app.state.md_db_path)
    return request.app.state.templates.TemplateResponse(
        request, "markdown_center.html",
        {"request": request, "result": result, "error": error, "priority": priority,
         "rows": _list_results(md_db, priority), "csrf_token": request.app.state.csrf_token},
        status_code=status_code)


@router.get("/markdown", response_class=HTMLResponse)
def markdown_page(request: Request, priority: str = ""):
    if priority not in PRIORITY_FILTERS:
        priority = ""
    return _render(request, priority=priority)


@router.post("/markdown", response_class=HTMLResponse)
async def markdown_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    csrf_token: str = Form(""),
    split_sections: str = Form(""),
    use_llm: str = Form(""),
    reprocess: str = Form(""),
):
    verify_csrf(request, csrf_token)
    uploads = [uf for uf in (files or []) if uf is not None and uf.filename]
    if not uploads:
        raise HTTPException(status_code=400, detail="no file uploaded")
    md_db = Path(request.app.state.md_db_path)
    batch_id = datetime.now().strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    batch_dir = md_db.parent / "uploads" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    stored: list[tuple[str, Path]] = []
    rejected: list[dict] = []
    total = 0

    def _store(name: str, data: bytes) -> None:
        target = batch_dir / f"{len(stored) + 1:04d}_{_safe_name(name)}"
        if target.suffix.lower() not in MARKDOWN_SUFFIXES:
            target = target.with_suffix(".md")
        target.write_bytes(data)
        stored.append((name, target))

    for uf in uploads:
        filename = Path((uf.filename or "").replace("\\", "/")).name
        suffix = Path(filename).suffix.lower()
        if suffix not in UPLOAD_SUFFIXES:
            raise HTTPException(status_code=400, detail="only .md / .markdown / .zip upload is supported")
        buf = io.BytesIO()
        while True:
            chunk = await uf.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_MD_BATCH_BYTES:
                raise HTTPException(status_code=413, detail="BATCH_TOO_LARGE")
            if suffix != ".zip" and buf.tell() + len(chunk) > MAX_MD_FILE_BYTES:
                rejected.append({"file_name": filename, "reason": "超过单文件 5MB 上限"})
                buf = None
                break
            buf.write(chunk)
        if buf is None:
            continue
        if suffix != ".zip":
            _store(filename, buf.getvalue())
            continue
        try:
            zf = zipfile.ZipFile(buf)
        except zipfile.BadZipFile:
            rejected.append({"file_name": filename, "reason": "ZIP 文件损坏"})
            continue
        unzipped = 0
        with zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
            if len(members) > MAX_ZIP_MEMBERS:
                raise HTTPException(status_code=413, detail="ZIP_TOO_MANY_FILES")
            for info in members:
                name = info.filename.replace("\\", "/")
                base = Path(name).name
                if "__MACOSX/" in name or base.startswith("._"):
                    continue
                if Path(base).suffix.lower() not in MARKDOWN_SUFFIXES:
                    continue
                if info.file_size > MAX_MD_FILE_BYTES:
                    rejected.append({"file_name": f"{filename}/{name}", "reason": "超过单文件 5MB 上限"})
                    continue
                with zf.open(info) as fh:
                    data = fh.read(MAX_MD_FILE_BYTES + 1)
                if len(data) > MAX_MD_FILE_BYTES:
                    rejected.append({"file_name": f"{filename}/{name}", "reason": "超过单文件 5MB 上限"})
                    continue
                unzipped += len(data)
                if unzipped > MAX_MD_BATCH_BYTES:
                    raise HTTPException(status_code=413, detail="ZIP_UNCOMPRESSED_TOO_LARGE")
                _store(f"{filename}/{name}", data)

    if not stored:
        return _render(request, error="没有可处理的 Markdown 文件（.md / .markdown）", status_code=400)
    result = await run_in_threadpool(_run_batch, md_db, stored, batch_id,
                                     bool(split_sections), bool(use_llm), bool(reprocess))
    result["errors"] = rejected + result["errors"]
    return _render(request, result=result)


@router.get("/markdown/detail", response_class=HTMLResponse)
def markdown_detail(request: Request, id: str = ""):
    with _connect(Path(request.app.state.md_db_path)) as conn:
        row = conn.execute("SELECT * FROM md_results WHERE email_id=?", (id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    row = dict(row)
    detail = json.loads(row.get("detail_json") or "{}")
    return request.app.state.templates.TemplateResponse(
        request, "markdown_detail.html", {"request": request, "row": row, "detail": detail})


@router.get("/markdown/export.csv")
def markdown_export(request: Request, priority: str = ""):
    rows = _list_results(Path(request.app.state.md_db_path),
                         priority if priority in PRIORITY_FILTERS else "")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["等级", "分数", "文件", "标题", "类别", "Pattern", "摘要", "处理时间"])
    for r in rows:
        w.writerow([spreadsheet_safe(str(v if v is not None else "")) for v in (
            r["priority"], r["final_score"], r["file_name"], r["subject"], r["categories"],
            r["patterns"], r["summary"], r["processed_at"])])
    return Response(content="\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="md_priority_queue.csv"'})
