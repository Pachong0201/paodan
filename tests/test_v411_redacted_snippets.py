# -*- coding: utf-8 -*-
"""V4.1.1 P1-4: REDACTED_SNIPPETS 必须保护中文姓名/来源身份/PII。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.client import LLMClient
from app.models import LLMResult, RuleResult
from app.security.payload_builder import SafePayloadBuilder
from app.security.policy import SecurityPolicy


class _Resp:
    status_code = 200
    text = ""
    def raise_for_status(self):
        return None
    def json(self):
        return {"choices": [{"message": {"content": '{"ok": 1}'}}]}


CANARIES = [
    "王小明", "陈先生", "李○○", "John Smith",
    "secret.source.2026@example.com", "0912345678",
    "line-source-secret-8899", "地址：台北市信义区松高路1号",
    "<super-secret-message@example.com>", r"D:\Secret\Whistleblower\raw.eml",
    "/home/private/source/raw.eml", "812345678901234567",
]


def _policy():
    return SecurityPolicy(privacy_level="REDACTED_SNIPPETS", allow_safe_snippets=True,
                          external_llm_enabled=True)


def test_redacted_snippets_remove_names_and_pii():
    rule = RuleResult(matched_categories=["A03"],
                      evidence_snippets=["、".join(CANARIES)])
    llm = LLMResult(evidence_items=["王小明昨天告诉我，陈先生提供附件，李○○补充说明。"])
    p = SafePayloadBuilder(_policy()).build_v1(rule=rule, llm=llm, email_id="MID:x")
    blob = " ".join(p.safe_snippets)
    for c in CANARIES:
        assert c not in blob, (c, blob)


def test_final_external_http_body_has_no_redacted_snippet_leak():
    pol = _policy()
    rule = RuleResult(matched_categories=["A03"],
                      evidence_snippets=["、".join(CANARIES)])
    payload = SafePayloadBuilder(pol).build_v1(rule=rule, email_id="MID:x")
    client = LLMClient("sk-test-token", "https://api.openai.com/v1", "gpt-test", policy=pol)
    with patch("app.llm.client.requests.post", return_value=_Resp()) as post:
        out = client.chat_safe("系统提示", payload)
    assert out == {"ok": 1}
    body = post.call_args.kwargs["json"]["messages"][1]["content"]
    for c in CANARIES:
        assert c not in body, (c, body)
    assert "safe_snippets" in body


def test_production_default_remains_structured_only():
    assert SecurityPolicy().privacy_level == "STRUCTURED_ONLY"
