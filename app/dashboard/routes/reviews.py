"""人工审核 Review API。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request

from ..db import connect_dashboard
from ..queries import get_review, save_review
from ..schemas import validate_review_input

router = APIRouter()


@router.post("/emails/{email_id}/review")
def review_post(request: Request, email_id: str,
                status: str = Form(...),
                editor_note: str = Form(""),
                csrf_token: Optional[str] = Form(None)):
    expected = getattr(request.app.state, "csrf_token", "")
    if expected and csrf_token != expected:
        # 本地 MVP：允许无 Origin 的测试/本机表单；若带 Origin 则必须是 localhost。
        origin = request.headers.get("origin") or ""
        if origin and not any(h in origin for h in ("127.0.0.1", "localhost")):
            raise HTTPException(status_code=400, detail="invalid csrf token")
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
