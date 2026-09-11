"""首页和 Review Queue 页面路由。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ..config import load_dashboard_config
from ..db import connect_dashboard
from ..queries import get_dashboard_stats, list_emails, list_review_queue
from ..schemas import normalize_search
from ..view_models import PRIORITY_RANK, REVIEW_STATUS_LABELS, TRACK_LABELS
from ...workbench.job_repository import WorkbenchRepository
from ...workbench.llm_profiles import load_llm_profiles, profile_status
from ...workbench.review_export import count_exportable_emails
from ...workbench.runtime_settings import get_selected_profile_id

router = APIRouter()


def _parse_priorities(values: List[str]) -> List[str]:
    out: List[str] = []
    for raw in values:
        for p in str(raw or "").split(","):
            p = p.strip().upper()
            if p in ("S", "A", "B", "C", "D") and p not in out:
                out.append(p)
    return out


def _page_context(request: Request, filters: Dict[str, Any], page: int,
                  page_size: int, days: int, total: int, items: list,
                  page_title: str = "首页") -> Dict[str, Any]:
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    return {
        "request": request,
        "filters": filters,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "items": items,
        "review_statuses": REVIEW_STATUS_LABELS,
        "track_labels": TRACK_LABELS,
        "page_title": page_title,
    }


def _common_params(request: Request, days_default: int, timezone_name: str = "Asia/Shanghai") -> Dict[str, Any]:
    qp = request.query_params
    days_raw = qp.get("days")
    if days_raw in ("today",):
        days = 1
    elif days_raw in ("all",):
        days = None
    elif days_raw is not None:
        try:
            days = max(1, int(days_raw))
        except ValueError:
            days = days_default
    else:
        days = days_default

    date_from = None
    date_to = None
    date_from_utc = None
    date_to_utc = None
    if days is not None:
        tz = ZoneInfo(timezone_name)
        local_today = datetime.now(tz).date()
        date_to = local_today.isoformat()
        date_from = (local_today - timedelta(days=days - 1)).isoformat()
        start_local = datetime.combine(local_today - timedelta(days=days - 1), time.min, tzinfo=tz)
        end_local = datetime.combine(local_today + timedelta(days=1), time.min, tzinfo=tz)
        date_from_utc = start_local.astimezone(timezone.utc).isoformat(timespec="seconds")
        date_to_utc = end_local.astimezone(timezone.utc).isoformat(timespec="seconds")
    return {
        "priorities": _parse_priorities(qp.getlist("priority") or ["S", "A", "B"]),
        "track": str(qp.get("track") or "").upper(),
        "category": str(qp.get("category") or "").strip().upper(),
        "review_status": str(qp.get("review_status") or "").upper(),
        "date_from": date_from,
        "date_to": date_to,
        "date_from_utc": date_from_utc,
        "date_to_utc": date_to_utc,
        "q": normalize_search(qp.get("q") or ""),
        "days": "all" if days is None else str(days),
    }


@router.get("/", response_class=HTMLResponse)
def homepage(request: Request, page: int = Query(1, ge=1),
             page_size: int = Query(0)):
    cfg = load_dashboard_config()
    page_size = page_size or cfg.page_size
    page_size = max(1, min(page_size, cfg.max_page_size))
    filters = _common_params(request, cfg.default_days, cfg.timezone)
    with connect_dashboard(request.app.state.db_path) as conn:
        stats = get_dashboard_stats(conn, default_days=cfg.default_days,
                                    today=datetime.now(ZoneInfo(cfg.timezone)).date().isoformat(),
                                    timezone_name=cfg.timezone)
        items, total = list_emails(conn, filters=filters, page=page, page_size=page_size)
    with connect_dashboard(request.app.state.db_path) as conn2:
        repo = WorkbenchRepository(conn2)
        ready_batches = repo.list_ready_import_batches(limit=1)
        active_job = repo.get_active_job()
        selected_profile = get_selected_profile_id(conn2)
        selected_status = profile_status(selected_profile, db_path=request.app.state.db_path)
    profiles = load_llm_profiles()
    context = _page_context(request, filters, page, page_size, filters.get("days", cfg.default_days),
                            total, items, page_title="首页")
    context["stats"] = stats
    context["workbench"] = {
        "ready_import": ready_batches[0] if ready_batches else None,
        "active_job": active_job,
        "selected_profile": selected_profile,
        "selected_profile_name": profiles[selected_profile].name if selected_profile in profiles else selected_profile,
        "selected_profile_status": selected_status,
    }
    return request.app.state.templates.TemplateResponse(request, "dashboard.html", context)


@router.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request, page: int = Query(1, ge=1),
               page_size: int = Query(0)):
    cfg = load_dashboard_config()
    page_size = page_size or cfg.page_size
    page_size = max(1, min(page_size, cfg.max_page_size))
    filters = _common_params(request, cfg.default_days, cfg.timezone)
    with connect_dashboard(request.app.state.db_path) as conn:
        items, total = list_emails(conn, filters=filters, page=page, page_size=page_size)
    context = _page_context(request, filters, page, page_size, filters.get("days", cfg.default_days),
                            total, items, page_title="高价值线索")
    return request.app.state.templates.TemplateResponse(request, "leads.html", context)


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, page: int = Query(1, ge=1),
                page_size: int = Query(0)):
    cfg = load_dashboard_config()
    page_size = page_size or cfg.page_size
    page_size = max(1, min(page_size, cfg.max_page_size))
    filters = _common_params(request, cfg.default_days, cfg.timezone)
    if not filters.get("review_status"):
        filters["review_status"] = ""
    with connect_dashboard(request.app.state.db_path) as conn:
        items, total = list_review_queue(conn, filters=filters, page=page, page_size=page_size)
        export_counts = {
            "VERIFIED": count_exportable_emails(conn, ["VERIFIED"]),
            "PRIORITY": count_exportable_emails(conn, ["PRIORITY"]),
            "VERIFIED,PRIORITY": count_exportable_emails(conn, ["VERIFIED", "PRIORITY"]),
        }
    context = _page_context(request, filters, page, page_size, filters.get("days", cfg.default_days),
                            total, items, page_title="Review Queue")
    context["export_counts"] = export_counts
    return request.app.state.templates.TemplateResponse(request, "review_queue.html", context)
