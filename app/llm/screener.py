"""LLM 语义筛选器：把规则包提示词作为系统提示 + 动态注入规则命中上下文。

设计：
- 必须读取 llm_email_screening_prompt.md 为基础系统提示（不重写）；
- 注入「本邮件规则识别结果」结构化附录；
- 输出 JSON schema 校验，失败重试一次，再失败 llm_status=failed；
- LLM_MODE=template 或未配置 key 时，用本地模板结果兜底，保证无 LLM 环境下端到端仍可运行。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import (LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODE, LLM_MODEL,
                      LLM_TEMPLATE_FILE, LLM_TIMEOUT, NEWS_SIGNAL_DIR)
from ..models import LLMResult, RuleResult
from ..preprocessing.normalization import mask_sensitive
from ..rules.config_loader import RuleConfig
from .client import LLMClient, LLMError
from .schemas import SCHEMA_FIELDS, validate_result

logger = logging.getLogger(__name__)

# 模板兜底中常用词
STAGE_GUESS_RE = re.compile(r"(E[0-5X])")

SENSITIVE_KEYS = ("verification_targets", "reason_for_attention", "one_sentence_summary")


def build_rule_context(rr: RuleResult) -> str:
    """把规则引擎结果渲染为 LLM 的「本邮件规则识别结果」附录."""
    lines = ["【本邮件规则识别结果】"]
    if rr.matched_categories:
        lines.append("命中类别：" + "、".join(rr.matched_categories))
    for ktype, label in (("H", "H(高强度行为词)"), ("M", "M(隐性表达)"), ("C", "C(关系/场景)"),
                         ("E", "E(原始证据)"), ("S", "S(程序词)")):
        terms = list(dict.fromkeys(h.term for h in rr.matched_keywords.get(ktype, [])))
        if terms:
            lines.append(f"{label}：{'、'.join(terms[:25])}")
    if rr.matched_patterns:
        lines.append("Pattern：" + "、".join(p.pattern_id for p in rr.matched_patterns))
    if rr.money:
        money_desc = "、".join(f"{m.get('raw')}({m.get('amount')} {m.get('currency')})" for m in rr.money[:10])
        lines.append("金额：" + money_desc)
    if rr.x_terms:
        lines.append("反向结果词：" + "、".join(rr.x_terms[:10]))
    if rr.no_new_evidence:
        lines.append("判定：未见新增证据")
    return "\n".join(lines)


def _rule_context_snippets(rr: RuleResult) -> List[str]:
    out = list(rr.evidence_snippets[:5])
    for p in rr.matched_patterns[:5]:
        for s in p.evidence_snippets[:1]:
            if s and s not in out:
                out.append(s)
    return out[:6]


def truncate_for_llm(text: str, max_chars: int = 12000) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    head = text[: max_chars * 2 // 3]
    tail = text[-max_chars // 3:]
    return head + "\n……[中间省略]……\n" + tail


class LLMScreener:
    def __init__(self, rule_config: Optional[RuleConfig] = None,
                 mode: Optional[str] = None,
                 template_file: Optional[Path] = None):
        self.rule_config = rule_config
        self.mode = mode or LLM_MODE
        self.template_file = Path(template_file) if template_file else LLM_TEMPLATE_FILE
        self.client: Optional[LLMClient] = None
        if self.mode == "api":
            try:
                self.client = LLMClient(LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT)
            except LLMError as e:
                logger.warning("LLM API 初始化失败，回退模板模式: %s", e)
                self.client = None
                self.mode = "template"
        self._prompt = ""
        if self.rule_config is not None:
            self._prompt = self.rule_config.llm_prompt

    # ------------------------------------------------------------------
    def screen(self, email_text: str, subject: str, sender: str,
               rule_result: RuleResult) -> LLMResult:
        """完整筛选；结构化校验失败自动重试一次."""
        try:
            if self.mode == "api" and self.client is not None:
                out = self._screen_api(email_text, subject, sender, rule_result)
            else:
                out = self._screen_template(email_text, subject, sender, rule_result)
            ok, cleaned, errors = validate_result(out)
            if not ok:
                # 重试一次
                logger.info("LLM 输出校验失败(%s)，重试…", errors[:3])
                if self.mode == "api" and self.client is not None:
                    out2 = self._screen_api(email_text, subject, sender, rule_result, retry=True)
                else:
                    out2 = self._screen_template(email_text, subject, sender, rule_result)
                ok, cleaned, errors = validate_result(out2)
                if not ok:
                    raise LLMError(f"结构化校验两次失败: {errors[:5]}")
            llm = LLMResult(raw=out, **{k: v for k, v in cleaned.items() if k in SCHEMA_FIELDS})
            llm.llm_status = "ok" if self.mode == "api" else "template"
            return llm
        except LLMError as e:
            logger.warning("LLM 筛选失败: %s", e)
            return LLMResult(llm_status="failed", relevant=False,
                             reason_for_attention=f"LLM 调用失败: {e}",
                             one_sentence_summary="LLM 处理失败，待人工复核")

    # ------------------------------------------------------------------
    def _build_user_content(self, email_text: str, subject: str, sender: str,
                            rule_result: RuleResult, retry: bool = False) -> str:
        rule_ctx = build_rule_context(rule_result)
        snippets = _rule_context_snippets(rule_result)
        parts = [rule_ctx]
        if snippets:
            parts.append("\n原文证据片段：\n" + "\n".join("…" + s + "…" for s in snippets[:6]))
        parts.append("\n=== 邮件内容（原文） ===")
        meta = []
        if subject:
            meta.append(f"主旨：{subject}")
        if sender:
            meta.append(f"寄件人：{sender}")
        parts.append("\n".join(meta))
        parts.append(truncate_for_llm(mask_sensitive(email_text)))
        if retry:
            parts.append("\n（上次输出不符合 JSON schema，请严格按系统提示的 JSON 结构输出）")
        return "\n\n".join(parts)

    def _screen_api(self, email_text: str, subject: str, sender: str,
                    rule_result: RuleResult, retry: bool = False) -> dict:
        user = self._build_user_content(email_text, subject, sender, rule_result, retry)
        return self.client.chat_json(self._prompt, user, retries=1)

    # ---------------- 模板模式（离线确定性兜底） ----------------
    def _screen_template(self, email_text: str, subject: str, sender: str,
                         rule_result: RuleResult, retry: bool = False) -> dict:
        """无 LLM 环境下的可解释兜底：由规则命中生成结构化 JSON，保证端到端链路."""
        cats = list(rule_result.matched_categories)
        # 若规则没给类别但命中了 pattern 的类别，补上
        for p in rule_result.matched_patterns:
            for c in str(p.category).split("|"):
                if c in {f"A{i:02d}" for i in range(1, 19)} and c not in cats:
                    cats.append(c)
        # 无收敛类别但规则分高且 H/M 词强 -> 保底 A03；弱命中不硬造类别
        strong_signal = bool(rule_result.matched_patterns) or rule_result.rule_score >= 80 or (
            rule_result.target_persons_found and rule_result.money)
        if not cats and strong_signal and (rule_result.matched_keywords.get("H") or rule_result.matched_keywords.get("M")):
            cats = ["A03"]
        evidence_items = []
        for h in rule_result.matched_keywords.get("E", [])[:8]:
            evidence_items.append(h.term)
        for p in rule_result.matched_patterns[:5]:
            evidence_items.append(f"{p.pattern_id}({p.name})")
        money_desc = []
        for m in rule_result.money[:5]:
            money_desc.append(f"{m.get('raw')} {m.get('currency')}")

        # 从规则结果推断关键要素
        target_persons = list(rule_result.target_persons_found[:5])
        target_orgs = list(rule_result.target_orgs_found[:5])
        cats_for_label = list(dict.fromkeys(cats))
        patterns = [p.pattern_id for p in rule_result.matched_patterns]

        # evidence stage：有 EX 词 -> EX；有 S2+ 词 -> E2/E3。
        # 若 EX 但同时有新增证据(金额/原始证据模式) -> 记为 E1 爆料并保留反向记录
        # （规则包 hard_rules：不得因 EX 掩盖新证据）
        stage = "E1"
        has_new_ev = bool(rule_result.money) or any(
            p.pattern_id == "P19" for p in rule_result.matched_patterns)
        if rule_result.ex_present and not has_new_ev:
            stage = "EX"
        elif rule_result.matched_keywords.get("S") and not rule_result.ex_present:
            stages = set(h.stage for h in rule_result.matched_keywords.get("S", []) if h.stage)
            if any(s in stages for s in ("S5", "S6")):
                stage = "E4"
            elif any(s in stages for s in ("S3", "S4")):
                stage = "E3"
            elif "S2" in stages:
                stage = "E2"

        relevant = bool(cats or patterns) and not (rule_result.ex_present and rule_result.no_new_evidence and not rule_result.new_evidence_after_ex)

        # 证据词/关系链
        chains = []
        persons = [x["text"] for x in rule_result.entities if x.get("type") == "PERSON"][:3]
        companies = [x["text"] for x in rule_result.entities if x.get("type") == "COMPANY"][:3]
        if persons:
            chains.append("人物:" + "、".join(persons))
        if companies:
            chains.append("公司:" + "、".join(companies))
        if money_desc:
            chains.append("资金:" + "、".join(money_desc))
        m_terms = [h.term for h in rule_result.matched_keywords.get("M", [])][:5]
        if m_terms:
            chains.append("隐性表达:" + "、".join(m_terms))
        for p in rule_result.matched_patterns[:3]:
            chains.append(f"{p.pattern_id} 命中({p.name})")

        # 生成简短但可解释的 summary / reason
        neg_note = ""
        if rule_result.ex_present:
            neg_note = "；该事项已有反向结果(如不起诉/无罪)，需查明本邮件是否提供新证据"
        evidence_note = "附件/邮件含原始证据词(" + "、".join(evidence_items[:4]) + ")" if evidence_items else "未见明确原始证据词"
        one_line = f"邮件指称涉及{'、'.join(cats_for_label[:3]) or '待定类别'}相关事项：{'；'.join(chains[:4]) or '内容以情绪性表述为主'}{neg_note}。"
        reason = (f"规则引擎识别到{'、'.join(cats_for_label[:3]) or '相关类别'}信号，"
                  f"命中关键词 {sum(len(v) for v in rule_result.matched_keywords.values())} 项、"
                  f"Pattern {'/'.join(patterns) if patterns else '无'}，{evidence_note}，"
                  f"资金记录 {len(rule_result.money)} 笔。{neg_note}。")

        verification = self._make_verification_targets(rule_result, target_persons, money_desc)
        confidence = 0.6 if (rule_result.matched_patterns or evidence_items) else 0.3

        return {
            "relevant": relevant,
            "priority_level": "",
            "importance_score": 0.0,   # FinalScorer 决定，不用 LLM 自报
            "target_persons": target_persons,
            "target_organizations": target_orgs,
            "categories": cats_for_label,
            "subcategories": [],
            "specific_behaviors": m_terms + [h.term for h in rule_result.matched_keywords.get("H", [])][:5],
            "evidence_stage": stage,
            "allegation_status": "待核实爆料" if stage == "E1" else
                                 ("反向结果记录" if stage == "EX" else "正式程序进行中"),
            "related_entities": {
                "companies": companies,
                "relatives": [x["text"] for x in rule_result.entities if x.get("type") == "PERSON" and x["text"] in (rule_result.target_persons_found or [])][:3],
                "assistants": [],
                "intermediaries": [],
                "officials": [],
                "others": [h.term for h in rule_result.matched_keywords.get("C", [])][:6],
            },
            "projects_or_cases": [x["text"] for x in rule_result.entities if x.get("type") == "PROJECT"][:5],
            "money_or_benefits": money_desc,
            "power_actions": [h.term for h in rule_result.matched_keywords.get("H", []) if h.term in
                              ("关说", "施压", "协调", "护航", "放水", "删改", "要求通过", "帮忙")][:8],
            "evidence_items": list(dict.fromkeys(evidence_items))[:10],
            "suspicious_phrases": m_terms,
            "relationship_chain": chains[:8],
            "negative_or_exculpatory_evidence": list(rule_result.x_terms)[:8],
            "new_information": [],
            "known_old_information": [],
            "verification_targets": verification,
            "public_interest_reason": ("涉及公职伦理与公权力行使" if cats else ""),
            "one_sentence_summary": one_line[:300],
            "reason_for_attention": reason[:500],
            "needs_human_review": True,
            "confidence": confidence,
        }

    def _make_verification_targets(self, rr: RuleResult, persons, money_desc) -> List[str]:
        targets = []
        cats = rr.matched_categories
        ps = persons or [x["text"] for x in rr.entities if x.get("type") == "PERSON"][:3]
        orgs = [x["text"] for x in rr.entities if x.get("type") in ("COMPANY", "ORGANIZATION")][:3]
        if ps:
            targets.append(f"核实{'、'.join(ps)}的身份、任职与公开职务记录")
        if orgs:
            targets.append(f"查询{'、'.join(orgs)}的工商登记、负责人、股东与标案记录")
        for m in rr.money[:3]:
            targets.append(f"核验{m.get('raw')}汇款/资金记录的真实性、交易时间与账户")
        if rr.ex_present:
            targets.append("查询该案既有不起诉/无罪/查无不法记录及其范围")
        if rr.matched_patterns:
            names = [p.name for p in rr.matched_patterns[:3]]
            targets.append("对照Pattern线索(" + "、".join(names) + ")比对行政流程与时间线")
        if any("LINE" in h.term or "对话" in h.term for h in rr.matched_keywords.get("E", [])):
            targets.append("核验LINE/聊天记录是否完整导出，时间戳与上下文是否被截断")
        if any(h.term in ("银行流水", "汇款单", "提款记录", "存折") for h in rr.matched_keywords.get("E", [])):
            targets.append("调取并核验银行流水/汇款单原件真伪")
        if len(targets) < 3:
            targets.append("查证邮件所述时间、地点、项目名称等具体细节是否可独立佐证")
        targets = targets[:8]
        return targets
