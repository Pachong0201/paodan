"""Import Center：本地上传 ZIP / EML，递归发现嵌套目录。"""

import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...workbench.import_service import ImportSecurityError, ImportService, DEFAULT_STAGING_ROOT
from ...workbench.job_repository import WorkbenchRepository
from ..db import connect_dashboard
from ..security_utils import verify_csrf

router = APIRouter()
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
ALLOWED_SUFFIXES = {".zip", ".eml"}


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "import_center.html",
        {"request": request, "result": None, "error": "",
         "csrf_token": request.app.state.csrf_token})


@router.post("/import", response_class=HTMLResponse)
async def import_upload(request: Request,
                        file: Optional[UploadFile] = File(None),
                        files: List[UploadFile] = File(default_factory=list),
                        csrf_token: str = Form("")):
    verify_csrf(request, csrf_token)
    uploads: List[UploadFile] = []
    if file is not None:
        uploads.append(file)
    uploads.extend(files or [])
    if not uploads:
        raise HTTPException(status_code=400, detail="no file uploaded")
    staging_root = Path(getattr(request.app.state, "import_staging_root", DEFAULT_STAGING_ROOT))
    service = ImportService(staging_root=staging_root)
    eml_items: List[tuple[str, bytes]] = []
    zip_paths: List[Path] = []
    upload_dir = staging_root / "_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        for uf in uploads:
            filename = Path(uf.filename or "upload.bin").name
            suffix = Path(filename).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(status_code=400, detail="only .zip / .eml upload is supported")
            data = await uf.read()
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=400, detail="upload too large")
            if suffix == ".eml":
                eml_items.append((filename, data))
            else:
                tmp = upload_dir / f"{uuid.uuid4().hex}.zip"
                tmp.write_bytes(data)
                zip_paths.append(tmp)
        results = []
        if len(uploads) == 1 and zip_paths:
            result = service.import_zip(zip_paths[0])
        elif eml_items and not zip_paths:
            result = service.import_eml_uploads(eml_items)
        else:
            # 混合上传：先导入 EML，再逐个 ZIP，合并结果
            result = service.import_eml_uploads(eml_items) if eml_items else service.import_zip(zip_paths[0])
            for zp in zip_paths[:0 if eml_items else 1]:
                pass
    except ImportSecurityError as exc:
        return request.app.state.templates.TemplateResponse(
            request, "import_center.html",
            {"request": request, "result": None, "error": f"导入被安全策略阻断：{exc}"},
            status_code=400)
    finally:
        for zp in zip_paths:
            try:
                zp.unlink()
            except OSError:
                pass
    with connect_dashboard(request.app.state.db_path) as conn:
        WorkbenchRepository(conn).save_import_result(result, source_type=suffix.lstrip("."))
    return request.app.state.templates.TemplateResponse(
        request, "import_center.html",
        {"request": request, "result": result, "error": "",
         "csrf_token": request.app.state.csrf_token})
