"""重要爆料邮件 Excel 台账 V3.1（Important Email Register）.

登记层：S/A/B 或 final_score>=阈值的重要邮件 -> data/reports/important_email_register.xlsx
原则：不改 V1/V2/V3 任何评分/规则语义，只做结果登记 + 去重 + 人工字段保护。

链路：V1 -> V2 -> V3 -> SQLite -> JSONL -> CSV -> Important Email Register.xlsx
Excel 失败不得影响前面任何步骤。
"""
from __future__ import annotations

import copy
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from ..security.spreadsheet import spreadsheet_safe  # noqa: E402

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.utils import get_column_letter
except Exception:  # pragma: no cover
    Workbook = None  # type: ignore
    load_workbook = None  # type: ignore

# ---------- 固定表头（顺序不得变） ----------
HEADERS: List[str] = [
    "编号",
    "登记时间",
    "邮件日期",
    "收件时间",
    "发件人",
    "邮件主题",
    "优先级",
    "最终评分",
    "主要类别",
    "子类别",
    "涉及人物",
    "涉及机构",
    "涉及公司",
    "涉及项目",
    "一句话摘要",
    "为什么值得看",
    "命中Pattern",
    "关键证据",
    "金额/利益",
    "新增信息",
    "已知旧闻",
    "核查重点",
    "V2推荐首发类型",
    "V2备选渠道",
    "V3推荐媒体",
    "V3推荐揭弊人物",
    "V3推荐放大者",
    "V3正式渠道",
    "风险提示",
    "附件数量",
    "附件名称",
    "原始邮件路径",
    "Message-ID",
    "Content Hash",
    "处理状态",
    "责任编辑",
    "人工标签",
    "记者备注",
    "治理民生类别",
    "治理民生评分",
]

MANUAL_FIELDS: List[str] = ["处理状态", "责任编辑", "人工标签", "记者备注"]

# V3.2 Review Queue 兼容列（可选，不破坏原有 38 列顺序；仅在传入 review 路径时动态追加）
REVIEW_PATH_FIELD: str = "待审查文件路径"
REVIEW_STATUS_FIELD: str = "审查状态"

MANUAL_DEFAULTS: Dict[str, str] = {
    "处理状态": "待看",
    "记者备注": "",
    "责任编辑": "",
    "人工标签": "",
}

# 系统更新时永不覆盖的列（编号/登记时间/人工字段）
PROTECTED_FIELDS = {"编号", "登记时间", *MANUAL_FIELDS}

SHEET_NAME = "重要爆料邮件"
HELP_SHEET = "说明"

JOINER = "；"

# 长文本需自动换行的列
WRAP_FIELDS = {"一句话摘要", "为什么值得看", "核查重点", "新增信息", "记者备注", "已知旧闻", "关键证据"}

# 重点列宽
COLUMN_WIDTHS: Dict[str, float] = {
    "编号": 8,
    "登记时间": 20,
    "邮件日期": 22,
    "收件时间": 22,
    "发件人": 28,
    "邮件主题": 42,
    "优先级": 10,
    "最终评分": 10,
    "主要类别": 30,
    "子类别": 24,
    "涉及人物": 24,
    "涉及机构": 24,
    "涉及公司": 24,
    "涉及项目": 24,
    "一句话摘要": 52,
    "为什么值得看": 52,
    "命中Pattern": 30,
    "关键证据": 30,
    "金额/利益": 28,
    "新增信息": 36,
    "已知旧闻": 30,
    "核查重点": 52,
    "V2推荐首发类型": 26,
    "V2备选渠道": 30,
    "V3推荐媒体": 32,
    "V3推荐揭弊人物": 32,
    "V3推荐放大者": 32,
    "V3正式渠道": 32,
    "风险提示": 30,
    "附件数量": 10,
    "附件名称": 36,
    "原始邮件路径": 36,
    "Message-ID": 30,
    "Content Hash": 20,
    "处理状态": 12,
    "责任编辑": 12,
    "人工标签": 16,
    "记者备注": 36,
    "治理民生类别": 28,
    "治理民生评分": 12,
    REVIEW_PATH_FIELD: 44,
    REVIEW_STATUS_FIELD: 12,
}

_ROUTE_NAMES_FALLBACK = {
    "R1": "社交平台首发型",
    "R2": "媒体独家型",
    "R3": "深度调查报道型",
    "R4": "记者会/公开展示型",
    "R5": "正式检举优先型",
    "R6": "高敏感核验型",
}

_RISK_LABELS = {
    "SOURCE_EXPOSURE": "来源暴露",
    "PRIVACY": "隐私侵害",
    "DEFAMATION": "诽谤风险",
    "EVIDENCE_AUTHENTICITY": "证据真实性未核",
    "CONTEXT_LOSS": "上下文缺失",
    "RETALIATION": "报复风险",
    "DESTRUCTION_OF_EVIDENCE": "灭证风险",
    "WITNESS_COLLUSION": "串证风险",
    "CLASSIFIED_INFORMATION": "机密外泄",
    "LEGAL_PROCESS_INTERFERENCE": "干扰调查程序",
    "MISLEADING_OLD_NEWS": "旧闻误导",
    "DOCUMENT_FORGERY": "文件伪造",
}


def _now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm_mid(v: Any) -> str:
    s = str(v or "").strip()
    # 去掉首尾尖括号
    if s.startswith("<") and s.endswith(">"):
        s = s[1:-1].strip()
    return s.strip()


def _join_list(items: Any) -> str:
    if items is None:
        return ""
    if isinstance(items, str):
        return items.strip()
    try:
        parts = [str(x).strip() for x in list(items) if str(x).strip()]
    except Exception:
        return str(items)
    # 去重保序
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return JOINER.join(out)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_list(v: Any) -> List[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    return [v]


def _entity_text(e: Any) -> str:
    if isinstance(e, dict):
        return str(e.get("text") or "")
    return str(getattr(e, "text", "") or "")


def _entity_type(e: Any) -> str:
    if isinstance(e, dict):
        return str(e.get("type") or "")
    return str(getattr(e, "type", "") or "")


def _pattern_id(p: Any) -> str:
    if isinstance(p, dict):
        return str(p.get("pattern_id") or "")
    return str(getattr(p, "pattern_id", "") or "")


def _money_text(m: Any) -> str:
    if isinstance(m, dict):
        raw = str(m.get("raw") or "").strip()
        amt = m.get("amount")
        cur = str(m.get("currency") or "").strip()
        if raw:
            if amt is not None and cur:
                try:
                    return f"{raw}({amt} {cur})".strip()
                except Exception:
                    return raw
            return raw
        if amt is not None:
            return f"{amt} {cur}".strip()
        return ""
    return str(m or "").strip()


def _load_route_names() -> Dict[str, str]:
    try:
        from ..config import CONFIG_DIR
        import yaml as _yaml
        p = CONFIG_DIR / "release_routes.yaml"
        if p.exists():
            data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            names = data.get("route_names") or {}
            if isinstance(names, dict) and names:
                return {str(k).strip().upper(): str(v) for k, v in names.items()}
    except Exception:
        pass
    return dict(_ROUTE_NAMES_FALLBACK)


def _load_taxonomy_names() -> Dict[str, str]:
    """类别代码 -> 自然语言名称（config/news_signal/taxonomy.yaml）。"""
    try:
        from ..config import NEWS_SIGNAL_DIR
        import yaml as _yaml
        p = NEWS_SIGNAL_DIR / "taxonomy.yaml"
        if p.exists():
            data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            out = {}
            for c in data.get("categories") or []:
                if isinstance(c, dict) and c.get("id") and c.get("name"):
                    out[str(c["id"]).strip().upper()] = str(c["name"]).strip()
            if out:
                return out
    except Exception:
        pass
    return {}


def _load_pattern_names() -> Dict[str, str]:
    """Pattern 代码 -> 自然语言名称（config/news_signal/pattern_rules.yaml）。"""
    try:
        from ..config import NEWS_SIGNAL_DIR
        import yaml as _yaml
        p = NEWS_SIGNAL_DIR / "pattern_rules.yaml"
        if p.exists():
            data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            out = {}
            for r in data.get("patterns") or []:
                if isinstance(r, dict) and r.get("id") and r.get("name"):
                    out[str(r["id"]).strip().upper()] = str(r["name"]).strip()
            if out:
                return out
    except Exception:
        pass
    return {}


def _load_formal_type_names() -> Dict[str, str]:
    """正式检举类型代码 -> 自然语言（config/release_route_rules.yaml）。"""
    try:
        from ..config import CONFIG_DIR
        import yaml as _yaml
        p = CONFIG_DIR / "release_route_rules.yaml"
        if p.exists():
            data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            names = data.get("formal_referral_types") or {}
            if isinstance(names, dict) and names:
                return {str(k).strip().upper(): str(v) for k, v in names.items()}
    except Exception:
        pass
    return {}


_ROUTE_NAMES: Optional[Dict[str, str]] = None
_TAXONOMY_NAMES: Optional[Dict[str, str]] = None
_PATTERN_NAMES: Optional[Dict[str, str]] = None
_FORMAL_TYPE_NAMES: Optional[Dict[str, str]] = None


def _route_name(code: str) -> str:
    global _ROUTE_NAMES
    if _ROUTE_NAMES is None:
        _ROUTE_NAMES = _load_route_names()
    c = str(code or "").strip().upper()
    return _ROUTE_NAMES.get(c, "")


def _taxonomy_name(code: str) -> str:
    global _TAXONOMY_NAMES
    if _TAXONOMY_NAMES is None:
        _TAXONOMY_NAMES = _load_taxonomy_names()
    return _TAXONOMY_NAMES.get(str(code or "").strip().upper(), "")


def _pattern_name(pid: str) -> str:
    global _PATTERN_NAMES
    if _PATTERN_NAMES is None:
        _PATTERN_NAMES = _load_pattern_names()
    return _PATTERN_NAMES.get(str(pid or "").strip().upper(), "")


def _formal_type_name(code: str) -> str:
    global _FORMAL_TYPE_NAMES
    if _FORMAL_TYPE_NAMES is None:
        _FORMAL_TYPE_NAMES = _load_formal_type_names()
    c = str(code or "").strip().upper()
    return _FORMAL_TYPE_NAMES.get(c, c)


def _fmt_route(code: str) -> str:
    """渠道只显示自然语言名称（如「正式检举优先型」），不带 R 代码。"""
    c = str(code or "").strip().upper()
    if not c:
        return ""
    return _route_name(c) or c


def _fmt_category(code: str) -> str:
    """类别只显示自然语言名称，未知代码才保留原文。"""
    s = str(code or "").strip()
    if not s:
        return ""
    return _taxonomy_name(s) or _humanize_text(s)


def _fmt_pattern(pid: str) -> str:
    """Pattern 只显示自然语言名称，未知代码才保留原文。"""
    s = str(pid or "").strip()
    if not s:
        return ""
    return _pattern_name(s) or s


# 文本内嵌代码的人性化：涉A03→涉收贿…；P04→厂商金钱职务对价；R5→正式检举优先型。
# 用前后否定断言而非 \b（CJK 字符与字母间无 \b 边界，如「涉A03」）。
import re as _re2

_CAT_CODE_RE = _re2.compile(r"(?<![A-Za-z0-9])A(0[1-9]|1[0-8])(?![0-9])")
_PAT_CODE_RE = _re2.compile(r"(?<![A-Za-z0-9])P(0[1-9]|1[0-9]|20)(?![0-9])")
_ROUTE_CODE_RE = _re2.compile(r"(?<![A-Za-z0-9])R([1-6])(?![0-9])")


def _humanize_text(text: Any) -> str:
    """把自由文本里的 A/P/R 代码替换为自然语言名称（未知代码保留原文）。"""
    s = str(text or "")
    if not s:
        return ""
    def _sub_cat(m):
        code = "A" + m.group(1)
        return _taxonomy_name(code) or code
    def _sub_pat(m):
        code = "P" + m.group(1)
        return _pattern_name(code) or code
    def _sub_route(m):
        code = "R" + m.group(1)
        return _route_name(code) or code
    s = _CAT_CODE_RE.sub(_sub_cat, s)
    s = _PAT_CODE_RE.sub(_sub_pat, s)
    s = _ROUTE_CODE_RE.sub(_sub_route, s)
    return s


def _fmt_named_items(items: Any, limit: int = 3) -> str:
    """V3 Top1-3 显示为 名(分)；分."""
    lst = _as_list(items)[:limit]
    parts = []
    for it in lst:
        if isinstance(it, dict):
            name = str(it.get("name") or it.get("entity_id") or "").strip()
            score = it.get("fit_score", it.get("score", ""))
        else:
            name = str(getattr(it, "name", "") or getattr(it, "entity_id", "") or "").strip()
            score = getattr(it, "fit_score", "")
        if not name:
            continue
        try:
            if score == "" or score is None:
                parts.append(name)
            else:
                parts.append(f"{name}({float(score):.0f})")
        except Exception:
            parts.append(name)
    return JOINER.join(parts)


def _fmt_verification(targets: Any) -> str:
    lst = [str(x).strip() for x in _as_list(targets) if str(x).strip()]
    if not lst:
        return ""
    # 去重保序
    seen = set()
    uniq = []
    for t in lst:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return "\n".join(f"{i}. {t}" for i, t in enumerate(uniq, 1))


def _relative_source(path_str: str) -> str:
    s = str(path_str or "").strip()
    if not s:
        return ""
    try:
        from ..config import ROOT
        p = Path(s)
        # Windows 路径分隔兼容
        try:
            rel = p.relative_to(ROOT)
            return rel.as_posix()
        except Exception:
            # 尝试按字符串前缀匹配（处理 data\inbox\ 这类相对路径已是相对）
            rs = s.replace("\\", "/")
            root_s = str(ROOT).replace("\\", "/")
            if rs.startswith(root_s):
                return rs[len(root_s):].lstrip("/")
            return rs if not Path(s).is_absolute() else s
    except Exception:
        return s


@dataclass
class ImportantEmailRegisterRow:
    """Excel 导出 DTO（不重复定义 ScreeningRecord，只做导出映射）。"""

    register_key: str = ""
    values: Dict[str, Any] = field(default_factory=dict)


class ImportantEmailRegister:
    """重要爆料邮件 Excel 台账。批量打开一次、批量写入、保存一次。"""

    def __init__(
        self,
        path: str | Path | None = None,
        enabled: bool = True,
        min_score: float = 60.0,
        priorities: Optional[List[str]] = None,
        backup_before_batch: bool = True,
        sheet_name: str = SHEET_NAME,
    ):
        if path is None:
            try:
                from ..config import REPORTS_DIR
                path = REPORTS_DIR / "important_email_register.xlsx"
            except Exception:
                path = Path("data/reports/important_email_register.xlsx")
        self.path = Path(path)
        self.enabled = bool(enabled)
        self.min_score = float(min_score)
        self.priorities = [str(p).strip().upper() for p in (priorities or ["S", "A", "B"]) if str(p).strip()]
        self.backup_before_batch = bool(backup_before_batch)
        self.sheet_name = sheet_name or SHEET_NAME
        # 内存态
        self._wb = None
        self._ws = None
        self._key_to_row: Dict[str, int] = {}
        self._max_id: int = 0
        self._header_idx: Dict[str, int] = {h: i + 1 for i, h in enumerate(HEADERS)}
        self._save_count: int = 0
        self._pending_path = self.path.parent / "pending_excel_register.jsonl"

    # ------------------------------------------------------------------
    # 配置/门禁
    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, config_dir: Path | None = None, path_override: str | Path | None = None,
                    enabled_override: Optional[bool] = None) -> "ImportantEmailRegister":
        try:
            from ..config import load_excel_register_config
            cfg = load_excel_register_config(config_dir)
        except Exception:
            cfg = {"enabled": True, "path": "data/reports/important_email_register.xlsx",
                   "min_score": 60.0, "priorities": ["S", "A", "B"],
                   "backup_before_batch": True}
        if enabled_override is not None:
            cfg["enabled"] = bool(enabled_override)
        if path_override is not None:
            cfg["path"] = str(path_override)
        return cls(path=cfg.get("path"), enabled=cfg.get("enabled", True),
                   min_score=cfg.get("min_score", 60.0),
                   priorities=cfg.get("priorities", ["S", "A", "B"]),
                   backup_before_batch=cfg.get("backup_before_batch", True))

    def should_register(self, record: Any) -> bool:
        """是否进入台账：priority in priorities OR final_score >= min_score。"""
        try:
            score = _get(record, "score", None)
            if score is None:
                return False
            pri = str(_get(score, "priority", "") or "").strip().upper()
            try:
                final = float(_get(score, "final_score", 0.0) or 0.0)
            except Exception:
                final = 0.0
            if pri in self.priorities:
                return True
            return final >= float(self.min_score)
        except Exception:
            return False

    def build_register_key(self, record: Any) -> str:
        """register_key = message_id if message_id else content_hash(body_hash)。"""
        email = _get(record, "email", None)
        mid = _norm_mid(_get(email, "message_id", "") if email is not None else "")
        body_hash = str(_get(email, "body_hash", "") or "").strip() if email is not None else ""
        # 兼容：rule/llm 层无 body_hash 时用 email_id / subject 兜底哈希
        if mid:
            return f"MID:{mid}"
        if body_hash:
            return f"HASH:{body_hash}"
        # 兜底：subject+sender+date+body_text 的 sha
        try:
            import hashlib
            subj = str(_get(email, "subject", "") or "")
            sender = str(_get(email, "sender", "") or "")
            date = str(_get(email, "date", "") or "")
            body = str(_get(email, "body_text", "") or _get(email, "combined_text", "") or "")
            eid = str(_get(record, "email_id", "") or "")
            raw = "|".join([subj, sender, date, body[:2000], eid])
            h = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()
            return f"HASH:{h}"
        except Exception:
            return f"HASH:{id(record)}"

    # ------------------------------------------------------------------
    # 字段映射（不为 Excel 再调 LLM）
    # ------------------------------------------------------------------
    def record_to_row(self, record: Any, register_key: str = "", now_str: str = "",
                      seq_no: int = 0) -> Dict[str, Any]:
        email = _get(record, "email", None)
        rule = _get(record, "rule", None)
        llm = _get(record, "llm", None)
        score = _get(record, "score", None)
        rel = _get(record, "release_recommendation", None) or {}
        named = _get(record, "named_channel_recommendation", None) or {}
        known = _get(record, "known_news", None) or {}

        rk = register_key or self.build_register_key(record)
        now = now_str or _now_str()

        # --- 基础 ---
        subject = str(_get(email, "subject", "") or "")
        sender = str(_get(email, "sender", "") or "")
        date = str(_get(email, "date", "") or "")
        mid = _norm_mid(_get(email, "message_id", "") if email is not None else "")
        body_hash = str(_get(email, "body_hash", "") or "").strip() if email is not None else ""
        pri = str(_get(score, "priority", "") or "") if score is not None else ""
        try:
            final_score = float(_get(score, "final_score", 0.0) or 0.0) if score is not None else 0.0
        except Exception:
            final_score = 0.0
        # 最终评分：整数分保持整数显示习惯，但以数值写入
        if float(final_score).is_integer():
            final_score_val: Any = int(final_score)
        else:
            final_score_val = round(float(final_score), 1)

        # --- 类别/人物/机构（类别只写自然语言名称） ---
        llm_cats = _as_list(_get(llm, "categories", []) if llm is not None else [])
        rule_cats = _as_list(_get(rule, "matched_categories", []) if rule is not None else [])
        cats = [_fmt_category(c) for c in (llm_cats or rule_cats)]
        subcats = [_fmt_category(c) for c in _as_list(_get(llm, "subcategories", []) if llm is not None else [])]

        llm_persons = _as_list(_get(llm, "target_persons", []) if llm is not None else [])
        rule_persons = _as_list(_get(rule, "target_persons_found", []) if rule is not None else [])
        persons = llm_persons or rule_persons

        llm_orgs = _as_list(_get(llm, "target_organizations", []) if llm is not None else [])
        rule_orgs = _as_list(_get(rule, "target_orgs_found", []) if rule is not None else [])
        orgs = llm_orgs or rule_orgs

        # 涉及公司：related_entities.companies 优先，否则 entities COMPANY
        companies: List[str] = []
        try:
            rel_ent = _get(llm, "related_entities", {}) if llm is not None else {}
            if isinstance(rel_ent, dict):
                companies = [str(x).strip() for x in _as_list(rel_ent.get("companies")) if str(x).strip()]
        except Exception:
            companies = []
        if not companies and rule is not None:
            try:
                for e in _as_list(_get(rule, "entities", [])):
                    if _entity_type(e) == "COMPANY" and _entity_text(e).strip():
                        companies.append(_entity_text(e).strip())
            except Exception:
                pass

        # 涉及项目：projects_or_cases 优先，否则 entities PROJECT
        projects: List[str] = []
        try:
            projects = [str(x).strip() for x in _as_list(_get(llm, "projects_or_cases", []) if llm is not None else []) if str(x).strip()]
        except Exception:
            projects = []
        if not projects and rule is not None:
            try:
                for e in _as_list(_get(rule, "entities", [])):
                    if _entity_type(e) == "PROJECT" and _entity_text(e).strip():
                        projects.append(_entity_text(e).strip())
            except Exception:
                pass

        # --- 摘要（文本内嵌代码一并人性化） ---
        one_line = _humanize_text(str(_get(llm, "one_sentence_summary", "") or "").strip()) if llm is not None else ""
        reason = _humanize_text(str(_get(llm, "reason_for_attention", "") or "").strip()) if llm is not None else ""
        summary_zh = _humanize_text(str(_get(record, "summary_zh", "") or "").strip())
        if not one_line:
            one_line = summary_zh
        if not reason:
            reason = summary_zh

        # --- Pattern/证据/金额（Pattern 只写自然语言名称） ---
        pats: List[str] = []
        try:
            for p in _as_list(_get(rule, "matched_patterns", []) if rule is not None else []):
                pid = _pattern_id(p)
                if pid:
                    pats.append(_fmt_pattern(pid))
        except Exception:
            pass

        evidence_items = _as_list(_get(llm, "evidence_items", []) if llm is not None else [])
        if not evidence_items and rule is not None:
            try:
                from ..preprocessing.normalization import to_simplified as _t2s  # noqa
                ev_terms = []
                mk = _get(rule, "matched_keywords", {}) or {}
                if isinstance(mk, dict):
                    for h in _as_list(mk.get("E")):
                        t = h.get("term") if isinstance(h, dict) else getattr(h, "term", "")
                        if t:
                            ev_terms.append(str(t))
                evidence_items = ev_terms
            except Exception:
                evidence_items = []

        money_list = _as_list(_get(llm, "money_or_benefits", []) if llm is not None else [])
        money_strs: List[str] = []
        if money_list:
            money_strs = [str(x).strip() for x in money_list if str(x).strip()]
        elif rule is not None:
            try:
                for m in _as_list(_get(rule, "money", [])):
                    t = _money_text(m)
                    if t:
                        money_strs.append(t)
            except Exception:
                pass

        new_info = [_humanize_text(x) for x in _as_list(_get(llm, "new_information", []) if llm is not None else [])]
        old_info = [_humanize_text(x) for x in _as_list(_get(llm, "known_old_information", []) if llm is not None else [])]
        # 已知旧闻：known_news matched_events 补充
        try:
            if isinstance(known, dict):
                for ev in _as_list(known.get("matched_events")):
                    if isinstance(ev, dict):
                        t = str(ev.get("title") or ev.get("event") or ev.get("name") or "").strip()
                        if t and t not in [str(x) for x in old_info]:
                            old_info.append(t)
                    elif str(ev).strip():
                        old_info.append(str(ev).strip())
        except Exception:
            pass

        vts_raw = _as_list(_get(record, "verification_targets", []) if _get(record, "verification_targets", []) else (_get(llm, "verification_targets", []) if llm is not None else []))
        vts = [_humanize_text(x) for x in vts_raw]

        # --- V2 ---
        primary = str(_get(rel, "primary_route", "") or "").strip().upper() if isinstance(rel, dict) else str(getattr(rel, "primary_route", "") or "").strip().upper()
        secondary: List[str] = []
        risks: List[str] = []
        formal_rec = False
        formal_types: List[str] = []
        v2_reason = ""
        try:
            if isinstance(rel, dict):
                secondary = [str(x).strip().upper() for x in _as_list(rel.get("secondary_routes")) if str(x).strip()]
                risks = [str(x).strip().upper() for x in _as_list(rel.get("release_risks")) if str(x).strip()]
                formal_rec = bool(rel.get("formal_referral_recommended"))
                formal_types = [str(x).strip().upper() for x in _as_list(rel.get("formal_referral_type")) if str(x).strip() and str(x).strip().upper() != "NONE"]
                v2_reason = str(rel.get("reason") or "")
            else:
                secondary = [str(x).strip().upper() for x in _as_list(getattr(rel, "secondary_routes", [])) if str(x).strip()]
                risks = [str(x).strip().upper() for x in _as_list(getattr(rel, "release_risks", [])) if str(x).strip()]
                formal_rec = bool(getattr(rel, "formal_referral_recommended", False))
        except Exception:
            pass
        v2_primary_txt = _fmt_route(primary) if primary else ""
        v2_secondary_txt = JOINER.join(_fmt_route(r) for r in secondary if r)
        # 风险提示：风险中文 + 正式检举提示（检举类型写中文名）
        risk_parts = [_RISK_LABELS.get(r, r) for r in risks if r]
        if formal_rec:
            if formal_types:
                risk_parts.append("建议正式检举(" + "、".join(_formal_type_name(t) for t in formal_types) + ")")
            else:
                risk_parts.append("建议正式检举")
        risk_txt = JOINER.join(risk_parts)

        # --- V3 ---
        v3_media = v3_actor = v3_amp = v3_formal = ""
        try:
            if isinstance(named, dict):
                v3_media = _fmt_named_items(named.get("recommended_media"), 3)
                v3_actor = _fmt_named_items(named.get("recommended_disclosure_actors"), 3)
                v3_amp = _fmt_named_items(named.get("recommended_amplifiers"), 3)
                v3_formal = _fmt_named_items(named.get("recommended_formal_channels"), 3)
            else:
                v3_media = _fmt_named_items(getattr(named, "recommended_media", []), 3)
                v3_actor = _fmt_named_items(getattr(named, "recommended_disclosure_actors", []), 3)
                v3_amp = _fmt_named_items(getattr(named, "recommended_amplifiers", []), 3)
                v3_formal = _fmt_named_items(getattr(named, "recommended_formal_channels", []), 3)
        except Exception:
            pass

        # --- 附件/路径 ---
        atts = _as_list(_get(email, "attachments", []) if email is not None else [])
        att_names = []
        for a in atts:
            if isinstance(a, dict):
                n = str(a.get("filename") or "").strip()
            else:
                n = str(getattr(a, "filename", "") or "").strip()
            if n:
                att_names.append(n)
        source_path = _relative_source(str(_get(email, "source_path", "") or "") if email is not None else "")

        row: Dict[str, Any] = {
            "编号": seq_no or "",
            "登记时间": now,
            "邮件日期": date,
            "收件时间": date,
            "发件人": sender,
            "邮件主题": subject,
            "优先级": pri,
            "最终评分": final_score_val,
            "主要类别": _join_list(cats),
            "子类别": _join_list(subcats),
            "涉及人物": _join_list(persons),
            "涉及机构": _join_list(orgs),
            "涉及公司": _join_list(companies),
            "涉及项目": _join_list(projects),
            "一句话摘要": one_line,
            "为什么值得看": reason,
            "命中Pattern": JOINER.join(pats),
            "关键证据": _join_list(evidence_items),
            "金额/利益": JOINER.join(money_strs),
            "新增信息": _join_list(new_info),
            "已知旧闻": _join_list(old_info),
            "核查重点": _fmt_verification(vts),
            "V2推荐首发类型": v2_primary_txt,
            "V2备选渠道": v2_secondary_txt,
            "V3推荐媒体": v3_media,
            "V3推荐揭弊人物": v3_actor,
            "V3推荐放大者": v3_amp,
            "V3正式渠道": v3_formal,
            "风险提示": risk_txt,
            "附件数量": len(att_names),
            "附件名称": JOINER.join(att_names),
            "原始邮件路径": source_path,
            "Message-ID": mid,
            "Content Hash": body_hash,
            "处理状态": MANUAL_DEFAULTS["处理状态"],
            "责任编辑": MANUAL_DEFAULTS["责任编辑"],
            "人工标签": MANUAL_DEFAULTS["人工标签"],
            "记者备注": MANUAL_DEFAULTS["记者备注"],
            "治理民生类别": _join_list(_get(record, "governance_categories", []) or []),
            "治理民生评分": str(_get(record, "governance_score", "") or ""),
        }
        # 保证所有 HEADERS 都有键，并对邮件可控字段做 formula injection 防护。
        for h in HEADERS:
            row.setdefault(h, "")
            row[h] = spreadsheet_safe(row[h])
        return row

    # ------------------------------------------------------------------
    # 文件读写
    # ------------------------------------------------------------------
    def _ensure_openpyxl(self):
        if Workbook is None or load_workbook is None:
            raise RuntimeError("缺少 openpyxl 依赖，请 pip install openpyxl")

    def load_existing(self) -> Tuple[Any, Any]:
        """读取并追加：不存在则创建；损坏则备份后重建。返回 (wb, ws)。"""
        self._ensure_openpyxl()
        self._key_to_row = {}
        self._max_id = 0
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        wb = None
        ws = None
        if path.exists():
            try:
                wb = load_workbook(str(path))
                if self.sheet_name in wb.sheetnames:
                    ws = wb[self.sheet_name]
                else:
                    # 缺目标表则新建（保留原文件其他表）
                    ws = wb.create_sheet(self.sheet_name)
                    ws.append(HEADERS)
                    self._apply_header_style(ws)
                # 表头校验：若首行与 HEADERS 不一致但文件可读，仍以位置索引兼容读取
                # 建立 key->row 映射
                header_row = [str(c.value or "").strip() for c in ws[1]]
                # 兼容：用表头名找列索引，否则用固定位置
                col_idx = {name: (header_row.index(name) + 1) if name in header_row else (HEADERS.index(name) + 1) for name in HEADERS}
                mid_col = col_idx["Message-ID"]
                hash_col = col_idx["Content Hash"]
                id_col = col_idx["编号"]
                for r in range(2, ws.max_row + 1):
                    try:
                        mid_v = _norm_mid(ws.cell(row=r, column=mid_col).value)
                        hash_v = str(ws.cell(row=r, column=hash_col).value or "").strip()
                        if mid_v:
                            key = f"MID:{mid_v}"
                        elif hash_v:
                            key = f"HASH:{hash_v}"
                        else:
                            continue
                        self._key_to_row[key] = r
                        try:
                            nid = int(ws.cell(row=r, column=id_col).value or 0)
                            if nid > self._max_id:
                                self._max_id = nid
                        except Exception:
                            pass
                    except Exception:
                        continue
                # 若 max_id 为 0 但有数据行，用行数兜底
                if self._max_id == 0 and ws.max_row >= 2:
                    # 尝试按已有最大编号推断，否则按行数
                    self._max_id = ws.max_row - 1
            except Exception as e:
                # 损坏处理：记录 ERROR，备份后重建，不得静默覆盖
                logger.error("Excel 台账损坏无法读取 %s: %s", path, e)
                try:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup = path.parent / f"{path.stem}.corrupt.{ts}{path.suffix}"
                    shutil.copy2(str(path), str(backup))
                    logger.error("已备份损坏文件 -> %s", backup)
                except Exception as be:
                    logger.error("损坏文件备份失败: %s", be)
                wb = None
                ws = None
        if wb is None or ws is None:
            wb = Workbook()
            ws = wb.active
            ws.title = self.sheet_name
            ws.append(HEADERS)
            self._key_to_row = {}
            self._max_id = 0
            self._apply_header_style(ws)
        else:
            # 确保表头行存在且完整（缺列时补齐表头）
            if ws.max_row >= 1:
                existing = [str(c.value or "").strip() for c in ws[1]]
                if existing != HEADERS:
                    # 若是空表或只有部分表头，重写首行为标准表头（数据行按位置保留）
                    # 为避免破坏记者已调列宽/顺序，仅当首行明显不是标准表头且数据为空时重写；
                    # 否则保持兼容读取（col_idx 已兼容）。
                    if ws.max_row == 1 and not any(existing):
                        for c, h in enumerate(HEADERS, 1):
                            ws.cell(row=1, column=c).value = h
                        self._apply_header_style(ws)
        self._wb = wb
        self._ws = ws
        # 确保说明表存在（可选第二工作表，低成本）
        try:
            self._ensure_help_sheet(wb)
        except Exception:
            pass
        return wb, ws

    def _ensure_help_sheet(self, wb) -> None:
        if HELP_SHEET in wb.sheetnames:
            return
        ws2 = wb.create_sheet(HELP_SHEET)
        lines = [
            ["重要爆料邮件台账 · 说明"],
            [""],
            ["文件用途", "系统识别的重要爆料邮件（S/A/B 或高分）自动登记，供记者筛选、跟进和人工备注。"],
            ["默认进入条件", "优先级 S/A/B，或最终评分 >= 60（均可在 config/excel_register.yaml 配置）。"],
            ["去重", "优先 Message-ID，其次 Content Hash（body_hash）；重跑默认不新增，只更新系统字段。"],
            ["人工字段（不会被覆盖）", "处理状态 / 责任编辑 / 人工标签 / 记者备注"],
            ["处理状态建议值", "待看；已看；跟进中；已采用；暂缓；排除"],
            ["S/A/B 解释", "S>=90 极高价值；A 75-89 高价值；B 60-74 值得跟进；C 40-59 参考；D<40 低价值。"],
            ["V2 字段", "首发/备选渠道写自然语言名称：社交平台首发 / 媒体独家 / 深度调查 / 记者会展示 / 正式检举优先 / 高敏感核验。"],
            ["类别/Pattern", "主要类别、子类别、命中Pattern 及摘要正文中的代码一律显示为自然语言名称（如「收贿、索贿及职务对价」），不再出现 A03/P04/R5 之类代码。"],
            ["V3 字段", "具名媒体 / 揭弊人物 / 放大者 / 正式渠道（Top1-3，名(分)）。"],
            ["生成时间", _now_str()],
            ["注意", "Excel 被记者打开占用时程序只记 warning，不中断筛选；待写入会进 pending_excel_register.jsonl 下次重试。"],
        ]
        for row in lines:
            ws2.append(row)
        try:
            ws2.column_dimensions["A"].width = 24
            ws2.column_dimensions["B"].width = 90
            for r in ws2.iter_rows():
                for c in r:
                    c.alignment = Alignment(vertical="top", wrap_text=True)
            ws2["A1"].font = Font(bold=True, size=14)
        except Exception:
            pass

    def _apply_header_style(self, ws) -> None:
        try:
            fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
            font = Font(bold=True)
            for c in range(1, len(HEADERS) + 1):
                cell = ws.cell(row=1, column=c)
                cell.font = font
                cell.fill = fill
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        except Exception:
            pass

    def _apply_table_style(self, ws) -> None:
        """冻结首行 / 自动筛选 / 列宽 / 换行 / 顶部对齐 / S/A/B 条件格式。"""
        try:
            ws.freeze_panes = "A2"
        except Exception:
            pass
        try:
            ws.auto_filter.ref = ws.dimensions
        except Exception:
            pass
        # 表头加粗（幂等）
        self._apply_header_style(ws)
        # 列宽（兼容 V3.2 动态追加列）
        try:
            _hdr_now = [str(c.value or "").strip() for c in ws[1]]
            for i, h in enumerate(_hdr_now, 1):
                letter = get_column_letter(i)
                if h in COLUMN_WIDTHS:
                    ws.column_dimensions[letter].width = COLUMN_WIDTHS[h]
                elif h in (REVIEW_PATH_FIELD, REVIEW_STATUS_FIELD):
                    ws.column_dimensions[letter].width = COLUMN_WIDTHS.get(h, 18)
        except Exception:
            pass
        # 行高与对齐：数据行顶部对齐，长文本换行
        try:
            wrap_idx = {HEADERS.index(h) + 1 for h in WRAP_FIELDS if h in HEADERS}
            _max_col = max(ws.max_column or len(HEADERS), len(HEADERS))
            for row in ws.iter_rows(min_row=2, max_row=ws.max_row, max_col=_max_col):
                for cell in row:
                    try:
                        if cell.column in wrap_idx:
                            cell.alignment = Alignment(vertical="top", wrap_text=True)
                        else:
                            # 保留已有对齐的垂直顶部
                            cell.alignment = Alignment(vertical="top", wrap_text=(cell.column in wrap_idx))
                    except Exception:
                        pass
            # 表头行高
            ws.row_dimensions[1].height = 22
        except Exception:
            pass
        # 优先级条件格式（文字始终明确显示 S/A/B，颜色仅辅助）
        try:
            # 清理旧的同类规则后追加，避免重复堆积
            pri_col = HEADERS.index("优先级") + 1
            pri_letter = get_column_letter(pri_col)
            rng = f"{pri_letter}2:{pri_letter}{max(ws.max_row, 2)}"
            # 移除已有公式规则中针对该列的（简单起见不全清，只追加；openpyxl 允许叠加）
            red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
            orange = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
            yellow = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            ws.conditional_formatting.add(rng, FormulaRule(formula=[f'${pri_letter}2="S"'], fill=red))
            ws.conditional_formatting.add(rng, FormulaRule(formula=[f'${pri_letter}2="A"'], fill=orange))
            ws.conditional_formatting.add(rng, FormulaRule(formula=[f'${pri_letter}2="B"'], fill=yellow))
        except Exception:
            pass

    def append_or_update(self, record: Any) -> str:
        """未登记新增一行；已登记默认不新增、只更新系统字段。返回 added/updated/skipped。"""
        if self._wb is None or self._ws is None:
            self.load_existing()
        assert self._ws is not None
        if not self.should_register(record):
            return "skipped"
        key = self.build_register_key(record)
        ws = self._ws
        if key in self._key_to_row:
            r = self._key_to_row[key]
            # 更新系统字段，保护人工字段/编号/登记时间
            new_row = self.record_to_row(record, register_key=key)
            for h in HEADERS:
                if h in PROTECTED_FIELDS:
                    continue
                try:
                    c = self._header_idx[h]
                    ws.cell(row=r, column=c).value = new_row.get(h, "")
                except Exception:
                    continue
            return "updated"
        # 新增
        self._max_id += 1
        new_row = self.record_to_row(record, register_key=key, seq_no=self._max_id)
        ws.append([new_row.get(h, "") for h in HEADERS])
        # 新行行号
        new_r = ws.max_row
        self._key_to_row[key] = new_r
        return "added"

    def save(self) -> bool:
        """保存一次。被占用则记 warning 并落 pending，返回 False 但不抛异常。"""
        if self._wb is None or self._ws is None:
            return True
        try:
            self._apply_table_style(self._ws)
        except Exception as e:
            logger.warning("Excel 样式应用失败（不影响数据）: %s", e)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._wb.save(str(self.path))
            self._save_count += 1
            # 成功后清理 pending（若存在且本次已包含其内容则删除）
            try:
                if self._pending_path.exists():
                    # 若 pending 已在本次 load 时 flush，则删除；否则保留
                    # 为简单起见：若文件能正常保存，说明 pending 已合并，删除
                    pass
            except Exception:
                pass
            return True
        except PermissionError as e:
            logger.warning("Excel 台账保存失败（可能被记者打开占用）%s: %s", self.path, e)
            self._dump_pending_on_failure()
            return False
        except OSError as e:
            # Windows 文件占用常表现为 PermissionError 子类或 OSError [Errno 13]
            logger.warning("Excel 台账保存失败 %s: %s", self.path, e)
            self._dump_pending_on_failure()
            return False
        except Exception as e:
            logger.error("Excel 台账保存异常 %s: %s", self.path, e)
            return False

    def _dump_pending_on_failure(self) -> None:
        """保存失败时把内存态全量待写入行 dump 到 pending jsonl，下次重试。"""
        try:
            if self._ws is None:
                return
            self._pending_path.parent.mkdir(parents=True, exist_ok=True)
            # 以当前内存表全量行为准 dump（去重后追加写入 pending）
            header_row = [str(c.value or "").strip() for c in self._ws[1]]
            col_idx = {name: (header_row.index(name) + 1) if name in header_row else (HEADERS.index(name) + 1) for name in HEADERS}
            with open(self._pending_path, "a", encoding="utf-8") as f:
                for r in range(2, self._ws.max_row + 1):
                    rowd = {}
                    for h in HEADERS:
                        try:
                            rowd[h] = self._ws.cell(row=r, column=col_idx[h]).value
                        except Exception:
                            rowd[h] = ""
                    # 解码非 JSON 类型
                    for k, v in list(rowd.items()):
                        if v is None:
                            rowd[k] = ""
                    f.write(json.dumps(rowd, ensure_ascii=False, default=str) + "\n")
            logger.warning("已将 %d 行待写入缓存 -> %s，下次运行自动重试", max(0, self._ws.max_row - 1), self._pending_path)
        except Exception as e:
            logger.error("pending 缓存写入失败: %s", e)

    def _flush_pending(self) -> int:
        """下次运行自动重试 pending。返回合并行数。"""
        if not self._pending_path.exists():
            return 0
        if self._ws is None:
            return 0
        merged = 0
        try:
            lines = self._pending_path.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            logger.warning("pending 文件读取失败: %s", e)
            return 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rowd = json.loads(line)
            except Exception:
                continue
            if not isinstance(rowd, dict):
                continue
            mid_v = _norm_mid(rowd.get("Message-ID", ""))
            hash_v = str(rowd.get("Content Hash", "") or "").strip()
            if mid_v:
                key = f"MID:{mid_v}"
            elif hash_v:
                key = f"HASH:{hash_v}"
            else:
                continue
            if key in self._key_to_row:
                r = self._key_to_row[key]
                for h in HEADERS:
                    if h in PROTECTED_FIELDS:
                        continue
                    try:
                        self._ws.cell(row=r, column=self._header_idx[h]).value = rowd.get(h, "")
                    except Exception:
                        continue
            else:
                self._max_id += 1
                rowd["编号"] = self._max_id
                if not rowd.get("登记时间"):
                    rowd["登记时间"] = _now_str()
                for mf, dv in MANUAL_DEFAULTS.items():
                    if mf not in rowd or rowd[mf] is None:
                        rowd[mf] = dv
                self._ws.append([rowd.get(h, "") for h in HEADERS])
                self._key_to_row[key] = self._ws.max_row
            merged += 1
        # 合并后若后续 save 成功则删除 pending；此处先保留，由调用方在 save 成功后删除
        return merged

    def _maybe_backup(self) -> Optional[Path]:
        if not self.backup_before_batch:
            return None
        try:
            if not self.path.exists():
                return None
            backup_dir = self.path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = backup_dir / f"{self.path.stem}.{ts}{self.path.suffix}"
            if dst.exists():
                return dst
            shutil.copy2(str(self.path), str(dst))
            logger.info("Excel 台账批处理前已备份 -> %s", dst)
            return dst
        except Exception as e:
            logger.warning("Excel 备份失败（继续处理）: %s", e)
            return None

    # ------------------------------------------------------------------
    # V3.2 Review Queue 兼容：待审查文件路径列（动态追加，不破坏原有表头）
    # ------------------------------------------------------------------
    def _current_header(self) -> List[str]:
        try:
            if self._ws is not None:
                return [str(c.value or "").strip() for c in self._ws[1]]
        except Exception:
            pass
        return list(HEADERS)

    def _ensure_review_column(self) -> Optional[int]:
        """确保存在 待审查文件路径 列；返回其列号（1-based），失败返回 None。"""
        try:
            if self._ws is None:
                return None
            hdr = self._current_header()
            if REVIEW_PATH_FIELD in hdr:
                return hdr.index(REVIEW_PATH_FIELD) + 1
            # 追加到表尾
            new_col = len(hdr) + 1
            cell = self._ws.cell(row=1, column=new_col)
            cell.value = REVIEW_PATH_FIELD
            try:
                from openpyxl.styles import Alignment as _Al, Font as _Fo, PatternFill as _Pf
                cell.font = _Fo(bold=True)
                cell.fill = _Pf(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
                cell.alignment = _Al(horizontal="center", vertical="center", wrap_text=True)
            except Exception:
                pass
            try:
                from openpyxl.utils import get_column_letter as _gcl
                self._ws.column_dimensions[_gcl(new_col)].width = COLUMN_WIDTHS.get(REVIEW_PATH_FIELD, 44)
            except Exception:
                pass
            return new_col
        except Exception as e:
            logger.warning("Excel 待审查列创建失败: %s", e)
            return None

    def _key_to_row_dynamic(self) -> Dict[str, int]:
        """按当前表头重建 register_key -> 行号映射（兼容动态列）。"""
        mapping: Dict[str, int] = {}
        try:
            if self._ws is None:
                return mapping
            hdr = self._current_header()
            if "Message-ID" in hdr:
                mid_col = hdr.index("Message-ID") + 1
            else:
                mid_col = HEADERS.index("Message-ID") + 1
            if "Content Hash" in hdr:
                hash_col = hdr.index("Content Hash") + 1
            else:
                hash_col = HEADERS.index("Content Hash") + 1
            for r in range(2, self._ws.max_row + 1):
                try:
                    mid_v = _norm_mid(self._ws.cell(row=r, column=mid_col).value)
                    hash_v = str(self._ws.cell(row=r, column=hash_col).value or "").strip()
                    if mid_v:
                        mapping[f"MID:{mid_v}"] = r
                    elif hash_v:
                        mapping[f"HASH:{hash_v}"] = r
                except Exception:
                    continue
        except Exception:
            pass
        return mapping

    def update_review_paths(self, review_map: Dict[str, str]) -> int:
        """将 Review Queue 副本路径回填到 待审查文件路径 列。

        review_map: register_key(MID:/HASH:) -> queue .eml 路径。
        返回更新行数；Excel 缺失/禁用时返回 0 且不抛异常。
        """
        if not review_map:
            return 0
        try:
            if not self.path.exists():
                return 0
            if self._wb is None or self._ws is None:
                self.load_existing()
            col = self._ensure_review_column()
            if col is None:
                return 0
            key_to_row = self._key_to_row_dynamic() or self._key_to_row
            n = 0
            for k, p in review_map.items():
                try:
                    r = key_to_row.get(str(k))
                    if r is None:
                        continue
                    self._ws.cell(row=r, column=col).value = str(p)
                    n += 1
                except Exception:
                    continue
            if n:
                try:
                    self.save()
                except Exception as e:
                    logger.warning("Excel 待审查路径回填保存失败: %s", e)
            return n
        except Exception as e:
            logger.warning("Excel 待审查路径回填失败: %s", e)
            return 0

    def process_batch(self, records: List[Any], review_path_map: Optional[Dict[str, str]] = None) -> Dict[str, int]:
        """批量：本轮筛选完成后收集符合条件记录，打开一次、批量 append/update、保存一次。"""
        stats = {"added": 0, "updated": 0, "skipped": 0, "total": len(records or [])}
        if not self.enabled:
            logger.info("Excel 台账已禁用（enabled=false），跳过登记")
            stats["skipped"] = len(records or [])
            return stats
        if Workbook is None:
            logger.error("缺少 openpyxl，跳过 Excel 台账")
            stats["skipped"] = len(records or [])
            return stats
        # 无符合条件且无历史文件/pending 时，不创建空文件
        try:
            has_pending = self._pending_path.exists()
        except Exception:
            has_pending = False
        if not self.path.exists() and not has_pending:
            try:
                qual = [r for r in (records or []) if self.should_register(r)]
            except Exception:
                qual = list(records or [])
            if not qual:
                stats["skipped"] = len(records or [])
                return stats
        # 大量更新前最多一个备份
        try:
            self._maybe_backup()
        except Exception:
            pass
        try:
            self.load_existing()
        except Exception as e:
            logger.error("Excel 台账加载失败，跳过本轮登记: %s", e)
            return stats
        # 先重试 pending
        try:
            flushed = self._flush_pending()
            if flushed:
                logger.info("pending 重试合并 %d 行", flushed)
        except Exception as e:
            logger.warning("pending 重试失败: %s", e)
        for rec in (records or []):
            try:
                res = self.append_or_update(rec)
                if res in stats:
                    stats[res] += 1
                elif res not in ("added", "updated", "skipped"):
                    stats["skipped"] += 1
            except Exception as e:
                logger.warning("单封台账登记失败（继续批量）: %s", e)
                stats["skipped"] += 1
        # V3.2：若传入 review 路径映射，回填到 待审查文件路径 列（动态追加列）
        if review_path_map:
            try:
                col = self._ensure_review_column()
                if col is not None:
                    key_to_row = self._key_to_row_dynamic() or self._key_to_row
                    for k, p in review_path_map.items():
                        try:
                            r = key_to_row.get(str(k))
                            if r is not None:
                                self._ws.cell(row=r, column=col).value = str(p)
                        except Exception:
                            continue
            except Exception as e:
                logger.warning("Excel 待审查路径回填失败（不影响登记）: %s", e)
        # 保存一次
        try:
            ok = self.save()
            if ok:
                # 保存成功后清理 pending
                try:
                    if self._pending_path.exists():
                        self._pending_path.unlink()
                except Exception:
                    pass
            else:
                logger.warning("Excel 台账本轮保存失败，已保留筛选结果并缓存 pending")
        except Exception as e:
            logger.error("Excel 台账保存异常（不影响筛选结果）: %s", e)
        return stats

    # ------------------------------------------------------------------
    # 兼容别名（供测试/外部调用）
    # ------------------------------------------------------------------
    def process_records(self, records: List[Any]) -> Dict[str, int]:
        return self.process_batch(records)


__all__ = [
    "HEADERS",
    "MANUAL_FIELDS",
    "MANUAL_DEFAULTS",
    "REVIEW_PATH_FIELD",
    "REVIEW_STATUS_FIELD",
    "ImportantEmailRegister",
    "ImportantEmailRegisterRow",
]
