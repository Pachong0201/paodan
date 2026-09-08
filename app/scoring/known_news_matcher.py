"""已知新闻匹配器：V4.1 明确 local_stub；为 V5 预留 KnownNewsProvider 接口。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from ..config import KNOWN_CASES_FILE
from ..preprocessing.normalization import to_simplified

logger = logging.getLogger(__name__)


class KnownNewsProvider:
    """V5 预留接口：未来可接台湾新闻数据库。"""

    known_news_mode = "local_stub"

    def match(self, persons=None, companies=None, projects=None) -> dict:  # pragma: no cover
        raise NotImplementedError


class JsonlKnownNewsProvider(KnownNewsProvider):
    """基于本地 JSONL 的已知新闻 Provider（local_stub）。"""

    known_news_mode = "local_stub"

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
            "known_news_mode": self.known_news_mode,
            # 明确：[] 不等于“确认没有新增信息”，只代表本地 stub 无法判断。
            "novelty_status": "unknown",
        }


class KnownNewsMatcher(JsonlKnownNewsProvider):
    """兼容旧名称；当前模式为 local_stub。"""

    @property
    def mode(self) -> str:
        return self.known_news_mode
