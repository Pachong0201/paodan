# -*- coding: utf-8 -*-
"""V4.1.1 P0-2: External privacy BLOCK must degrade gracefully, not break V1 pipeline."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.llm.client import LLMClient
from app.llm.screener import LLMScreener
from app.pipeline.screening_pipeline import ScreeningPipeline
from app.rules.config_loader import RuleConfig
from app.security.models import SafeLLMPayload
from app.security.payload_builder import SafePayloadBuilder
from app.security.policy import load_security_policy

EML = Path("tests/fixtures/emails/hv01_headcount.eml")


def _pipe(url: str, monkeypatch=None, inject_canary: bool = False):
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    screener = LLMScreener(cfg, mode="template")
    pol = load_security_policy(config_dir="config")
    screener.mode = "api"
    screener.client = LLMClient("sk-test-token", url, "gpt-test", policy=pol)
    if inject_canary and monkeypatch is not None:
        original = SafePayloadBuilder.build_v1

        def _inject(self, *args, **kwargs):
            return SafeLLMPayload(email_ref="REF:x",
                                  safe_snippets=["RAW_PRIVATE_CANARY_9F72C31A"])

        monkeypatch.setattr(SafePayloadBuilder, "build_v1", _inject)
    return ScreeningPipeline(cfg, db=None, llm_screener=screener, allow_llm=True)


def _assert_degraded(rec):
    assert rec is not None
    assert rec.error == ""
    assert rec.score is not None
    assert rec.summary_zh
    assert rec.verification_targets is not None
    assert rec.llm is not None
    assert rec.llm.llm_status == "blocked"


def test_privacy_canary_payload_block_degrades(monkeypatch):
    pipe = _pipe("https://api.openai.com/v1", monkeypatch=monkeypatch, inject_canary=True)
    with patch("app.llm.client.requests.post") as post:
        rec = pipe.process_file(EML)
    assert post.call_count == 0
    _assert_degraded(rec)


def test_unknown_host_block_degrades():
    pipe = _pipe("https://evil.example.com/v1")
    with patch("app.llm.client.requests.post") as post:
        rec = pipe.process_file(EML)
    assert post.call_count == 0
    _assert_degraded(rec)


def test_http_external_block_degrades():
    pipe = _pipe("http://api.openai.com/v1")
    with patch("app.llm.client.requests.post") as post:
        rec = pipe.process_file(EML)
    assert post.call_count == 0
    _assert_degraded(rec)
