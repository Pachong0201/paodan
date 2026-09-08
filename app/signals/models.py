"""UnifiedSignalSet：V1 Political + V4 Governance 的统一语义载体。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List


class Track(str, Enum):
    POLITICAL = "POLITICAL"
    GOVERNANCE = "GOVERNANCE"
    MIXED = "MIXED"
    NONE = "NONE"

    @classmethod
    def parse(cls, value: Any) -> "Track":
        raw = str(value or "").strip().upper()
        try:
            return cls(raw)
        except ValueError:
            return cls.NONE


@dataclass
class UnifiedSignalSet:
    email_id: str = ""
    political_categories: List[str] = field(default_factory=list)
    governance_categories: List[str] = field(default_factory=list)

    political_patterns: List[str] = field(default_factory=list)
    governance_patterns: List[str] = field(default_factory=list)

    political_score: float = 0.0
    governance_score: float = 0.0

    primary_track: str = Track.NONE.value
    secondary_track: str = ""

    entities: List[Dict[str, Any]] = field(default_factory=list)
    money: List[Dict[str, Any]] = field(default_factory=list)

    evidence_shapes: List[str] = field(default_factory=list)
    evidence_stage: str = "E1"

    risk_flags: List[str] = field(default_factory=list)

    old_news: List[str] = field(default_factory=list)
    new_information: List[str] = field(default_factory=list)

    public_interest: bool = False
    track_confidence: float = 0.0

    # 便于下游直接消费的融合结果
    final_score: float = 0.0
    priority: str = "D"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def all_categories(self) -> List[str]:
        out: List[str] = []
        for c in list(self.political_categories) + list(self.governance_categories):
            if c and c not in out:
                out.append(c)
        return out

    @property
    def has_governance(self) -> bool:
        return bool(self.governance_categories)

    @property
    def has_political(self) -> bool:
        return bool(self.political_categories)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UnifiedSignalSet":
        allowed = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})
