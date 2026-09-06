"""规则包加载器：读取 config/news_signal 全部 YAML，提供统一访问与统计."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

REQUIRED_FILES = [
    "taxonomy.yaml",
    "keywords.yaml",
    "pattern_rules.yaml",
    "negative_rules.yaml",
    "scoring_rules.yaml",
    "llm_email_screening_prompt.md",
]

# 自定义槽位 -> 六类标识映射（用于统一命中与统计）
# power_actions/retaliation/candidate_knowledge 等视为强信号(H)；
# implicit/fund_flow/benefits/actions 等为隐性(M)；关系类入 C；证据类入 E。
SLOT_TYPE_MAP = {
    "H": "H", "M": "M", "C": "C", "E": "E", "S": "S",
    "target_role_terms": "C",
    "implicit_behavior_terms": "M",
    "evidence_terms": "E",
    "procedure_terms": "S",
    "fund_flow": "M",
    "power_actions": "H",
    "benefits": "M",
    "retaliation": "H",
    "actions": "M",
    "candidate_knowledge": "H",
    "related_entities": "C",
}

# category -> 权重（规则引擎粗评分）
TYPE_WEIGHT = {"H": 14.0, "M": 6.0, "C": 2.0, "E": 8.0, "S": 5.0, "X": -30.0}


class RuleConfigError(RuntimeError):
    pass


class RuleConfig:
    """加载并持有规则包全部内容."""

    def __init__(self, directory: Optional[Path] = None):
        self.directory = Path(directory) if directory else None
        self.taxonomy: Dict[str, Any] = {}
        self.keywords: Dict[str, Any] = {}
        self.patterns: Dict[str, Any] = {}
        self.negative: Dict[str, Any] = {}
        self.scoring: Dict[str, Any] = {}
        self.llm_prompt: str = ""
        self.load_status: Dict[str, bool] = {}

    # ---------- 加载 ----------
    def load_all(self) -> "RuleConfig":
        attr_map = {
            "taxonomy.yaml": "taxonomy",
            "keywords.yaml": "keywords",
            "pattern_rules.yaml": "patterns",
            "negative_rules.yaml": "negative",
            "scoring_rules.yaml": "scoring",
        }
        errors = []
        for name, attr in attr_map.items():
            path = self.directory / name if self.directory else Path(name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if not isinstance(data, dict):
                    raise RuleConfigError(f"{name} 顶层必须是 mapping")
                setattr(self, attr, data)
                self.load_status[name] = True
            except Exception as e:  # noqa: BLE001
                self.load_status[name] = False
                errors.append(f"{name}: {e}")
                logger.error("规则文件加载失败 %s: %s", name, e)
        md = self.directory / "llm_email_screening_prompt.md" if self.directory else Path("llm_email_screening_prompt.md")
        try:
            self.llm_prompt = md.read_text(encoding="utf-8")
            self.load_status["llm_email_screening_prompt.md"] = True
        except Exception as e:  # noqa: BLE001
            self.load_status["llm_email_screening_prompt.md"] = False
            errors.append(f"llm_email_screening_prompt.md: {e}")
        if errors:
            raise RuleConfigError("规则包加载失败: " + "; ".join(errors))
        return self

    # ---------- taxonomy ----------
    @property
    def categories(self) -> List[Dict[str, Any]]:
        return list(self.taxonomy.get("categories", []))

    @property
    def category_ids(self) -> List[str]:
        return [c.get("id") for c in self.categories if c.get("id")]

    def category_name(self, cid: str) -> str:
        for c in self.categories:
            if c.get("id") == cid:
                return c.get("name", cid)
        return cid

    # ---------- 关键词词典（组装后的 类型->[{term,category,stage,slot}]） ----------
    def build_keyword_index(self) -> Dict[str, List[Dict[str, Any]]]:
        """返回 {TYPE: [dict(term, category, slot, stage?)]}，类型见 SLOT_TYPE_MAP."""
        index: Dict[str, List[Dict[str, Any]]] = {}
        kw = self.keywords
        g = kw.get("global", {})

        def add(slot: str, terms: List[str], category: str, stage: Optional[str] = None):
            t = SLOT_TYPE_MAP.get(slot)
            if not t or not terms:
                return
            bucket = index.setdefault(t, [])
            for term in terms:
                bucket.append({"term": term, "category": category, "slot": slot, "stage": stage})

        for term in g.get("target_role_terms", []):
            add("target_role_terms", [term], "GLOBAL")
        for term in g.get("implicit_behavior_terms", []):
            add("implicit_behavior_terms", [term], "GLOBAL")
        for term in g.get("evidence_terms", []):
            add("evidence_terms", [term], "GLOBAL")
        for stage, terms in (g.get("procedure_terms") or {}).items():
            for term in terms:
                add("procedure_terms", [term], "GLOBAL", stage=stage)

        cats = kw.get("categories") or {}
        for cid, body in cats.items():
            if not isinstance(body, dict):
                continue
            for slot, terms in body.items():
                add(slot, terms, cid)
        return index

    def keyword_stats(self) -> Dict[str, int]:
        index = self.build_keyword_index()
        stats = {t: len(v) for t, v in index.items()}
        stats["X"] = len(self.x_terms())
        return stats

    # ---------- patterns ----------
    def pattern_list(self) -> List[Dict[str, Any]]:
        return list(self.patterns.get("patterns", []))

    # ---------- negative ----------
    def x_terms(self) -> List[str]:
        return list(self.negative.get("global_X_terms", []))

    def negative_rules(self) -> List[Dict[str, Any]]:
        return list(self.negative.get("downgrade_rules", []))

    def context_exclusions(self) -> List[Dict[str, Any]]:
        return list(self.negative.get("context_exclusions", []))

    # ---------- scoring ----------
    def scoring_data(self) -> Dict[str, Any]:
        return self.scoring

    def summary(self) -> Dict[str, Any]:
        st = self.keyword_stats()
        return {
            "taxonomy_categories": len(self.categories),
            "patterns": len(self.pattern_list()),
            "negative_rules": len(self.negative_rules()),
            "x_terms": st.get("X", 0),
            "keyword_counts": {k: st.get(k, 0) for k in ("H", "M", "C", "E", "S", "X")},
            "llm_prompt_chars": len(self.llm_prompt),
            "files_loaded_ok": sum(1 for v in self.load_status.values() if v),
            "files_total": len(self.load_status),
        }
