# -*- coding: utf-8 -*-
"""Governance data models."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class GovKeywordHit:
    term: str
    ktype: str  # G-H/G-M/G-C/G-E/G-X/REPEAT/GROUP_IMPACT/LOSS/FAILED_COMPLAINT
    category: str  # G01..G12 or GLOBAL
    count: int = 1
    snippet: str = ""

    def to_dict(self) -> dict:
        return {"term": self.term, "ktype": self.ktype, "category": self.category,
                "count": self.count, "snippet": self.snippet}


@dataclass
class GovPatternHit:
    pattern_id: str
    category: str
    name: str
    matched_terms: list = field(default_factory=list)
    evidence_snippets: list = field(default_factory=list)
    pattern_score: float = 0.0
    window: str = ""

    def to_dict(self) -> dict:
        return {"pattern_id": self.pattern_id, "category": self.category, "name": self.name,
                "matched_terms": self.matched_terms, "evidence_snippets": self.evidence_snippets,
                "pattern_score": self.pattern_score, "window": self.window}


@dataclass
class GovNegativeMatch:
    rule_id: str
    condition: str
    score_delta: float
    max_score: float | None
    matched_terms: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "condition": self.condition,
                "score_delta": self.score_delta, "max_score": self.max_score,
                "matched_terms": self.matched_terms}


@dataclass
class GovernanceResult:
    categories: list = field(default_factory=list)
    keywords: dict = field(default_factory=dict)  # G-H/G-M/G-C/G-E/G-X (+enhance)
    patterns: list = field(default_factory=list)  # GovPatternHit
    negatives: list = field(default_factory=list)  # GovNegativeMatch
    enhance: dict = field(default_factory=dict)  # REPEAT/GROUP_IMPACT/LOSS/FAILED_COMPLAINT hits
    score: float = 0.0
    priority: str = "D"
    dims: dict = field(default_factory=dict)
    details: list = field(default_factory=list)
    normalized: str = ""

    def to_dict(self) -> dict:
        return {
            "governance_categories": self.categories,
            "governance_keywords": {k: [h.to_dict() if hasattr(h, "to_dict") else h for h in v]
                                    for k, v in self.keywords.items()},
            "governance_patterns": [p.to_dict() if hasattr(p, "to_dict") else p for p in self.patterns],
            "governance_negatives": [n.to_dict() if hasattr(n, "to_dict") else n for n in self.negatives],
            "governance_enhance": self.enhance,
            "governance_score": self.score,
            "governance_priority": self.priority,
            "governance_dims": self.dims,
            "governance_details": self.details,
        }
