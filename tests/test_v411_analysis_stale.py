# -*- coding: utf-8 -*-
"""V4.1.1 P1-3: Analysis Versioning / stale detection."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.llm.screener import LLMScreener
from app.pipeline.screening_pipeline import ScreeningPipeline
from app.rules.config_loader import RuleConfig
from app.storage.database import Database


def _pipe(tmp_path):
    db = Database(tmp_path / "x.db")
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=db, llm_screener=LLMScreener(cfg, mode="template"),
                             allow_llm=False)
    eml = tmp_path / "x.eml"
    eml.write_text("From: a@example.com\nTo: t@example.com\nSubject: test\n"
                   "Message-ID: <stale@example.com>\n\nbody", encoding="utf-8")
    return pipe, db, eml


def _same_args(pipe, model="gpt-4o-mini"):
    return dict(
        political_rule_pack_hash=pipe.political_rule_pack_hash,
        governance_rule_pack_hash=pipe.governance_rule_pack_hash,
        scoring_rule_hash=pipe.scoring_rule_hash,
        prompt_hash=pipe.prompt_hash,
        pipeline_version=pipe.pipeline_version,
        llm_mode=pipe.llm_screener.mode,
        llm_provider=pipe._llm_provider(),
        llm_model=model,
        release_rule_hash=pipe.release_rule_hash,
        named_rule_hash=pipe.named_rule_hash,
        channel_entity_hash=pipe.channel_entity_hash,
    )


def test_stale_detection_dimensions(tmp_path):
    pipe, db, eml = _pipe(tmp_path)
    assert pipe.process_file(eml) is not None
    eid = "MID:" + __import__("hashlib").sha256(b"stale@example.com").hexdigest()
    assert pipe.repo.analysis_is_stale(eid, **_same_args(pipe)) is False
    assert pipe.repo.analysis_is_stale(eid, **{**_same_args(pipe), "llm_model": "model-B"}) is True
    assert pipe.repo.analysis_is_stale(eid, **{**_same_args(pipe), "prompt_hash": "old"}) is True
    assert pipe.repo.analysis_is_stale(eid, **{**_same_args(pipe), "release_rule_hash": "old"}) is True
    assert pipe.repo.analysis_is_stale(eid, **{**_same_args(pipe), "named_rule_hash": "old"}) is True
    assert pipe.repo.analysis_is_stale(eid, **{**_same_args(pipe), "channel_entity_hash": "old"}) is True
    db.close()


def test_model_change_triggers_auto_reanalysis(tmp_path):
    pipe, db, eml = _pipe(tmp_path)
    assert pipe.process_file(eml) is not None
    assert len(db.query("SELECT * FROM analysis_runs")) == 1
    db.execute("UPDATE analysis_runs SET llm_model='model-B'")
    assert pipe.process_file(eml) is not None
    assert len(db.query("SELECT * FROM analysis_runs")) == 2
    db.close()


def test_v2_v3_rule_change_triggers_auto_reanalysis(tmp_path):
    pipe, db, eml = _pipe(tmp_path)
    assert pipe.process_file(eml) is not None
    db.execute("UPDATE analysis_runs SET named_rule_hash='old'")
    assert pipe.process_file(eml) is not None
    assert len(db.query("SELECT * FROM analysis_runs")) == 2
    db.execute("UPDATE analysis_runs SET release_rule_hash='old' WHERE id=(SELECT MAX(id) FROM analysis_runs)")
    assert pipe.process_file(eml) is not None
    assert len(db.query("SELECT * FROM analysis_runs")) == 3
    db.close()


def test_same_pipeline_detects_rule_file_change(tmp_path, monkeypatch):
    import app.pipeline.screening_pipeline as sp
    pipe, db, eml = _pipe(tmp_path)
    assert pipe.process_file(eml) is not None
    monkeypatch.setattr(sp, "_named_rule_hash", lambda cfg: "changed-hash")
    assert pipe.process_file(eml) is not None
    assert len(db.query("SELECT * FROM analysis_runs")) == 2
    db.close()
