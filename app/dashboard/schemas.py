"""Dashboard 输入校验（review status whitelist, note length）。"""
from __future__ import annotations

from typing import Dict

from .view_models import REVIEW_STATUS_LABELS

REVIEW_STATUSES = set(REVIEW_STATUS_LABELS.keys())
MAX_EDITOR_NOTE = 5000
MAX_SEARCH = 200


def normalize_status(value: str) -> str:
    return str(value or "").strip().upper()


def validate_review_input(status: str, editor_note: str) -> Dict[str, object]:
    status = normalize_status(status)
    if status not in REVIEW_STATUSES:
        raise ValueError("invalid review status")
    note = str(editor_note or "")
    if len(note) > MAX_EDITOR_NOTE:
        raise ValueError("editor note too long")
    return {"review_status": status, "editor_note": note}


def normalize_search(q: str) -> str:
    return (str(q or "").strip())[:MAX_SEARCH]
