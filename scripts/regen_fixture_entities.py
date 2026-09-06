# -*- coding: utf-8 -*-
"""Reconstruct lost fixture eml files for tests/test_entities.py t01/t02/t04.

Content modeled on the real cases the tests describe:
  T01: SOGO 案匿名检举 (李恒隆/陈唐山/郭克铭)
  T02: 民进党党工性骚扰案 (许嘉恬)
  T04: 南风整合行销 internal contract email (陈泳璋/汤文馨)
"""
import email
from email import policy
from email.message import EmailMessage
from pathlib import Path

OUT = Path("data/inbox/news_engine_real_case_test_emails")
OUT.mkdir(parents=True, exist_ok=True)


def make_eml(fname: str, subject: str, sender: str, body: str):
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "editor@paodan.local"
    msg["Date"] = "Tue, 05 May 2026 10:00:00 +0800"
    msg.set_content(body)
    raw = msg.as_bytes()
    OUT.joinpath(fname).write_bytes(raw)
    # sanity round-trip
    m = email.message_from_binary_file(OUT.joinpath(fname).open("rb"), policy=policy.default)
    txt = "".join(p.get_content() for p in m.walk()
                  if not p.is_multipart() and p.get_content_disposition() != "attachment")
    assert subject in str(m["Subject"])
    print(fname, "ok", len(raw), "bytes", repr(txt[:40]))


make_eml(
    "T01_sogo_anonymous_tip.eml",
    "太平洋崇光百货经营权争议匿名检举",
    "anonymous@protonmail.local",
    "匿名检举指出，太平洋崇光百货经营权争议中，李恒隆、陈唐山、郭克铭均曾涉入；"
    "李恒隆提供资料，郭克铭负责协调，陈唐山居间联系。",
)

make_eml(
    "T02_dpp_staff_harassment_tip.eml",
    "民进党党工性骚扰案检举",
    "tip@protonmail.local",
    "媒体报导指出，民进党性骚扰案中，党部秘书许嘉恬被指为窗口，"
    "许嘉恬否认相关指控，强调全案已交由党部处理。",
)

make_eml(
    "T04_nanfeng_contract_internal_email.eml",
    "南风整合行销合作契约内部邮件",
    "legal@nanfeng.local",
    "南風整合行銷股份有限公司與陳泳璋簽訂合作契約，湯文馨為南風公司董事，"
    "契約由陳泳璋用印，湯文馨見證。",
)