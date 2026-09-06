# -*- coding: utf-8 -*-
"""pytest 共享 fixture：规则包加载与样本期望。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIX_DIR = Path(__file__).resolve().parent / "fixtures"
EML_DIR = FIX_DIR / "emails"
EXP_FILE = FIX_DIR / "expectations.jsonl"


@pytest.fixture(scope="session")
def rule_config():
    from app.config import NEWS_SIGNAL_DIR
    from app.rules.config_loader import RuleConfig
    return RuleConfig(NEWS_SIGNAL_DIR).load_all()


@pytest.fixture(scope="session")
def expectations():
    exps = {}
    if EXP_FILE.exists():
        for line in EXP_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                exps[r["id"]] = r
    return exps


@pytest.fixture(scope="session")
def sample_emls():
    return sorted(EML_DIR.glob("*.eml"))


@pytest.fixture(scope="session")
def pipeline(rule_config):
    from app.llm.screener import LLMScreener
    from app.pipeline.screening_pipeline import ScreeningPipeline
    screener = LLMScreener(rule_config, mode="template")
    return ScreeningPipeline(rule_config, db=None, llm_screener=screener, allow_llm=False)


@pytest.fixture(scope="session")
def all_records(pipeline, sample_emls):
    """跑完全部 60 条样本（一次，供多个测试复用）。"""
    recs = {}
    for eml in sample_emls:
        rec = pipeline.process_file(eml)
        if rec is not None and rec.score is not None:
            recs[eml.stem] = rec
    return recs
