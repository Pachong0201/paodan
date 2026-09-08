"""LLM 首发渠道顾问：把规则特征 + 脱敏摘要注入提示词，输出结构化渠道建议。

模式与 V1 LLMScreener 一致：
- LLM_MODE=api 时调用 OpenAI-compatible API；否则走确定性模板（由规则引擎结果生成），
  保证无 LLM 环境端到端可运行；
- 输出经 schema.validate_result 校验，失败重试一次，再失败 llm_status=failed。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from ..config import (CONFIG_DIR, LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODE,
                      LLM_MODEL, LLM_TIMEOUT)
from ..llm.client import LLMClient, LLMError
from ..llm.screener import truncate_for_llm
from ..preprocessing.normalization import mask_sensitive
from . import schema as release_schema
from .feature_builder import ReleaseFeatureBuilder
from .models import ReleaseDecisionFeatures, ReleaseRecommendation
from .rule_engine import ReleaseRouteRuleEngine
from .schema import ROUTE_NAMES

logger = logging.getLogger(__name__)


def _feature_lines(f: ReleaseDecisionFeatures) -> List[str]:
    """把决策特征渲染为 LLM 附录文本."""
    lines = ["【结构化决策特征】"]
    lines.append("类别：" + ("、".join(f.categories) if f.categories else "未分类"))
    lines.append(f"优先级：{f.priority_level}  评分：{f.final_score:.0f}  证据阶段：{f.evidence_stage}")
    lines.append("证据形态：" + ("、".join(f.evidence_shapes) if f.evidence_shapes else "无明确证据形态"))
    flags = []
    for label, key in (("原始证据", "has_original_evidence"), ("金流", "has_money_flow"),
                       ("权力动作", "has_power_action"), ("第一人称叙述", "has_first_person_testimony"),
                       ("敏感个资", "has_sensitive_personal_data"), ("机密材料", "has_classified_material"),
                       ("匿名文件", "has_anonymous_documents"), ("需复杂解释", "requires_complex_explanation"),
                       ("灭证风险", "destruction_risk"), ("报复/串证风险", "retaliation_risk"),
                       ("来源要求匿名", "source_requests_anonymity"), ("公共利益成立", "public_interest_established"),
                       ("可视化可对照", "visually_clear"), ("可快速核验", "quickly_verifiable")):
        if getattr(f, key, False):
            flags.append(label)
    lines.append("特征：" + ("、".join(flags) if flags else "无显著特征"))
    if f.known_old_case:
        lines.append("注意：疑似旧案" + ("且含新增材料" if f.contains_new_information else "且无新增材料"))
    return lines


def _rules_context(rules: ReleaseRouteRuleEngine, f: ReleaseDecisionFeatures,
                   rec: ReleaseRecommendation) -> List[str]:
    lines = ["【确定性规则层建议】"]
    if rec.primary_route:
        lines.append(f"规则主渠道：{rec.primary_route} {rec.primary_route_name or ''}")
    else:
        lines.append("规则主渠道：未定（需语义研判）")
    if rec.secondary_routes:
        lines.append("规则备选：" + "、".join(rec.secondary_routes))
    if rec.avoid_routes:
        lines.append("规则不建议：" + "、".join(rec.avoid_routes))
    if rec.rule_hits:
        lines.append("命中规则：" + "、".join(rec.rule_hits))
    if rec.formal_referral_recommended:
        types = "、".join(rules.formal_label(t) for t in rec.formal_referral_type if t != "NONE")
        lines.append(f"规则建议正式检举：{types}")
    if rec.verification_before_release:
        lines.append("规则核验清单：" + "；".join(rec.verification_before_release[:5]))
    return lines


class ReleaseAdvisor:
    """端到端渠道顾问：feature build -> rule -> LLM -> scorer 融合."""

    def __init__(self, rule_config=None, mode: Optional[str] = None,
                 engine: Optional[ReleaseRouteRuleEngine] = None,
                 builder: Optional[ReleaseFeatureBuilder] = None,
                 allow_llm: bool = True,
                 min_score: Optional[float] = None):
        self.mode = mode or LLM_MODE
        self.allow_llm = allow_llm and LLM_ENABLED
        self.engine = engine or ReleaseRouteRuleEngine()
        # 阈值从 release_route_rules.yaml 读取：release_advisor_min_score
        cfg_min = getattr(self.engine, "min_score", None)
        if min_score is None:
            min_score = float(cfg_min) if cfg_min else 60.0
        self.min_score = float(min_score)
        self.builder = builder or ReleaseFeatureBuilder(rule_config, self.engine._rules)
        self.client: Optional[LLMClient] = None
        if self.mode == "api":
            try:
                self.client = LLMClient(LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT)
            except LLMError as e:
                logger.warning("LLM API 初始化失败，回退模板模式: %s", e)
                self.client = None
                self.mode = "template"
        prompt_path = CONFIG_DIR.parent / "prompts" / "release_route_advisor_prompt.md"
        try:
            self._prompt = prompt_path.read_text(encoding="utf-8")
        except OSError:
            self._prompt = ""
            logger.error("提示词缺失: %s", prompt_path)

    # ------------------------------------------------------------------
    def advise(self, email_id: str, rule=None, llm=None, score=None,
               doc=None, text: str = "", governance_categories=None,
               unified=None) -> ReleaseRecommendation:
        """完整链路：构建特征 -> 规则层 -> LLM -> 融合."""
        f = self.builder.build(email_id, rule, llm, score, doc,
                               governance_categories=governance_categories,
                               unified=unified)
        rec = self.engine.recommend(f)
        if self.allow_llm and self.client is not None:
            try:
                # 外部 LLM 由 _ask_llm 内部构造 SafePayloadBuilder；不得使用原始正文。
                llm_out = self._ask_llm(f, rec, text or "", unified=unified)
                rec = self._merge(f, rec, llm_out)
            except Exception as e:  # noqa: BLE001
                logger.warning("渠道顾问 LLM 调用失败，回退规则结果: %s", e)
                rec.source = "rule"
                rec.llm_status = "failed"
        else:
            # 模板模式：以规则结果为准，补模板文案
            rec.source = "rule"
            rec.llm_status = "template"
            self._finalize_template(rec, f)
        if rec.primary_route and not rec.primary_route_name:
            rec.primary_route_name = ROUTE_NAMES.get(rec.primary_route, "")
        return rec

    # ------------------------------------------------------------------
    def _ask_llm(self, f: ReleaseDecisionFeatures, rec: ReleaseRecommendation,
                 text: str, unified=None) -> dict:
        # External LLM 只能接收 SafeLLMPayload；LOCAL 才允许较完整文本模式。
        is_external = bool(getattr(self.client, "is_external", False))
        if is_external:
            from ..security.payload_builder import SafePayloadBuilder
            policy = getattr(self.client, "policy", None)
            safe = SafePayloadBuilder(policy=policy).build_v2(
                features=f, recommendation=rec, unified=unified)
            out = self.client.chat_safe(self._prompt, safe, retries=1)
        else:
            user = []
            user.extend(_feature_lines(f))
            user.extend(_rules_context(self.engine, f, rec))
            user.append("\n=== 线索材料（脱敏摘要） ===")
            user.append(truncate_for_llm(mask_sensitive(text or f.source_text or ""), 9000))
            user.append("\n请按系统提示词输出 JSON（不要多余文字）。")
            content = "\n".join(user)
            out = self.client.chat_json(self._prompt, content, retries=1)
        ok, cleaned, errors = release_schema.validate_result(out)
        if not ok:
            # 重试一次：External 仍只允许 SafeLLMPayload，绝不回退 raw。
            if is_external:
                out2 = self.client.chat_safe(self._prompt, safe, retries=1)
            else:
                out2 = self.client.chat_json(
                    self._prompt, content + "\n（上次输出不合 schema：" + "；".join(errors[:3]) + "）",
                    retries=1)
            ok, cleaned, errors = release_schema.validate_result(out2)
            if not ok:
                raise LLMError(f"渠道输出校验失败: {errors[:5]}")
        return cleaned

    # ------------------------------------------------------------------
    def _merge(self, f: ReleaseDecisionFeatures, rec: ReleaseRecommendation,
               llm_out: dict) -> ReleaseRecommendation:
        """规则层与 LLM 融合：LLM 作为语义修正，规则 avoid/安全硬约束优先。"""
        from .scorer import merge_recommendations
        return merge_recommendations(rec, llm_out, self.engine)

    def _finalize_template(self, rec: ReleaseRecommendation,
                           f: ReleaseDecisionFeatures) -> None:
        """模板模式补全 reason/editor_note/sequence（不改变渠道判定）。"""
        if not rec.reason:
            rec.reason = "基于规则层证据形态判定（离线模板模式，未接 LLM 语义研判）。"
        if not rec.recommended_release_sequence:
            rec.recommended_release_sequence = self.engine._sequence(f, rec)
