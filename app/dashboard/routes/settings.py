"""Workbench 设置页：AI Profile / 只读安全信息。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...config import LLM_BASE_URL, LLM_MODEL
from ...security.policy import load_security_policy
from ...workbench.llm_profiles import LLMProfileError, load_llm_profiles, profile_status
from ...workbench.runtime_settings import get_selected_profile_id
from ..db import connect_dashboard
from ..security_utils import verify_csrf

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    policy = load_security_policy()
    profiles = load_llm_profiles()
    with connect_dashboard(request.app.state.db_path) as conn:
        current = get_selected_profile_id(conn)
    statuses = {pid: profile_status(pid, policy) for pid in profiles}
    return request.app.state.templates.TemplateResponse(
        request, "settings.html", {
            "request": request,
            "profiles": [p.safe_dict() for p in profiles.values()],
            "profile_statuses": statuses,
            "current_profile": current,
            "privacy_level": policy.privacy_level,
            "allowed_hosts": policy.allowed_hosts,
            "api_key_configured": bool(__import__("os").getenv("LLM_API_KEY", "")),
            "dashboard_timezone": request.app.state.dashboard_config.timezone,
            "llm_model": LLM_MODEL,
            "llm_base_url_host": __import__("urllib.parse", fromlist=["urlparse"]).urlparse(LLM_BASE_URL).hostname or "",
        })


@router.post("/settings/profile")
def set_profile(request: Request, csrf_token: Optional[str] = Form(None),
                profile_id: str = Form(...)):
    verify_csrf(request, csrf_token)
    try:
        load_llm_profiles()
        with connect_dashboard(request.app.state.db_path) as conn:
            from ...workbench.runtime_settings import set_selected_profile_id
            set_selected_profile_id(conn, profile_id)
    except LLMProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"selected_profile": profile_id}
