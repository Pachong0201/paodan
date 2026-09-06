"""已知新闻匹配器：V1 用本地 known_cases.jsonl 模拟（未来接新闻库）。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from ..config import KNOWN_CASES_FILE
from ..preprocessing.normalization import to_simplified

logger = logging.getLogger(__name__)


class KnownNewsMatcher:
    """输入人物/公司/项目/事件 -> known 事件与可能的新增信息."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else KNOWN_CASES_FILE
        self.cases: List[dict] = []
        self._load()

    def _load(self):
        if not self.path.exists():
            logger.info("known_cases.jsonl 不存在(%s)，已知新闻匹配为空", self.path)
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    case = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # 字段归一
                case.setdefault("persons", [])
                case.setdefault("companies", [])
                case.setdefault("projects", [])
                case.setdefault("events", [])
                case.setdefault("outcome", "")
                case.setdefault("date", "")
                self.cases.append(case)
        except OSError as e:
            logger.warning("读取 known_cases 失败: %s", e)

    def match(self, persons: Optional[List[str]] = None,
              companies: Optional[List[str]] = None,
              projects: Optional[List[str]] = None) -> dict:
        persons = [to_simplified(p) for p in (persons or []) if p]
        companies = [to_simplified(c) for c in (companies or []) if c]
        projects = [to_simplified(p) for p in (projects or []) if p]
        matched = []
        for case in self.cases:
            cps = [to_simplified(p) for p in case.get("persons", [])]
            ccs = [to_simplified(c) for c in case.get("companies", [])]
            cpr = [to_simplified(p) for p in case.get("projects", [])]
            hit = set()
            for p in persons:
                if any(p in cp or cp in p for cp in cps):
                    hit.add(p)
            for c in companies:
                if any(c in cc or cc in c for cc in ccs):
                    hit.add(c)
            for p in projects:
                if any(p in cp or cp in p for cp in cpr):
                    hit.add(p)
            if hit:
                matched.append({**case, "matched_keys": sorted(hit)})
        return {
            "known": bool(matched),
            "matched_events": matched,
            "possible_new_information": [],
        }
