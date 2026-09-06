"""Repository：把筛选记录持久化到 SQLite（含去重与附件哈希去重）。"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

from ..models import EmailDocument, RuleResult, LLMResult, FinalScore, ScreeningRecord
from .database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ---------- 去重 ----------
    @staticmethod
    def dedup_key(doc: EmailDocument) -> str:
        """subject+sender+date+body_hash 辅助去重键."""
        import hashlib
        raw = "|".join([doc.subject or "", doc.sender or "", doc.date or "", doc.body_hash or ""])
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    def is_duplicate(self, doc: EmailDocument) -> bool:
        return self.db.exists_email(doc.message_id or "", self.dedup_key(doc))

    def attachment_known(self, content_hash: str) -> bool:
        if not content_hash:
            return False
        row = self.db.query("SELECT 1 FROM attachments WHERE content_hash=? LIMIT 1", (content_hash,))
        return bool(row)

    # ---------- 写入 ----------
    def save_email(self, doc: EmailDocument) -> bool:
        """返回 False 表示重复已跳过."""
        dk = self.dedup_key(doc)
        if self.db.exists_email(doc.message_id or "", dk):
            return False
        self.db.execute(
            """INSERT OR REPLACE INTO emails
               (email_id, message_id, subject, sender, recipients, cc, date,
                body_text, body_hash, source_path, processed_at, dedup_key)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc.email_id, doc.message_id or "", doc.subject or "", doc.sender or "",
             json.dumps(doc.recipients, ensure_ascii=False),
             json.dumps(doc.cc, ensure_ascii=False), doc.date or "",
             doc.body_text or "", doc.body_hash or "", doc.source_path or "",
             _now(), dk))
        for att in doc.attachments:
            meta = getattr(att, "metadata", {}) or {}
            cached = meta.get("cached_path", "")
            if not cached:
                continue
            # 附件解析去重依赖 content_hash；如尚未解析则为空 -> 标记 file sha
            sha = att.sha256 or ""
            if not sha and cached:
                try:
                    sha = hashlib.sha256(open(cached, "rb").read()).hexdigest()
                except OSError:
                    sha = ""
            self.db.execute(
                """INSERT OR REPLACE INTO attachments
                   (email_id, filename, file_type, sha256, content_hash, text,
                    metadata, extraction_status, warnings)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (doc.email_id, att.filename or "", att.file_type or "", sha,
                 att.content_hash or "", att.text or "",
                 json.dumps(meta, ensure_ascii=False), att.extraction_status or "",
                 json.dumps(att.warnings, ensure_ascii=False)))
        return True

    def save_entities(self, email_id: str, entities: list):
        rows = []
        for e in entities:
            rows.append((email_id, e.get("type", ""), e.get("text", ""), int(e.get("count", 1))))
        if rows:
            self.db.executemany("INSERT INTO entities (email_id, entity_type, text, count) VALUES (?,?,?,?)", rows)

    def save_rule(self, email_id: str, rr: RuleResult):
        self.db.execute(
            "INSERT OR REPLACE INTO rule_matches (email_id, result_json, rule_score) VALUES (?,?,?)",
            (email_id, json.dumps(rr.to_dict(), ensure_ascii=False), float(rr.rule_score)))
        for p in rr.matched_patterns:
            self.db.execute(
                """INSERT OR REPLACE INTO pattern_matches
                   (email_id, pattern_id, category, name, pattern_score, matched_terms,
                    evidence_snippets, window)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (email_id, p.pattern_id, p.category, p.name, float(p.pattern_score),
                 json.dumps(p.matched_terms, ensure_ascii=False),
                 json.dumps(p.evidence_snippets, ensure_ascii=False), p.window))

    def save_llm(self, email_id: str, llm: LLMResult):
        self.db.execute(
            "INSERT OR REPLACE INTO llm_results (email_id, llm_json, llm_status, evidence_stage) VALUES (?,?,?,?)",
            (email_id, json.dumps(llm.to_dict(), ensure_ascii=False), llm.llm_status,
             llm.evidence_stage or ""))

    def save_score(self, email_id: str, fs: FinalScore):
        self.db.execute(
            """INSERT OR REPLACE INTO scores
               (email_id, final_score, priority, dimensions_json, stage_adjustment, negative_adjustment)
               VALUES (?,?,?,?,?,?)""",
            (email_id, float(fs.final_score), fs.priority,
             json.dumps(fs.dimension_scores, ensure_ascii=False),
             float(fs.stage_adjustment), float(fs.negative_adjustment)))

    def save_review_queue(self, rec: ScreeningRecord):
        if rec.score is None or rec.email is None:
            return
        if rec.score.priority in ("D",) and rec.score.final_score < 40:
            return  # D 不进入队列
        self.db.execute(
            """INSERT OR REPLACE INTO review_queue
               (email_id, priority, final_score, subject, sender, summary_zh, verification_targets, queued_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (rec.email_id, rec.score.priority, float(rec.score.final_score),
             rec.email.subject or "", rec.email.sender or "",
             rec.summary_zh or "", json.dumps(rec.verification_targets or [], ensure_ascii=False),
             _now()))

    # ---------- V3 具名渠道建议 ----------
    def save_named_channel_recommendations(self, email_id: str,
                                           named_dict: dict) -> int:
        """持久化 V3 具名渠道推荐（每个实体一行）。返回写入行数。"""
        import json as _json
        rows = []
        groups = [
            ("recommended_media", "media"), ("recommended_disclosure_actors", "disclosure"),
            ("recommended_amplifiers", "amplifier"),
            ("recommended_formal_channels", "formal"),
            ("recommended_platforms", "platform"), ("local_channels", "local"),
        ]
        for field, group in groups:
            for i, it in enumerate((named_dict.get(field) or []), 1):
                if isinstance(it, dict):
                    eid = str(it.get("entity_id") or "")
                    name = str(it.get("name") or "")
                    etype = str(it.get("entity_type") or "")
                    roles = it.get("roles") or []
                    score = float(it.get("fit_score") or 0)
                    reason = str(it.get("reason") or "")
                else:
                    eid, name, etype = "", "", ""
                    roles, score, reason = [], 0.0, ""
                if not eid:
                    continue
                rows.append((email_id, eid, name or eid, etype, group,
                             _json.dumps(roles, ensure_ascii=False), float(score),
                             i, reason, _now()))
        if rows:
            self.db.executemany(
                """INSERT INTO named_channel_recommendations
                   (email_id, entity_id, entity_name, entity_type, channel_group,
                    channel_role, fit_score, rank, reason, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
        return len(rows)

    # ---------- 首发渠道建议 ----------
    def save_release_recommendation(self, rr: "ReleaseRecommendation"):
        """持久化渠道建议；不覆盖已存在记录时如需更新由调用方决定（此处 INSERT OR REPLACE）。"""
        self.db.execute(
            """INSERT OR REPLACE INTO release_recommendations
               (email_id, primary_route, secondary_routes, avoid_routes, route_confidence,
                prepublication_verification_required, verification_before_release,
                formal_referral_recommended, formal_referral_type, release_risks,
                reason, recommended_release_sequence, headline_angle, editor_note,
                rule_hits, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rr.email_id, rr.primary_route or "",
             json.dumps(rr.secondary_routes or [], ensure_ascii=False),
             json.dumps(rr.avoid_routes or [], ensure_ascii=False),
             float(rr.route_confidence or 0.0),
             1 if rr.prepublication_verification_required else 0,
             json.dumps(rr.verification_before_release or [], ensure_ascii=False),
             1 if rr.formal_referral_recommended else 0,
             json.dumps(rr.formal_referral_type or [], ensure_ascii=False),
             json.dumps(rr.release_risks or [], ensure_ascii=False),
             rr.reason or "",
             json.dumps(rr.recommended_release_sequence or [], ensure_ascii=False),
             rr.headline_angle or "", rr.editor_note or "",
             json.dumps(rr.rule_hits or [], ensure_ascii=False),
             rr.source or "rule", _now()))

    def save_record(self, rec: ScreeningRecord, store_full: bool = True):
        """一次性保存全部（须先 save_email 判重后调用）。"""
        if rec.email is not None and store_full:
            self.save_email(rec.email)
        self.save_entities(rec.email_id, rec.rule.entities if rec.rule else [])
        if rec.rule is not None:
            self.save_rule(rec.email_id, rec.rule)
        if rec.llm is not None:
            self.save_llm(rec.email_id, rec.llm)
        if rec.score is not None:
            self.save_score(rec.email_id, rec.score)
            self.save_review_queue(rec)
