"""Import Center：本地上传 ZIP / EML，递归发现嵌套目录。"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...workbench.import_service import ImportSecurityError, ImportService, DEFAULT_STAGING_ROOT

router = APIRouter()
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
ALLOWED_SUFFIXES = {".zip", ".eml"}


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "import_center.html",
        {"request": request, "result": None, "error": ""})


@router.post("/import", response_class=HTMLResponse)
async def import_upload(request: Request, file: UploadFile = File(...)):
    filename = Path(file.filename or "upload.zip").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=400, detail="only .zip / .eml upload is supported")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="upload too large")
    staging_root = Path(getattr(request.app.state, "import_staging_root", DEFAULT_STAGING_ROOT))
    upload_dir = staging_root / "_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    tmp = upload_dir / f"{uuid.uuid4().hex}{suffix}"
    try:
        tmp.write_bytes(data)
        service = ImportService(staging_root=staging_root)
        if suffix == ".eml":
            result = service.import_eml_bytes(data, source_name=filename)
        else:
            result = service.import_zip(tmp)
    except ImportSecurityError as exc:
        return request.app.state.templates.TemplateResponse(
            request, "import_center.html",
            {"request": request, "result": None, "error": f"导入被安全策略阻断：{exc}"},
            status_code=400)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return request.app.state.templates.TemplateResponse(
        request, "import_center.html",
        {"request": request, "result": result, "error": ""})
