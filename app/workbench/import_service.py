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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        self.staging_root = Path(staging_root) if staging_root else DEFAULT_STAGING_ROOT
        self.db_path = Path(db_path) if db_path else Path(DB_PATH)
        self.staging_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def _new_import_dir(self, import_id: Optional[str] = None) -> Tuple[str, Path]:
        iid = import_id or f"IMP-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        d = self.staging_root / iid
        (d / "files").mkdir(parents=True, exist_ok=True)
        return iid, d

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
            "items": [it.to_dict() for it in result.items],
            "errors": list(result.errors),
        }
        path = Path(result.staging_dir) / "manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result.manifest_path = str(path)

    def _stage_bytes(self, import_dir: Path, data: bytes, original_name: str) -> Tuple[str, str]:
        sha = _sha256_bytes(data)
        target_dir = import_dir / "files" / sha
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / _safe_basename(original_name)
        if not target.exists():
            target.write_bytes(data)
        return sha, str(target)

    # ------------------------------------------------------------------
    def import_zip(self, zip_path: str | Path,
                   import_id: Optional[str] = None) -> ImportBatchResult:
        zip_path = Path(zip_path)
        if not zip_path.exists() or not zipfile.is_zipfile(zip_path):
            raise ImportSecurityError("INVALID_ZIP")
        iid, import_dir = self._new_import_dir(import_id)
        result = ImportBatchResult(import_id=iid, staging_dir=str(import_dir))
        seen_sha: Set[str] = set()
        total_uncompressed = 0

        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                name = info.filename
                if not name:
                    continue
                try:
                    normalized = _validate_member_path(name)
                except ImportSecurityError:
                    raise
                if info.is_dir() or normalized.endswith("/"):
                    continue
                if _is_ignored_entry(normalized):
                    continue
                depth = _entry_depth(normalized)
                if not _is_eml_path(normalized):
                    continue
                result.discovered += 1
                if result.discovered > self.max_files_per_batch:
                    item = ImportItem(archive_relative_path=normalized, depth=depth,
                                      size=int(info.file_size or 0), status="rejected",
                                      reason="MAX_FILES_EXCEEDED")
                    result.rejected += 1
                    result.items.append(item)
                    continue
                item = ImportItem(archive_relative_path=normalized, depth=depth,
                                  size=int(info.file_size or 0), status="accepted")
                if depth > self.max_nesting_depth:
                    item.status = "rejected"
                    item.reason = "NESTING_DEPTH_EXCEEDED"
                    result.rejected += 1
                    result.items.append(item)
                    continue
                if info.file_size > self.max_file_size:
                    item.status = "invalid"
                    item.reason = "FILE_TOO_LARGE"
                    result.invalid += 1
                    result.items.append(item)
                    continue
                total_uncompressed += int(info.file_size or 0)
                if total_uncompressed > self.max_total_size:
                    item.status = "rejected"
                    item.reason = "TOTAL_SIZE_EXCEEDED"
                    result.rejected += 1
                    result.items.append(item)
                    continue
                compressed = max(1, int(info.compress_size or 0))
                ratio = float(info.file_size or 0) / compressed
                if ratio > self.max_compression_ratio:
                    item.status = "rejected"
                    item.reason = "COMPRESSION_RATIO_EXCEEDED"
                    result.rejected += 1
                    result.items.append(item)
                    continue
                try:
                    data = zf.read(info)
                except Exception as exc:  # noqa: BLE001
                    item.status = "invalid"
                    item.reason = f"ZIP_READ_ERROR: {exc}"
                    result.invalid += 1
                    result.items.append(item)
                    continue
                sha = _sha256_bytes(data)
                item.raw_sha256 = sha
                if sha in seen_sha:
                    item.status = "duplicate"
                    item.reason = "DUPLICATE_RAW_EML_SHA256"
                    result.duplicates += 1
                    result.items.append(item)
                    continue
                seen_sha.add(sha)
                if not _is_valid_eml_bytes(data):
                    item.status = "invalid"
                    item.reason = "INVALID_EML"
                    result.invalid += 1
                    result.items.append(item)
                    continue
                _, staged = self._stage_bytes(import_dir, data, normalized)
                item.status = "accepted"
                item.staged_path = staged
                result.accepted += 1
                result.items.append(item)

        self._write_manifest(result)
        return result

    # ------------------------------------------------------------------
    def import_directory(self, root_dir: str | Path,
                         import_id: Optional[str] = None) -> ImportBatchResult:
        root = Path(root_dir)
        if not root.exists() or not root.is_dir():
            raise ImportSecurityError("INVALID_DIRECTORY")
        iid, import_dir = self._new_import_dir(import_id)
        result = ImportBatchResult(import_id=iid, staging_dir=str(import_dir))
        seen_sha: Set[str] = set()
        # 目录导入先完整发现，再按深度拒绝并计数。
        for path in discover_eml_files(root, recursive=self.recursive, max_depth=10 ** 6):
            rel = path.relative_to(root).as_posix()
            depth = len(path.relative_to(root).parts) - 1
            item = ImportItem(archive_relative_path=rel, depth=depth,
                              size=path.stat().st_size, status="accepted")
            result.discovered += 1
            if depth > self.max_nesting_depth:
                item.status = "rejected"
                item.reason = "NESTING_DEPTH_EXCEEDED"
                result.rejected += 1
                result.items.append(item)
                continue
            data = path.read_bytes()
            sha = _sha256_bytes(data)
            item.raw_sha256 = sha
            if sha in seen_sha:
                item.status = "duplicate"
                item.reason = "DUPLICATE_RAW_EML_SHA256"
                result.duplicates += 1
                result.items.append(item)
                continue
            seen_sha.add(sha)
            if not _is_valid_eml_bytes(data):
                item.status = "invalid"
                item.reason = "INVALID_EML"
                result.invalid += 1
                result.items.append(item)
                continue
            _, staged = self._stage_bytes(import_dir, data, path.name)
            item.staged_path = staged
            result.accepted += 1
            result.items.append(item)
        self._write_manifest(result)
        return result

    # ------------------------------------------------------------------
    def import_eml_bytes(self, data: bytes, source_name: str = "message.eml",
                         import_id: Optional[str] = None) -> ImportBatchResult:
        iid, import_dir = self._new_import_dir(import_id)
        result = ImportBatchResult(import_id=iid, staging_dir=str(import_dir))
        result.discovered = 1
        item = ImportItem(archive_relative_path=_safe_basename(source_name),
                          size=len(data), status="accepted")
        if not _is_valid_eml_bytes(data):
            item.status = "invalid"
            item.reason = "INVALID_EML"
            result.invalid = 1
            result.items.append(item)
            self._write_manifest(result)
            return result
        sha, staged = self._stage_bytes(import_dir, data, source_name)
        item.raw_sha256 = sha
        item.staged_path = staged
        result.accepted = 1
        result.items.append(item)
        self._write_manifest(result)
        return result

    def import_eml_uploads(self, items: Iterable[tuple[str, bytes]],
                           import_id: Optional[str] = None) -> ImportBatchResult:
        iid, import_dir = self._new_import_dir(import_id)
        result = ImportBatchResult(import_id=iid, staging_dir=str(import_dir))
        seen_sha: Set[str] = set()
        for filename, data in items:
            if result.discovered >= self.max_files_per_batch:
                item = ImportItem(archive_relative_path=_safe_basename(filename), status="rejected",
                                  reason="MAX_FILES_EXCEEDED")
                result.rejected += 1
                result.items.append(item)
                continue
            result.discovered += 1
            item = ImportItem(archive_relative_path=_safe_basename(filename),
                              size=len(data), status="accepted")
            if len(data) > self.max_file_size:
                item.status = "invalid"
                item.reason = "FILE_TOO_LARGE"
                result.invalid += 1
                result.items.append(item)
                continue
            sha = _sha256_bytes(data)
            item.raw_sha256 = sha
            if sha in seen_sha:
                item.status = "duplicate"
                item.reason = "DUPLICATE_RAW_EML_SHA256"
                result.duplicates += 1
                result.items.append(item)
                continue
            seen_sha.add(sha)
            if not _is_valid_eml_bytes(data):
                item.status = "invalid"
                item.reason = "INVALID_EML"
                result.invalid += 1
                result.items.append(item)
                continue
            _, staged = self._stage_bytes(import_dir, data, filename)
            item.staged_path = staged
            result.accepted += 1
            result.items.append(item)
        self._write_manifest(result)
        return result

    def import_path(self, path: str | Path,
                    import_id: Optional[str] = None) -> ImportBatchResult:
        p = Path(path)
        if p.is_dir():
            return self.import_directory(p, import_id=import_id)
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
