# -*- coding: utf-8 -*-
"""--reprocess / stale analysis 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.llm.screener import LLMScreener
from app.pipeline.screening_pipeline import ScreeningPipeline
from app.rules.config_loader import RuleConfig
from app.storage.database import Database


def _mk_eml(tmp_path: Path) -> Path:
    p = tmp_path / "x.eml"
    p.write_text("From: a@example.com\nTo: t@example.com\nSubject: test\n"
                 "Message-ID: <reprocess@example.com>\n\nbody", encoding="utf-8")
    return p


def test_reprocess_creates_new_analysis_run(tmp_path):
    db = Database(tmp_path / "x.db")
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=db, llm_screener=LLMScreener(cfg, mode="template"),
                             allow_llm=False)
    eml = _mk_eml(tmp_path)
    assert pipe.process_file(eml) is not None
    assert len(db.query("SELECT * FROM analysis_runs")) == 1
    # 已导入且 up-to-date -> 跳过
    assert pipe.process_file(eml) is None
    assert len(db.query("SELECT * FROM analysis_runs")) == 1
    # --reprocess -> 新 analysis_run
    assert pipe.process_file(eml, reprocess=True) is not None
    assert len(db.query("SELECT * FROM analysis_runs")) == 2
    db.close()


def test_stale_analysis_auto_reanalyzed(tmp_path):
    db = Database(tmp_path / "x.db")
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=db, llm_screener=LLMScreener(cfg, mode="template"),
                             allow_llm=False)
    eml = _mk_eml(tmp_path)
    assert pipe.process_file(eml) is not None
    db.execute("UPDATE analysis_runs SET political_rule_pack_hash='stale' "
               "WHERE id=(SELECT MAX(id) FROM analysis_runs)")
    assert pipe.process_file(eml) is not None
    assert len(db.query("SELECT * FROM analysis_runs")) == 2
    db.close()
