# -*- coding: utf-8 -*-
"""WP1 附件身份/缓存隔离/去重测试。"""
from __future__ import annotations

import sys
from email import policy
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.llm.screener import LLMScreener
from app.parsers import parse_eml
from app.pipeline.screening_pipeline import ScreeningPipeline
from app.rules.config_loader import RuleConfig
from app.storage.database import Database


def _mk_eml(tmp_path: Path, name: str, attachment: bytes, filename: str = "evidence.pdf") -> Path:
    msg = EmailMessage(policy=policy.default)
    msg["Subject"] = name
    msg["From"] = "source@example.com"
    msg["To"] = "tips@news.tw"
    msg["Date"] = "Mon, 01 Sep 2026 08:00:00 +0800"
    msg.set_content("body " + name)
    msg.add_attachment(attachment, maintype="application", subtype="octet-stream",
                       filename=filename)
    p = tmp_path / f"{name}.eml"
    p.write_bytes(msg.as_bytes())
    return p


def test_same_filename_different_bytes_isolated(tmp_path):
    a = _mk_eml(tmp_path, "mail_A", b"PRIVATE_A")
    b = _mk_eml(tmp_path, "mail_B", b"PRIVATE_B")
    da = parse_eml(a)
    db = parse_eml(b)
    assert da.attachments[0].source_sha256 != db.attachments[0].source_sha256
    assert da.attachments[0].metadata["cached_path"] != db.attachments[0].metadata["cached_path"]
    # 用 pipeline 走附件解析
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=None, llm_screener=LLMScreener(cfg, mode="template"),
                             allow_llm=False)
    ra = pipe.process_file(a)
    rb = pipe.process_file(b)
    assert "PRIVATE_A" in ra.email.attachments[0].text
    assert "PRIVATE_B" not in ra.email.attachments[0].text
    assert "PRIVATE_B" in rb.email.attachments[0].text
    assert "PRIVATE_A" not in rb.email.attachments[0].text


def test_same_attachment_bytes_reuses_parse_cache(tmp_path, monkeypatch):
    a = _mk_eml(tmp_path, "mail_A", b"PRIVATE_A", filename="a.pdf")
    b = _mk_eml(tmp_path, "mail_B", b"PRIVATE_A", filename="a.pdf")
    c = _mk_eml(tmp_path, "mail_C", b"PRIVATE_A", filename="a.pdf")
    db = Database(tmp_path / "x.db")
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=db, llm_screener=LLMScreener(cfg, mode="template"),
                             allow_llm=False)
    import app.pipeline.screening_pipeline as sp
    original = sp.parse_attachment
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(sp, "parse_attachment", counted)
    pipe.process_file(a)
    pipe.process_file(b)
    pipe.process_file(c)
    # 三封邮件附件 bytes 相同：仅第一次 parse，后续全部复用 parse cache。
    assert len(calls) == 1
    assert len(db.query("SELECT * FROM attachment_parse_cache")) == 1
    db.close()
