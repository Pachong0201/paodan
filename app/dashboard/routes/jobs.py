"""Workbench 任务中心：启动筛选、进度、取消。"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ...workbench.job_repository import WorkbenchRepository
from ...workbench.llm_profiles import get_profile, profile_status
from ...workbench.models import JobRuntimeConfig
from ...workbench.runtime_settings import get_selected_profile_id
from ..db import connect_dashboard
from ..security_utils import verify_csrf

router = APIRouter()


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request):
    with connect_dashboard(request.app.state.db_path) as conn:
        repo = WorkbenchRepository(conn)
        jobs = repo.list_jobs()
    return request.app.state.templates.TemplateResponse(
        request, "jobs.html", {"request": request, "jobs": jobs})


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(request: Request, job_id: str):
    with connect_dashboard(request.app.state.db_path) as conn:
        repo = WorkbenchRepository(conn)
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="not found")
        items = repo.list_job_items(job_id)
    return request.app.state.templates.TemplateResponse(
        request, "job_detail.html", {"request": request, "job": job, "items": items})


@router.get("/jobs/{job_id}/status")
def job_status(request: Request, job_id: str):
    with connect_dashboard(request.app.state.db_path) as conn:
        repo = WorkbenchRepository(conn)
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="not found")
        items = repo.list_job_items(job_id)
    out = dict(job)
    out["items"] = items
    return JSONResponse(out)


@router.post("/jobs/start")
def start_job(request: Request, csrf_token: Optional[str] = Form(None),
              import_id: str = Form(...), profile_id: Optional[str] = Form(None)):
    verify_csrf(request, csrf_token)
    with connect_dashboard(request.app.state.db_path) as conn:
        repo = WorkbenchRepository(conn)
        batch = repo.get_import_batch(import_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="import batch not found")
        if str(batch.get("status") or "") != "READY" or int(batch.get("accepted_files") or 0) <= 0:
            raise HTTPException(status_code=409, detail="import batch is not READY")
        profile = get_profile(profile_id or get_selected_profile_id(conn))
        status = profile_status(profile.id)
        if not status.get("available", False):
            raise HTTPException(status_code=400, detail=status.get("message") or "profile unavailable")
        runtime = JobRuntimeConfig(
            llm_enabled=(profile.type != "off"),
            llm_mode=("api" if profile.type == "api" else "template"),
            llm_profile_id=profile.id,
            llm_model=profile.resolved_model(),
            llm_base_url=profile.resolved_base_url(),
            release_advisor_enabled=True,
            named_advisor_enabled=True,
        )
        try:
            job_id = request.app.state.job_runner.start_job(import_id, runtime)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc))
    if "text/html" in (request.headers.get("accept") or ""):
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    return {"job_id": job_id}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: str, csrf_token: Optional[str] = Form(None)):
    verify_csrf(request, csrf_token)
    request.app.state.job_runner.request_cancel(job_id)
    if "text/html" in (request.headers.get("accept") or ""):
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)
    return {"job_id": job_id, "status": "CANCEL_REQUESTED"}
