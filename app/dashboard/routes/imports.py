"""Import Center：本地上传 ZIP / EML，递归发现嵌套目录。

V5.0.1 hardening：
- 浏览器表单与后端统一使用 ``files`` 多文件字段；
- 上传边读边写临时文件、边计算 SHA256，超限立即停止；
- 多 ZIP / EML+ZIP 混合上传合并为同一个 import batch；
- 整个请求的压缩文件总大小与解压后总大小分别受配置限制。
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...workbench.import_service import (
    DEFAULT_STAGING_ROOT,
    ImportSecurityError,
    ImportService,
    UploadSource,
)
from ...workbench.job_repository import WorkbenchRepository
from ..db import connect_dashboard
from ..security_utils import verify_csrf

router = APIRouter()
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
ALLOWED_SUFFIXES = {".zip", ".eml"}


def _source_type(uploads: List[UploadSource]) -> str:
    return ImportService.source_type_for_suffixes(u.suffix for u in uploads)


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "import_center.html",
        {
            "request": request,
            "result": None,
            "error": "",
            "csrf_token": request.app.state.csrf_token,
        },
    )


@router.post("/import", response_class=HTMLResponse)
async def import_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    csrf_token: str = Form(""),
):
    verify_csrf(request, csrf_token)
    uploads = [uf for uf in (files or []) if uf is not None]
    if not uploads:
        raise HTTPException(status_code=400, detail="no file uploaded")

    staging_root = Path(getattr(request.app.state, "import_staging_root", DEFAULT_STAGING_ROOT))
    service = ImportService(staging_root=staging_root, db_path=request.app.state.db_path)
    max_file_size = int(service.max_file_size or 0)
    # max_batch_size_mb 是上传压缩文件总大小；保留模块常量便于测试/紧急降级。
    configured_batch_limit = int(service.max_upload_size or 0)
    max_upload_bytes = configured_batch_limit
    # 仅当模块常量被测试/紧急降级设置得比配置更小时才收紧；
    # 否则以 config/workbench.yaml 的 max_batch_size_mb 为准。
    if MAX_UPLOAD_BYTES and configured_batch_limit and int(MAX_UPLOAD_BYTES) < configured_batch_limit:
        max_upload_bytes = int(MAX_UPLOAD_BYTES)

    request_id = uuid.uuid4().hex
    upload_dir = staging_root / "_uploads" / request_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    sources: List[UploadSource] = []
    total_uploaded = 0

    try:
        for uf in uploads:
            filename = Path(uf.filename or "upload.bin").name
            suffix = Path(filename).suffix.lower()
            if suffix not in ALLOWED_SUFFIXES:
                raise HTTPException(
                    status_code=400,
                    detail="only .zip / .eml upload is supported",
                )
            target = upload_dir / f"{uuid.uuid4().hex}{suffix}"
            hasher = hashlib.sha256()
            size = 0
            with open(target, "wb") as out:
                while True:
                    # 必须带 size 参数；禁止无参数 UploadFile.read 把大文件整包读入内存。
                    chunk = await uf.read(READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if max_file_size and size > max_file_size:
                        raise HTTPException(status_code=413, detail="FILE_TOO_LARGE")
                    total_uploaded += len(chunk)
                    if max_upload_bytes and total_uploaded > max_upload_bytes:
                        raise HTTPException(status_code=413, detail="BATCH_TOO_LARGE")
                    hasher.update(chunk)
                    out.write(chunk)
            sources.append(UploadSource(
                path=target,
                filename=filename,
                suffix=suffix,
                size=size,
                sha256=hasher.hexdigest(),
            ))

        result = service.import_upload_batch(sources)
    except HTTPException:
        # finally 会清理 _uploads 临时文件；不创建 READY Batch。
        raise
    except ImportSecurityError as exc:
        return request.app.state.templates.TemplateResponse(
            request,
            "import_center.html",
            {
                "request": request,
                "result": None,
                "error": f"导入被安全策略阻断：{str(exc)[:200]}",
            },
            status_code=400,
        )
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    source_type = _source_type(sources) or "upload"
    with connect_dashboard(request.app.state.db_path) as conn:
        WorkbenchRepository(conn).save_import_result(result, source_type=source_type)

    return request.app.state.templates.TemplateResponse(
        request,
        "import_center.html",
        {
            "request": request,
            "result": result,
            "error": "",
            "csrf_token": request.app.state.csrf_token,
        },
    )
