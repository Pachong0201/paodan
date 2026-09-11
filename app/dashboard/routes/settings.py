"""Workbench 设置页：AI Profile / 自定义本地 LLM API / 只读安全信息。"""
from __future__ import annotations

import os
from typing import Optional
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...config import LLM_BASE_URL, LLM_MODEL
from ...security.policy import load_security_policy
from ...workbench.llm_profiles import LLMProfileError, load_llm_profiles, profile_status
from ...workbench.runtime_settings import (
    get_custom_llm_settings,
    get_selected_profile_id,
    set_custom_llm_settings,
    set_selected_profile_id,
)
from ..db import connect_dashboard
from ..security_utils import verify_csrf

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", error: str = ""):
    policy = load_security_policy()
    db_path = request.app.state.db_path
    profiles = load_llm_profiles(db_path=db_path)
    with connect_dashboard(db_path) as conn:
        current = get_selected_profile_id(conn)
        custom = get_custom_llm_settings(conn)
    statuses = {pid: profile_status(pid, policy, db_path=db_path) for pid in profiles}
    custom_profile = profiles.get("custom")
    custom_base_url = str(custom.get("base_url") or (custom_profile.resolved_base_url() if custom_profile else ""))
    custom_model = str(custom.get("model") or (custom_profile.resolved_model() if custom_profile else ""))
    custom_api_key_configured = bool(str(custom.get("api_key") or "").strip()
                                     or os.getenv("LLM_API_KEY", "").strip())
    return request.app.state.templates.TemplateResponse(
        request, "settings.html", {
            "request": request,
            "profiles": [p.safe_dict() for p in profiles.values()],
            "profile_statuses": statuses,
            "current_profile": current,
            "privacy_level": policy.privacy_level,
            "allowed_hosts": policy.allowed_hosts,
            "api_key_configured": bool(os.getenv("LLM_API_KEY", "")),
            "dashboard_timezone": request.app.state.dashboard_config.timezone,
            "llm_model": LLM_MODEL,
            "llm_base_url_host": urlparse(LLM_BASE_URL).hostname or "",
            "custom_base_url": custom_base_url,
            "custom_model": custom_model,
            "custom_api_key_configured": custom_api_key_configured,
            "custom_status": statuses.get("custom", {}),
            "saved": saved,
            "error": error,
        })


@router.post("/settings/profile")
def set_profile(request: Request, csrf_token: Optional[str] = Form(None),
                profile_id: str = Form(...)):
    verify_csrf(request, csrf_token)
    try:
        load_llm_profiles(db_path=request.app.state.db_path)
        with connect_dashboard(request.app.state.db_path) as conn:
            set_selected_profile_id(conn, profile_id)
    except LLMProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"selected_profile": profile_id}


@router.post("/settings/llm")
def set_llm(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    base_url: str = Form(""),
    model: str = Form(""),
    api_key: str = Form(""),
    clear_api_key: str = Form(""),
):
    verify_csrf(request, csrf_token)
    base_url = (base_url or "").strip()
    model = (model or "").strip()
    if not base_url or not model:
        return RedirectResponse(
            "/settings?error=" + quote("API Base URL 和 Model 不能为空"), status_code=303)
    parsed = urlparse(base_url if "://" in base_url else "https://" + base_url)
    if not parsed.hostname:
        return RedirectResponse(
            "/settings?error=" + quote("API Base URL 格式无效"), status_code=303)
    if parsed.username or parsed.password:
        return RedirectResponse(
            "/settings?error=" + quote("API Base URL 不得包含账号密码"), status_code=303)
    with connect_dashboard(request.app.state.db_path) as conn:
        set_custom_llm_settings(
            conn, base_url, model, api_key=api_key,
            clear_api_key=bool((clear_api_key or "").strip()))
        # 保存后自动选中 custom profile，让用户下次开始筛选直接生效。
        set_selected_profile_id(conn, "custom")
    return RedirectResponse("/settings?saved=1", status_code=303)
