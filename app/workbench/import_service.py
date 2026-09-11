"""V5.0 Import Center：递归发现 ZIP/目录中的 EML，并安全 staging。

安全原则：
- 不直接 extractall；
- 每个 ZipInfo 先校验路径、深度、大小、压缩比；
- staging 使用 <sha256>/<safe_filename>，同名不同内容不覆盖；
- 去重基于 raw EML SHA256，不基于 filename。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
import uuid
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import ROOT, WorkbenchImportConfig, load_workbench_config
from ..config import DB_PATH

logger = logging.getLogger(__name__)

DEFAULT_STAGING_ROOT = ROOT / "data" / "import_staging"
IGNORED_DIR_NAMES = {"__MACOSX"}
IGNORED_BASENAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


class ImportSecurityError(RuntimeError):
    """ZIP Slip / 压缩炸弹 / 不可用压缩包等安全错误。"""


# 兼容常见命名
ZipSlipError = ImportSecurityError
ArchiveSecurityError = ImportSecurityError


@dataclass
class ImportItem:
    archive_relative_path: str = ""
    raw_sha256: str = ""
    staged_path: str = ""
    size: int = 0
    status: str = "accepted"       # accepted / duplicate / invalid / rejected
    reason: str = ""
    depth: int = 0

    @property
    def relative_path(self) -> str:
        return self.archive_relative_path

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UploadSource:
    """Web Upload 流式落盘后的临时文件描述。

    路由层负责分块读取并计算 SHA256；服务层只消费临时文件路径，
    因此大型 EML 不会作为 ``list[tuple[str, bytes]]`` 常驻内存。
    """
    path: str | Path
    filename: str
    suffix: str = ""
    size: int = 0
    sha256: str = ""

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.suffix:
            self.suffix = Path(self.filename).suffix.lower()
        if not self.size and self.path.exists():
            try:
                self.size = int(self.path.stat().st_size)
            except OSError:
                self.size = 0
        if not self.sha256 and self.path.exists():
            try:
                self.sha256 = _sha256_file(self.path)
            except OSError:
                self.sha256 = ""


@dataclass
class ImportBatchResult:
    import_id: str = ""
    staging_dir: str = ""
    manifest_path: str = ""
    discovered: int = 0
    accepted: int = 0
    duplicates: int = 0
    invalid: int = 0
    rejected: int = 0
    status: str = "ok"
    items: List[ImportItem] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    uploaded_bytes: int = 0
    uncompressed_bytes: int = 0
    accepted_bytes: int = 0
    file_count: int = 0
    security_failures: int = 0
    source_type: str = ""

    @property
    def total(self) -> int:
        return self.discovered

    @property
    def valid(self) -> int:
        return self.accepted

    @property
    def accepted_count(self) -> int:
        return self.accepted

    @property
    def rejected_count(self) -> int:
        return self.rejected

    @property
    def duplicate_count(self) -> int:
        return self.duplicates

    @property
    def invalid_count(self) -> int:
        return self.invalid

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["items"] = [x.to_dict() if hasattr(x, "to_dict") else x for x in self.items]
        return d

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass
class _BatchState:
    """一个导入批次共享的累积状态。

    一次 Web 上传可能包含 N 个 ZIP + M 个 EML；这些文件必须共享
    import_id、seen_sha、uncompressed 预算与统计，不能每个 ZIP 单独一份。
    """
    import_id: str
    import_dir: Path
    result: ImportBatchResult
    seen_sha: Set[str] = field(default_factory=set)
    total_uncompressed: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _is_valid_eml_file(path: str | Path) -> bool:
    """以流式/文件对象方式验证 EML，避免先把整封邮件读成 bytes。"""
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
            if not head:
                return False
            head_text = head.decode("utf-8", errors="replace")
            if not (head_text.startswith("From ") or re.search(r"(?m)^[A-Za-z0-9\-]+:\s*\S", head_text)):
                return False
            f.seek(0)
            msg = BytesParser(policy=policy.default).parse(f)
            return bool(msg.keys())
    except Exception:
        return False


def _safe_basename(name: str, max_len: int = 120) -> str:
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name, flags=re.UNICODE).strip("._")
    return (name or "message.eml")[:max_len]


def _normalize_entry_name(name: str) -> str:
    return str(name or "").replace("\\", "/")


def _entry_parts(name: str) -> List[str]:
    return [p for p in _normalize_entry_name(name).split("/") if p not in ("", ".")]


def _entry_depth(name: str) -> int:
    parts = _entry_parts(name)
    if not parts:
        return 0
    return max(0, len(parts) - 1)


def _is_ignored_entry(name: str) -> bool:
    parts = _entry_parts(name)
    if any(p in IGNORED_DIR_NAMES for p in parts):
        return True
    base = parts[-1] if parts else ""
    if base in IGNORED_BASENAMES:
        return True
    if base.startswith("._"):
        return True
    return False


def _validate_member_path(name: str) -> str:
    """校验 ZIP 内路径，阻断 Zip Slip / 绝对路径 / Windows drive。"""
    raw = str(name or "")
    if "\x00" in raw:
        raise ImportSecurityError("ZIP_ENTRY_NUL_BYTE")
    normalized = _normalize_entry_name(raw)
    if normalized.startswith("/"):
        raise ImportSecurityError(f"ZIP_SLIP_ABSOLUTE_PATH: {raw}")
    if re.match(r"^[A-Za-z]:", normalized):
        raise ImportSecurityError(f"ZIP_SLIP_WINDOWS_DRIVE: {raw}")
    parts = normalized.split("/")
    if any(p == ".." for p in parts):
        raise ImportSecurityError(f"ZIP_SLIP_PARENT_TRAVERSAL: {raw}")
    return normalized


def _is_eml_path(name: str) -> bool:
    return Path(_normalize_entry_name(name)).suffix.lower() == ".eml"


def _is_valid_eml_bytes(data: bytes) -> bool:
    if not data:
        return False
    # EML 必须至少有邮件头；纯文本文件改扩展名不得被接受。
    head = data[:8192].decode("utf-8", errors="replace")
    if not (head.startswith("From ") or re.search(r"(?m)^[A-Za-z0-9\-]+:\s*\S", head)):
        return False
    try:
        msg = BytesParser(policy=policy.default).parsebytes(data)
    except Exception:
        return False
    return bool(msg.keys())


def discover_eml_files(root_dir: str | Path, recursive: bool = True,
                       max_depth: Optional[int] = None,
                       max_nesting_depth: Optional[int] = None) -> List[Path]:
    """递归发现目录中的 .eml（目录名不参与判断）。"""
    root = Path(root_dir)
    if not root.exists() or not root.is_dir():
        return []
    depth_limit = max_depth if max_depth is not None else (max_nesting_depth if max_nesting_depth is not None else 10)
    out: List[Path] = []
    iterator = root.rglob("*") if recursive else root.glob("*")
    for p in iterator:
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in IGNORED_DIR_NAMES for part in rel.parts):
            continue
        if p.name in IGNORED_BASENAMES or p.name.startswith("._"):
            continue
        if p.suffix.lower() != ".eml":
            continue
        if len(rel.parts) - 1 > depth_limit:
            continue
        out.append(p)
    return sorted(out, key=lambda x: str(x))


class ImportService:
    def __init__(self, staging_root: str | Path | None = None,
                 recursive: Optional[bool] = None,
                 max_nesting_depth: Optional[int] = None,
                 max_file_size: Optional[int] = None,
                 max_total_size: Optional[int] = None,
                 max_compression_ratio: Optional[float] = None,
                 max_files_per_batch: Optional[int] = None,
                 max_upload_size: Optional[int] = None,
                 db_path: str | Path | None = None):
        self.config = load_workbench_config()
        self.recursive = self.config.recursive if recursive is None else bool(recursive)
        self.max_nesting_depth = (self.config.max_nesting_depth if max_nesting_depth is None
                                  else int(max_nesting_depth))
        self.max_file_size = self.config.max_file_size if max_file_size is None else int(max_file_size)
        self.max_total_size = self.config.max_total_size if max_total_size is None else int(max_total_size)
        self.max_compression_ratio = (self.config.max_compression_ratio
                                      if max_compression_ratio is None else float(max_compression_ratio))
        self.max_files_per_batch = (self.config.max_files_per_batch
                                    if max_files_per_batch is None else int(max_files_per_batch))
        # 整个 Web 上传请求的压缩文件总大小；默认由 max_batch_size_mb 换算。
        self.max_upload_size = (int(max_upload_size) if max_upload_size is not None
                                else int(self.config.max_batch_size_mb) * 1024 * 1024)
        self.staging_root = Path(staging_root) if staging_root else DEFAULT_STAGING_ROOT
        self.db_path = Path(db_path) if db_path else Path(DB_PATH)
        self.staging_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def _new_import_dir(self, import_id: Optional[str] = None) -> Tuple[str, Path]:
        iid = import_id or f"IMP-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        d = self.staging_root / iid
        (d / "files").mkdir(parents=True, exist_ok=True)
        return iid, d

    def _new_state(self, import_id: Optional[str] = None) -> _BatchState:
        iid, import_dir = self._new_import_dir(import_id)
        result = ImportBatchResult(import_id=iid, staging_dir=str(import_dir))
        return _BatchState(import_id=iid, import_dir=import_dir, result=result)

    def _write_manifest(self, result: ImportBatchResult) -> None:
        manifest = {
            "import_id": result.import_id,
            "created_at": _now(),
            "recursive": self.recursive,
            "max_nesting_depth": self.max_nesting_depth,
            "discovered": result.discovered,
            "accepted": result.accepted,
            "duplicates": result.duplicates,
            "invalid": result.invalid,
            "rejected": result.rejected,
            "uploaded_bytes": result.uploaded_bytes,
            "uncompressed_bytes": result.uncompressed_bytes,
            "accepted_bytes": result.accepted_bytes,
            "file_count": result.file_count,
            "security_failures": result.security_failures,
            "items": [
                {k: v for k, v in it.to_dict().items() if k != "staged_path"} | {
                    "stored_filename": Path(it.staged_path).name if it.staged_path else ""
                }
                for it in result.items
            ],
            "errors": list(result.errors),
        }
        path = Path(result.staging_dir) / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result.manifest_path = str(path)

    def _finalize(self, state: _BatchState) -> ImportBatchResult:
        state.result.uncompressed_bytes = state.total_uncompressed
        state.result.file_count = state.result.discovered
        self._write_manifest(state.result)
        return state.result

    def _stage_bytes(self, import_dir: Path, data: bytes, original_name: str) -> Tuple[str, str]:
        sha = _sha256_bytes(data)
        target_dir = import_dir / "files" / sha
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / _safe_basename(original_name)
        if not target.exists():
            target.write_bytes(data)
        return sha, str(target)

    def _stage_file(self, import_dir: Path, source_path: str | Path,
                    original_name: str, expected_sha: str = "") -> Tuple[str, str]:
        """把临时上传文件流式复制到 staging，并校验/计算 SHA256。"""
        source_path = Path(source_path)
        expected_sha = str(expected_sha or "")
        if expected_sha:
            target_dir = import_dir / "files" / expected_sha
            target = target_dir / _safe_basename(original_name)
            if target.exists():
                return expected_sha, str(target)
        target_dir = import_dir / "files" / "_tmp"
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp = target_dir / f"{uuid.uuid4().hex}.part"
        h = hashlib.sha256()
        try:
            with open(source_path, "rb") as src, open(tmp, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
                    dst.write(chunk)
            sha = h.hexdigest()
            if expected_sha and sha != expected_sha:
                raise ImportSecurityError("SHA256_MISMATCH")
            final_dir = import_dir / "files" / sha
            final_dir.mkdir(parents=True, exist_ok=True)
            final = final_dir / _safe_basename(original_name)
            if final.exists():
                tmp.unlink(missing_ok=True)
            else:
                tmp.replace(final)
            try:
                target_dir.rmdir()
            except OSError:
                pass
            return sha, str(final)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    def _lookup_duplicate_reason(self, sha256: str) -> str:
        """跨 Import Batch 去重查询；只把终态或仍活跃的批次视为重复。

        V5.0.2 语义：
        - completed / duplicate import_files：永久历史重复
        - emails.raw_sha256：旧 Pipeline 已完成结果
        - 仍处于 READY/PROCESSING 的 accepted：活跃导入重复（不永久锁死）
        """
        sha256 = str(sha256 or "").strip().lower()
        if not sha256 or not self.db_path or not Path(self.db_path).exists():
            return ""
        conn = None
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """SELECT 1 FROM import_files
                       WHERE source_sha256=?
                         AND status IN ('completed','duplicate','COMPLETED','DUPLICATE')
                       LIMIT 1""", (sha256,)).fetchone()
                if row is not None:
                    return "DUPLICATE_COMPLETED"
            except sqlite3.OperationalError:
                pass
            try:
                row = conn.execute(
                    "SELECT 1 FROM emails WHERE raw_sha256=? LIMIT 1", (sha256,)).fetchone()
                if row is not None:
                    return "DUPLICATE_EMAIL_DB"
            except sqlite3.OperationalError:
                pass
            try:
                row = conn.execute(
                    """SELECT 1 FROM import_files f
                       JOIN import_batches b ON b.import_id=f.import_id
                       WHERE f.source_sha256=?
                         AND f.status='accepted'
                         AND b.status IN ('READY','PROCESSING')
                       LIMIT 1""", (sha256,)).fetchone()
                if row is not None:
                    return "DUPLICATE_ACTIVE_IMPORT"
            except sqlite3.OperationalError:
                pass
            return ""
        except sqlite3.Error:
            return ""
        finally:
            if conn is not None:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass

    def _duplicate_reason(self, state: _BatchState, sha256: str) -> str:
        if not sha256:
            return ""
        if sha256 in state.seen_sha:
            return "DUPLICATE_RAW_EML_SHA256"
        return self._lookup_duplicate_reason(sha256)

    def _accept_candidate(self, state: _BatchState, *, rel_path: str, depth: int,
                          size: int, original_name: str,
                          data: bytes | None = None,
                          source_path: str | Path | None = None,
                          sha256: str = "") -> None:
        """去重 → 合法性校验 → staging 的共享逻辑；seen_sha 批次级共享。"""
        result = state.result
        item = ImportItem(archive_relative_path=rel_path, depth=depth,
                          size=int(size or 0), status="accepted")
        if sha256:
            sha = str(sha256).strip().lower()
        elif data is not None:
            sha = _sha256_bytes(data)
        elif source_path is not None:
            sha = _sha256_file(source_path)
        else:
            sha = ""
        item.raw_sha256 = sha

        duplicate_reason = self._duplicate_reason(state, sha)
        if duplicate_reason:
            item.status = "duplicate"
            item.reason = duplicate_reason
            result.duplicates += 1
            result.items.append(item)
            return

        if sha:
            state.seen_sha.add(sha)

        if data is not None:
            valid = _is_valid_eml_bytes(data)
        elif source_path is not None:
            valid = _is_valid_eml_file(source_path)
        else:
            valid = False
        if not valid:
            item.status = "invalid"
            item.reason = "INVALID_EML"
            result.invalid += 1
            result.items.append(item)
            return

        try:
            if source_path is not None:
                _, staged = self._stage_file(state.import_dir, source_path, original_name, expected_sha=sha)
            else:
                _, staged = self._stage_bytes(state.import_dir, data or b"", original_name)
        except ImportSecurityError:
            raise
        except OSError:
            item.status = "invalid"
            item.reason = "STAGING_IO_ERROR"
            result.invalid += 1
            result.items.append(item)
            return
        item.staged_path = staged
        result.accepted += 1
        result.accepted_bytes += int(size or 0)
        result.items.append(item)

    # ------------------------------------------------------------------
    def _process_eml_into(self, state: _BatchState, filename: str, data: bytes) -> None:
        result = state.result
        size = len(data or b"")
        safe_name = _safe_basename(filename)
        result.discovered += 1
        if result.discovered > self.max_files_per_batch:
            result.rejected += 1
            result.items.append(ImportItem(archive_relative_path=safe_name, size=size,
                                           status="rejected", reason="MAX_FILES_EXCEEDED"))
            return
        if size > self.max_file_size:
            result.invalid += 1
            result.items.append(ImportItem(archive_relative_path=safe_name, size=size,
                                           status="invalid", reason="FILE_TOO_LARGE"))
            return
        state.total_uncompressed += size
        result.uncompressed_bytes = state.total_uncompressed
        if state.total_uncompressed > self.max_total_size:
            result.rejected += 1
            result.items.append(ImportItem(archive_relative_path=safe_name, size=size,
                                           status="rejected", reason="TOTAL_SIZE_EXCEEDED"))
            return
        self._accept_candidate(state, rel_path=safe_name, depth=0, size=size,
                               original_name=filename, data=data)

    def _process_eml_file_into(self, state: _BatchState, upload: UploadSource) -> None:
        result = state.result
        safe_name = _safe_basename(upload.filename)
        size = int(upload.size or 0)
        result.discovered += 1
        if result.discovered > self.max_files_per_batch:
            result.rejected += 1
            result.items.append(ImportItem(archive_relative_path=safe_name, size=size,
                                           status="rejected", reason="MAX_FILES_EXCEEDED"))
            return
        if size > self.max_file_size:
            result.invalid += 1
            result.items.append(ImportItem(archive_relative_path=safe_name, size=size,
                                           status="invalid", reason="FILE_TOO_LARGE"))
            return
        state.total_uncompressed += size
        result.uncompressed_bytes = state.total_uncompressed
        if state.total_uncompressed > self.max_total_size:
            result.rejected += 1
            result.items.append(ImportItem(archive_relative_path=safe_name, size=size,
                                           status="rejected", reason="TOTAL_SIZE_EXCEEDED"))
            return
        self._accept_candidate(state, rel_path=safe_name, depth=0, size=size,
                               original_name=upload.filename, source_path=upload.path,
                               sha256=upload.sha256)

    def _process_zip_into(self, state: _BatchState, zip_path: str | Path) -> None:
        zip_path = Path(zip_path)
        if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
            raise ImportSecurityError("INVALID_ZIP")
        result = state.result
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = info.filename
                if not name:
                    continue
                # 先校验路径，危险条目在任何读取/解压前阻断。
                normalized = _validate_member_path(name)
                if info.is_dir() or normalized.endswith("/"):
                    continue
                if _is_ignored_entry(normalized):
                    continue
                if not _is_eml_path(normalized):
                    continue
                depth = _entry_depth(normalized)
                result.discovered += 1
                if result.discovered > self.max_files_per_batch:
                    result.rejected += 1
                    result.items.append(ImportItem(
                        archive_relative_path=normalized, depth=depth,
                        size=int(info.file_size or 0), status="rejected",
                        reason="MAX_FILES_EXCEEDED"))
                    continue
                if depth > self.max_nesting_depth:
                    result.rejected += 1
                    result.items.append(ImportItem(
                        archive_relative_path=normalized, depth=depth,
                        size=int(info.file_size or 0), status="rejected",
                        reason="NESTING_DEPTH_EXCEEDED"))
                    continue
                if int(info.file_size or 0) > self.max_file_size:
                    result.invalid += 1
                    result.items.append(ImportItem(
                        archive_relative_path=normalized, depth=depth,
                        size=int(info.file_size or 0), status="invalid",
                        reason="FILE_TOO_LARGE"))
                    continue
                state.total_uncompressed += int(info.file_size or 0)
                result.uncompressed_bytes = state.total_uncompressed
                if state.total_uncompressed > self.max_total_size:
                    result.rejected += 1
                    result.items.append(ImportItem(
                        archive_relative_path=normalized, depth=depth,
                        size=int(info.file_size or 0), status="rejected",
                        reason="TOTAL_SIZE_EXCEEDED"))
                    continue
                compressed = max(1, int(info.compress_size or 0))
                ratio = float(info.file_size or 0) / compressed
                if ratio > self.max_compression_ratio:
                    result.rejected += 1
                    result.items.append(ImportItem(
                        archive_relative_path=normalized, depth=depth,
                        size=int(info.file_size or 0), status="rejected",
                        reason="COMPRESSION_RATIO_EXCEEDED"))
                    continue
                try:
                    data = zf.read(info)
                except Exception:
                    # 不把本机临时路径或底层异常回显到页面/DB。
                    result.invalid += 1
                    result.items.append(ImportItem(
                        archive_relative_path=normalized, depth=depth,
                        size=int(info.file_size or 0), status="invalid",
                        reason="ZIP_READ_ERROR"))
                    continue
                self._accept_candidate(state, rel_path=normalized, depth=depth,
                                       size=int(info.file_size or 0), data=data,
                                       original_name=normalized)

    @staticmethod
    def source_type_for_suffixes(suffixes: Iterable[str]) -> str:
        values = {str(s or "").lower() for s in suffixes if str(s or "").strip()}
        if values == {".eml"}:
            return "eml"
        if values == {".zip"}:
            return "zip"
        if values:
            return "mixed"
        return "upload"

    # ------------------------------------------------------------------
    def import_zip(self, zip_path: str | Path,
                   import_id: Optional[str] = None) -> ImportBatchResult:
        zip_path = Path(zip_path)
        if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
            raise ImportSecurityError("INVALID_ZIP")
        state = self._new_state(import_id)
        state.result.source_type = "zip"
        try:
            state.result.uploaded_bytes = int(zip_path.stat().st_size)
        except OSError:
            state.result.uploaded_bytes = 0
        self._process_zip_into(state, zip_path)
        return self._finalize(state)

    # ------------------------------------------------------------------
    def import_directory(self, root_dir: str | Path,
                         import_id: Optional[str] = None) -> ImportBatchResult:
        root = Path(root_dir)
        if not root.exists() or not root.is_dir():
            raise ImportSecurityError("INVALID_DIRECTORY")
        state = self._new_state(import_id)
        result = state.result
        result.source_type = "eml"
        files = discover_eml_files(root, recursive=self.recursive, max_depth=10 ** 6)
        try:
            result.uploaded_bytes = sum(int(p.stat().st_size) for p in files if p.exists())
        except OSError:
            result.uploaded_bytes = 0
        for path in files:
            rel = path.relative_to(root).as_posix()
            depth = len(path.relative_to(root).parts) - 1
            try:
                size = int(path.stat().st_size)
            except OSError:
                size = 0
            result.discovered += 1
            if result.discovered > self.max_files_per_batch:
                result.rejected += 1
                result.items.append(ImportItem(archive_relative_path=rel, depth=depth,
                                               size=size, status="rejected",
                                               reason="MAX_FILES_EXCEEDED"))
                continue
            if depth > self.max_nesting_depth:
                result.rejected += 1
                result.items.append(ImportItem(archive_relative_path=rel, depth=depth,
                                               size=size, status="rejected",
                                               reason="NESTING_DEPTH_EXCEEDED"))
                continue
            if size > self.max_file_size:
                result.invalid += 1
                result.items.append(ImportItem(archive_relative_path=rel, depth=depth,
                                               size=size, status="invalid",
                                               reason="FILE_TOO_LARGE"))
                continue
            state.total_uncompressed += size
            result.uncompressed_bytes = state.total_uncompressed
            if state.total_uncompressed > self.max_total_size:
                result.rejected += 1
                result.items.append(ImportItem(archive_relative_path=rel, depth=depth,
                                               size=size, status="rejected",
                                               reason="TOTAL_SIZE_EXCEEDED"))
                continue
            self._accept_candidate(state, rel_path=rel, depth=depth, size=size,
                                   original_name=path.name, source_path=path)
        return self._finalize(state)

    # ------------------------------------------------------------------
    def import_eml_bytes(self, data: bytes, source_name: str = "message.eml",
                         import_id: Optional[str] = None) -> ImportBatchResult:
        state = self._new_state(import_id)
        state.result.source_type = "eml"
        state.result.uploaded_bytes = len(data or b"")
        self._process_eml_into(state, source_name, data or b"")
        return self._finalize(state)

    def import_eml_uploads(self, items: Iterable[tuple[str, bytes]],
                           import_id: Optional[str] = None) -> ImportBatchResult:
        materialized = list(items or [])
        state = self._new_state(import_id)
        state.result.source_type = "eml"
        state.result.uploaded_bytes = sum(len(data or b"") for _, data in materialized)
        for filename, data in materialized:
            self._process_eml_into(state, filename, data or b"")
        return self._finalize(state)

    def import_upload_batch(self, uploads: Iterable[UploadSource | str | Path],
                            import_id: Optional[str] = None) -> ImportBatchResult:
        """Web 多文件上传唯一入口：N ZIP + M EML → 单一 import batch。

        使用共享的 import_id / seen_sha / uncompressed 预算，因此跨 ZIP、
        跨 EML/ZIP 去重和 max_files_per_batch 都是批次级的。
        """
        normalized: List[UploadSource] = []
        for item in (uploads or []):
            if isinstance(item, UploadSource):
                normalized.append(item)
            else:
                p = Path(item)
                normalized.append(UploadSource(path=p, filename=p.name))
        state = self._new_state(import_id)
        result = state.result
        result.uploaded_bytes = sum(int(u.size or 0) for u in normalized)
        result.source_type = self.source_type_for_suffixes(
            (u.suffix or Path(u.filename).suffix) for u in normalized)
        for upload in normalized:
            suffix = (upload.suffix or Path(upload.filename).suffix).lower()
            try:
                if suffix == ".zip":
                    if not Path(upload.path).exists() or not zipfile.is_zipfile(upload.path):
                        result.rejected += 1
                        result.items.append(ImportItem(
                            archive_relative_path=_safe_basename(upload.filename),
                            size=int(upload.size or 0), status="rejected",
                            reason="INVALID_ZIP"))
                        continue
                    self._process_zip_into(state, upload.path)
                elif suffix == ".eml":
                    self._process_eml_file_into(state, upload)
                else:
                    result.rejected += 1
                    result.items.append(ImportItem(
                        archive_relative_path=_safe_basename(upload.filename),
                        size=int(upload.size or 0), status="rejected",
                        reason="UNSUPPORTED_FILE_TYPE"))
            except ImportSecurityError as exc:
                # 安全攻击型错误：记录为 rejected / security failure，
                # 但绝不继续读取该 ZIP 的危险内容，也不把本机路径带入页面。
                reason = str(exc)[:200]
                result.security_failures += 1
                result.rejected += 1
                result.errors.append(reason)
                result.items.append(ImportItem(
                    archive_relative_path=_safe_basename(upload.filename),
                    size=int(upload.size or 0), status="rejected", reason=reason))
            except (OSError, zipfile.BadZipFile):
                result.rejected += 1
                result.items.append(ImportItem(
                    archive_relative_path=_safe_basename(upload.filename),
                    size=int(upload.size or 0), status="rejected",
                    reason="INVALID_ZIP"))
        return self._finalize(state)

    def import_path(self, path: str | Path,
                    import_id: Optional[str] = None) -> ImportBatchResult:
        p = Path(path)
        if p.is_dir():
            return self.import_directory(p, import_id=import_id)
        if p.suffix.lower() == ".eml":
            state = self._new_state(import_id)
            state.result.source_type = "eml"
            upload = UploadSource(path=p, filename=p.name)
            state.result.uploaded_bytes = int(upload.size or 0)
            self._process_eml_file_into(state, upload)
            return self._finalize(state)
        return self.import_zip(p, import_id=import_id)

def load_workbench_import_config(config_dir: Path | str | None = None) -> WorkbenchImportConfig:
    return load_workbench_config(config_dir)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.workbench.import_service")
    parser.add_argument("--zip", dest="zip_path", help="ZIP 文件路径")
    parser.add_argument("--dir", dest="dir_path", help="本地目录路径")
    parser.add_argument("--staging", dest="staging_root", default=None)
    parser.add_argument("--import-id", default=None)
    args = parser.parse_args(argv)
    if not args.zip_path and not args.dir_path:
        parser.error("需要 --zip 或 --dir")
    service = ImportService(staging_root=args.staging_root)
    target = args.zip_path or args.dir_path
    try:
        result = service.import_path(target, import_id=args.import_id)
    except ImportSecurityError as exc:
        print(f"IMPORT BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
