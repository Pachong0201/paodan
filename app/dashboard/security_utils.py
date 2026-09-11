"""Dashboard/Workbench 共用 CSRF / Origin 校验。"""
from __future__ import annotations

import ipaddress
import secrets
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from pathlib import Path


def _is_trusted_local_origin(hostname: str) -> bool:
    """允许 loopback 及解析到 loopback 的本机名称（含 WSL localhost 别名）。"""
    hostname = (hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname in ("127.0.0.1", "localhost", "::1", "wsl.localhost"):
        return True
    if hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(hostname, None)
        addrs = {str(info[4][0]) for info in infos if info and len(info) >= 5}
        return bool(addrs) and all(ipaddress.ip_address(a).is_loopback for a in addrs)
    except Exception:
        return False


def _log_invalid_origin(origin: str, host: str) -> None:
    try:
        root = Path(__file__).resolve().parents[2]
        with (root / "logs" / "csrf_invalid_origin.log").open("a", encoding="utf-8") as f:
            f.write(f"origin={origin!r} host={host!r}\n")
    except Exception:
        pass


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
        if not _is_trusted_local_origin(hostname):
            _log_invalid_origin(origin, request.headers.get("host") or "")
            raise HTTPException(status_code=403, detail="invalid origin")
