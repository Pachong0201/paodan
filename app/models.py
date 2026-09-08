"""统一数据模型：邮件、附件、规则结果、评分、报告."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


@dataclass
class AttachmentDoc:
    filename: str = ""
    file_type: str = ""            # pdf/docx/xlsx/csv/txt/jpg/png/eml/unknown
    text: str = ""
    metadata: dict = field(default_factory=dict)   # pages, sheets, size, ocr 状态等
    tables: list = field(default_factory=list)     # [{sheet,row,col,value}...]
    extraction_status: str = "success"  # success/partial/failed/skipped(无OCR)
    warnings: list = field(default_factory=list)
    sha256: str = ""
    source_sha256: str = ""          # 原始附件 bytes SHA256（附件身份）
    text_sha256: str = ""            # 抽取文本标准化后的 SHA256
    parser_version: str = "attachment-parser-v1"
    ocr_version: str = "ocr-v1"
    ocr_used: bool = False
    content_hash: str = ""           # 旧字段：兼容文本内容哈希，不再作为唯一附件身份

    @property
    def source_hash(self) -> str:
        return self.source_sha256

    @property
    def text_hash(self) -> str:
        return self.text_sha256

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EmailDocument:
    email_id: str = ""
    message_id: str = ""
    subject: str = ""
    sender: str = ""
    recipients: list = field(default_factory=list)
    cc: list = field(default_factory=list)
    date: str = ""
    body_text: str = ""              # 原始正文（清洗后）
    html_body: str = ""
    original_text: str = ""          # 原始全文（含附件文本）
    normalized_text: str = ""        # 繁转简+标准化的全文
    body_hash: str = ""
    raw_sha256: str = ""             # 原始 EML bytes SHA256（无 Message-ID 时主键）
    normalized_message_id: str = ""  # 归一化 Message-ID（ingestion identity 优先）
    attachments: list = field(default_factory=list)  # AttachmentDoc
    combined_text: str = ""          # 全文（body+所有附件文本拼接）
    source_path: str = ""

    @property
    def raw_eml_sha256(self) -> str:
        return self.raw_sha256

    @property
    def email_source_hash(self) -> str:
        return self.raw_sha256

    @property
    def ingestion_identity(self) -> str:
        if self.normalized_message_id:
            return f"MID:{self.normalized_message_id}"
        if self.raw_sha256:
            return f"RAW:{self.raw_sha256}"
        return self.email_id

    @property
    def all_text(self) -> str:
        return self.combined_text or self.body_text

    def to_dict(self) -> dict:
        d = asdict(self)
        d["attachments"] = [a.to_dict() if hasattr(a, "to_dict") else a for a in self.attachments]
        return d


# ---------- 规则引擎结果 ----------

@dataclass
class EntityHit:
    text: str
    type: str          # PERSON/ORGANIZATION/COMPANY/GOVERNMENT_AGENCY/POLITICAL_PARTY/
                       # PROJECT/LOCATION/DATE/MONEY/BANK_ACCOUNT/PHONE/EMAIL/DOCUMENT/ROLE
    count: int = 1
    sample: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KeywordHit:
    term: str
    ktype: str         # H/M/C/E/S/X 或自定义类型
    category: str      # GLOBAL 或 A01..A18
    count: int = 1
    snippet: str = ""
    stage: str = ""    # S 类词的 S1..S6 阶段

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PatternHit:
    pattern_id: str
    category: str            # A02 / A03 / A11|A12 / GLOBAL
    name: str
    matched_terms: list = field(default_factory=list)
    evidence_snippets: list = field(default_factory=list)
    pattern_score: float = 0.0
    window: str = ""         # sentence/paragraph/context3/full
    signal_terms: list = field(default_factory=list)  # 动态组命中（如 target_person 词）

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NegativeMatch:
    rule_id: str
    condition: str
    score_delta: float
    max_score: Optional[float]
    matched_terms: list = field(default_factory=list)
    snippets: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuleResult:
    matched_categories: list = field(default_factory=list)
    matched_keywords: dict = field(default_factory=dict)  # {type: [KeywordHit]}
    matched_patterns: list = field(default_factory=list)
    negative_matches: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    money: list = field(default_factory=list)      # MoneyHit 序列化 dict
    evidence_snippets: list = field(default_factory=list)
    rule_score: float = 0.0
    rule_pass: bool = False
    normalized: str = ""
    x_terms: list = field(default_factory=list)    # 命中的反向词
    ex_present: bool = False
    new_evidence_after_ex: bool = False
    no_new_evidence: bool = False
    target_persons_found: list = field(default_factory=list)
    target_orgs_found: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["matched_keywords"] = {
            k: [h if isinstance(h, dict) else h.to_dict() for h in v]
            for k, v in self.matched_keywords.items()
        }
        return d


@dataclass
class LLMResult:
    raw: Optional[dict] = None
    relevant: bool = True
    priority_level: str = ""
    importance_score: float = 0.0
    target_persons: list = field(default_factory=list)
    target_organizations: list = field(default_factory=list)
    categories: list = field(default_factory=list)
    subcategories: list = field(default_factory=list)
    specific_behaviors: list = field(default_factory=list)
    evidence_stage: str = "E1"
    allegation_status: str = "待核实爆料"
    related_entities: dict = field(default_factory=dict)
    projects_or_cases: list = field(default_factory=list)
    money_or_benefits: list = field(default_factory=list)
    power_actions: list = field(default_factory=list)
    evidence_items: list = field(default_factory=list)
    suspicious_phrases: list = field(default_factory=list)
    relationship_chain: list = field(default_factory=list)
    negative_or_exculpatory_evidence: list = field(default_factory=list)
    new_information: list = field(default_factory=list)
    known_old_information: list = field(default_factory=list)
    verification_targets: list = field(default_factory=list)
    public_interest_reason: str = ""
    one_sentence_summary: str = ""
    reason_for_attention: str = ""
    needs_human_review: bool = True
    confidence: float = 0.0
    llm_status: str = "ok"     # ok/failed/template(离线模板)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


@dataclass
class FinalScore:
    final_score: float = 0.0
    priority: str = "D"
    dimension_scores: dict = field(default_factory=dict)
    dimension_details: list = field(default_factory=list)
    stage_adjustment: float = 0.0
    negative_adjustment: float = 0.0
    evidence_stage: str = "E1"
    # V4.1 统一双轨融合结果
    governance_score: float = 0.0
    primary_track: str = "NONE"
    unified_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScreeningRecord:
    email_id: str = ""
    email: Optional[EmailDocument] = None
    rule: Optional[RuleResult] = None
    llm: Optional[LLMResult] = None
    score: Optional[FinalScore] = None
    summary_zh: str = ""
    verification_targets: list = field(default_factory=list)
    known_news: Optional[dict] = None
    error: str = ""
    schema_version: str = "4.1.1"
    # V4.1 统一双轨信号（UnifiedSignalSet；Pipeline 在 V1+V4 后填充）
    unified_signals: Optional[object] = None
    # V2 首发渠道推荐（可选，S/A/B 经 Release Advisor 后填充）
    release_recommendation: Optional[dict] = None
    # V3 具名渠道推荐（可选，S/A/B 经 Named Channel Advisor 后填充）
    named_channel_recommendation: Optional[dict] = None
    # V4 治理民生投诉（G01-G12 并行，不影响 A01-A18 语义）
    governance_categories: list = field(default_factory=list)
    governance_keywords: dict = field(default_factory=dict)
    governance_patterns: list = field(default_factory=list)
    governance_score: float = 0.0
    governance_priority: str = ""
    governance_dims: dict = field(default_factory=dict)
    governance_negatives: list = field(default_factory=list)
    governance_enhance: dict = field(default_factory=dict)
    governance_details: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "email_id": self.email_id,
            "error": self.error,
        }
        if self.email is not None:
            e = self.email.to_dict()
            e.pop("body_text", None)
            e.pop("html_body", None)
            e.pop("combined_text", None)
            e.pop("original_text", None)
            e.pop("normalized_text", None)
            e.pop("body_hash", None)
            d["email"] = e
        if self.rule is not None:
            d["rule"] = self.rule.to_dict()
        if self.llm is not None:
            d["llm"] = self.llm.to_dict()
        if self.score is not None:
            d["score"] = self.score.to_dict()
        d["summary_zh"] = self.summary_zh
        d["verification_targets"] = self.verification_targets
        if self.known_news is not None:
            d["known_news"] = self.known_news
        if self.unified_signals is not None:
            d["unified_signals"] = (self.unified_signals.to_dict()
                                    if hasattr(self.unified_signals, "to_dict")
                                    else self.unified_signals)
        if self.release_recommendation is not None:
            d["release_recommendation"] = self.release_recommendation
        if self.named_channel_recommendation is not None:
            d["named_channel_recommendation"] = self.named_channel_recommendation
        # V4 governance block（并行输出，不影响原 rule_score/final_score 语义）
        if self.governance_categories or self.governance_score:
            d["governance_categories"] = list(self.governance_categories)
            gov_kw = {}
            for k, v in (self.governance_keywords or {}).items():
                lst = []
                for h in v:
                    lst.append(h.to_dict() if hasattr(h, "to_dict") else h)
                gov_kw[k] = lst
            d["governance_keywords"] = gov_kw
            d["governance_patterns"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in (self.governance_patterns or [])]
            d["governance_score"] = float(self.governance_score or 0)
            d["governance_priority"] = self.governance_priority or ""
            d["governance_dims"] = dict(self.governance_dims or {})
            d["governance_negatives"] = [n.to_dict() if hasattr(n, "to_dict") else n
                                         for n in (self.governance_negatives or [])]
            d["governance_enhance"] = dict(self.governance_enhance or {})
            d["governance_details"] = list(self.governance_details or [])
        return d

    def to_jsonl_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
