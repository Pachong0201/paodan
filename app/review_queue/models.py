"""V3.2 Review Queue 数据模型（轻量 DTO，不重复定义 ScreeningRecord）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class QueuedItem:
    review_key: str = ""
    priority: str = ""
    final_score: float = 0.0
    queue_path: str = ""
    sidecar_path: str = ""
    status: str = ""  # added/deduped/moved/removed/skipped/error
    error: str = ""


@dataclass
class QueueBatchStats:
    total: int = 0
    queued: int = 0
    added: int = 0
    deduped: int = 0
    removed: int = 0
    skipped: int = 0
    errors: int = 0
    paths: Dict[str, str] = field(default_factory=dict)  # review_key -> queue .eml path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "queued": self.queued,
            "added": self.added,
            "deduped": self.deduped,
            "removed": self.removed,
            "skipped": self.skipped,
            "errors": self.errors,
            "paths": dict(self.paths),
        }


__all__ = ["QueuedItem", "QueueBatchStats"]
