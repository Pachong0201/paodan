"""Sensitive Reader：原始邮件正文 / 来源 reveal / 附件提取文本。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..view_models import REVIEW_STATUS_LABELS
from ...workbench.reader_service import (
    get_attachment_text,
    get_reader_view,
    get_source,
)
from ..db import connect_dashboard

router = APIRouter()


def _valid_email_id(email_id: str) -> bool:
    return bool(email_id) and "/" not in email_id and "\\" not in email_id and email_id not in (".", "..")


@router.get("/emails/{email_id}/reader", response_class=HTMLResponse)
def reader_page(request: Request, email_id: str):
    if not _valid_email_id(email_id):
        raise HTTPException(status_code=404, detail="not found")
    with connect_dashboard(request.app.state.db_path) as conn:
        view = get_reader_view(conn, email_id)
    if view is None:
        raise HTTPException(status_code=404, detail="not found")
    return request.app.state.templates.TemplateResponse(
        request, "email_reader.html",
        {"request": request, "reader": view,
         "review_statuses": REVIEW_STATUS_LABELS,
         "csrf_token": request.app.state.csrf_token})


@router.get("/emails/{email_id}/reader/source")
def reader_source(request: Request, email_id: str):
    if not _valid_email_id(email_id):
        raise HTTPException(status_code=404, detail="not found")
    with connect_dashboard(request.app.state.db_path) as conn:
        source = get_source(conn, email_id)
    if source is None:
        raise HTTPException(status_code=404, detail="not found")
    resp = JSONResponse(source)
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@router.get("/emails/{email_id}/reader/attachments/{attachment_id}", response_class=HTMLResponse)
def reader_attachment(request: Request, email_id: str, attachment_id: int):
    if not _valid_email_id(email_id):
        raise HTTPException(status_code=404, detail="not found")
    with connect_dashboard(request.app.state.db_path) as conn:
        att = get_attachment_text(conn, email_id, attachment_id)
    if att is None:
        raise HTTPException(status_code=404, detail="not found")
    resp = request.app.state.templates.TemplateResponse(
        request, "attachment_text.html", {"request": request, "att": att})
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp
