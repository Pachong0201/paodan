"""Repository：SQLite 持久化（ingestion identity 去重、附件 parse cache、治理结果、analysis_runs）。"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from ..models import (AttachmentDoc, EmailDocument, FinalScore, LLMResult,
                      RuleResult, ScreeningRecord)
from .database import Database

logger = logging.getLogger(__name__)

PARSER_VERSION = "attachment-parser-v1"
OCR_VERSION = "tesseract-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ---------- identity / dedup ----------
    @staticmethod
    def dedup_key(doc: EmailDocument) -> str:
        """辅助 dedup key（不能与主键冲突；主键由 email_id/identity 决定）。"""
        # 辅助 dedup 只用于无新身份字段的兼容路径；加入 raw_sha256 避免与 ingestion identity 冲突。
        raw = "|".join([doc.subject or "", doc.sender or "", doc.date or "",
                        doc.body_hash or "", doc.raw_sha256 or ""])
        return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()

    def is_duplicate(self, doc: EmailDocument) -> bool:
        """ingestion identity：优先 normalized Message-ID，其次 raw_eml_sha256。"""
        if doc.normalized_message_id or doc.raw_sha256:
            return self.db.exists_identity(doc.normalized_message_id, doc.raw_sha256)
        # 兼容手工构造/旧数据：仅在无新身份字段时才用旧辅助键
        return self.db.exists_email(doc.message_id or "", self.dedup_key(doc))

    # ---------- attachment parse cache ----------
    def attachment_known(self, content_hash: str) -> bool:
        """兼容旧接口：按 text hash / source hash 查询附件是否已存在。"""
        if not content_hash:
            return False
        row = self.db.query(
            "SELECT 1 FROM attachments WHERE content_hash=? OR source_sha256=? OR text_sha256=? LIMIT 1",
            (content_hash, content_hash, content_hash))
        return bool(row)

    def lookup_attachment_parse_cache(self, source_sha256: str,
                                      parser_version: str = PARSER_VERSION,
                                      ocr_version: str = OCR_VERSION) -> Optional[dict]:
        if not source_sha256:
            return None
        row = self.db.query_one(
            "SELECT source_sha256, parser_version, ocr_version, text, text_sha256, metadata, "
            "tables_json, extraction_status, updated_at FROM attachment_parse_cache "
            "WHERE source_sha256=? AND parser_version=? AND ocr_version=?",
            (source_sha256, parser_version, ocr_version))
        if row is None:
            return None
        return {
            "source_sha256": row["source_sha256"],
            "parser_version": row["parser_version"],
            "ocr_version": row["ocr_version"],
            "text": row["text"] or "",
            "text_sha256": row["text_sha256"] or "",
            "metadata": json.loads(row["metadata"] or "{}"),
            "tables": json.loads(row["tables_json"] or "[]"),
            "extraction_status": row["extraction_status"] or "",
        }

    def save_attachment_parse_cache(self, att: AttachmentDoc) -> None:
        source_sha = att.source_sha256 or att.sha256
        if not source_sha:
            return
        self.db.execute(
            """INSERT OR REPLACE INTO attachment_parse_cache
               (source_sha256, parser_version, ocr_version, text, text_sha256, metadata,
                tables_json, extraction_status, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (source_sha, att.parser_version or PARSER_VERSION, att.ocr_version or "none",
             att.text or "", att.text_sha256 or "", _json(att.metadata or {}),
             _json(att.tables or []), att.extraction_status or "", _now()))

    # ---------- writes ----------
    def save_email(self, doc: EmailDocument, force: bool = False) -> bool:
        """返回 False 表示重复已跳过；force=True 用于 reprocess/stale 覆盖。"""
        dk = self.dedup_key(doc)
        if not force and self.is_duplicate(doc):
            return False
        self.db.execute(
            """INSERT OR REPLACE INTO emails
               (email_id, message_id, subject, sender, recipients, cc, date,
                body_text, body_hash, source_path, processed_at, dedup_key,
                raw_sha256, normalized_message_id, ingestion_identity)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc.email_id, doc.message_id or "", doc.subject or "", doc.sender or "",
             _json(doc.recipients), _json(doc.cc), doc.date or "",
             doc.body_text or "", doc.body_hash or "", doc.source_path or "",
             _now(), dk, doc.raw_sha256 or "", doc.normalized_message_id or "",
             doc.ingestion_identity))
        for att in doc.attachments:
            meta = getattr(att, "metadata", {}) or {}
            cached = meta.get("cached_path", "")
            source_sha = att.source_sha256 or att.sha256 or ""
            if not source_sha and cached:
                try:
                    from ..parsers.attachment_parser import sha256_file
                    source_sha = sha256_file(cached)
                except Exception:  # noqa: BLE001
                    source_sha = ""
            self.db.execute(
                """INSERT OR REPLACE INTO attachments
                   (email_id, filename, file_type, sha256, content_hash, text,
                    metadata, extraction_status, warnings, source_sha256, text_sha256,
                    parser_version, ocr_version, cached_path)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doc.email_id, att.filename or "", att.file_type or "", source_sha,
                 att.text_sha256 or att.content_hash or "", att.text or "",
                 _json(meta), att.extraction_status or "", _json(att.warnings or []),
                 source_sha, att.text_sha256 or "", att.parser_version or "",
                 att.ocr_version or "", cached))
        return True

    def save_entities(self, email_id: str, entities: list):
        rows = []
        for e in entities or []:
            if isinstance(e, dict):
                rows.append((email_id, e.get("type", ""), e.get("text", ""),
                             int(e.get("count", 1) or 1)))
            else:
                rows.append((email_id, getattr(e, "type", ""), getattr(e, "text", ""),
                             int(getattr(e, "count", 1) or 1)))
        if rows:
            self.db.executemany(
                "INSERT OR REPLACE INTO entities (email_id, entity_type, text, count) VALUES (?,?,?,?)",
                rows)

    def save_rule(self, email_id: str, rr: RuleResult):
        self.db.execute(
            "INSERT OR REPLACE INTO rule_matches (email_id, result_json, rule_score) VALUES (?,?,?)",
            (email_id, _json(rr.to_dict()), float(rr.rule_score)))
        for p in rr.matched_patterns:
            self.db.execute(
                """INSERT OR REPLACE INTO pattern_matches
                   (email_id, pattern_id, category, name, pattern_score, matched_terms,
                    evidence_snippets, window)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (email_id, p.pattern_id, p.category, p.name, float(p.pattern_score),
                 _json(p.matched_terms), _json(p.evidence_snippets), p.window or ""))

    def save_llm(self, email_id: str, llm: LLMResult):
        self.db.execute(
            "INSERT OR REPLACE INTO llm_results (email_id, llm_json, llm_status, evidence_stage) VALUES (?,?,?,?)",
            (email_id, _json(llm.to_dict()), llm.llm_status, llm.evidence_stage or ""))

    def save_score(self, email_id: str, fs: FinalScore, unified: Any = None):
        self.db.execute(
            """INSERT OR REPLACE INTO scores
               (email_id, final_score, priority, dimensions_json, stage_adjustment,
                negative_adjustment, governance_score, primary_track, unified_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (email_id, float(fs.final_score), fs.priority,
             _json(fs.dimension_scores), float(fs.stage_adjustment),
             float(fs.negative_adjustment),
             float(getattr(fs, "governance_score", 0.0) or 0.0),
             str(getattr(fs, "primary_track", "") or ""),
             _json(unified.to_dict() if hasattr(unified, "to_dict") else unified or {})))

    def save_governance(self, email_id: str, governance: Any):
        if governance is None:
            return
        g = governance.to_dict() if hasattr(governance, "to_dict") else dict(governance)
        existing = self.db.query_one("SELECT * FROM governance_results WHERE email_id=?", (email_id,))

        # 兼容两种输入：GovernanceResult.to_dict() 与 ScreeningRecord 字段名。
        cats = g.get("governance_categories") if g.get("governance_categories") is not None else g.get("categories")
        kws = g.get("governance_keywords") if g.get("governance_keywords") is not None else g.get("keywords")
        pats = g.get("governance_patterns") if g.get("governance_patterns") is not None else g.get("patterns")
        negs = g.get("governance_negatives") if g.get("governance_negatives") is not None else g.get("negatives")
        enh = g.get("governance_enhance") if g.get("governance_enhance") is not None else g.get("enhance")
        dims = g.get("governance_dims") if g.get("governance_dims") is not None else g.get("dims")
        details = g.get("governance_details") if g.get("governance_details") is not None else g.get("details")
        if existing is not None:
            # 用已有完整值补齐 ScreeningRecord 持久化时的空字段，避免二次写入覆盖。
            if not cats:
                try: cats = json.loads(existing["categories_json"] or "[]")
                except Exception: cats = cats or []
            if not kws:
                try: kws = json.loads(existing["keywords_json"] or "{}")
                except Exception: kws = kws or {}
            if not pats:
                try: pats = json.loads(existing["patterns_json"] or "[]")
                except Exception: pats = pats or []
            if not negs:
                try: negs = json.loads(existing["negatives_json"] or "[]")
                except Exception: negs = negs or []
            if not enh:
                try: enh = json.loads(existing["enhance_json"] or "{}")
                except Exception: enh = enh or {}
            if not dims:
                try: dims = json.loads(existing["dimensions_json"] or "{}")
                except Exception: dims = dims or {}
            if not details:
                try: details = json.loads(existing["details_json"] or "[]")
                except Exception: details = details or []
        self.db.execute(
            """INSERT OR REPLACE INTO governance_results
               (email_id, categories_json, keywords_json, patterns_json, negatives_json,
                enhance_json, score, priority, dimensions_json, details_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (email_id, _json(cats or []), _json(kws or {}), _json(pats or []),
             _json(negs or []), _json(enh or {}),
             float(g.get("governance_score") or g.get("score") or 0.0),
             str(g.get("governance_priority") or g.get("priority") or ""),
             _json(dims or {}), _json(details or []), _now()))

    def start_analysis_run(self, email_id: str, *, pipeline_version: str = "4.1",
                           political_rule_pack_hash: str = "",
                           governance_rule_pack_hash: str = "",
                           scoring_rule_hash: str = "", prompt_hash: str = "",
                           llm_mode: str = "", llm_provider: str = "",
                           llm_model: str = "") -> int:
        cur = self.db.execute(
            """INSERT INTO analysis_runs
               (email_id, pipeline_version, political_rule_pack_hash, governance_rule_pack_hash,
                scoring_rule_hash, prompt_hash, llm_mode, llm_provider, llm_model,
                analysis_started_at, result_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (email_id, pipeline_version, political_rule_pack_hash, governance_rule_pack_hash,
             scoring_rule_hash, prompt_hash, llm_mode, llm_provider, llm_model,
             _now(), "running"))
        return int(cur.lastrowid)

    def finish_analysis_run(self, run_id: int, status: str = "ok") -> None:
        self.db.execute(
            "UPDATE analysis_runs SET analysis_finished_at=?, result_status=? WHERE id=?",
            (_now(), status, int(run_id)))

    def latest_analysis(self, email_id: str) -> Optional[dict]:
        row = self.db.query_one(
            "SELECT * FROM analysis_runs WHERE email_id=? ORDER BY id DESC LIMIT 1", (email_id,))
        return dict(row) if row is not None else None

    def analysis_is_stale(self, email_id: str, political_rule_pack_hash: str,
                          governance_rule_pack_hash: str, scoring_rule_hash: str,
                          prompt_hash: str) -> bool:
        latest = self.latest_analysis(email_id)
        if not latest:
            return True
        return any([
            (latest.get("political_rule_pack_hash") or "") != (political_rule_pack_hash or ""),
            (latest.get("governance_rule_pack_hash") or "") != (governance_rule_pack_hash or ""),
            (latest.get("scoring_rule_hash") or "") != (scoring_rule_hash or ""),
            (latest.get("prompt_hash") or "") != (prompt_hash or ""),
        ])

    # ---------- higher-level ----------
    def save_review_queue(self, rec: ScreeningRecord):
        if rec.score is None or rec.email is None:
            return
        if rec.score.priority in ("D",) and rec.score.final_score < 40:
            return
        self.db.execute(
            """INSERT OR REPLACE INTO review_queue
               (email_id, priority, final_score, subject, sender, summary_zh, verification_targets, queued_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (rec.email_id, rec.score.priority, float(rec.score.final_score),
             rec.email.subject or "", rec.email.sender or "",
             rec.summary_zh or "", _json(rec.verification_targets or []), _now()))

    def save_named_channel_recommendations(self, email_id: str, named_dict: dict) -> int:
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
                             _json(roles), float(score), i, reason, _now()))
        if rows:
            self.db.executemany(
                """INSERT OR REPLACE INTO named_channel_recommendations
                   (email_id, entity_id, entity_name, entity_type, channel_group,
                    channel_role, fit_score, rank, reason, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
        return len(rows)

    def save_release_recommendation(self, rr: "ReleaseRecommendation"):
        self.db.execute(
            """INSERT OR REPLACE INTO release_recommendations
               (email_id, primary_route, secondary_routes, avoid_routes, route_confidence,
                prepublication_verification_required, verification_before_release,
                formal_referral_recommended, formal_referral_type, release_risks,
                reason, recommended_release_sequence, headline_angle, editor_note,
                rule_hits, source, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rr.email_id, rr.primary_route or "",
             _json(rr.secondary_routes or []), _json(rr.avoid_routes or []),
             float(rr.route_confidence or 0.0),
             1 if rr.prepublication_verification_required else 0,
             _json(rr.verification_before_release or []),
             1 if rr.formal_referral_recommended else 0,
             _json(rr.formal_referral_type or []),
             _json(rr.release_risks or []), rr.reason or "",
             _json(rr.recommended_release_sequence or []),
             rr.headline_angle or "", rr.editor_note or "",
             _json(rr.rule_hits or []), rr.source or "rule", _now()))

    def save_record(self, rec: ScreeningRecord, store_full: bool = True, force_email: bool = False):
        if rec.email is not None and store_full:
            self.save_email(rec.email, force=force_email)
        if rec.rule is not None:
            self.save_entities(rec.email_id, rec.rule.entities)
            self.save_rule(rec.email_id, rec.rule)
        if rec.llm is not None:
            self.save_llm(rec.email_id, rec.llm)
        if rec.score is not None:
            self.save_score(rec.email_id, rec.score, rec.unified_signals)
            self.save_review_queue(rec)
        # V4 governance persistence
        if rec.governance_categories or rec.governance_score:
            self.save_governance(rec.email_id, {
                "governance_categories": rec.governance_categories,
                "governance_keywords": {k: [h.to_dict() if hasattr(h, "to_dict") else h for h in v]
                                        for k, v in (rec.governance_keywords or {}).items()},
                "governance_patterns": [p.to_dict() if hasattr(p, "to_dict") else p
                                        for p in (rec.governance_patterns or [])],
                "governance_score": rec.governance_score,
                "governance_priority": rec.governance_priority,
                "governance_dims": rec.governance_dims,
                "governance_negatives": [n.to_dict() if hasattr(n, "to_dict") else n
                                         for n in (rec.governance_negatives or [])],
                "governance_enhance": rec.governance_enhance or {},
                "governance_details": rec.governance_details or [],
            })
