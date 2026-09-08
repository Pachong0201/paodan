# -*- coding: utf-8 -*-
"""External LLM Privacy Gateway 端到端离线测试（全部 mock requests.post）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.client import LLMClient
from app.security.models import SafeLLMPayload, SecurityBlockedError
from app.security.policy import load_security_policy


class _Resp:
    status_code = 200
    text = ""
    def raise_for_status(self):
        return None
    def json(self):
        return {"choices": [{"message": {"content": '{"ok": 1}'}}]}


def _client(url: str):
    return LLMClient("sk-test-token", url, "gpt-test",
                     policy=load_security_policy(config_dir="config"))


def test_external_raw_chat_json_blocks_network():
    c = _client("https://api.openai.com/v1")
    with patch("app.llm.client.requests.post", return_value=_Resp()) as post:
        try:
            c.chat_json("sys", "RAW_PRIVATE_CANARY_9F72C31A")
            assert False
        except SecurityBlockedError:
            pass
    assert post.call_count == 0


def test_unknown_host_blocks_network():
    c = _client("https://evil.example.com/v1")
    with patch("app.llm.client.requests.post", return_value=_Resp()) as post:
        try:
            c.chat_safe("sys", SafeLLMPayload())
            assert False
        except SecurityBlockedError:
            pass
    assert post.call_count == 0


def test_http_external_blocks_network():
    c = _client("http://api.openai.com/v1")
    with patch("app.llm.client.requests.post", return_value=_Resp()) as post:
        try:
            c.chat_safe("sys", SafeLLMPayload())
            assert False
        except SecurityBlockedError:
            pass
    assert post.call_count == 0


def test_localhost_raw_allowed_offline():
    c = _client("http://127.0.0.1:11434/v1")
    with patch("app.llm.client.requests.post", return_value=_Resp()) as post:
        out = c.chat_json("sys", "local raw text")
    assert out == {"ok": 1}
    assert post.call_count == 1


def test_external_safe_body_has_no_canary():
    c = _client("https://api.openai.com/v1")
    safe = SafeLLMPayload(email_ref="REF:x", political_categories=["A03"],
                          safe_snippets=[])
    with patch("app.llm.client.requests.post", return_value=_Resp()) as post:
        out = c.chat_safe("sys", safe)
    assert out == {"ok": 1}
    body = post.call_args.kwargs["json"]
    content = body["messages"][1]["content"]
    for canary in ("RAW_PRIVATE_CANARY_9F72C31A", "ATTACHMENT_PRIVATE_CANARY_E817AC21",
                   "secret.source.2026@example.com", "812345678901234567",
                   "super-secret-message", r"D:\Secret", "/home/private"):
        assert canary not in content
    assert "body_text" not in content and "combined_text" not in content


def test_external_safe_payload_with_canary_blocks():
    c = _client("https://api.openai.com/v1")
    safe = SafeLLMPayload(safe_snippets=["RAW_PRIVATE_CANARY_9F72C31A"])
    with patch("app.llm.client.requests.post", return_value=_Resp()) as post:
        try:
            c.chat_safe("sys", safe)
            assert False
        except SecurityBlockedError:
            pass
    assert post.call_count == 0


def test_v1_pipeline_external_http_body_has_no_raw_email(tmp_path):
    """V1 external 链路：HTTP body 只能来自 SafeLLMPayload，不得包含正文/附件/sender。"""
    import json
    from pathlib import Path
    from app.config import NEWS_SIGNAL_DIR
    from app.llm.screener import LLMScreener
    from app.pipeline.screening_pipeline import ScreeningPipeline
    from app.rules.config_loader import RuleConfig

    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    screener = LLMScreener(cfg, mode="template")
    pol = load_security_policy(config_dir="config")
    screener.mode = "api"
    screener.client = LLMClient("sk-test-token", "https://api.openai.com/v1", "gpt-test",
                                policy=pol)
    pipe = ScreeningPipeline(cfg, db=None, llm_screener=screener, allow_llm=True)
    eml = Path("tests/fixtures/emails/hv01_headcount.eml")
    doc = __import__("app.parsers", fromlist=["parse_eml"]).parse_eml(eml)
    rule = pipe.rule_engine.evaluate(doc.body_text)
    template_out = screener._screen_template(doc.body_text, doc.subject, doc.sender, rule)
    resp = _Resp()
    resp.json = lambda: {"choices": [{"message": {"content": json.dumps(template_out, ensure_ascii=False)}}]}
    with patch("app.llm.client.requests.post", return_value=resp) as post:
        rec = pipe.process_file(eml)
    assert rec is not None
    assert post.call_count == 1
    content = post.call_args.kwargs["json"]["messages"][1]["content"]
    for bad in ("body_text", "combined_text", "source_path", "sender", "message_id",
                "attachment_text", "@example.com"):
        assert bad not in content


def test_security_audit_log_has_no_canary(tmp_path):
    from app.security.audit import SecurityAuditLogger
    from app.security.policy import SecurityPolicy

    policy = SecurityPolicy(audit_enabled=True, audit_log_path=str(tmp_path / "audit.jsonl"))
    logger = SecurityAuditLogger(policy)
    logger.log(event="test", email_ref_hash="RAW_PRIVATE_CANARY_9F72C31A",
               purpose="secret.source.2026@example.com",
               provider_host="evil.example.com", model="sk-super-secret-test-token",
               result="blocked", reason_codes=["RAW_PRIVATE_CANARY_9F72C31A"])
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    for canary in ("RAW_PRIVATE_CANARY_9F72C31A", "secret.source.2026@example.com",
                   "sk-super-secret-test-token"):
        assert canary not in text
