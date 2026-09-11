"""Paodan V4.2 Local Dashboard 入口。

运行：
    python -m app.dashboard.server
默认：
    http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..config import DB_PATH
from ..workbench.job_runner import JobRunner
from .config import DashboardConfigError, load_dashboard_config
from .db import connect_dashboard, ensure_database
from .queries import get_dashboard_stats, list_emails
from .routes import dashboard as dashboard_routes
from .routes import emails as email_routes
from .routes import imports as import_routes
from .routes import jobs as job_routes
from .routes import reader as reader_routes
from .routes import reviews as review_routes
from .routes import settings as setting_routes

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "::1", "testserver"]


def create_app(db_path: str | Path | None = None,
               config_dir: str | Path | None = None,
               import_staging_root: str | Path | None = None) -> FastAPI:
    cfg = load_dashboard_config(config_dir=config_dir)
    if not cfg.enabled:
        raise DashboardConfigError("Dashboard disabled by config")
    resolved_db = ensure_database(db_path or DB_PATH)

    app = FastAPI(title="Paodan V5.0.1 Local Workbench", docs_url=None,
                  redoc_url=None, openapi_url=None)
    app.state.db_path = resolved_db
    app.state.dashboard_config = cfg
    app.state.csrf_token = secrets.token_urlsafe(24)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.import_staging_root = (Path(import_staging_root) if import_staging_root
                                    else BASE_DIR.parents[1] / "data" / "import_staging")
    app.state.job_runner = JobRunner(resolved_db, staging_root=app.state.import_staging_root)
    app.state.job_runner.recover_on_startup()

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none';"
        )
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(dashboard_routes.router)
    app.include_router(email_routes.router)
    app.include_router(import_routes.router)
    app.include_router(job_routes.router)
    app.include_router(reader_routes.router)
    app.include_router(setting_routes.router)
    app.include_router(review_routes.router)

    @app.get("/api/stats")
    def api_stats():
        with connect_dashboard(resolved_db) as conn:
            return get_dashboard_stats(conn, default_days=cfg.default_days).to_dict()

    @app.get("/api/emails")
    def api_emails(page: int = 1, page_size: int = 30):
        page_size = max(1, min(page_size, cfg.max_page_size))
        with connect_dashboard(resolved_db) as conn:
            items, total = list_emails(conn, filters={}, page=page, page_size=page_size)
        return {"total": total, "items": [i.to_dict() for i in items]}

    # 统一错误页
    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        return app.state.templates.TemplateResponse(
            request, "errors/404.html", {"request": request}, status_code=404)

    @app.exception_handler(Exception)
    async def server_error(request: Request, exc):
        # 不把 traceback/SQL/path 写入 HTML；只记录内部 logger。
        import logging
        logging.getLogger("app.dashboard").exception("Dashboard 500: %s", request.url.path)
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": "internal server error"}, status_code=500)
        return app.state.templates.TemplateResponse(
            request, "errors/500.html", {"request": request}, status_code=500)

    return app


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.dashboard.server")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_dashboard_config()
    if args.host:
        cfg.host = args.host
        cfg.validate()
    if args.port:
        cfg.port = args.port
    if args.selfcheck:
        print("Dashboard selfcheck: PASS")
        return 0
    try:
        resolved_db = ensure_database(DB_PATH)
    except Exception as e:
        print("Dashboard startup FAIL: %s" % e, file=sys.stderr)
        return 1
    import uvicorn
    print("Paodan V5.0.1 Local Workbench")
    print(f"http://{cfg.host}:{cfg.port}")
    print(f"database: {Path(resolved_db).name}")
    uvicorn.run(create_app(DB_PATH), host=cfg.host, port=cfg.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
