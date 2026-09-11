"""Sensitive Reader：原始邮件正文 / 来源 reveal / 附件提取文本。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ..view_models import REVIEW_STATUS_LABELS
from ...workbench.reader_service import (
    get_attachment_image,
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


@router.get("/emails/{email_id}/reader/attachments/{attachment_id}/raw")
def reader_attachment_raw(request: Request, email_id: str, attachment_id: int):
    """内联返回**本地**图片附件字节。

    安全性：类型由 magic bytes 白名单判定（位图格式，排除 SVG）；任何不匹配的
    内容一律 404，因此伪装成图片的 HTML/SVG 不会被同源内联返回。
    """
    if not _valid_email_id(email_id):
        raise HTTPException(status_code=404, detail="not found")
    with connect_dashboard(request.app.state.db_path) as conn:
        img = get_attachment_image(conn, email_id, attachment_id)
    if img is None:
        raise HTTPException(status_code=404, detail="not found")
    resp = Response(content=img["data"], media_type=img["media_type"])
    safe_name = img["filename"].replace('"', "").replace("\\", "").replace("/", "")
    resp.headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp
