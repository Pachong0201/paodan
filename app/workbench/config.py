"""Workbench Import 配置：默认 recursive=true。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


@dataclass
class WorkbenchImportConfig:
    recursive: bool = True
    max_nesting_depth: int = 10
    max_file_size: int = 50 * 1024 * 1024
    max_total_size: int = 500 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_file_size_mb: int = 50
    max_batch_size_mb: int = 500
    max_files_per_batch: int = 2000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recursive": self.recursive,
            "max_nesting_depth": self.max_nesting_depth,
            "max_file_size": self.max_file_size,
            "max_total_size": self.max_total_size,
            "max_compression_ratio": self.max_compression_ratio,
            "max_file_size_mb": self.max_file_size_mb,
            "max_batch_size_mb": self.max_batch_size_mb,
            "max_files_per_batch": self.max_files_per_batch,
        }


def load_workbench_config(config_dir: Path | str | None = None) -> WorkbenchImportConfig:
    base = Path(config_dir) if config_dir else CONFIG_DIR
    cfg = WorkbenchImportConfig()
    path = base / "workbench.yaml"
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        section = data.get("workbench", data) if isinstance(data, dict) else {}
        imp = section.get("import", section) if isinstance(section, dict) else {}
        if isinstance(imp, dict):
            if imp.get("recursive") is not None:
                cfg.recursive = str(imp["recursive"]).strip().lower() not in ("0", "false", "no", "off", "")
            for key in ("max_nesting_depth", "max_file_size", "max_total_size"):
                if imp.get(key) is not None:
                    try:
                        setattr(cfg, key, int(imp[key]))
                    except (TypeError, ValueError):
                        pass
            if imp.get("max_compression_ratio") is not None:
                try:
                    cfg.max_compression_ratio = float(imp["max_compression_ratio"])
                except (TypeError, ValueError):
                    pass
            for key in ("max_file_size_mb", "max_batch_size_mb", "max_files_per_batch"):
                if imp.get(key) is not None:
                    try:
                        setattr(cfg, key, int(imp[key]))
                    except (TypeError, ValueError):
                        pass
            # 字节字段优先；若只配置了 MB 字段，才换算为字节。
            # max_file_size 管单封 EML；max_total_size 管解压后总大小；
            # max_batch_size_mb 管整个 Web 上传请求的压缩文件总大小。
            if imp.get("max_file_size") is None:
                cfg.max_file_size = cfg.max_file_size_mb * 1024 * 1024
            if imp.get("max_total_size") is None:
                cfg.max_total_size = cfg.max_batch_size_mb * 1024 * 1024
    if os.getenv("WORKBENCH_IMPORT_RECURSIVE") is not None:
        cfg.recursive = str(os.getenv("WORKBENCH_IMPORT_RECURSIVE")).strip().lower() not in ("0", "false", "no", "off", "")
    if os.getenv("WORKBENCH_IMPORT_MAX_NESTING_DEPTH") is not None:
        try:
            cfg.max_nesting_depth = int(os.getenv("WORKBENCH_IMPORT_MAX_NESTING_DEPTH"))
        except ValueError:
            pass
    for env_name, attr in (
        ("WORKBENCH_IMPORT_MAX_FILE_SIZE_MB", "max_file_size_mb"),
        ("WORKBENCH_IMPORT_MAX_BATCH_SIZE_MB", "max_batch_size_mb"),
        ("WORKBENCH_IMPORT_MAX_FILES_PER_BATCH", "max_files_per_batch"),
        ("WORKBENCH_IMPORT_MAX_TOTAL_SIZE", "max_total_size"),
    ):
        raw = os.getenv(env_name)
        if raw is None:
            continue
        try:
            setattr(cfg, attr, int(raw))
        except (TypeError, ValueError):
            continue
    if os.getenv("WORKBENCH_IMPORT_MAX_FILE_SIZE_MB") is not None:
        cfg.max_file_size = cfg.max_file_size_mb * 1024 * 1024
    return cfg
