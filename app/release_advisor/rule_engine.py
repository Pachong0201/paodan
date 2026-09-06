"""渠道规则引擎：按 config/release_route_rules.yaml 的 RR01-RR12 规则评估
ReleaseDecisionFeatures，输出结构化推荐建议 + 风险矩阵 + 核验清单。

规则引擎只做确定性判定；与 LLM 结果的融合在 scorer.py。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import yaml

from ..config import CONFIG_DIR
from .models import (ReleaseDecisionFeatures, ReleaseRecommendation,
                     VALID_EVIDENCE_SHAPES, VALID_FORMAL_TYPES)
from .schema import ROUTE_NAMES

logger = logging.getLogger(__name__)

# 规则依赖的布尔特征（白名单，防误用字符串字段）
_BOOL_KEYS = {
    "has_original_evidence", "has_money_flow", "has_power_action",
    "has_first_person_testimony", "has_sensitive_personal_data",
    "has_classified_material", "has_anonymous_documents",
    "requires_complex_explanation", "destruction_risk", "retaliation_risk",
    "source_requests_anonymity", "known_old_case", "contains_new_information",
    "public_interest_established", "visually_clear", "quickly_verifiable",
    "sensitive_material", "has_organized_crime_signal",
}

# avoid 降权：recommend 中出现的渠道需要优先权重差
AVOID_PENALTY = 0.45
PRIMARY_CONF_BASE = 0.62
SECONDARY_CONF = 0.40


def _canon(v: str) -> str:
    return (v or "").strip().upper()


class ReleaseRouteRuleEngine:
    """确定性渠道规则引擎。规则从 YAML 加载，不硬编码 RR 逻辑。"""

    def __init__(self, routes_file: Optional[Path] = None,
                 rules_file: Optional[Path] = None):
        self.routes_file = Path(routes_file) if routes_file else CONFIG_DIR / "release_routes.yaml"
        self.rules_file = Path(rules_file) if rules_file else CONFIG_DIR / "release_route_rules.yaml"
        self._routes: Dict[str, dict] = {}
        self._route_names: Dict[str, str] = {}
        self._rules: Dict[str, dict] = {}
        self._formal_labels: Dict[str, str] = {}
        self._category_formal: Dict[str, str] = {}
        self._criminal_cats: Set[str] = set()
        self._formal_strong: Set[str] = set()
        self._risk_labels: Dict[str, str] = {}
        self._shape_meta: Dict[str, dict] = {}
        self.min_score: Optional[float] = None   # 进入渠道判断的分数阈值
        self.load()

    # ------------------------------------------------------------------
    def load(self) -> None:
        with open(self.routes_file, "r", encoding="utf-8") as f:
            routes_data = yaml.safe_load(f) or {}
        with open(self.rules_file, "r", encoding="utf-8") as f:
            rules_data = yaml.safe_load(f) or {}
        for r in (routes_data.get("routes") or {}).values():
            if isinstance(r, dict) and r.get("id"):
                self._routes[r["id"]] = r
        self._route_names = {k: str(v) for k, v in
                             (routes_data.get("route_names") or {}).items()}
        for k, v in (routes_data.get("routes") or {}).items():
            if isinstance(v, dict) and v.get("name"):
                self._route_names.setdefault(k, v["name"])
        for rid, rule in (rules_data.get("rules") or {}).items():
            if isinstance(rule, dict) and rule.get("id"):
                self._rules[rid] = rule
        self._formal_labels = {str(k): str(v) for k, v in
                               (rules_data.get("formal_referral_types") or {}).items()}
        self._category_formal = {str(k): str(v) for k, v in
                                 (rules_data.get("category_formal_default") or {}).items()}
        self._criminal_cats = {str(c) for c in
                               (rules_data.get("criminal_priority_categories") or [])}
        self._formal_strong = {str(k) for k in
                               (rules_data.get("formal_strong_features") or [])}
        self._risk_labels = {str(k): str(v) for k, v in
                             (rules_data.get("release_risks") or {}).items()}
        self._shape_meta = {str(k): dict(v or {}) for k, v in
                            (rules_data.get("evidence_shapes") or {}).items()}
        try:
            self.min_score = float(rules_data.get("release_advisor_min_score") or 60.0)
        except (TypeError, ValueError):
            self.min_score = 60.0

    # ---------- 只读访问 ----------
    @property
    def routes(self) -> Dict[str, dict]:
        return self._routes

    @property
    def rule_ids(self) -> List[str]:
        return sorted(self._rules.keys())

    @property
    def category_formal_default(self) -> Dict[str, str]:
        return self._category_formal

    def route_name(self, rid: str) -> str:
        return self._route_names.get(rid, rid)

    def formal_label(self, code: str) -> str:
        return self._formal_labels.get(code, code)

    def risk_label(self, code: str) -> str:
        return self._risk_labels.get(code, code)

    def shape_meta(self, shape: str) -> dict:
        return self._shape_meta.get(shape, {})

    # ------------------------------------------------------------------
    # 条件评估
    # ------------------------------------------------------------------
    def _eval_conditions(self, rule: dict, f: ReleaseDecisionFeatures,
                         cats: Set[str]) -> Tuple[bool, str]:
        """逐条评估 rule.conditions / conditions_any / categories / required_shapes."""
        cats_ok = True
        want_cats = {str(c) for c in (rule.get("categories") or [])}
        if want_cats and not cats.intersection(want_cats):
            cats_ok = False
        shapes_ok = True
        want_shapes = {str(s).upper() for s in (rule.get("required_shapes") or [])}
        if want_shapes and not want_shapes.intersection(
                {s.upper() for s in f.evidence_shapes}):
            shapes_ok = False
        if not cats_ok or not shapes_ok:
            return False, "类别/证据形态不匹配"
        conds = rule.get("conditions") or {}
        for key, want in conds.items():
            if key not in _BOOL_KEYS:
                continue
            if bool(f.signal_dict().get(key, False)) != bool(want):
                return False, f"条件 {key}={want} 不满足"
        # categories 匹配且 required_shapes 满足后再看 conditions_any（任一命中）
        conds_any = rule.get("conditions_any") or []
        if conds_any:
            hit_any = False
            for group in conds_any:
                if isinstance(group, dict) and all(
                        bool(f.signal_dict().get(k, False)) == bool(v)
                        for k, v in group.items() if k in _BOOL_KEYS):
                    hit_any = True
                    break
            if not hit_any:
                return False, "conditions_any 全不满足"
        return True, ""

    def _formal_for(self, f: ReleaseDecisionFeatures) -> Tuple[bool, List[str]]:
        """正式检举建议：类别默认 + 强特征（金流/灭证/机密）+ RR05 学伦。

        灭证/串证/报复风险只有在刑事类别（A03/A10/A13/A15/A17）语境才升级到
        检察机关；职场/性骚/吃案类走劳动/性平与内部申诉，避免过度刑事化。
        """
        cats = {c for c in f.categories if c and c != "GLOBAL"}
        types: List[str] = []
        criminal_hit = bool(cats.intersection(self._criminal_cats))
        strong = any(f.signal_dict().get(k, False) for k in self._formal_strong)
        # 刑事/金流/灭证优先
        if f.has_money_flow or strong or criminal_hit:
            if (f.has_money_flow or strong) and criminal_hit:
                types.append("PROSECUTOR")
            elif criminal_hit:
                types.append("PROSECUTOR")
            elif f.has_classified_material:
                types.append("INVESTIGATION_BUREAU")
        if (f.destruction_risk or f.retaliation_risk) and criminal_hit:
            if "PROSECUTOR" not in types:
                types.append("PROSECUTOR")
        for c in sorted(cats):
            default = self._category_formal.get(c)
            if default and default not in types:
                types.append(default)
        if "A01" in cats and "ACADEMIC_ETHICS" not in types:
            types.append("ACADEMIC_ETHICS")
        if "A11" in cats and "LABOR_OR_EQUALITY_AUTHORITY" not in types:
            types.append("LABOR_OR_EQUALITY_AUTHORITY")
        # 仅内部申诉类（A12 单独）不强推机关
        recommend = bool(types) and not (cats == {"A12"} and len(types) == 1
                                         and types[0] == "INTERNAL_COMPLAINT" and
                                         not f.has_first_person_testimony)
        return recommend, (types[:3] or ["NONE"])

    def _risks_for(self, f: ReleaseDecisionFeatures,
                   primary: str, avoid: Set[str]) -> List[str]:
        """风险矩阵：渠道固有风险 + 特征派生风险。"""
        risks: List[str] = []
        meta = self._shape_meta
        # 证据真实性与来源风险
        any_unverified = any(s in f.evidence_shapes for s in
                             ("CHAT_RECORD", "AUDIO", "VIDEO", "PHOTO", "ANONYMOUS_DOCUMENT",
                              "BANK_RECORD", "INTERNAL_DOCUMENT", "DATABASE_RECORD"))
        if any_unverified or f.has_anonymous_documents:
            risks.append("EVIDENCE_AUTHENTICITY")
        if f.has_first_person_testimony or f.source_requests_anonymity or \
                "SOURCE_EXPOSURE" in self._risk_labels:
            if f.source_requests_anonymity or f.has_first_person_testimony:
                risks.append("SOURCE_EXPOSURE")
        if f.destruction_risk:
            risks.append("DESTRUCTION_OF_EVIDENCE")
        if f.retaliation_risk or "WITNESS_COLLUSION" in self._risk_labels:
            risks.append("WITNESS_COLLUSION")
            risks.append("RETALIATION")
        if f.has_classified_material or "CLASSIFIED_DOCUMENT" in f.evidence_shapes:
            risks.append("CLASSIFIED_INFORMATION")
        if f.has_sensitive_personal_data:
            risks.append("PRIVACY")
        # 渠道固有风险
        route_meta = self._routes.get(primary, {})
        for r in (route_meta.get("risks") or []):
            if r not in risks:
                risks.append(str(r))
        if f.known_old_case and not f.contains_new_information:
            risks.append("MISLEADING_OLD_NEWS")
        # 隐私/诽谤/上下文：聊天与私德材料天然高风险
        if "CHAT_RECORD" in f.evidence_shapes or any(c in f.categories for c in ("A11", "A14")):
            for r in ("CONTEXT_LOSS", "PRIVACY", "DEFAMATION"):
                if r not in risks:
                    risks.append(r)
        # 归一去重 + 截断
        return list(dict.fromkeys(risks))[:6]

    # ------------------------------------------------------------------
    def recommend(self, f: ReleaseDecisionFeatures) -> ReleaseRecommendation:
        """对一条线索给出规则层推荐。返回的推荐对象含 rule_hits 溯源。"""
        cats = {c for c in f.categories if c and c != "GLOBAL"}
        rec = ReleaseRecommendation(email_id=f.email_id, source="rule")
        # 入口门禁：S/A/B + final_score>=60（阈值可配于 yaml；engine 由调用方控制）
        # 旧案无新增材料 -> 不建议任何公开首发
        if f.known_old_case and not f.contains_new_information:
            rec.primary_route = ""
            rec.secondary_routes = []
            rec.avoid_routes = ["R1", "R2", "R3", "R4"]
            rec.route_confidence = 0.15
            rec.release_risks = ["MISLEADING_OLD_NEWS"]
            rec.reason = "该材料属已公开旧案且未提供新增信息，不建议作为首发线索公开。"
            rec.prepublication_verification_required = True
            rec.verification_before_release = ["确认是否存在此前未曝光的新文件、新人物或新金额"]
            return rec

        hit_rules: List[str] = []
        votes: Dict[str, float] = {}
        avoid_votes: Dict[str, float] = {}
        secondaries: Dict[str, float] = {}
        formal_extra: List[str] = []

        for rid in sorted(self._rules.keys()):
            rule = self._rules[rid]
            ok, why = self._eval_conditions(rule, f, cats)
            if not ok:
                continue
            primary = _canon(rule.get("primary") or "")
            secondary = [_canon(x) for x in (rule.get("secondary") or []) if x]
            avoid = [_canon(x) for x in (rule.get("avoid") or []) if x]
            weight = float(rule.get("weight") or 1.0)
            if rid == "RR12":
                # RR12 只提升 R4，不单独定主渠道
                for r in (rule.get("boost") or {}):
                    votes[r] = votes.get(r, 0.0) + float(rule["boost"][r])
                hit_rules.append(rid)
                continue
            if primary and primary in {"R1", "R2", "R3", "R4", "R5", "R6"}:
                votes[primary] = votes.get(primary, 0.0) + weight
            for r in secondary:
                secondaries[r] = max(secondaries.get(r, 0.0), weight * 0.8)
            for r in avoid:
                avoid_votes[r] = max(avoid_votes.get(r, 0.0), 1.0)
            if rule.get("formal_referral"):
                formal_extra.append(_canon(rule["formal_referral"]))
            hit_rules.append(rid)

        if not votes and not hit_rules:
            # 无规则命中 -> 返回空主渠道（由 scorer/LLM 决定），avoid 依特征
            rec.rule_hits = []
            rec.primary_route = ""
            rec.secondary_routes = sorted(secondaries.keys())
            rec.avoid_routes = self._feature_avoid(f)
            rec.route_confidence = 0.3
            rec.release_risks = self._risks_for(f, "", set(rec.avoid_routes))
            fr, ftypes = self._formal_for(f)
            rec.formal_referral_recommended = fr
            rec.formal_referral_type = ftypes
            rec.prepublication_verification_required = self._verify_required(f, "")
            rec.verification_before_release = self._make_verification_list(f, "")
            rec.reason = "未命中确定性渠道规则，需结合语义进一步研判。"
            if rec.formal_referral_recommended:
                rec.reason += "但存在正式检举需求，建议先向" + "、".join(
                    self.formal_label(t) for t in ftypes if t != "NONE") + "提交。"
            return rec

        # 冲突消解：avoid 权重折扣；secondary 且被 avoid -> 从 secondary 剔除
        for r, w in avoid_votes.items():
            if r in votes:
                votes[r] = votes[r] * (1.0 - AVOID_PENALTY)
        for r in list(secondaries.keys()):
            if r in avoid_votes:
                del secondaries[r]
        # 机密/匿名材料场景：R6 语义绝对优先，任何 R6 规则命中即 +0.8 加成
        if (f.has_classified_material or f.has_anonymous_documents) and "R6" in votes:
            votes["R6"] = votes["R6"] + 0.8

        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        primary = ranked[0][0] if ranked else ""
        # RR08 特有：A14 私德无违法 -> avoid R5；有违法时 R5 可能成为 secondary
        avoid_set = {r for r, w in avoid_votes.items() if w >= 0.5}
        if primary and primary in avoid_set and len(ranked) > 1:
            primary = ranked[1][0]

        secondary_out: List[str] = []
        for r, w in sorted(secondaries.items(), key=lambda kv: -kv[1]):
            if r != primary and r not in avoid_set:
                secondary_out.append(r)
        # primary 独占的二次渠道补充：投票次高者可作 secondary
        for r, w in ranked[1:]:
            if r not in secondary_out and r not in avoid_set and r != primary:
                secondary_out.append(r)
                break
        secondary_out = list(dict.fromkeys(secondary_out))[:3]
        avoid_out = sorted(avoid_set)
        # 安全互斥（硬约束）：R6 与 R1/R4 不并存于 primary/secondary 正推
        if primary == "R6":
            secondary_out = [r for r in secondary_out if r not in ("R1", "R4")]
            if "R5" not in secondary_out and f.has_classified_material:
                secondary_out.append("R5")
        # primary 为空兜底：取未 avoid 的最高票
        if not primary and ranked:
            for r, _w in ranked:
                if r not in avoid_set:
                    primary = r
                    break

        rec.rule_hits = hit_rules
        rec.primary_route = primary
        rec.secondary_routes = secondary_out
        rec.avoid_routes = sorted(set(avoid_out) | set(self._feature_avoid(f)))
        rec.route_confidence = round(
            min(0.95, PRIMARY_CONF_BASE + 0.09 * max(1, len(hit_rules)) +
                (0.05 if f.destruction_risk else 0.0)), 2)
        rec.release_risks = self._risks_for(f, primary, set(rec.avoid_routes))
        # 正式检举
        fr, ftypes = self._formal_for(f)
        rec.formal_referral_recommended = fr
        rec.formal_referral_type = list(dict.fromkeys(formal_extra + ftypes))[:4]
        rec.prepublication_verification_required = self._verify_required(f, primary)
        rec.verification_before_release = self._make_verification_list(f, primary)
        rec.primary_route_name = ROUTE_NAMES.get(primary, "")
        rec.reason = self._reason_text(f, primary, hit_rules, rec)
        rec.recommended_release_sequence = self._sequence(f, rec)
        rec.headline_angle = ""
        rec.editor_note = ""
        rec.llm_status = ""
        return rec

    # ------------------------------------------------------------------
    def _feature_avoid(self, f: ReleaseDecisionFeatures) -> List[str]:
        """特征级 avoid：不论规则是否命中都应避免的渠道。"""
        avoid: Set[str] = set()
        # 高敏感（机密/匿名/真实未核）：禁社交直发与原件展示
        if f.has_classified_material or f.has_anonymous_documents:
            avoid.update({"R1", "R4"})
        # 匿名/身份保护：禁社交
        if f.source_requests_anonymity and not (
                f.has_first_person_testimony and not f.source_requests_anonymity):
            avoid.add("R1")
        # 金流+灭证/串证风险：禁社交首发
        if f.has_money_flow and (f.destruction_risk or f.retaliation_risk):
            avoid.add("R1")
        return sorted(avoid)

    def _verify_required(self, f: ReleaseDecisionFeatures, primary: str) -> bool:
        if f.has_classified_material or f.has_anonymous_documents:
            return True
        if primary in ("R6",):
            return True
        if f.has_money_flow and f.evidence_stage in ("E1", "EX"):
            return True
        if any(s in f.evidence_shapes for s in ("CHAT_RECORD", "AUDIO", "VIDEO", "PHOTO")):
            return True
        return False

    def _make_verification_list(self, f: ReleaseDecisionFeatures,
                                primary: str) -> List[str]:
        """发布前核验清单：按证据形态生成。"""
        out: List[str] = []
        shapes = {s.upper() for s in f.evidence_shapes}
        cats = set(f.categories)
        if f.has_anonymous_documents or "ANONYMOUS_DOCUMENT" in shapes:
            out.append("确认匿名文件来源链与真实作者，检查 metadata 与数字指纹")
        if f.has_classified_material or "CLASSIFIED_DOCUMENT" in shapes:
            out.append("启动编辑部安全审阅与法律审查，评估保密法规风险")
        if "BANK_RECORD" in shapes or f.has_money_flow:
            out.append("核验银行流水/汇款是否为原件，确认账户实际控制人与交易对手")
        if "CHAT_RECORD" in shapes:
            out.append("核验聊天记录是否为原始导出，时间戳与上下文是否完整")
        if "ACADEMIC_DOCUMENT" in shapes:
            out.append("确认论文比对报告原始出处与检测方法，排除伪造比对")
        if "PROCUREMENT_FILE" in shapes:
            out.append("比对招标文件版本与公开招标公告，核验时间线")
        if "CONTRACT" in shapes:
            out.append("核验合同签章主体与内容版本，向相对方函证")
        if "PHOTO" in shapes or "VIDEO" in shapes:
            out.append("对照片/视频做来源与完整性取证（metadata/原始檔）")
        if f.source_requests_anonymity or f.has_first_person_testimony:
            out.append("确认当事人身份保护安排与知情同意")
        if any(c in cats for c in ("A03", "A10", "A15")) and f.has_money_flow:
            out.append("评估司法保全优先性：是否先正式检举以固定证据")
        if f.known_old_case:
            out.append("比对既有公开报道，确认新增材料边界")
        if primary == "R6":
            out.append("数字取证：确认文件无变造、来源非黑客取得")
        if not out:
            out.append("向爆料人索取原始档并核验基础事实（时间/地点/主体）")
        return list(dict.fromkeys(out))[:8]

    def _reason_text(self, f: ReleaseDecisionFeatures, primary: str,
                     hit_rules: List[str], rec: ReleaseRecommendation) -> str:
        parts: List[str] = []
        cat_txt = "、".join(f.categories[:3]) or "未分类"
        parts.append(f"线索类别{cat_txt}")
        if f.has_money_flow:
            parts.append("含金流证据")
        if f.destruction_risk:
            parts.append("存在灭证风险")
        if f.retaliation_risk:
            parts.append("存在串证/报复风险")
        if f.source_requests_anonymity:
            parts.append("来源要求匿名")
        if f.has_classified_material or f.has_anonymous_documents:
            parts.append("材料高敏感需先核真")
        if f.has_first_person_testimony:
            parts.append("属第一人称叙述")
        if f.known_old_case:
            parts.append("属旧案且含新增材料")
        route_txt = "；".join(rec.secondary_routes[:2])
        tail = f"。故建议主渠道{primary}"
        if route_txt:
            tail += f"，后备{route_txt}"
        if rec.avoid_routes:
            tail += "，不建议" + "、".join(rec.avoid_routes[:3])
        return "；".join(parts) + tail + "。" + ("（规则命中：" + "/".join(hit_rules[:4]) + "）" if hit_rules else "")

    def _sequence(self, f: ReleaseDecisionFeatures,
                  rec: ReleaseRecommendation) -> List[dict]:
        """多阶段发布序列（Step1 核验 -> Step2 检举 -> Step3 公开）。"""
        seq: List[dict] = []
        step = 1
        if rec.prepublication_verification_required:
            seq.append({"step": step, "action": "EDITORIAL_VERIFICATION", "route": "",
                        "reason": "先完成证据真伪与来源核验"}); step += 1
        if rec.formal_referral_recommended:
            rt = next((t for t in rec.formal_referral_type if t != "NONE"), "NONE")
            seq.append({"step": step, "action": "FORMAL_REFERRAL", "route": rt,
                        "reason": f"先向{self.formal_label(rt)}检举以保全证据" if rt != "NONE"
                        else "启动正式申诉程序"}); step += 1
        if rec.primary_route:
            seq.append({"step": step, "action": "PUBLIC_RELEASE",
                        "route": rec.primary_route,
                        "reason": f"经{rec.primary_route_name or rec.primary_route}渠道公开"})
        for r in rec.secondary_routes:
            step += 1
            seq.append({"step": step, "action": "PUBLIC_RELEASE", "route": r,
                        "reason": f"视反应与核验进度，经{r}跟进公开"})
        return seq[:8]
