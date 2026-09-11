"""Dashboard/Workbench 共用 CSRF / Origin 校验。"""
from __future__ import annotations

import secrets
from urllib.parse import urlparse

from fastapi import HTTPException, Request


def verify_csrf(request: Request, csrf_token: str | None) -> None:
    expected = str(getattr(request.app.state, "csrf_token", "") or "")
    if not csrf_token or not expected or not secrets.compare_digest(str(csrf_token), expected):
        raise HTTPException(status_code=403, detail="invalid csrf token")
    origin = request.headers.get("origin") or ""
    if origin:
        try:
            hostname = (urlparse(origin).hostname or "").lower()
        except Exception:
            raise HTTPException(status_code=403, detail="invalid origin")
        if hostname not in ("127.0.0.1", "localhost", "::1"):
            raise HTTPException(status_code=403, detail="invalid origin")
