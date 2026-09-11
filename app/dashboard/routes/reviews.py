"""人工审核 Review API。"""
from __future__ import annotations

from typing import Optional

import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Form, HTTPException, Request

from ..db import connect_dashboard
from ..queries import get_review, save_review
from ..schemas import validate_review_input
from ..security_utils import _is_trusted_local_origin

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
    if origin:
        try:
            o = urlparse(origin)
            hostname = (o.hostname or "").lower()
        except Exception:
            raise HTTPException(status_code=403, detail="invalid origin")
        if not _is_trusted_local_origin(hostname):
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
