# -*- coding: utf-8 -*-
"""SafeLLMPayload 白名单测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import FinalScore, LLMResult, RuleResult
from app.security.models import FORBIDDEN_FIELD_NAMES, SafeLLMPayload
from app.security.payload_builder import SafePayloadBuilder
from app.security.policy import load_security_policy


def test_payload_has_no_forbidden_fields():
    fields = set(SafeLLMPayload.__dataclass_fields__.keys())
    assert not (fields & FORBIDDEN_FIELD_NAMES)


def test_v1_payload_structured_only_and_pseudonymized():
    rule = RuleResult(matched_categories=["A03"], rule_score=72,
                      target_persons_found=["林某某"], target_orgs_found=["某公司"],
                      money=[{"amount": 800000, "currency": "TWD"}])
    llm = LLMResult(categories=["A03"], target_persons=["林某某"], evidence_items=["汇款单"])
    score = FinalScore(final_score=80, priority="A")
    p = SafePayloadBuilder(load_security_policy(config_dir="config")).build_v1(
        rule=rule, llm=llm, score=score, email_id="MID:secret")
    d = p.to_dict()
    assert d["safe_snippets"] == []
    assert d["political_categories"] == ["A03"]
    assert "林某某" not in str(d)
    assert "某公司" not in str(d)
    assert d["email_ref"].startswith("REF:")
    assert not (set(d.keys()) & FORBIDDEN_FIELD_NAMES)


def test_v2_v3_payload_only_public_entities():
    from app.release_advisor.models import ReleaseDecisionFeatures, ReleaseRecommendation
    f = ReleaseDecisionFeatures(categories=["G08"], governance_categories=["G08"],
                                priority_level="A", final_score=80,
                                evidence_shapes=["OFFICIAL_DOCUMENT"])
    rel = ReleaseRecommendation(primary_route="R3", secondary_routes=["R2"])
    p2 = SafePayloadBuilder().build_v2(features=f, recommendation=rel)
    assert p2.governance_categories == ["G08"]
    assert p2.release_route == "R3"
    named = {"recommended_media": [{"entity_id": "m1", "name": "公开媒体", "fit_score": 90}]}
    p3 = SafePayloadBuilder().build_v3(named_recommendation=named, features=f,
                                       release_recommendation=rel)
    assert p3.public_entities and p3.public_entities[0]["name"] == "公开媒体"
