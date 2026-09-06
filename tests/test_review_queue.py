# -*- coding: utf-8 -*-
"""V3.2 重要邮件待审查文件夹测试（>=20项，全部使用 tmp_path）。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import EmailDocument, FinalScore, LLMResult, PatternHit, RuleResult, ScreeningRecord
from app.review_queue import ReviewQueueManager
from app.review_queue.manager import sanitize_subject


def make_source(tmp_path, name="mail.eml", content=None):
    src_dir = Path(tmp_path) / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    p = src_dir / name
    p.write_bytes((content if content is not None else
                   "From: a@x\nTo: b@x\nSubject: t\nMessage-ID: <x>\n\nbody text here\n").encode("utf-8"))
    return str(p)


def make_record(priority="A", score=82.0, message_id="mid-test-001@example.com",
                body_hash="hash-001", subject="某建商金流线索",
                sender="whistle@example.com", date="Wed, 02 Sep 2026 10:00:00 +0800",
                source_path="", body_text="正文内容"):
    email = EmailDocument(
        email_id=message_id or body_hash, message_id=message_id or "",
        subject=subject, sender=sender, date=date,
        body_text=body_text, body_hash=body_hash or "",
        source_path=source_path,
    )
    rule = RuleResult(matched_categories=["A03"],
                      matched_patterns=[PatternHit(pattern_id="P04", category="A03", name="m")],
                      target_persons_found=["林某某"])
    llm = LLMResult(categories=["A03"], target_persons=["林某某"],
                    one_sentence_summary="一句话摘要",
                    reason_for_attention="值得关注原因",
                    verification_targets=["核验汇款"])
    fs = FinalScore(final_score=float(score), priority=priority)
    return ScreeningRecord(email_id=email.email_id, email=email, rule=rule, llm=llm,
                           score=fs, summary_zh="一句话摘要",
                           verification_targets=["核验汇款"])


def _new_manager(tmp_path, **kw):
    params = dict(path=tmp_path / "review_queue", enabled=True,
                  priorities=["S", "A", "B"], min_score=60.0)
    params.update(kw)
    return ReviewQueueManager(**params)


def _all_eml(base):
    return sorted(Path(base).rglob("*.eml"))


# 01-03 S/A/B 进入对应目录
def test_01_s_enters_s(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    out = rq.copy_to_queue(make_record(priority="S", score=94, source_path=src))
    assert out is not None and Path(out).exists()
    assert Path(out).parent.name == "S"
    assert (Path(tmp_path) / "review_queue" / "S").is_dir()


def test_02_a_enters_a(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    out = rq.copy_to_queue(make_record(priority="A", score=82, source_path=src))
    assert out is not None and Path(out).parent.name == "A"


def test_03_b_enters_b(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    out = rq.copy_to_queue(make_record(priority="B", score=65, source_path=src))
    assert out is not None and Path(out).parent.name == "B"


# 04-05 C/D 默认不进入
def test_04_c_not_queued(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    assert rq.copy_to_queue(make_record(priority="C", score=50, source_path=src)) is None
    assert _all_eml(tmp_path / "review_queue") == []


def test_05_d_not_queued(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    assert rq.copy_to_queue(make_record(priority="D", score=20, source_path=src)) is None
    assert _all_eml(tmp_path / "review_queue") == []


# 06 min_score 配置生效（OR 逻辑）
def test_06_min_score_config(tmp_path):
    src = make_source(tmp_path)
    rq60 = _new_manager(tmp_path / "q60", path=tmp_path / "q60")
    assert rq60.should_queue(make_record(priority="C", score=70, source_path=src)) is True
    rq75 = _new_manager(tmp_path / "q75", path=tmp_path / "q75", min_score=75.0)
    assert rq75.should_queue(make_record(priority="C", score=70, source_path=src)) is False
    assert rq75.should_queue(make_record(priority="C", score=80, source_path=src)) is True
    assert rq75.should_queue(make_record(priority="B", score=65, source_path=src)) is True


# 07 Message-ID 去重
def test_07_message_id_dedup(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    rec = make_record(priority="A", score=82, message_id="dup-mid@example.com",
                      body_hash="h1", source_path=src)
    assert rq.copy_to_queue(rec) is not None
    assert rq.copy_to_queue(rec) is not None
    assert len(_all_eml(tmp_path / "review_queue")) == 1


# 08 hash fallback 去重（无 Message-ID）
def test_08_hash_fallback_dedup(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    r1 = make_record(priority="A", score=82, message_id="", body_hash="same-hash-xyz",
                     source_path=src, body_text="same body")
    r2 = make_record(priority="A", score=82, message_id="", body_hash="same-hash-xyz",
                     source_path=src, body_text="same body")
    k1, k2 = rq.build_review_key(r1), rq.build_review_key(r2)
    assert k1.startswith("HASH:") and k1 == k2
    assert rq.copy_to_queue(r1) is not None
    assert rq.copy_to_queue(r2) is not None
    assert len(_all_eml(tmp_path / "review_queue")) == 1
    # 不同 hash 应新增
    r3 = make_record(priority="A", score=82, message_id="", body_hash="other-hash",
                     source_path=src)
    assert rq.copy_to_queue(r3) is not None
    assert len(_all_eml(tmp_path / "review_queue")) == 2


# 09 重跑不重复
def test_09_rerun_no_duplicate(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    recs = [make_record(priority="A", score=82, message_id=f"m{i}@x",
                        body_hash=f"h{i}", source_path=src) for i in range(5)]
    s1 = rq.process_batch(recs)
    n1 = len(_all_eml(tmp_path / "review_queue"))
    s2 = rq.process_batch(recs)
    n2 = len(_all_eml(tmp_path / "review_queue"))
    assert n1 == 5 and n2 == 5
    assert s2["added"] == 0 and s2["deduped"] == 5


# 10 A→S 目录同步
def test_10_a_to_s_sync(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    rq.copy_to_queue(make_record(priority="A", score=82, message_id="mv@x",
                                 body_hash="h", source_path=src))
    assert (tmp_path / "review_queue" / "A").is_dir()
    rq.copy_to_queue(make_record(priority="S", score=94, message_id="mv@x",
                                 body_hash="h", source_path=src))
    assert list((tmp_path / "review_queue" / "A").glob("*.eml")) == []
    assert len(list((tmp_path / "review_queue" / "S").glob("*.eml"))) == 1
    assert len(_all_eml(tmp_path / "review_queue")) == 1


# 11 S→A 目录同步
def test_11_s_to_a_sync(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    rq.copy_to_queue(make_record(priority="S", score=94, message_id="mv2@x",
                                 body_hash="h", source_path=src))
    rq.copy_to_queue(make_record(priority="A", score=82, message_id="mv2@x",
                                 body_hash="h", source_path=src))
    assert list((tmp_path / "review_queue" / "S").glob("*.eml")) == []
    assert len(list((tmp_path / "review_queue" / "A").glob("*.eml"))) == 1
    assert len(_all_eml(tmp_path / "review_queue")) == 1


# 12 A→C 后移出 Queue
def test_12_a_to_c_removed(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    rq.copy_to_queue(make_record(priority="A", score=82, message_id="down@x",
                                 body_hash="h", source_path=src))
    assert len(_all_eml(tmp_path / "review_queue")) == 1
    rq.process_batch([make_record(priority="C", score=40, message_id="down@x",
                                  body_hash="h", source_path=src)])
    assert _all_eml(tmp_path / "review_queue") == []
    assert len(list((tmp_path / "review_queue").rglob("*.review.json"))) == 0
    # 原邮件不受影响
    assert Path(src).exists()


# 13 原邮件仍存在（复制不移动）
def test_13_original_preserved(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    out = rq.copy_to_queue(make_record(priority="S", score=94, source_path=src))
    assert Path(src).exists()
    assert Path(out).exists()
    assert Path(src).read_bytes() == Path(out).read_bytes()


# 14 Windows 非法字符清洗
def test_14_illegal_chars_cleaned(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    bad = 'a/b\\c:d*e?f"g<h>i|j'
    rec = make_record(priority="A", score=82, subject=bad, source_path=src)
    fname = rq.build_filename(rec)
    for ch in '\\/:*?"<>|':
        assert ch not in fname
    assert fname.endswith(".eml")
    out = rq.copy_to_queue(rec)
    assert Path(out).exists()
    assert sanitize_subject(bad).find("/") == -1


# 15 超长主题处理
def test_15_long_subject(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    rec = make_record(priority="A", score=82, subject="某" * 300, source_path=src)
    fname = rq.build_filename(rec)
    assert len(fname) <= 180 and fname.endswith(".eml")
    assert rq.copy_to_queue(rec) is not None


# 16 sidecar 生成
def test_16_sidecar_created(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    out = rq.copy_to_queue(make_record(priority="S", score=94, source_path=src))
    sc = Path(out).with_name(Path(out).stem + ".review.json")
    assert sc.exists()


# 17 sidecar 内容正确（且不含正文/API Key）
def test_17_sidecar_content(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    rec = make_record(priority="S", score=94, message_id="sc@x", source_path=src)
    out = rq.copy_to_queue(rec)
    sc = Path(out).with_name(Path(out).stem + ".review.json")
    data = json.loads(sc.read_text(encoding="utf-8"))
    assert data["review_key"] == "MID:sc@x"
    assert data["priority"] == "S"
    assert data["final_score"] == 94
    for k in ("categories", "target_persons", "matched_patterns",
              "one_sentence_summary", "reason_for_attention",
              "verification_targets", "source_path", "queued_at"):
        assert k in data
    blob = json.dumps(data, ensure_ascii=False)
    assert "正文内容" not in blob  # 不写完整正文
    assert "API" not in blob and "API_KEY" not in blob


# 18 source_path 不存在不崩
def test_18_missing_source_no_crash(tmp_path):
    rq = _new_manager(tmp_path)
    rec = make_record(priority="A", score=82,
                      source_path=str(tmp_path / "no_such.eml"))
    assert rq.copy_to_queue(rec) is None
    stats = rq.process_batch([rec])
    assert stats["queued"] == 0


# 19 copy 异常不崩
def test_19_copy_error_no_crash(tmp_path, monkeypatch):
    import shutil as _sh
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    rec = make_record(priority="A", score=82, source_path=src)

    def _boom(*a, **k):
        raise PermissionError(13, "denied")
    monkeypatch.setattr(_sh, "copy2", _boom)
    assert rq.copy_to_queue(rec) is None
    stats = rq.process_batch([rec])
    assert stats["queued"] == 0 and stats["errors"] == 0


# 20 批量 100 封正常处理
def test_20_batch_100(tmp_path):
    src = make_source(tmp_path)
    rq = _new_manager(tmp_path)
    recs = [make_record(priority="A" if i % 2 == 0 else "B", score=80,
                        message_id=f"batch-{i:03d}@x", body_hash=f"h{i}",
                        source_path=src) for i in range(100)]
    stats = rq.process_batch(recs)
    assert stats["queued"] == 100 and stats["added"] == 100
    assert len(_all_eml(tmp_path / "review_queue")) == 100


# 21 CLI enable（真实 parser 解析 + 开关语义）
def test_21_cli_enable_parsed(monkeypatch):
    import app.main as _m
    captured = {}

    def _fake_run(args):
        captured["enable"] = args.enable_review_queue
        captured["disable"] = args.disable_review_queue
        return 0
    monkeypatch.setattr(_m, "run", _fake_run)
    assert _m.main(["--input", "x", "--enable-review-queue"]) == 0
    assert captured["enable"] is True and captured["disable"] is False
    assert _m.resolve_toggle(True, False, False) is True


# 22 CLI disable（且 disable 优先于 enable）
def test_22_cli_disable_wins(monkeypatch):
    import app.main as _m
    captured = {}

    def _fake_run(args):
        captured["enable"] = args.enable_review_queue
        captured["disable"] = args.disable_review_queue
        return 0
    monkeypatch.setattr(_m, "run", _fake_run)
    assert _m.main(["--input", "x", "--enable-review-queue", "--disable-review-queue"]) == 0
    assert captured["enable"] is True and captured["disable"] is True
    # disable 优先
    assert _m.resolve_toggle(True, True, True) is False
    assert _m.resolve_toggle(False, True, True) is False
    # 禁用后不落文件
    rq = ReviewQueueManager(path="/tmp/should_not_be_used_rq", enabled=False)
    assert rq.process_batch([make_record()])["queued"] == 0


# 23 Excel 路径字段兼容（V3.1 存在时回填待审查文件路径）
def test_23_excel_review_path_compat(tmp_path):
    from app.reports.excel_register import ImportantEmailRegister, REVIEW_PATH_FIELD
    src = make_source(tmp_path)
    reg = ImportantEmailRegister(path=tmp_path / "reg.xlsx", enabled=True,
                                 min_score=60.0, priorities=["S", "A", "B"],
                                 backup_before_batch=False)
    rec = make_record(priority="A", score=82, message_id="excel-rq@x",
                      body_hash="h", source_path=src)
    stats = reg.process_batch([rec])
    assert stats["added"] == 1
    # 回填待审查路径
    rq = _new_manager(tmp_path)
    out = rq.copy_to_queue(rec)
    n = reg.update_review_paths({reg.build_register_key(rec): out})
    assert n == 1
    from openpyxl import load_workbook
    wb = load_workbook(str(reg.path))
    ws = wb["重要爆料邮件"]
    header = [c.value for c in ws[1]]
    assert REVIEW_PATH_FIELD in header
    col = header.index(REVIEW_PATH_FIELD) + 1
    assert str(ws.cell(row=2, column=col).value) == str(out)
    # 处理状态仍为唯一人工状态字段（不因回填被改）
    assert ws.cell(row=2, column=header.index("处理状态") + 1).value == "待看"


# 24 配置文件加载（yaml + 默认）
def test_24_config_load(tmp_path):
    from app.config import load_review_queue_config
    cfg = load_review_queue_config()
    assert cfg["enabled"] is True
    assert set(["S", "A", "B"]) <= set(cfg["priorities"])
    assert float(cfg["min_score"]) == 60
    assert cfg["sync_priority_changes"] is True
    assert cfg["move_original"] is False
    # yaml 文件存在
    assert (Path(__file__).resolve().parent.parent / "config" / "review_queue.yaml").exists()
