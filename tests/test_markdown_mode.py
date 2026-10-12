"""独立 Markdown 内部稿入口测试（app.md_main / process_markdown_*）。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import EmailDocument
from app.parsers import parse_markdown, parse_markdown_documents
from app.parsers.markdown_parser import SOURCE_KIND_INTERNAL_DRAFT, markdown_to_text

DRAFT_MD = """---
title: 議員胞姐掛名助理調查
date: 2026-10-01
tags: [貪污, 弊案]
---
# 林文杰議員胞姐掛名公費助理

**本報記者**調查發現，林文杰議員的胞姐去年起掛名公費助理，未實際上班。

- 每月助理薪資入帳後全數領出
- 提款卡長期由辦公室主任王雅婷保管

記者取得的[薪資單](https://example.com/x)與提款紀錄顯示，2025年3月至12月共領出新台幣48萬元。

| 月份 | 金額 |
|---|---|
| 3月 | 4.8萬 |

林文杰辦公室否認相關指控。
"""

# 含「記者+去年」但无金额/证据词：邮件模式会被判旧闻（新颖度 0），内部稿模式不应如此
OLD_STYLE_TEXT = "本報記者獲悉，林文杰議員的胞姐去年起掛名公費助理，未實際上班，助理薪資由辦公室主任王雅婷代領。"

MULTI_MD = """# 本週調查彙編

導語：本週三條線索。

## 線索一：掛名助理

林文杰議員的胞姐掛名公費助理，未實際上班，記者取得的薪資單與提款紀錄顯示共領出新台幣48萬元。

## 線索二：餐會

某里長參加社區餐會，現場氣氛熱烈。
"""


@pytest.fixture()
def md_pipeline(rule_config):
    from app.llm.screener import LLMScreener
    from app.pipeline.screening_pipeline import ScreeningPipeline
    return ScreeningPipeline(rule_config, db=None,
                             llm_screener=LLMScreener(rule_config, mode="template"), allow_llm=False)


def _write(p: Path, text: str, encoding: str = "utf-8") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode(encoding))
    return p


def test_markdown_to_text_strips_syntax():
    out = markdown_to_text("# 標題\n\n**粗體** 與 [連結](http://a.b/c) ![圖](x.png)\n\n"
                           "| a | b |\n|---|:-:|\n| 1 | 2 |\n\n```\ncode 行\n```\n> 引用\n- 項目\n---\n")
    assert "#" not in out and "**" not in out and "http://a.b" not in out and "```" not in out
    for frag in ("標題", "粗體 與 連結 圖", "a | b", "1 | 2", "code 行", "引用", "項目"):
        assert frag in out
    assert "---" not in out and ":-:" not in out


def test_parse_markdown_identity_and_metadata(tmp_path):
    p = _write(tmp_path / "稿件A.md", DRAFT_MD)
    doc = parse_markdown(p)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    assert doc.email_id == f"MD:{sha}" and doc.raw_sha256 == sha
    assert doc.source_kind == SOURCE_KIND_INTERNAL_DRAFT
    assert doc.attachments == [] and doc.sender == "" and doc.message_id == ""
    assert doc.subject == "議員胞姐掛名助理調查" and doc.date == "2026-10-01"
    assert doc.source_path == str(p)
    # front-matter 元数据（tags 里的「貪污/弊案」）不进入正文，避免误触发规则
    assert "tags" not in doc.body_text and "貪污" not in doc.body_text
    assert "林文杰議員胞姐掛名公費助理" in doc.body_text
    # 同内容不同文件名 -> 同一身份（去重）；内容不同 -> 不同身份
    assert parse_markdown(_write(tmp_path / "copy.md", DRAFT_MD)).email_id == doc.email_id
    assert parse_markdown(_write(tmp_path / "b.md", DRAFT_MD + "補充")).email_id != doc.email_id


def test_parse_markdown_title_fallback_and_encodings(tmp_path):
    assert parse_markdown(_write(tmp_path / "無標題.md", "正文內容")).subject == "無標題"
    assert parse_markdown(_write(tmp_path / "h1.md", "# 一級標題\n正文")).subject == "一級標題"
    bom = tmp_path / "bom.md"
    bom.write_bytes(b"\xef\xbb\xbf" + "# BOM標題\n內容".encode())
    assert parse_markdown(bom).subject == "BOM標題"
    gbk = _write(tmp_path / "gbk.md", "# 简体标题\n议员助理薪资", encoding="gb18030")
    d = parse_markdown(gbk)
    assert d.subject == "简体标题" and "议员助理薪资" in d.body_text


def test_parse_markdown_size_limit(tmp_path, md_pipeline):
    p = _write(tmp_path / "big.md", "x" * 2000)
    with pytest.raises(ValueError):
        parse_markdown(p, max_bytes=1000)
    recs = md_pipeline.process_markdown_file(_write(tmp_path / "empty.md", ""))
    assert len(recs) == 1 and recs[0].score is not None  # 空文件不崩溃


def test_markdown_draft_scores_high_value(tmp_path, md_pipeline):
    recs = md_pipeline.process_markdown_file(_write(tmp_path / "draft.md", DRAFT_MD))
    assert len(recs) == 1
    rec = recs[0]
    assert rec.score.priority in ("S", "A")
    assert rec.rule.internal_draft is True
    # 正文提及的证据（薪资单/提款纪录）照常计入证据质量
    assert rec.score.dimension_scores["evidence_quality"] >= 15


def test_internal_draft_not_treated_as_old_news(md_pipeline):
    def run(kind):
        doc = EmailDocument(email_id=f"T:{kind}", subject="t", body_text=OLD_STYLE_TEXT, source_kind=kind)
        return md_pipeline.process_document(doc)
    draft, email = run(SOURCE_KIND_INTERNAL_DRAFT), run("email")
    # 邮件模式原有行为保持不变
    assert email.rule.no_new_evidence is True and email.score.dimension_scores["novelty"] == 0
    assert draft.rule.no_new_evidence is False and draft.score.dimension_scores["novelty"] == 10
    assert draft.score.final_score > email.score.final_score


def test_internal_draft_explicit_no_new_evidence_still_flagged(md_pipeline):
    doc = EmailDocument(email_id="T:explicit", subject="t", source_kind=SOURCE_KIND_INTERNAL_DRAFT,
                        body_text=OLD_STYLE_TEXT + "本稿為舊聞轉發，沒有新證據。")
    assert md_pipeline.process_document(doc).rule.no_new_evidence is True


def test_split_sections(tmp_path, md_pipeline):
    p = _write(tmp_path / "multi.md", MULTI_MD)
    docs = parse_markdown_documents(p, split_sections=True)
    assert [d.subject for d in docs] == ["本週調查彙編 / 导语", "本週調查彙編 / 線索一：掛名助理",
                                         "本週調查彙編 / 線索二：餐會"]
    assert len({d.email_id for d in docs}) == 3 and len({d.raw_sha256 for d in docs}) == 3
    assert all(d.email_id.startswith("MD:") and "#S" in d.email_id for d in docs)
    recs = {r.email.subject: r for r in md_pipeline.process_markdown_file(p, split_sections=True)}
    assert recs["本週調查彙編 / 線索一：掛名助理"].score.final_score > \
        recs["本週調查彙編 / 線索二：餐會"].score.final_score
    assert len(parse_markdown_documents(p)) == 1  # 默认整篇一个结果


def test_markdown_directory_scan(tmp_path, md_pipeline):
    _write(tmp_path / "a.md", DRAFT_MD)
    _write(tmp_path / "b.markdown", "# B\n內容")
    _write(tmp_path / "sub" / "c.md", "# C\n內容")
    _write(tmp_path / "ignored.eml", "Subject: x\n\nbody")
    _write(tmp_path / "ignored.txt", "text")
    flat = md_pipeline.process_markdown_directory(tmp_path)
    assert sorted(Path(r.email.source_path).name for r in flat) == ["a.md", "b.markdown"]
    deep = md_pipeline.process_markdown_directory(tmp_path, recursive=True)
    assert sorted(Path(r.email.source_path).name for r in deep) == ["a.md", "b.markdown", "c.md"]


def test_markdown_persistence_and_dedup(tmp_path, rule_config):
    from app.llm.screener import LLMScreener
    from app.pipeline.screening_pipeline import ScreeningPipeline
    from app.storage.database import Database
    db = Database(tmp_path / "md.db")
    try:
        pipe = ScreeningPipeline(rule_config, db=db,
                                 llm_screener=LLMScreener(rule_config, mode="template"), allow_llm=False)
        p = _write(tmp_path / "in" / "draft.md", DRAFT_MD)
        first = pipe.process_markdown_file(p)
        assert len(first) == 1 and first[0].error in ("", None)
        assert pipe.process_markdown_file(p) == []          # 已处理且 analysis 未过期 -> 跳过
        assert len(pipe.process_markdown_file(p, reprocess=True)) == 1
    finally:
        db.close()


def test_external_payload_excludes_markdown_text(tmp_path, md_pipeline):
    from app.security.outbound_guard import OutboundGuard
    from app.security.payload_builder import SafePayloadBuilder
    from app.security.policy import load_security_policy
    rec = md_pipeline.process_markdown_file(_write(tmp_path / "draft.md", DRAFT_MD))[0]
    policy = load_security_policy(config_dir="config")
    payload = SafePayloadBuilder(policy=policy).build_v1(
        rule=rec.rule, unified=rec.unified_signals, email_id=rec.email_id)
    body = json.dumps(payload.to_dict(), ensure_ascii=False)
    for secret in ("林文杰", "王雅婷", "胞姐掛名公費助理", "提款卡長期", str(tmp_path), "draft.md"):
        assert secret not in body
    assert OutboundGuard(policy).check(payload).allowed


def test_md_main_cli(tmp_path, monkeypatch):
    from app import md_main
    _write(tmp_path / "in" / "draft.md", DRAFT_MD)
    out = tmp_path / "out"
    rc = md_main.main(["--input", str(tmp_path / "in"), "--no-llm", "--quiet",
                       "--db", str(tmp_path / "md.db"), "--output", str(out)])
    assert rc == 0
    rows = (out / "md_screening_results.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    assert (out / "md_priority_queue.csv").exists()
    assert md_main.main(["--file", str(tmp_path / "x.eml"), "--db", str(tmp_path / "md.db")]) == 2
