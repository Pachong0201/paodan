# -*- coding: utf-8 -*-
"""UnifiedSignalSet / primary_track / mixed track 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import FinalScore, RuleResult
from app.signals.merger import SignalMerger, UnifiedFinalScorer


def test_governance_only_track():
    merger = SignalMerger()
    us = merger.merge(political_categories=[], governance_categories=["G08"],
                      political_score=0, governance_score=80)
    assert us.primary_track == "GOVERNANCE"
    assert us.governance_categories == ["G08"]


def test_political_only_track():
    merger = SignalMerger()
    us = merger.merge(political_categories=["A03"], governance_categories=[],
                      political_score=80, governance_score=0)
    assert us.primary_track == "POLITICAL"


def test_mixed_track():
    merger = SignalMerger()
    us = merger.merge(political_categories=["A06"], governance_categories=["G01"],
                      political_score=75, governance_score=70)
    assert us.primary_track == "MIXED"
    assert us.political_categories == ["A06"]
    assert us.governance_categories == ["G01"]


def test_unified_final_score_does_not_suppress_governance():
    scorer = UnifiedFinalScorer()
    score, priority, reason = scorer.fuse(20, 88, political_categories=[],
                                          governance_categories=["G08"],
                                          governance_priority="A")
    assert score == 88
    assert priority == "A"
    assert "governance" in reason or "max" in reason
