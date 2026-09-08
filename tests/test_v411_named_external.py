# -*- coding: utf-8 -*-
"""V4.1.1 P0-1: V3 External LLM Candidate Pool must be entity IDs, not NC rule IDs."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import EmailDocument, FinalScore, LLMResult, RuleResult
from app.named_channel.advisor import NamedChannelAdvisor
from app.security.policy import SecurityPolicy


class _FakeExternalClient:
    is_external = True

    def __init__(self, output):
        self.output = output
        self.calls = []
        self.policy = SecurityPolicy()

    def chat_safe(self, system_prompt, safe_payload, retries=1):
        self.calls.append(safe_payload)
        return self.output


def _advisor(output):
    adv = NamedChannelAdvisor(mode="template", allow_llm=True)
    adv.mode = "api"
    adv.allow_llm = True
    adv.client = _FakeExternalClient(output)
    return adv


def _inputs():
    doc = EmailDocument(email_id="v3x", subject="收贿金流", sender="a@example.com",
                        body_text="银行流水显示汇款80万元给某公司，另有LINE对话。",
                        combined_text="银行流水显示汇款80万元给某公司，另有LINE对话。")
    rule = RuleResult(matched_categories=["A03"], rule_score=90,
                      normalized="银行流水显示汇款80万元给某公司，另有line对话。")
    llm = LLMResult(categories=["A03"], evidence_items=["银行流水"], llm_status="template")
    score = FinalScore(final_score=88, priority="A")
    rel = {"primary_route": "R5", "secondary_routes": ["R3"]}
    return doc, rule, llm, score, rel


def test_candidate_pool_uses_entity_ids_and_llm_rerank_applies():
    out = {
        "recommended_media": [
            {"id": "media_udn", "name": "联合报", "fit_score": 99, "reason": "深度调查"},
            {"id": "media_mirror", "name": "镜周刊", "fit_score": 80, "reason": "调查"},
        ],
        "recommended_disclosure_actors": [],
        "recommended_amplifiers": [],
        "recommended_formal_channels": [],
        "recommended_platforms": [],
        "avoid_named_channels": [],
        "recommended_sequence": [],
    }
    adv = _advisor(out)
    doc, rule, llm, score, rel = _inputs()
    rec = adv.advise("v3x", rule=rule, llm=llm, score=score,
                     release_recommendation=rel, doc=doc)
    assert rec.source == "rule+llm"
    assert rec.llm_status == "ok"
    ids = [x["entity_id"] for x in rec.recommended_media]
    assert ids and ids[0] == "media_udn", ids
    assert rec.recommended_media[0]["fit_score"] == 99
    # 候选池校验参数必须是实体 ID；不能因为 rec.rule_hits 是 NC01/NC03 而误判。
    assert any("NC" in r for r in rec.rule_hits)


def test_out_of_pool_llm_output_rejected_and_rule_fallback():
    out = {
        "recommended_media": [{"id": "media_not_in_pool", "name": "不存在媒体",
                               "fit_score": 99, "reason": "x"}],
        "recommended_disclosure_actors": [],
        "recommended_amplifiers": [],
        "recommended_formal_channels": [],
        "recommended_platforms": [],
        "avoid_named_channels": [],
        "recommended_sequence": [],
    }
    adv = _advisor(out)
    doc, rule, llm, score, rel = _inputs()
    rec = adv.advise("v3x", rule=rule, llm=llm, score=score,
                     release_recommendation=rel, doc=doc)
    assert rec.source == "rule"
    assert rec.llm_status == "failed"
    all_ids = [x["entity_id"] for key in (
        "recommended_media", "recommended_disclosure_actors",
        "recommended_amplifiers", "recommended_formal_channels",
        "recommended_platforms") for x in (rec.to_dict().get(key) or [])]
    assert "media_not_in_pool" not in all_ids


def test_schema_only_allows_pool_entities():
    from app.named_channel.schema import validate_llm_output
    pool = {"media_mirror", "media_udn", "actor_huang_kuochang"}
    out = {"recommended_media": [{"id": "media_mirror", "fit_score": 90}],
           "recommended_disclosure_actors": [{"id": "actor_huang_kuochang"}],
           "recommended_amplifiers": [], "recommended_formal_channels": [],
           "recommended_platforms": [], "avoid_named_channels": [],
           "recommended_sequence": []}
    ok, cleaned, errors = validate_llm_output(out, pool)
    assert ok and not errors
    bad = dict(out)
    bad["recommended_media"] = [{"id": "media_not_in_pool"}]
    ok2, cleaned2, errors2 = validate_llm_output(bad, pool)
    assert not ok2 and cleaned2["recommended_media"] == []


def test_real_external_client_v3_rerank_works():
    import json
    from unittest.mock import patch
    from app.llm.client import LLMClient
    from app.security.policy import load_security_policy

    response = {
        "recommended_media": [{"id": "media_mirror", "name": "镜周刊",
                               "fit_score": 99, "reason": "调查"}],
        "recommended_disclosure_actors": [],
        "recommended_amplifiers": [],
        "recommended_formal_channels": [],
        "recommended_platforms": [],
        "avoid_named_channels": [],
        "recommended_sequence": [],
    }

    class _Resp:
        status_code = 200
        text = ""
        def raise_for_status(self):
            return None
        def json(self):
            return {"choices": [{"message": {"content": json.dumps(response, ensure_ascii=False)}}]}

    adv = NamedChannelAdvisor(mode="template", allow_llm=True)
    adv.mode = "api"
    adv.allow_llm = True
    adv.client = LLMClient("sk-test-token", "https://api.openai.com/v1", "gpt-test",
                           policy=load_security_policy(config_dir="config"))
    doc, rule, llm, score, rel = _inputs()
    with patch("app.llm.client.requests.post", return_value=_Resp()) as post:
        rec = adv.advise("v3x", rule=rule, llm=llm, score=score,
                         release_recommendation=rel, doc=doc)
    assert post.call_count == 1
    assert rec.source == "rule+llm" and rec.llm_status == "ok"
    assert rec.recommended_media[0]["entity_id"] == "media_mirror"
