"""V5.0 Workbench 数据模型：Job 运行时配置、状态常量。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

APP_VERSION = "5.0"

IMPORT_STATUSES = {"UPLOADING", "READY", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"}
JOB_STATUSES = {"PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCEL_REQUESTED", "CANCELLED", "INTERRUPTED"}
JOB_ITEM_STATUSES = {"PENDING", "RUNNING", "COMPLETED", "FAILED", "DUPLICATE", "SKIPPED"}


@dataclass
class JobRuntimeConfig:
    """创建 Job 时的不可变运行配置快照；绝不包含 API Key。"""
    llm_enabled: bool = True
    llm_mode: str = "template"          # off / template / api
    llm_profile_id: str = "template"
    llm_model: str = ""
    llm_base_url: str = ""
    min_priority: str = "D"
    release_advisor_enabled: bool = True
    named_advisor_enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JobRuntimeConfig":
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})


@dataclass
class ImportBatch:
    import_id: str
    status: str = "READY"
    total_files: int = 0
    accepted_files: int = 0
    rejected_files: int = 0
    duplicate_files: int = 0
    total_bytes: int = 0
    created_at: str = ""
    finished_at: str = ""
    source_type: str = "upload"
    error_summary: str = ""


@dataclass
class AnalysisJob:
    job_id: str
    import_id: str = ""
    status: str = "PENDING"
    total_count: int = 0
    processed_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    duplicate_count: int = 0
    s_count: int = 0
    a_count: int = 0
    b_count: int = 0
    c_count: int = 0
    d_count: int = 0
    llm_profile_id: str = ""
    llm_enabled: int = 0
    llm_mode: str = ""
    llm_model: str = ""
    runtime_config_json: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    cancel_requested: int = 0
    current_filename: str = ""
    last_error: str = ""
