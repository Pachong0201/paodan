"""安全 ViewModel：绝不放 raw body、sender、message_id、source_path、cached_path。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import yaml

from .config import ROOT, REVIEW_STATUSES

REVIEW_STATUS_LABELS = {
    "UNREVIEWED": "未审核",
    "VERIFY": "待核查",
    "PRIORITY": "重点跟进",
    "VERIFIED": "已核实",
    "LOW_VALUE": "价值有限",
    "FALSE_POSITIVE": "误报",
    "ARCHIVED": "已归档",
}
TRACK_LABELS = {
    "POLITICAL": "政治爆料",
    "GOVERNANCE": "治理民生",
    "MIXED": "双轨",
    "NONE": "未归类",
}
PRIORITY_RANK = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}

_CATEGORY_LABELS: Optional[Dict[str, str]] = None


def _load_category_labels() -> Dict[str, str]:
    global _CATEGORY_LABELS
    if _CATEGORY_LABELS is not None:
        return _CATEGORY_LABELS
    labels: Dict[str, str] = {}
    try:
        tax = yaml.safe_load((ROOT / "config/news_signal/taxonomy.yaml").read_text(encoding="utf-8"))
        for c in (tax or {}).get("categories", []):
            labels[str(c.get("id"))] = str(c.get("name") or c.get("id"))
    except Exception:
        pass
    try:
        gov = yaml.safe_load((ROOT / "config/news_signal/governance_complaints.yaml").read_text(encoding="utf-8"))
        for c in (gov or {}).get("categories", []):
            labels[str(c.get("id"))] = str(c.get("name") or c.get("id"))
    except Exception:
        pass
    _CATEGORY_LABELS = labels
    return labels


def category_label(cid: str) -> str:
    cid = str(cid or "").strip().upper()
    labels = _load_category_labels()
    name = labels.get(cid)
    return f"{cid} {name}" if name else cid


def route_name(route: str) -> str:
    names = {
        "R1": "社交平台首发型",
        "R2": "媒体独家型",
        "R3": "深度调查报道型",
        "R4": "记者会/公开展示型",
        "R5": "正式检举优先型",
        "R6": "高敏感核验型",
    }
    return f"{route} {names.get(route, '')}".strip()


@dataclass
class ReviewState:
    email_id: str = ""
    review_status: str = "UNREVIEWED"
    editor_note: str = ""
    reviewed_at: str = ""
    updated_at: str = ""
    created_at: str = ""

    @property
    def status_label(self) -> str:
        return REVIEW_STATUS_LABELS.get(self.review_status, self.review_status)


@dataclass
class EmailListItem:
    email_id: str
    subject: str
    priority: str
    final_score: float
    primary_track: str
    political_categories: List[str] = field(default_factory=list)
    governance_categories: List[str] = field(default_factory=list)
    summary: str = ""
    review_status: str = "UNREVIEWED"
    review_status_label: str = "未审核"
    processed_at: str = ""
    in_system_review_queue: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DashboardStats:
    today_total: int = 0
    today_sa: int = 0
    today_governance: int = 0
    pending_review: int = 0
    recent7_total: int = 0
    recent7_sab: int = 0
    track_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AttachmentInfo:
    filename: str = ""
    file_type: str = ""
    extraction_status: str = ""
    warnings_count: int = 0


@dataclass
class NamedChannelInfo:
    group: str = ""
    entity_name: str = ""
    entity_type: str = ""
    fit_score: float = 0.0
    reason: str = ""
    roles: List[str] = field(default_factory=list)


@dataclass
class EmailDetail:
    email_id: str
    subject: str
    processed_at: str = ""
    priority: str = ""
    final_score: float = 0.0
    primary_track: str = ""
    political_score: float = 0.0
    governance_score: float = 0.0
    political_categories: List[str] = field(default_factory=list)
    governance_categories: List[str] = field(default_factory=list)
    political_patterns: List[str] = field(default_factory=list)
    governance_patterns: List[str] = field(default_factory=list)
    summary: str = ""
    reason: str = ""
    evidence_stage: str = ""
    evidence_shapes: List[str] = field(default_factory=list)
    money_facts: List[Dict[str, Any]] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    pattern_ids: List[str] = field(default_factory=list)
    verification_targets: List[str] = field(default_factory=list)
    release: Optional[Dict[str, Any]] = None
    named_channels: List[NamedChannelInfo] = field(default_factory=list)
    attachments: List[AttachmentInfo] = field(default_factory=list)
    review: ReviewState = field(default_factory=ReviewState)
    in_system_review_queue: bool = False
    analysis_pipeline_version: str = ""
    analysis_finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["named_channels"] = [asdict(x) for x in self.named_channels]
        d["attachments"] = [asdict(x) for x in self.attachments]
        return d
