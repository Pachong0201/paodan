"""报告导出：screening_results.jsonl + priority_queue.csv."""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import List

from ..models import ScreeningRecord
from ..security.spreadsheet import spreadsheet_safe_row

logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    "priority", "final_score", "subject", "sender", "date", "target_persons",
    "categories", "matched_patterns", "one_sentence_summary",
    "reason_for_attention", "source_path",
    # 首发渠道推荐（V2）
    "recommended_release_route", "secondary_release_routes",
    "formal_referral_recommended", "release_risk", "release_reason",
    # 具名渠道推荐（V3）
    "top_media", "top_disclosure_actor", "top_amplifier",
    "top_formal_channel", "named_channel_reason",
    # V4.1 双轨统一信号
    "primary_track", "governance_categories", "governance_score",
]


def _top_name(items) -> str:
    if not items:
        return ""
    it = items[0]
    if isinstance(it, dict):
        return str(it.get("name") or it.get("entity_id") or "")
    return str(getattr(it, "name", "") or getattr(it, "entity_id", ""))


def _named_top_reason(named: dict) -> str:
    for key in ("recommended_media", "recommended_disclosure_actors",
                "recommended_formal_channels"):
        for it in named.get(key) or []:
            if isinstance(it, dict) and it.get("reason"):
                return str(it["reason"])
    return ""


def export_jsonl(records: List[ScreeningRecord], path: str | Path,
                 min_priority_value: float = 0.0) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            if rec.score is None or rec.score.final_score < min_priority_value:
                continue
            f.write(rec.to_jsonl_line() + "\n")
            written += 1
    logger.info("JSONL 导出 %d 条 -> %s", written, path)
    return written


def export_csv(records: List[ScreeningRecord], path: str | Path,
               min_priority_value: float = 0.0) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for rec in records:
            if rec.score is None or rec.email is None or rec.score.final_score < min_priority_value:
                continue
            llm = rec.llm
            rule = rec.rule
            rel = rec.release_recommendation or {}
            named = rec.named_channel_recommendation or {}
            row = {
                "priority": rec.score.priority,
                "final_score": rec.score.final_score,
                "subject": rec.email.subject or "",
                "sender": rec.email.sender or "",
                "date": rec.email.date or "",
                "target_persons": "、".join((llm.target_persons if llm else []) or
                                           (rule.target_persons_found if rule else []) or []),
                "categories": "、".join((llm.categories if llm else []) or
                                       (rule.matched_categories if rule else []) or []),
                "matched_patterns": "/".join(p.pattern_id for p in (rule.matched_patterns if rule else [])),
                "one_sentence_summary": (llm.one_sentence_summary if llm else "") or rec.summary_zh,
                "reason_for_attention": (llm.reason_for_attention if llm else "") or "",
                "source_path": rec.email.source_path or "",
                "recommended_release_route": rel.get("primary_route") or "",
                "secondary_release_routes": "、".join(rel.get("secondary_routes") or []),
                "formal_referral_recommended": "是" if rel.get("formal_referral_recommended") else "",
                "release_risk": "、".join(rel.get("release_risks") or []),
                "release_reason": rel.get("reason") or "",
                "top_media": _top_name(named.get("recommended_media") or []),
                "top_disclosure_actor": _top_name(named.get("recommended_disclosure_actors") or []),
                "top_amplifier": _top_name(named.get("recommended_amplifiers") or []),
                "top_formal_channel": _top_name(named.get("recommended_formal_channels") or []),
                "named_channel_reason": _named_top_reason(named),
                "primary_track": (rec.unified_signals.primary_track
                                  if getattr(rec, "unified_signals", None) is not None else ""),
                "governance_categories": "、".join(rec.governance_categories or []),
                "governance_score": float(rec.governance_score or 0.0),
            }
            w.writerow(spreadsheet_safe_row(row))
            written += 1
    logger.info("CSV 导出 %d 条 -> %s", written, path)
    return written
