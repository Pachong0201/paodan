# -*- coding: utf-8 -*-
"""人名/公司实体抽取回归测试：锁定 T01-T06 公开报道重建样本的误抓修复.

覆盖两类历史缺陷：
1. 重叠吞字：主任許嘉恬 -> 任許嘉（吞許嘉恬）；董事湯文馨 -> 董事湯（吞湯文馨）。
2. 常用词误判：向經濟部 -> 向经济；曾在民進黨 -> 曾在民；歷史原始 -> 史原始；
   中間人協助處理公司 / 份資料究竟是電信公司等句子片段公司名。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rules.pattern_engine import extract_person_candidates, is_person_token
from app.rules.rule_entities import _clean_company_name, extract_basic_entities
from app.preprocessing.normalization import Normalizer, to_simplified


def _persons(text):
    return set(extract_basic_entities(text))


def _person_texts(text):
    return {e.text for e in extract_basic_entities(text) if e.type == "PERSON"}


def test_overlap_director_name_kept():
    cands = extract_person_candidates(Normalizer.normalize("主任許嘉恬反映"))
    assert to_simplified("許嘉恬") in cands, cands
    assert "任许嘉" not in cands, cands
    assert not is_person_token("任许嘉", prev_char="主")


def test_overlap_director_tang_kept():
    cands = extract_person_candidates(Normalizer.normalize("董事湯文馨"))
    assert to_simplified("湯文馨") in cands, cands
    assert "董事汤" not in cands, cands


def test_function_word_not_person():
    for bad in ["曾在民", "曾向当", "曾遭民", "向经济", "方电讯", "史原始",
                "向我表", "曾对相", "方电", "向民", "于测", "向结", "向规"]:
        assert not is_person_token(bad), bad


def test_real_names_kept():
    for good in ["李恒隆", "陈唐山", "郭克铭", "许嘉恬", "王义川",
                 "陈泳璋", "汤文馨", "蔡英文", "赖清德", "侯汉廷"]:
        assert is_person_token(good), good


def test_t01_persons_exact():
    bodies = Path("tests/fixtures/synthetic_emails/T01_sogo_anonymous_tip.eml").read_bytes()
    import email
    from email.policy import default as pol
    msg = email.message_from_binary_file(__import__("io").BytesIO(bodies), policy=pol)
    body = "".join(p.get_content() for p in msg.walk() if not p.is_multipart() and p.get_content_disposition() != "attachment")
    got = _person_texts(body)
    assert {"李恒隆", "陈唐山", "郭克铭"} <= got, got
    assert not any(x in got for x in ("史原始", "史原", "向经济", "向经", "曾有前")), got


def test_t02_person_single():
    import email, io
    from email.policy import default as pol
    raw = Path("tests/fixtures/synthetic_emails/T02_dpp_staff_harassment_tip.eml").read_bytes()
    msg = email.message_from_binary_file(io.BytesIO(raw), policy=pol)
    body = "".join(p.get_content() for p in msg.walk() if not p.is_multipart() and p.get_content_disposition() != "attachment")
    got = _person_texts(body)
    assert got == {"许嘉恬"}, got


def test_t04_persons_and_company():
    import email, io
    from email.policy import default as pol
    raw = Path("tests/fixtures/synthetic_emails/T04_nanfeng_contract_internal_email.eml").read_bytes()
    msg = email.message_from_binary_file(io.BytesIO(raw), policy=pol)
    body = "".join(p.get_content() for p in msg.walk() if not p.is_multipart() and p.get_content_disposition() != "attachment")
    ents = extract_basic_entities(body)
    persons = {e.text for e in ents if e.type == "PERSON"}
    companies = {e.text for e in ents if e.type == "COMPANY"}
    assert persons == {"陈泳璋", "汤文馨"}, persons
    assert "南風整合行銷股份有限公司" in companies, companies
    assert "南風公司" in companies, companies


def test_company_fragments_rejected():
    assert _clean_company_name("中間人協助處理公司") is None
    assert _clean_company_name("份資料究竟是電信公司") is None
    assert _clean_company_name("央與南風整合行銷股份有限公司") == "南風整合行銷股份有限公司"
