"""Dashboard integration test DB helpers (synthetic only)."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.storage.database import Database
from app.storage.migrations import SCHEMA_VERSION


def create_schema(path: Path) -> Database:
    db = Database(path)
    return db


def close_db(db):
    db.close()


def insert_email(db: Database, email_id: str, subject: str,
                 processed_at: str | None = None, sender: str = "sender@example.com",
                 body: str = "RAW_BODY_CANARY_8192") -> None:
    if processed_at is None:
        processed_at = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """INSERT OR REPLACE INTO emails
           (email_id, subject, sender, body_text, source_path, processed_at)
           VALUES (?,?,?,?,?,?)""",
        (email_id, subject, sender, body, "data/inbox/x.eml", processed_at))


def insert_score(db: Database, email_id: str, priority: str, final_score: float,
                 primary_track: str = "POLITICAL", governance_score: float = 0.0,
                 unified: Optional[dict] = None) -> None:
    unified = unified or {
        "political_categories": [], "governance_categories": [],
        "political_patterns": [], "governance_patterns": [],
        "political_score": final_score, "governance_score": governance_score,
        "primary_track": primary_track,
    }
    db.execute(
        """INSERT OR REPLACE INTO scores
           (email_id, final_score, priority, governance_score, primary_track, unified_json)
           VALUES (?,?,?,?,?,?)""",
        (email_id, final_score, priority, governance_score, primary_track,
         json.dumps(unified, ensure_ascii=False)))


def insert_review_queue(db: Database, email_id: str, priority: str, score: float,
                        summary_zh: str, verification_targets: Optional[List[str]] = None) -> None:
    db.execute(
        """INSERT OR REPLACE INTO review_queue
           (email_id, priority, final_score, subject, sender, summary_zh, verification_targets, queued_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (email_id, priority, score, "", "", summary_zh,
         json.dumps(verification_targets or [], ensure_ascii=False),
         datetime.now().isoformat(timespec="seconds")))


def insert_llm(db: Database, email_id: str, llm_json: dict) -> None:
    db.execute(
        "INSERT OR REPLACE INTO llm_results (email_id, llm_json, llm_status, evidence_stage) VALUES (?,?,?,?)",
        (email_id, json.dumps(llm_json, ensure_ascii=False), "ok", llm_json.get("evidence_stage", "E1")))


def insert_governance(db: Database, email_id: str, categories: List[str], score: float = 0.0) -> None:
    db.execute(
        """INSERT OR REPLACE INTO governance_results
           (email_id, categories_json, keywords_json, patterns_json, negatives_json,
            enhance_json, score, priority, dimensions_json, details_json, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (email_id, json.dumps(categories), "{}", "[]", "[]", "{}", score, "B", "{}", "[]", ""))


def insert_release(db: Database, email_id: str, primary_route: str = "R3") -> None:
    db.execute(
        """INSERT OR REPLACE INTO release_recommendations
           (email_id, primary_route, secondary_routes, avoid_routes, route_confidence,
            verification_before_release, formal_referral_recommended, formal_referral_type,
            release_risks, reason, recommended_release_sequence, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (email_id, primary_route, '["R2"]', '["R1"]', 0.8, '["核验"]', 0, '["NONE"]',
         '["EVIDENCE_AUTHENTICITY"]', "理由", '[]', ""))


def insert_named(db: Database, email_id: str, group: str, name: str, rank: int = 1,
                 reason: str = "") -> None:
    db.execute(
        """INSERT INTO named_channel_recommendations
           (email_id, entity_id, entity_name, entity_type, channel_group, channel_role,
            fit_score, rank, reason, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (email_id, f"id_{name}", name, "MEDIA", group, '["INVESTIGATIVE"]', 90, rank, reason, ""))


def insert_attachment(db: Database, email_id: str, filename: str = "evidence.pdf",
                      file_type: str = "pdf", extraction_status: str = "success",
                      warnings: Optional[list] = None) -> None:
    db.execute(
        """INSERT INTO attachments
           (email_id, filename, file_type, text, extraction_status, warnings)
           VALUES (?,?,?,?,?,?)""",
        (email_id, filename, file_type, "ATTACHMENT_PRIVATE_CANARY_E817AC21",
         extraction_status, json.dumps(warnings or [])))


def insert_analysis(db: Database, email_id: str, pipeline_version: str = "4.1.1") -> None:
    db.execute(
        """INSERT INTO analysis_runs
           (email_id, pipeline_version, analysis_finished_at, result_status)
           VALUES (?,?,?,?)""",
        (email_id, pipeline_version, datetime.now().isoformat(timespec="seconds"), "ok"))
