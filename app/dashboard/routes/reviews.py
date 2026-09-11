"""人工审核 Review API。"""
from __future__ import annotations

import os
from typing import Optional

import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ...workbench.review_export import (
    NoReviewedEmailsError,
    create_review_export_zip,
    normalize_export_statuses,
)
from ..db import connect_dashboard
from ..queries import get_review, save_review
from ..schemas import validate_review_input
from ..security_utils import _is_allowed_origin, verify_csrf

router = APIRouter()


@router.post("/emails/{email_id}/review")
def review_post(request: Request, email_id: str,
                status: str = Form(...),
                editor_note: str = Form(""),
                csrf_token: Optional[str] = Form(None)):
    expected = str(getattr(request.app.state, "csrf_token", "") or "")
    if not csrf_token or not expected or not secrets.compare_digest(str(csrf_token), expected):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    # 第二层：若带 Origin，只允许 http://127.0.0.1:<port> / http://localhost:<port>
    origin = request.headers.get("origin") or ""
    if origin and not _is_allowed_origin(request, origin):
        raise HTTPException(status_code=403, detail="invalid origin")
    if not email_id or "/" in email_id or "\\" in email_id:
        raise HTTPException(status_code=404, detail="not found")
    try:
        data = validate_review_input(status, editor_note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with connect_dashboard(request.app.state.db_path) as conn:
        # 确认邮件存在，避免写入不存在 email_id。
        exists = conn.execute("SELECT 1 FROM emails WHERE email_id=?", (email_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="not found")
        review = save_review(conn, email_id, data["review_status"], data["editor_note"])
        conn.commit()
    return {
        "email_id": email_id,
        "review_status": review.review_status,
        "updated_at": review.updated_at,
    }


@router.get("/api/reviews/{email_id}")
def get_review_api(request: Request, email_id: str):
    with connect_dashboard(request.app.state.db_path) as conn:
        review = get_review(conn, email_id)
    return {
        "email_id": review.email_id,
        "review_status": review.review_status,
        "editor_note": review.editor_note,
        "updated_at": review.updated_at,
    }


@router.post("/review/export")
def export_reviewed(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    reviewed_status: str = Form("VERIFIED"),
):
    """把人工审核通过（VERIFIED / PRIORITY）的邮件打包为 ZIP 下载。"""
    verify_csrf(request, csrf_token)
    statuses = normalize_export_statuses([reviewed_status])
    if not statuses:
        raise HTTPException(status_code=400, detail="invalid review status for export")
    try:
        zip_path, count, filename = create_review_export_zip(
            request.app.state.db_path,
            request.app.state.import_staging_root,
            statuses,
        )
    except NoReviewedEmailsError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if count <= 0:
        try:
            os.unlink(zip_path)
        except OSError:
            pass
        raise HTTPException(status_code=404, detail="没有符合条件的人工审核通过邮件")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(lambda p=zip_path: os.path.exists(p) and os.unlink(p)),
    )
