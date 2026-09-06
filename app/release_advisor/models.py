"""首发渠道推荐模块数据模型。

ReleaseDecisionFeatures —— 渠道决策特征（由 rule/llm 结果构建，规则引擎与 LLM 共用）；
ReleaseRecommendation —— 最终推荐（含渠道、风险、核验、正式检举、多阶段发布序列）。
本模块只输出建议，不执行任何发布/检举动作。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, List, Optional

# 合法枚举集合（与 config/release_route_rules.yaml / schema.py 保持一致）
VALID_ROUTES = {"R1", "R2", "R3", "R4", "R5", "R6"}
VALID_EVIDENCE_SHAPES = {
    "FIRST_PERSON_TESTIMONY", "CHAT_RECORD", "AUDIO", "VIDEO", "PHOTO",
    "BANK_RECORD", "CONTRACT", "INTERNAL_DOCUMENT", "OFFICIAL_DOCUMENT",
    "PROCUREMENT_FILE", "SPREADSHEET", "DATABASE_RECORD", "ACADEMIC_DOCUMENT",
    "LOCATION_DATA", "CLASSIFIED_DOCUMENT", "ANONYMOUS_DOCUMENT",
    "MULTI_SOURCE_PACKAGE",
}
VALID_RISKS = {
    "SOURCE_EXPOSURE", "PRIVACY", "DEFAMATION", "EVIDENCE_AUTHENTICITY",
    "CONTEXT_LOSS", "RETALIATION", "DESTRUCTION_OF_EVIDENCE",
    "WITNESS_COLLUSION", "CLASSIFIED_INFORMATION", "LEGAL_PROCESS_INTERFERENCE",
    "MISLEADING_OLD_NEWS", "DOCUMENT_FORGERY",
}
VALID_FORMAL_TYPES = {
    "PROSECUTOR", "INVESTIGATION_BUREAU", "CONTROL_YUAN", "ACADEMIC_ETHICS",
    "INTERNAL_COMPLAINT", "LABOR_OR_EQUALITY_AUTHORITY", "OTHER_REGULATOR", "NONE",
}
VALID_SEQUENCE_ACTIONS = {
    "EDITORIAL_VERIFICATION", "FORMAL_REFERRAL", "PUBLIC_RELEASE", "PRIVATE_OUTREACH",
}


@dataclass
class ReleaseDecisionFeatures:
    """一条 S/A/B 线索进入渠道判断的结构化特征。"""
    email_id: str = ""
    priority_level: str = "A"
    final_score: float = 0.0
    categories: List[str] = field(default_factory=list)
    evidence_stage: str = "E1"
    evidence_shapes: List[str] = field(default_factory=list)
    # 基础事实特征
    has_original_evidence: bool = False       # 邮件含 E 类原始证据词/附件原件
    has_money_flow: bool = False              # 存在银行流水/汇款/金流证据
    has_power_action: bool = False            # 存在关说/施压/护航等权力动作
    has_first_person_testimony: bool = False  # 第一人称受害/亲历叙述
    has_sensitive_personal_data: bool = False
    has_classified_material: bool = False     # 机密/密件/国安资料
    has_anonymous_documents: bool = False     # 匿名/无法溯源文件
    requires_complex_explanation: bool = False  # 多公司/多地块/复杂资金需要数据解释
    destruction_risk: bool = False            # 灭证风险（对方正在删记录等）
    retaliation_risk: bool = False            # 报复/串证风险（对爆料人或证人）
    source_requests_anonymity: bool = False   # 来源要求匿名/身份保护需求
    known_old_case: bool = False              # 旧案（无新信息则不应公开首发）
    contains_new_information: bool = True     # 相对旧案是否有新增材料
    # 附加语义特征（RR 规则使用）
    has_organized_crime_signal: bool = False  # 黑道/帮派/洗钱/诈骗等组织犯罪词
    public_interest_established: bool = False # A14 私德类公共利益是否成立
    source_identification: str = ""           # 实名/化名/匿名/未表明
    witness_role: str = ""                    # victim/insider/third_party
    anonymity_capability: str = ""            # 渠道匿名承载能力: full/partial/none
    visually_clear: bool = False              # 证据简单高度可视化（R4 加分）
    quickly_verifiable: bool = False          # 可用公开资料快速核验（R4 加分）
    sensitive_material: bool = False          # 兜底敏感标记（如含性骚细节/未成年/隐私）
    source_text: str = ""                     # 用于 LLM 的脱敏摘要（不落库原始材料）

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("source_text", None)
        return d

    def signal_dict(self) -> dict:
        """只返回布尔特征键值（供规则条件匹配，避免误用字符串字段）。"""
        d = asdict(self)
        drop = {"email_id", "priority_level", "final_score", "categories",
                "evidence_stage", "evidence_shapes", "source_identification",
                "witness_role", "anonymity_capability", "source_text"}
        return {k: v for k, v in d.items() if k not in drop}


@dataclass
class ReleaseSequenceStep:
    step: int = 1
    action: str = "EDITORIAL_VERIFICATION"   # 见 VALID_SEQUENCE_ACTIONS
    route: str = ""                          # FORMAL_REFERRAL -> 机关枚举；PUBLIC_RELEASE -> Rx
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReleaseRecommendation:
    """渠道建议最终对象（规则引擎 + LLM 融合后）。"""
    email_id: str = ""
    score: float = 0.0                       # 融合置信度 0-1
    primary_route: str = ""
    primary_route_name: str = ""
    secondary_routes: List[str] = field(default_factory=list)
    avoid_routes: List[str] = field(default_factory=list)
    route_confidence: float = 0.0
    prepublication_verification_required: bool = False
    verification_before_release: List[str] = field(default_factory=list)
    formal_referral_recommended: bool = False
    formal_referral_type: List[str] = field(default_factory=list)
    release_risks: List[str] = field(default_factory=list)
    reason: str = ""
    recommended_release_sequence: List[dict] = field(default_factory=list)
    headline_angle: str = ""
    editor_note: str = ""
    # 内部溯源字段
    rule_hits: List[str] = field(default_factory=list)      # 命中 RR 规则 id
    source: str = "rule"                                     # rule/llm/rule+llm/template
    llm_status: str = ""                                     # ok/template/failed

    def to_dict(self) -> dict:
        d = asdict(self)
        d["recommended_release_sequence"] = [
            s if isinstance(s, dict) else (s.to_dict() if hasattr(s, "to_dict") else dict(s))
            for s in self.recommended_release_sequence
        ]
        return d

    def to_jsonl_line(self) -> str:
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False)
