"""Dashboard 查询层：只读现有 paodan SQLite；只写 dashboard_reviews。

统计口径说明：
- “今日/最近 N 日”统一按 emails.processed_at 的日期（本机日期）计算；
- “待人工审核” = scores.priority in (S,A,B) 且 dashboard_review_status in
  (UNREVIEWED, VERIFY, PRIORITY)；无 dashboard_reviews 行视为 UNREVIEWED。
- Dashboard 不触发任何 RuleEngine/LLM/GovernanceEngine/重新评分。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .privacy import dashboard_safe_list, dashboard_safe_text
from .view_models import (
    AttachmentInfo,
    DashboardStats,
    EmailDetail,
    EmailListItem,
    NamedChannelInfo,
    PRIORITY_RANK,
    ReviewState,
    REVIEW_STATUS_LABELS,
    TRACK_LABELS,
)

REVIEW_STATUSES = set(REVIEW_STATUS_LABELS.keys())
SAFE_ENTITY_TYPES = {
    "TARGET", "PUBLIC_PERSON", "PUBLIC_ORGANIZATION",
    "ORGANIZATION", "COMPANY", "GOVERNMENT_AGENCY",
    "POLITICAL_PARTY", "PROJECT", "LOCATION", "ROLE",
}
PRIORITY_CASE = "CASE sc.priority WHEN 'S' THEN 5 WHEN 'A' THEN 4 WHEN 'B' THEN 3 WHEN 'C' THEN 2 ELSE 1 END"


def safe_json_loads(value: Any, default: Any = None) -> Any:
    if not value:
        return default
    try:
        data = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return data


def _today_local() -> str:
    return date.today().isoformat()


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def get_dashboard_stats(conn: sqlite3.Connection, today: str | None = None,
                        default_days: int = 7) -> DashboardStats:
    today = today or _today_local()
    start7 = (date.fromisoformat(today) - timedelta(days=default_days - 1)).isoformat()

    def one(sql: str, params: tuple) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0) if row else 0

    today_total = one(
        "SELECT COUNT(*) FROM emails WHERE date(processed_at)=?", (today,))
    today_sa = one(
        """SELECT COUNT(*) FROM emails e JOIN scores sc ON sc.email_id=e.email_id
           WHERE date(e.processed_at)=? AND sc.priority IN ('S','A')""", (today,))
    today_gov = one(
        """SELECT COUNT(*) FROM emails e JOIN scores sc ON sc.email_id=e.email_id
           WHERE date(e.processed_at)=?
             AND (sc.governance_score > 0 OR sc.primary_track IN ('GOVERNANCE','MIXED'))""",
        (today,))
    pending = one(
        """SELECT COUNT(*) FROM emails e
           JOIN scores sc ON sc.email_id=e.email_id
           LEFT JOIN dashboard_reviews dr ON dr.email_id=e.email_id
           WHERE sc.priority IN ('S','A','B')
             AND COALESCE(dr.review_status,'UNREVIEWED') IN ('UNREVIEWED','VERIFY','PRIORITY')""",
        ())
    recent7_total = one(
        "SELECT COUNT(*) FROM emails WHERE date(processed_at)>=?", (start7,))
    recent7_sab = one(
        """SELECT COUNT(*) FROM emails e JOIN scores sc ON sc.email_id=e.email_id
           WHERE date(e.processed_at)>=? AND sc.priority IN ('S','A','B')""", (start7,))

    rows = conn.execute(
        """SELECT COALESCE(sc.primary_track,'NONE') AS track, COUNT(*) AS n
           FROM emails e LEFT JOIN scores sc ON sc.email_id=e.email_id
           GROUP BY COALESCE(sc.primary_track,'NONE')""").fetchall()
    track_counts = {row["track"]: int(row["n"]) for row in rows}
    for track in ("POLITICAL", "GOVERNANCE", "MIXED", "NONE"):
        track_counts.setdefault(track, 0)

    return DashboardStats(
        today_total=today_total,
        today_sa=today_sa,
        today_governance=today_gov,
        pending_review=pending,
        recent7_total=recent7_total,
        recent7_sab=recent7_sab,
        track_counts=track_counts,
    )


# ---------------------------------------------------------------------------
# List / filters / pagination
# ---------------------------------------------------------------------------
def _build_list_sql(filters: Dict[str, Any], for_review: bool = False) -> tuple[str, list]:
    where: List[str] = []
    params: list = []

    if for_review:
        default_statuses = ["UNREVIEWED", "VERIFY", "PRIORITY"]
        if not filters.get("review_status"):
            where.append("COALESCE(dr.review_status,'UNREVIEWED') IN (%s)" % ",".join("?" * len(default_statuses)))
            params.extend(default_statuses)
    if filters.get("priorities"):
        where.append("sc.priority IN (%s)" % ",".join("?" * len(filters["priorities"])))
        params.extend(filters["priorities"])
    if filters.get("track"):
        where.append("COALESCE(sc.primary_track,'NONE')=?")
        params.append(filters["track"])
    if filters.get("review_status"):
        where.append("COALESCE(dr.review_status,'UNREVIEWED')=?")
        params.append(filters["review_status"])
    if filters.get("date_from"):
        where.append("date(e.processed_at)>=date(?)")
        params.append(filters["date_from"])
    if filters.get("date_to"):
        where.append("date(e.processed_at)<=date(?)")
        params.append(filters["date_to"])
    category = (filters.get("category") or "").strip().upper()
    if category:
        where.append("(COALESCE(sc.unified_json,'') LIKE ? OR COALESCE(gr.categories_json,'') LIKE ? OR "
                     "COALESCE(sc.primary_track,'') LIKE ?)")
        like = f"%{category}%"
        params.extend([like, like, like])
    search = (filters.get("q") or "").strip()
    if search:
        # 只搜 subject / summary_zh / LLM one_sentence_summary，不搜整个 llm_json。
        where.append("(e.subject LIKE ? OR COALESCE(rq.summary_zh,'') LIKE ? OR "
                     "COALESCE(json_extract(ll.llm_json, '$.one_sentence_summary'),'') LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])

    sql = f"""
        SELECT e.email_id, e.subject, e.processed_at,
               sc.priority, sc.final_score, sc.primary_track, sc.unified_json,
               rq.summary_zh, rq.verification_targets,
               dr.review_status, dr.editor_note, dr.reviewed_at, dr.updated_at, dr.created_at,
               ll.llm_json,
               CASE WHEN rq.email_id IS NOT NULL THEN 1 ELSE 0 END AS in_review
        FROM emails e
        LEFT JOIN scores sc ON sc.email_id=e.email_id
        LEFT JOIN review_queue rq ON rq.email_id=e.email_id
        LEFT JOIN dashboard_reviews dr ON dr.email_id=e.email_id
        LEFT JOIN llm_results ll ON ll.email_id=e.email_id
        LEFT JOIN governance_results gr ON gr.email_id=e.email_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + PRIORITY_CASE + " DESC, sc.final_score DESC, e.processed_at DESC"
    return sql, params


def _summary_from_row(row: sqlite3.Row) -> str:
    if row["summary_zh"]:
        return str(row["summary_zh"])
    llm = safe_json_loads(row["llm_json"], {})
    if isinstance(llm, dict):
        return str(llm.get("one_sentence_summary") or llm.get("reason_for_attention") or "")
    return ""


def _categories_from_unified(unified: Any) -> tuple[list, list]:
    if isinstance(unified, dict):
        return (
            [str(x) for x in (unified.get("political_categories") or []) if x],
            [str(x) for x in (unified.get("governance_categories") or []) if x],
        )
    return [], []


def _row_to_list_item(row: sqlite3.Row) -> EmailListItem:
    unified = safe_json_loads(row["unified_json"], {})
    pol_cats, gov_cats = _categories_from_unified(unified)
    status = str(row["review_status"] or "UNREVIEWED")
    return EmailListItem(
        email_id=str(row["email_id"]),
        subject=dashboard_safe_text(row["subject"] or ""),
        priority=str(row["priority"] or "D"),
        final_score=float(row["final_score"] or 0.0),
        primary_track=str(row["primary_track"] or "NONE"),
        political_categories=pol_cats,
        governance_categories=gov_cats,
        summary=dashboard_safe_text(_summary_from_row(row)),
        review_status=status,
        review_status_label=REVIEW_STATUS_LABELS.get(status, status),
        processed_at=str(row["processed_at"] or ""),
        in_system_review_queue=bool(row["in_review"]),
    )


def list_emails(conn: sqlite3.Connection, filters: Optional[Dict[str, Any]] = None,
                page: int = 1, page_size: int = 30) -> tuple[List[EmailListItem], int]:
    filters = filters or {}
    sql, params = _build_list_sql(filters)
    count_sql = "SELECT COUNT(*) FROM (" + sql.replace(
        "ORDER BY " + PRIORITY_CASE + " DESC, sc.final_score DESC, e.processed_at DESC", "") + ")"
    count = int(conn.execute(count_sql, params).fetchone()[0] or 0)
    total_pages = max(1, (count + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    offset = (page - 1) * page_size
    rows = conn.execute(sql + " LIMIT ? OFFSET ?", params + [page_size, offset]).fetchall()
    return [_row_to_list_item(r) for r in rows], count


def list_review_queue(conn: sqlite3.Connection, filters: Optional[Dict[str, Any]] = None,
                      page: int = 1, page_size: int = 30) -> tuple[List[EmailListItem], int]:
    filters = dict(filters or {})
    filters.setdefault("review_status", "")
    sql, params = _build_list_sql(filters, for_review=True)
    count_sql = "SELECT COUNT(*) FROM (" + sql.replace(
        "ORDER BY " + PRIORITY_CASE + " DESC, sc.final_score DESC, e.processed_at DESC", "") + ")"
    count = int(conn.execute(count_sql, params).fetchone()[0] or 0)
    total_pages = max(1, (count + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    offset = (page - 1) * page_size
    order_sql = (
        "CASE COALESCE(dr.review_status,'UNREVIEWED') WHEN 'PRIORITY' THEN 0 ELSE 1 END, "
        + PRIORITY_CASE + " DESC, sc.final_score DESC, e.processed_at DESC"
    )
    base = sql.split(" ORDER BY ")[0]
    rows = conn.execute(base + " ORDER BY " + order_sql + " LIMIT ? OFFSET ?",
                        params + [page_size, offset]).fetchall()
    return [_row_to_list_item(r) for r in rows], count


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
def _entity_text_safe(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    if any(x in s.lower() for x in ("@", "\\", "/home/", "c:\\", "sk-", "line-", "http://", "https://")):
        return False
    if any(ch.isdigit() for ch in s) and len([c for c in s if c.isdigit()]) >= 6:
        return False
    return True


def _resolve_political_score(unified: Any, score: Any) -> float:
    if isinstance(unified, dict) and unified.get("political_score") is not None:
        try:
            return float(unified["political_score"])
        except (TypeError, ValueError):
            pass
    if score is not None:
        # 仅在 POLITICAL-only 且没有 unified 分数时才回退 final_score。
        if str(score["primary_track"] or "") == "POLITICAL":
            try:
                return float(score["final_score"] or 0.0)
            except (TypeError, ValueError):
                pass
    return 0.0


def _resolve_governance_score(unified: Any, score: Any, gov: Any) -> float:
    if isinstance(unified, dict) and unified.get("governance_score") is not None:
        try:
            return float(unified["governance_score"])
        except (TypeError, ValueError):
            pass
    if score is not None and score["governance_score"] is not None:
        try:
            return float(score["governance_score"])
        except (TypeError, ValueError):
            pass
    if gov is not None and gov["score"] is not None:
        try:
            return float(gov["score"])
        except (TypeError, ValueError):
            pass
    return 0.0


def get_email_detail(conn: sqlite3.Connection, email_id: str) -> Optional[EmailDetail]:
    email = conn.execute("SELECT email_id, subject, processed_at FROM emails WHERE email_id=?",
                         (email_id,)).fetchone()
    if email is None:
        return None
    eid = email["email_id"]

    score = conn.execute("SELECT * FROM scores WHERE email_id=?", (eid,)).fetchone()
    review_q = conn.execute("SELECT * FROM review_queue WHERE email_id=?", (eid,)).fetchone()
    llm = conn.execute("SELECT * FROM llm_results WHERE email_id=?", (eid,)).fetchone()
    gov = conn.execute("SELECT * FROM governance_results WHERE email_id=?", (eid,)).fetchone()
    rule = conn.execute("SELECT * FROM rule_matches WHERE email_id=?", (eid,)).fetchone()
    release = conn.execute("SELECT * FROM release_recommendations WHERE email_id=?", (eid,)).fetchone()
    named_rows = conn.execute(
        """SELECT entity_id, entity_name, entity_type, channel_group, channel_role,
                  fit_score, rank, reason
           FROM named_channel_recommendations WHERE email_id=?
           ORDER BY channel_group, rank""", (eid,)).fetchall()
    att_rows = conn.execute(
        """SELECT filename, file_type, extraction_status, warnings
           FROM attachments WHERE email_id=? ORDER BY id""", (eid,)).fetchall()
    analysis = conn.execute(
        """SELECT pipeline_version, analysis_finished_at FROM analysis_runs
           WHERE email_id=? ORDER BY id DESC LIMIT 1""", (eid,)).fetchone()
    dr = conn.execute("SELECT * FROM dashboard_reviews WHERE email_id=?", (eid,)).fetchone()

    unified = safe_json_loads(score["unified_json"] if score else None, {})
    if not isinstance(unified, dict):
        unified = {}

    pol_cats = [str(x) for x in (unified.get("political_categories") or []) if x]
    gov_cats = [str(x) for x in (unified.get("governance_categories") or []) if x]
    pol_patterns = [str(x) for x in (unified.get("political_patterns") or []) if x]
    gov_patterns = [str(x) for x in (unified.get("governance_patterns") or []) if x]

    rule_json = safe_json_loads(rule["result_json"] if rule else None, {})
    if isinstance(rule_json, dict):
        if not pol_cats:
            pol_cats = [str(x) for x in (rule_json.get("matched_categories") or []) if x]
        if not pol_patterns:
            for p in (rule_json.get("matched_patterns") or []):
                pid = str(p.get("pattern_id") or "") if isinstance(p, dict) else str(p)
                if pid and pid not in pol_patterns:
                    pol_patterns.append(pid)
    gov_json = safe_json_loads(gov["categories_json"] if gov else None, [])
    if isinstance(gov_json, list):
        gov_cats = [str(x) for x in gov_json if x] or gov_cats

    llm_json = safe_json_loads(llm["llm_json"] if llm else None, {})
    if not isinstance(llm_json, dict):
        llm_json = {}

    summary = dashboard_safe_text((review_q["summary_zh"] if review_q and review_q["summary_zh"] else "")
                                 or llm_json.get("one_sentence_summary") or "")
    reason = dashboard_safe_text(llm_json.get("reason_for_attention") or "")
    verification = []
    if review_q and review_q["verification_targets"]:
        verification = dashboard_safe_list(safe_json_loads(review_q["verification_targets"], []) or [])
    else:
        verification = dashboard_safe_list(llm_json.get("verification_targets") or [])

    evidence_shapes = [str(x) for x in (unified.get("evidence_shapes") or []) if x]
    money = []
    for m in (unified.get("money") or []) if isinstance(unified, dict) else []:
        if isinstance(m, dict):
            amount = m.get("amount")
            cur = m.get("currency") or "TWD"
            if amount is not None:
                money.append({"amount": float(amount), "currency": str(cur)})

    entities: List[str] = []

    def _add_entity(typ: str, text: str) -> None:
        if typ not in SAFE_ENTITY_TYPES:
            return
        if not _entity_text_safe(text):
            return
        safe = dashboard_safe_text(text)
        if safe and safe not in entities:
            entities.append(safe)

    raw_entities = (unified.get("entities") or []) if isinstance(unified, dict) else []
    for item in raw_entities:
        if isinstance(item, dict):
            _add_entity(str(item.get("type") or ""), str(item.get("text") or ""))
        else:
            _add_entity("", str(item))
    if isinstance(rule_json, dict):
        for item in (rule_json.get("entities") or []):
            if isinstance(item, dict):
                _add_entity(str(item.get("type") or ""), str(item.get("text") or ""))
            else:
                _add_entity("", str(item))
    pattern_ids = list(dict.fromkeys(pol_patterns + gov_patterns))

    release_dict = None
    if release:
        release_dict = dict(release)
        for col in ("secondary_routes", "avoid_routes", "verification_before_release",
                    "formal_referral_type", "release_risks", "recommended_release_sequence",
                    "rule_hits"):
            release_dict[col] = safe_json_loads(release[col], [])
        for col in ("reason", "headline_angle", "editor_note"):
            if release_dict.get(col) is not None:
                release_dict[col] = dashboard_safe_text(release_dict[col])
        for col in ("verification_before_release", "release_risks", "secondary_routes",
                    "avoid_routes", "formal_referral_type", "rule_hits"):
            release_dict[col] = dashboard_safe_list(release_dict[col] or [])
        # sequence 内 action/reason 自由文本脱敏
        seq = []
        for item in release_dict.get("recommended_release_sequence") or []:
            if isinstance(item, dict):
                item = dict(item)
                for k in ("action", "reason", "route"):
                    if item.get(k) is not None:
                        item[k] = dashboard_safe_text(item[k])
            seq.append(item)
        release_dict["recommended_release_sequence"] = seq

    named_infos = []
    for r in named_rows:
        roles = safe_json_loads(r["channel_role"], [])
        if not isinstance(roles, list):
            roles = []
        named_infos.append(NamedChannelInfo(
            group=str(r["channel_group"] or ""),
            entity_name=str(r["entity_name"] or r["entity_id"] or ""),
            entity_type=str(r["entity_type"] or ""),
            fit_score=float(r["fit_score"] or 0.0),
            reason=dashboard_safe_text(r["reason"] or ""),
            roles=dashboard_safe_list(roles),
        ))

    att_infos = []
    for a in att_rows:
        try:
            warn = len(safe_json_loads(a["warnings"], []) or [])
        except Exception:
            warn = 0
        att_infos.append(AttachmentInfo(
            filename=dashboard_safe_text(a["filename"] or ""),
            file_type=str(a["file_type"] or ""),
            extraction_status=str(a["extraction_status"] or ""),
            warnings_count=int(warn),
        ))

    status = "UNREVIEWED"
    note = ""
    if dr:
        status = str(dr["review_status"] or "UNREVIEWED")
        note = str(dr["editor_note"] or "")
    review = ReviewState(email_id=eid, review_status=status, editor_note=note,
                         reviewed_at=str(dr["reviewed_at"] or "") if dr else "",
                         updated_at=str(dr["updated_at"] or "") if dr else "",
                         created_at=str(dr["created_at"] or "") if dr else "")

    return EmailDetail(
        email_id=eid,
        subject=dashboard_safe_text(email["subject"] or ""),
        processed_at=str(email["processed_at"] or ""),
        priority=str(score["priority"] if score else "D"),
        final_score=float(score["final_score"] if score else 0.0),
        primary_track=str(score["primary_track"] if score else "NONE"),
        political_score=_resolve_political_score(unified, score),
        governance_score=_resolve_governance_score(unified, score, gov),
        political_categories=pol_cats,
        governance_categories=gov_cats,
        political_patterns=pol_patterns,
        governance_patterns=gov_patterns,
        summary=summary,
        reason=reason,
        evidence_stage=str(llm_json.get("evidence_stage") or unified.get("evidence_stage") or ""),
        evidence_shapes=evidence_shapes,
        money_facts=money,
        entities=entities,
        pattern_ids=pattern_ids,
        verification_targets=verification,
        release=release_dict,
        named_channels=named_infos,
        attachments=att_infos,
        review=review,
        in_system_review_queue=review_q is not None,
        analysis_pipeline_version=str(analysis["pipeline_version"] or "") if analysis else "",
        analysis_finished_at=str(analysis["analysis_finished_at"] or "") if analysis else "",
    )


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------
def get_review(conn: sqlite3.Connection, email_id: str) -> ReviewState:
    dr = conn.execute("SELECT * FROM dashboard_reviews WHERE email_id=?", (email_id,)).fetchone()
    if dr is None:
        return ReviewState(email_id=email_id)
    return ReviewState(
        email_id=email_id,
        review_status=str(dr["review_status"] or "UNREVIEWED"),
        editor_note=str(dr["editor_note"] or ""),
        reviewed_at=str(dr["reviewed_at"] or ""),
        updated_at=str(dr["updated_at"] or ""),
        created_at=str(dr["created_at"] or ""),
    )


def save_review(conn: sqlite3.Connection, email_id: str, review_status: str,
                editor_note: str) -> ReviewState:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # 原子 UPSERT：并发 POST 不会因先查后写产生 UNIQUE 冲突。
    conn.execute(
        """INSERT INTO dashboard_reviews
           (email_id, review_status, editor_note, reviewed_at, updated_at, created_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(email_id) DO UPDATE SET
             review_status=excluded.review_status,
             editor_note=excluded.editor_note,
             reviewed_at=excluded.reviewed_at,
             updated_at=excluded.updated_at""",
        (email_id, review_status, editor_note, now, now, now))
    return get_review(conn, email_id)


def get_categories() -> List[str]:
    from .view_models import _load_category_labels
    return sorted(_load_category_labels().keys())
