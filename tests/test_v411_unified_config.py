# -*- coding: utf-8 -*-
"""V4.1.1 P1-2: SignalMerger 与 UnifiedFinalScorer 必须共用同一阈值源。"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.signals.merger import SignalMerger, UnifiedFinalScorer


def test_merger_and_scorer_share_custom_thresholds(tmp_path):
    cfg = tmp_path / "unified_signals.yaml"
    cfg.write_text(yaml.safe_dump({
        "unified_signals": {
            "political_threshold": 55,
            "governance_threshold": 50,
            "strong_political_threshold": 70,
            "strong_governance_threshold": 68,
            "mixed_bonus": 9,
        }
    }, allow_unicode=True), encoding="utf-8")
    merger = SignalMerger(config_path=cfg)
    assert merger.thresholds["political_threshold"] == 55
    assert merger.thresholds["governance_threshold"] == 50
    assert merger.thresholds["mixed_bonus"] == 9
    scorer = UnifiedFinalScorer(thresholds=merger.thresholds)
    assert scorer.thresholds == merger.thresholds
    # Track 与 Fusion 使用同一组阈值：52 分低于 political_threshold=55 -> NONE。
    us = merger.merge(political_categories=["A03"], governance_categories=[],
                      political_score=52, governance_score=0)
    assert us.primary_track == "NONE"
    score, _, _ = scorer.fuse(60, 60, political_categories=["A03"],
                              governance_categories=["G01"])
    assert score == 69.0  # mixed_bonus=9 生效


def test_strong_threshold_used_for_mixed_confidence():
    merger = SignalMerger(thresholds={
        "political_threshold": 40, "governance_threshold": 40,
        "strong_political_threshold": 80, "strong_governance_threshold": 80,
        "mixed_bonus": 5,
    })
    low = merger.merge(political_categories=["A03"], governance_categories=["G01"],
                       political_score=60, governance_score=60)
    high = merger.merge(political_categories=["A03"], governance_categories=["G01"],
                        political_score=85, governance_score=85)
    assert high.track_confidence > low.track_confidence


def test_pipeline_merger_and_scorer_share_thresholds():
    from app.config import NEWS_SIGNAL_DIR
    from app.llm.screener import LLMScreener
    from app.pipeline.screening_pipeline import ScreeningPipeline
    from app.rules.config_loader import RuleConfig
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=None, llm_screener=LLMScreener(cfg, mode="template"),
                             allow_llm=False)
    assert pipe.merger.thresholds == pipe.unified_scorer.thresholds
