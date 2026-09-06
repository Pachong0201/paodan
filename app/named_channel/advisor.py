"""V3 具名渠道推荐编排器：输入 V1 特征 + V2 推荐，输出具名渠道建议。

链路：rule/llm/score/release_recommendation
→ 特征 + location 抽取
→ NamedChannelEngine（NC 规则 → 候选）
→ 安全约束（匿名/敏感/角色硬规则）
→ LLM 排序/剔除/解释（仅候选池；无 LLM 时模板排序）
→ NamedChannelRecommendation
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..config import CONFIG_DIR, LLM_API_KEY, LLM_BASE_URL, LLM_ENABLED, LLM_MODE, \
    LLM_MODEL, LLM_TIMEOUT
from ..llm.client import LLMClient, LLMError
from .candidate_engine import NamedChannelEngine, detect_locations
from .entity_loader import ChannelEntityLoader
from .models import NamedChannelHit, NamedChannelRecommendation, OUTPUT_GROUPS

logger = logging.getLogger(__name__)

# 安全硬约束：这些实体在对应场景绝对 avoid（规则与 LLM 都不可突破）
# 高敏感(机密/匿名文件)场景禁用的公开放大渠道
_SENSITIVE_AVOID_IDS = {
    "platform_facebook", "platform_threads", "platform_ptt", "platform_x",
    "platform_youtube", "pundit_wu_tzechia", "pundit_chiu_yi", "actor_hsu_chiaohsin",
}
# 匿名来源场景禁用的公开实名平台
_ANON_AVOID_PLATFORMS = {"platform_facebook", "platform_threads", "platform_x"}
# 高敏感时禁用的政治人物/揭弊放大者
_ANON_AVOID_ACTORS = {"actor_huang_kuochang", "actor_wang_hungwei", "actor_hsu_chiaohsin",
                      "actor_ling_tao", "actor_hou_hanting", "actor_hsieh_lungchieh"}


def _grp_attr(rec: dict, key: str):
    return rec.get(key)


class NamedChannelAdvisor:
    """端到端具名渠道顾问。"""

    def __init__(self, loader: Optional[ChannelEntityLoader] = None,
                 engine: Optional[NamedChannelEngine] = None,
                 mode: Optional[str] = None,
                 allow_llm: bool = True):
        self.loader = loader or ChannelEntityLoader()
        self.engine = engine or NamedChannelEngine(self.loader)
        self.mode = mode or LLM_MODE
        self.allow_llm = allow_llm and LLM_ENABLED
        self.client: Optional[LLMClient] = None
        if self.mode == "api":
            try:
                self.client = LLMClient(LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT)
            except LLMError as e:
                logger.warning("LLM API 初始化失败，回退模板模式: %s", e)
                self.client = None
                self.mode = "template"
        prompt_path = CONFIG_DIR.parent / "prompts" / "named_channel_advisor_prompt.md"
        try:
            self._prompt = prompt_path.read_text(encoding="utf-8")
        except OSError:
            self._prompt = ""
            logger.error("提示词缺失: %s", prompt_path)

    # ------------------------------------------------------------------
    def advise(self, email_id: str, rule=None, llm=None, score=None,
               release_recommendation: Optional[dict] = None,
               doc=None) -> NamedChannelRecommendation:
        """完整链路。release_recommendation 为 V2 输出 dict。"""
        # 特征构建（复用 V2 feature builder 的特征提取能力）
        from ..release_advisor.feature_builder import ReleaseFeatureBuilder
        feat_builder = ReleaseFeatureBuilder()
        feats = feat_builder.build(email_id, rule, llm, score, doc)
        # location
        text_blob = ""
        if doc is not None and doc.combined_text:
            text_blob = doc.combined_text
        elif rule is not None:
            text_blob = rule.normalized or ""
        locations = detect_locations(text_blob)

        rel = release_recommendation or {}
        primary_route = str(rel.get("primary_route") or "") or \
            (feats.priority_level if feats.priority_level in ("S", "A", "B") else "")
        categories = feats.categories
        shapes = feats.evidence_shapes
        feat_dict = feats.signal_dict()

        rec = NamedChannelRecommendation(
            email_id=email_id,
            priority=feats.priority_level,
            final_score=feats.final_score,
            categories=categories,
            primary_route=primary_route,
        )
        if not categories:
            rec.editor_note = "无有效事件类别，跳过具名渠道推荐。"
            return rec
        # V2 语境：C/D 不进（外部由 pipeline 保证，这里兜底）
        if feats.priority_level not in ("S", "A", "B"):
            rec.editor_note = "非 S/A/B 线索，不进行具名渠道推荐。"
            return rec

        cands, rule_hits = self.engine.build_candidates(
            categories, shapes, feat_dict, primary_route=primary_route,
            locations=locations,
            source_requests_anonymity=feats.source_requests_anonymity,
            has_classified=feats.has_classified_material or feats.has_anonymous_documents,
            has_anonymous_docs=feats.has_anonymous_documents)
        rec.rule_hits = rule_hits
        rec.candidate_count = sum(len(v) for k, v in cands.items() if k != "avoid")

        # 安全硬约束（apply before ranking）
        self._apply_safety(feats, cands, primary_route)

        ranked = self.engine.rank_and_slice(cands)

        # 生成理由（rule 模式）
        self._attach_reasons(ranked, feats, categories, shapes, rel, rule_hits)
        rec.avoid_named_channels = self._avoid_list(cands)
        rec.source = "rule"
        rec.llm_status = "template"

        if self.allow_llm and self.client is not None:
            try:
                self._llm_rerank(rec, ranked, feats, rel, text_blob)
                rec.source = "rule+llm"
                rec.llm_status = "ok"
            except Exception as e:  # noqa: BLE001
                logger.warning("具名渠道 LLM 调用失败，回退规则结果: %s", e)
                rec.llm_status = "failed"

        # 组装输出组
        self._fill_output(rec, ranked, cands)
        # 序列
        rec.recommended_sequence = self._sequence(rec)
        if not rec.editor_note:
            rec.editor_note = self._editor_note(feats, rel, rec)
        return rec

    # ------------------------------------------------------------------
    # 安全约束
    # ------------------------------------------------------------------
    def _apply_safety(self, feats, cands: dict, primary_route: str) -> None:
        is_sensitive = bool(feats.has_classified_material or feats.has_anonymous_documents
                            or primary_route == "R6")
        # 匿名来源（明示或材料匿名）都不得让揭弊政治人物/公开社媒先行
        is_anon = bool(feats.source_requests_anonymity or feats.has_anonymous_documents)
        # 从媒体/人物/平台组剔除（敏感/匿名的安全硬约束）
        for group in ("media", "disclosure", "amplifier", "platform", "local"):
            bucket = cands.get(group, {})
            for eid in list(bucket.keys()):
                if is_sensitive and eid in _SENSITIVE_AVOID_IDS:
                    del bucket[eid]
                if is_anon and eid in _ANON_AVOID_PLATFORMS:
                    del bucket[eid]
                if is_anon and eid in _ANON_AVOID_ACTORS:
                    del bucket[eid]
        # avoid 组并入敏感场景固定名单
        if is_sensitive:
            for eid in _SENSITIVE_AVOID_IDS:
                cands["avoid"].setdefault(eid, NamedChannelHit(entity_id=eid))
        if is_anon:
            for eid in _ANON_AVOID_PLATFORMS:
                cands["avoid"].setdefault(eid, NamedChannelHit(entity_id=eid))
        if is_sensitive:
            for eid in _ANON_AVOID_ACTORS:
                cands["avoid"].setdefault(eid, NamedChannelHit(entity_id=eid))
        # 规则级 avoid vs 推荐冲突（非敏感/非匿名场景）：规则明确把某实体放入
        # 推荐组（如 NC07 当事人平台）时，从 avoid 解除，避免规则互相抵消
        if not is_sensitive and not is_anon:
            recommended_ids = set()
            for group in ("media", "disclosure", "amplifier", "platform", "formal", "local"):
                recommended_ids.update(cands.get(group, {}).keys())
            avoid_bucket = cands.get("avoid", {})
            for eid in list(avoid_bucket.keys()):
                if eid in recommended_ids:
                    del avoid_bucket[eid]

    def _avoid_list(self, cands: dict, ranked: Optional[dict] = None) -> List[str]:
        """avoid 名单：若实体实际进入了推荐组则自动解除 avoid（除非安全硬约束）。
        安全硬约束由 _apply_safety 在正式组中已剔除，此处避免组与正式组冲突检查。"""
        avoid = set(cands.get("avoid", {}).keys())
        # 推荐组中存在的实体不 avoid
        if ranked:
            for group, items in ranked.items():
                if group == "avoid":
                    continue
                for h in items:
                    avoid.discard(h.entity_id)
        return sorted(avoid)[:12]

    # ------------------------------------------------------------------
    # 理由与输出组装
    # ------------------------------------------------------------------
    def _attach_reasons(self, ranked: Dict[str, List[NamedChannelHit]], feats,
                        categories, shapes, rel, rule_hits) -> None:
        cat_txt = "、".join(categories[:3]) or "未分类"
        shape_txt = "、".join(shapes[:4]) or "无明显证据形态"
        for group, items in ranked.items():
            for h in items:
                body = self.loader.get(h.entity_id)
                if body is None:
                    continue
                strengths = "、".join((body.get("strengths") or [])[:3])
                if h.entity_type in ("MEDIA", "INTERNAL_NEWSROOM"):
                    reason = (f"线索属{cat_txt}、含{shape_txt}；{h.name}擅长{strengths}，"
                              f"来源保护{body.get('source_protection')}、"
                              f"复杂材料处理能力{body.get('complexity_capacity')}")
                elif h.entity_type in ("POLITICAL_ACTOR", "JOURNALIST_OR_COMMENTATOR"):
                    if group == "amplifier":
                        reason = f"线索需{cat_txt}议题放大与跟进，{h.name}在相关领域具公开传播影响力"
                    else:
                        reason = f"线索属{cat_txt}、涉{shape_txt}；{h.name}擅长{strengths}"
                elif h.entity_type == "FORMAL_AUTHORITY":
                    reason = f"线索涉{cat_txt}且有正式核验/保全需求，宜经{h.name}（系统不代送）"
                elif h.entity_type == "SOCIAL_PLATFORM":
                    reason = f"适合在{h.name}以当事人或具名文件形式发布/放大"
                else:
                    reason = f"适配{cat_txt}议题的{h.name}"
                h.reason = reason

    def _fill_output(self, rec: NamedChannelRecommendation,
                     ranked: Dict[str, List[NamedChannelHit]], cands: dict) -> None:
        map_to = {"media": "recommended_media", "disclosure": "recommended_disclosure_actors",
                  "amplifier": "recommended_amplifiers",
                  "formal": "recommended_formal_channels",
                  "platform": "recommended_platforms", "local": "local_channels"}
        for group, field in map_to.items():
            items = ranked.get(group, [])
            setattr(rec, field, [h.to_dict() for h in items if h.fit_score >= 30])

    def _sequence(self, rec: NamedChannelRecommendation) -> List[dict]:
        seq: List[dict] = []
        step = 0
        for h in rec.recommended_formal_channels[:1]:
            step += 1
            seq.append({"step": step, "channel_group": "FORMAL_REFERRAL",
                        "channel_id": h.get("entity_id") if isinstance(h, dict) else h.entity_id,
                        "name": h.get("name") if isinstance(h, dict) else h.name,
                        "reason": "先完成正式检举以保全证据"})
        for h in rec.recommended_media[:2]:
            step += 1
            seq.append({"step": step, "channel_group": "MEDIA",
                        "channel_id": h.get("entity_id") if isinstance(h, dict) else h.entity_id,
                        "name": h.get("name") if isinstance(h, dict) else h.name,
                        "reason": "经调查核验后首发"})
        for h in rec.recommended_platforms[:1]:
            step += 1
            seq.append({"step": step, "channel_group": "PLATFORM",
                        "channel_id": h.get("entity_id") if isinstance(h, dict) else h.entity_id,
                        "name": h.get("name") if isinstance(h, dict) else h.name,
                        "reason": "当事人意愿渠道"})
        return seq[:6]

    def _editor_note(self, feats, rel, rec: NamedChannelRecommendation) -> str:
        notes = []
        if feats.source_requests_anonymity:
            notes.append("来源要求匿名：所有对接动作须经编辑部匿名处理，禁止公开社媒首发。")
        if feats.has_classified_material or feats.has_anonymous_documents:
            notes.append("涉机密/匿名材料：先编辑部法审与数字取证，再决定是否公开。")
        if rec.rule_hits:
            notes.append("候选由规则 NC" + "、".join(
                r.replace("NC", "") for r in rec.rule_hits[:5]) + " 生成，最终对接前请编辑部人工复核名单。")
        return "；".join(notes)

    # ------------------------------------------------------------------
    # LLM 排序/剔除/解释（仅限候选池）
    # ------------------------------------------------------------------
    def _llm_rerank(self, rec: NamedChannelRecommendation,
                    ranked: Dict[str, List[NamedChannelHit]], feats, rel,
                    text_blob: str) -> None:
        """调用 LLM 在候选池内重排；失败不影响规则结果。"""
        from .schema import validate_llm_output
        pool_lines = []
        for group, items in ranked.items():
            for h in items:
                pool_lines.append(f"[{group}] {h.entity_id} | {h.name} | fit={h.fit_score} "
                                  f"| roles={','.join(h.roles)}")
        feature_lines = [
            f"类别：{'、'.join(feats.categories or [])}",
            f"优先级：{feats.priority_level}  评分：{feats.final_score:.0f}",
            f"证据形态：{'、'.join(feats.evidence_shapes or [])}",
            f"V2主渠道：{rec.primary_route}",
            f"匿名：{feats.source_requests_anonymity} 机密/匿名材料："
            f"{feats.has_classified_material or feats.has_anonymous_documents}",
        ]
        if feats.destruction_risk:
            feature_lines.append("灭证风险：是")
        user = "\n".join([
            "【线索特征】", "\n".join(feature_lines),
            "【候选渠道池】", "\n".join(pool_lines),
            "【历史参照】",
            self._case_ref(feats.categories),
            "请在候选池内排序/剔除/解释，输出 JSON。",
        ])
        try:
            out = self.client.chat_json(self._prompt, user, retries=1)
        except Exception:  # noqa: BLE001
            raise
        ok, cleaned, errors = validate_llm_output(out, set(rec.rule_hits))
        if not ok:
            raise LLMError(f"具名渠道输出校验失败: {errors[:5]}")
        # LLM 只调整顺序与 fit_score 微调与 reason（在候选池内）
        self._apply_llm_ranks(rec, ranked, cleaned)

    def _case_ref(self, categories) -> str:
        cases = self.loader.similar_cases(categories, limit=3)
        if not cases:
            return "（无同类历史案例）"
        lines = []
        for c in cases:
            lines.append(f"- {c.get('title')}：首发={c.get('first_release_source')}"
                         f"({c.get('first_release_type')})，放大={c.get('amplifiers')}，"
                         f"核验={c.get('verification_sources')}")
        return "\n".join(lines)

    def _apply_llm_ranks(self, rec, ranked, cleaned: dict) -> None:
        """把 LLM 给出的顺序应用到各组（仅重排与 reason 覆盖，不新增）。"""
        id_to_hit = {}
        for group, items in ranked.items():
            for h in items:
                id_to_hit[h.entity_id] = (group, h)
        by_group: Dict[str, List[dict]] = {}
        for key in ("recommended_media", "recommended_disclosure_actors",
                    "recommended_amplifiers", "recommended_formal_channels",
                    "recommended_platforms"):
            for item in cleaned.get(key) or []:
                eid = str(item.get("id") or "")
                if eid in id_to_hit:
                    by_group.setdefault(id_to_hit[eid][0], []).append(item)
        group_map = {"recommended_media": "media",
                     "recommended_disclosure_actors": "disclosure",
                     "recommended_amplifiers": "amplifier",
                     "recommended_formal_channels": "formal",
                     "recommended_platforms": "platform"}
        for key, group in group_map.items():
            items = ranked.get(group)
            if not items:
                continue
            llm_order = by_group.get(group) or []
            if llm_order:
                order_ids = [str(x.get("id")) for x in llm_order]
                llm_reasons = {str(x.get("id")): str(x.get("reason") or "")
                               for x in llm_order}
                llm_scores = {str(x.get("id")): float(x.get("fit_score") or 0)
                              for x in llm_order}
                items.sort(key=lambda h: order_ids.index(h.entity_id)
                           if h.entity_id in order_ids else 999)
                for h in items:
                    if llm_reasons.get(h.entity_id):
                        h.reason = llm_reasons[h.entity_id]
                    if llm_scores.get(h.entity_id, 0) > 0:
                        h.fit_score = round(llm_scores[h.entity_id], 1)
        # avoid 补充（LLM 只能从候选池 add avoid）
        for eid in (cleaned.get("avoid_named_channels") or []):
            if eid in id_to_hit:
                g, h = id_to_hit[eid]
                # 从正式组移除并入 avoid
                ranked[g] = [x for x in ranked[g] if x.entity_id != eid]
                rec.avoid_named_channels.append(eid)
        rec.avoid_named_channels = list(dict.fromkeys(rec.avoid_named_channels))[:12]
        if cleaned.get("editor_note"):
            rec.editor_note = str(cleaned["editor_note"])
        if cleaned.get("unlisted_channel_suggestion"):
            rec.unlisted_channel_suggestion = str(cleaned["unlisted_channel_suggestion"])
