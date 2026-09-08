"""邮件详情页。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..db import connect_dashboard
from ..queries import get_email_detail
from ..view_models import REVIEW_STATUS_LABELS, TRACK_LABELS

router = APIRouter()


@router.get("/emails/{email_id}", response_class=HTMLResponse)
def email_detail(request: Request, email_id: str):
    if not email_id or "/" in email_id or "\\" in email_id or email_id in (".", ".."):
        raise HTTPException(status_code=404, detail="not found")
    with connect_dashboard(request.app.state.db_path) as conn:
        detail = get_email_detail(conn, email_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="not found")
    return request.app.state.templates.TemplateResponse(
        request, "email_detail.html", {
            "request": request,
            "detail": detail,
            "review_statuses": REVIEW_STATUS_LABELS,
            "track_labels": TRACK_LABELS,
            "route_names": {
                "R1": "社交平台首发型", "R2": "媒体独家型", "R3": "深度调查报道型",
                "R4": "记者会/公开展示型", "R5": "正式检举优先型", "R6": "高敏感核验型",
            },
        })
