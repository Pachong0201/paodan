"""V3 具名渠道候选引擎：NC01-NC30 规则 -> 候选实体 -> fit_score -> 分组排序。

设计：
- 候选只来自 channel_entities.yaml（规则引用 id 或按画像匹配）；
- fit_score 由 route_match/category_match/evidence_match/source_protection/
  complexity/speed/location/risk_penalty 加权；
- 传播能力（reach）≠ 可信度：verification_score 独立计权，verifier 角色
  需要高 verification_score；
- 政治立场不参与匹配；
- 每组输出有数量上限（媒体3/人物3/放大2/正式2/平台2）。
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from ..preprocessing.normalization import to_simplified
from .entity_loader import ChannelEntityLoader
from .models import NamedChannelHit

logger = logging.getLogger(__name__)

# fit_score 权重
W_ROUTE = 22
W_CATEGORY = 20
W_EVIDENCE = 15
W_SOURCE_PROT = 12
W_COMPLEX = 10
W_SPEED = 3
W_LOCATION = 8
W_ROLE = 6
W_CONFIDENCE = 2
W_REACH = 2      # 仅轻微加成，避免传播力主导
RISK_PENALTY_MAX = 25

# 实体 score 键名映射（YAML scores: reach/verification/source_protection/speed/
# complexity/controversy_risk）
SCORE_KEYS = ("reach", "verification", "source_protection", "speed", "complexity",
              "controversy_risk")

# 台湾县市/地点词表（location-aware routing）
TAIWAN_REGIONS = {
    "台北": ["台北", "臺北", "台北市", "臺北市", "北市"], "新北": ["新北", "新北市"],
    "桃园": ["桃园", "桃園", "桃园市", "桃園市"], "台中": ["台中", "臺中", "台中市", "臺中市"],
    "台南": ["台南", "臺南", "台南市", "臺南市", "南市"], "高雄": ["高雄", "高雄市", "高市"],
    "基隆": ["基隆", "基隆市"], "新竹": ["新竹", "新竹市", "新竹县", "新竹縣"],
    "苗栗": ["苗栗", "苗栗县", "苗栗縣"], "彰化": ["彰化", "彰化县", "彰化縣"],
    "南投": ["南投", "南投县", "南投縣"], "云林": ["云林", "雲林", "云林县", "雲林縣"],
    "嘉义": ["嘉义", "嘉義", "嘉义市", "嘉義市", "嘉义县", "嘉義縣"],
    "屏东": ["屏东", "屏東", "屏东县", "屏東縣"], "宜兰": ["宜兰", "宜蘭", "宜兰县", "宜蘭縣"],
    "花莲": ["花莲", "花蓮", "花莲县", "花蓮縣"], "台东": ["台东", "臺東", "台东县", "臺東縣"],
    "澎湖": ["澎湖", "澎湖县", "澎湖縣"], "金门": ["金门", "金門"], "连江": ["连江", "連江"],
}


def detect_locations(text: str) -> List[str]:
    """从文本粗抽县市（供辖区/地方渠道路由）。无则返回空列表。"""
    if not text:
        return []
    t = to_simplified(text)
    found = []
    for region, tokens in TAIWAN_REGIONS.items():
        if any(tok in t for tok in tokens):
            found.append(region)
    # 去重并保持顺序
    return list(dict.fromkeys(found))


class NamedChannelEngine:
    """具名渠道候选生成 + 打分引擎。"""

    def __init__(self, loader: Optional[ChannelEntityLoader] = None):
        self.loader = loader or ChannelEntityLoader()
        self._id_index: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 条件评估（与 V2 相似：categories/required_shapes/conditions）
    # ------------------------------------------------------------------
    def _match_rule(self, rule: dict, categories: List[str],
                    shapes: List[str], feat: dict) -> bool:
        cats = {c for c in categories if c}
        want_cats = {str(c) for c in (rule.get("categories") or [])}
        if want_cats and not cats.intersection(want_cats):
            return False
        want_shapes = {str(s).upper() for s in (rule.get("required_shapes") or [])}
        if want_shapes and not want_shapes.intersection({s.upper() for s in shapes}):
            return False
        conds = rule.get("conditions") or {}
        for k, want in conds.items():
            got = feat.get(k)
            if got is None:
                if k not in {"has_first_person_testimony", "source_requests_anonymity",
                             "has_power_action", "destruction_risk",
                             "requires_complex_explanation", "has_money_flow",
                             "retaliation_risk", "known_old_case",
                             "contains_new_information", "has_anonymous_documents",
                             "has_classified_material", "public_interest_established",
                             "visually_clear"}:
                    return False
                got = False
            if bool(got) != bool(want):
                return False
        return True

    # ------------------------------------------------------------------
    # 候选生成：规则引用 + 画像补全
    # ------------------------------------------------------------------
    def build_candidates(self, categories: List[str], shapes: List[str],
                         feat: dict, primary_route: str = "",
                         locations: Optional[List[str]] = None,
                         source_requests_anonymity: bool = False,
                         has_classified: bool = False,
                         has_anonymous_docs: bool = False) -> Tuple[dict, List[str]]:
        """返回 (candidates_by_group, rule_hits)。"""
        locations = locations or []
        cands: Dict[str, Dict[str, NamedChannelHit]] = {
            "media": {}, "disclosure": {}, "amplifier": {}, "formal": {},
            "platform": {}, "local": {}, "avoid": {},
        }
        hits: List[str] = []
        seen_media: Dict[str, NamedChannelHit] = cands["media"]

        for rid in sorted(self.loader.rules.keys()):
            rule = self.loader.rules[rid]
            if not self._match_rule(rule, categories, shapes, feat):
                continue
            hits.append(rid)
            if rule.get("suppress"):
                continue
            # 按组收集
            for eid in rule.get("media") or []:
                self._add_candidate(cands, "media", eid, rule, categories, shapes,
                                    primary_route, locations)
            for eid in rule.get("disclosure_actors") or []:
                self._add_candidate(cands, "disclosure", eid, rule, categories, shapes,
                                    primary_route, locations)
            for eid in rule.get("amplifiers") or []:
                self._add_candidate(cands, "amplifier", eid, rule, categories, shapes,
                                    primary_route, locations)
            for eid in rule.get("formal_channels") or []:
                self._add_candidate(cands, "formal", eid, rule, categories, shapes,
                                    primary_route, locations)
            for eid in rule.get("platforms") or []:
                self._add_candidate(cands, "platform", eid, rule, categories, shapes,
                                    primary_route, locations)
            for eid in rule.get("local_channels") or []:
                self._add_candidate(cands, "local", eid, rule, categories, shapes,
                                    primary_route, locations)
            for eid in rule.get("avoid") or []:
                cands["avoid"].setdefault(eid, NamedChannelHit(entity_id=eid))
            # location_additions：地方场景追加
            for la in rule.get("location_additions") or []:
                loc = str(la.get("location") or "")
                if loc and loc in locations:
                    for eid in la.get("actors") or []:
                        self._add_candidate(cands, "disclosure", eid, rule,
                                            categories, shapes, primary_route,
                                            locations)
                    for eid in la.get("local_channels") or []:
                        self._add_candidate(cands, "local", eid, rule,
                                            categories, shapes, primary_route,
                                            locations)
        # NC 规则未命中时的画像兜底：逐组检查，组空才对该组按类别/证据形态匹配
        # active 实体，保证有 V2 路线的线索每组都有候选。
        for _group in ("media", "disclosure", "formal", "platform"):
            if not cands[_group]:
                self._fallback_group(cands, _group, categories, shapes,
                                     primary_route, locations,
                                     source_requests_anonymity, has_classified)
        return cands, hits

    def _fallback_group(self, cands: dict, group: str, categories, shapes,
                        primary_route: str, locations: List[str],
                        source_requests_anonymity: bool,
                        has_classified: bool) -> None:
        """组级画像兜底。"""
        type_ok = {"media": "MEDIA", "disclosure": "POLITICAL_ACTOR",
                   "formal": "FORMAL_AUTHORITY"}.get(group)
        if group == "disclosure" and cands["disclosure"]:
            # disclosure 为空时可补媒体人（JOURNALIST_OR_COMMENTATOR）
            type_ok = "POLITICAL_ACTOR"
        if group == "platform":
            type_ok = None  # 平台仅在规则给出时推荐，不做兜底避免噪音
            return
        if source_requests_anonymity and group == "disclosure":
            return  # 匿名场景不给揭弊人物兜底
        if has_classified and group in ("disclosure", "platform"):
            return
        cats = {c for c in categories if c}
        shapes_set = {s.upper() for s in shapes}
        scored = []
        for body in self.loader.entities.values():
            if body.get("status") != "active":
                continue
            if type_ok and body.get("entity_type") != type_ok:
                continue
            strong_cats = {str(c) for c in (body.get("strong_categories") or [])}
            cat_hit = len(cats & strong_cats)
            strong_shapes = {str(s).upper() for s in (body.get("strong_evidence_shapes") or [])}
            ev_hit = len(shapes_set & strong_shapes)
            if cat_hit == 0 and ev_hit == 0:
                continue
            score = self.fit_score(body, categories, shapes, primary_route,
                                   locations, None)
            scored.append((cat_hit * 1000 + ev_hit * 100 + score, body))
        scored.sort(key=lambda kv: -kv[0])
        for _, body in scored[:3]:
            eid = str(body.get("id") or "")
            if eid not in cands[group]:
                cands[group][eid] = NamedChannelHit(
                    entity_id=eid, name=str(body.get("name") or eid),
                    entity_type=str(body.get("entity_type") or ""),
                    channel_group=group,
                    fit_score=round(self.fit_score(body, categories, shapes,
                                                   primary_route, locations, None), 1),
                    roles=[r for r in (body.get("roles") or [])
                           if r in self._group_roles(group)],
                    rule_ids=["FALLBACK"], status="active")

    def _add_candidate(self, cands: dict, group: str, eid: str, rule: dict,
                       categories: List[str], shapes: List[str],
                       primary_route: str, locations: List[str]) -> None:
        body = self.loader.get(eid)
        if body is None:
            logger.warning("候选实体不存在: %s", eid)
            return
        if body.get("status") != "active":
            return  # inactive/historical/unknown 不进候选
        # 规则角色（实体角色子集）
        rule_roles = [str(r) for r in (rule.get("media_roles") or rule.get("actor_roles")
                                       or rule.get("amplifier_roles") or [])]
        if not rule_roles:
            rule_roles = list(body.get("roles") or [])
        roles = [r for r in rule_roles if r in self._group_roles(group)]
        hit = cands[group].get(eid)
        score = self.fit_score(body, categories, shapes, primary_route,
                               locations, rule)
        if hit is None:
            cands[group][eid] = NamedChannelHit(
                entity_id=eid, name=str(body.get("name") or eid),
                entity_type=str(body.get("entity_type") or ""),
                channel_group=group, fit_score=score, roles=roles,
                rule_ids=[str(rule.get("id") or "")],
                status=str(body.get("status") or "active"))
        else:
            hit.rule_ids.append(str(rule.get("id") or ""))
            hit.fit_score = max(hit.fit_score, score)
            if not hit.roles and roles:
                hit.roles = roles

    # ------------------------------------------------------------------
    # fit_score
    # ------------------------------------------------------------------
    def fit_score(self, body: dict, categories: List[str], shapes: List[str],
                  primary_route: str, locations: List[str],
                  rule: Optional[dict] = None) -> float:
        scores = body.get("scores") or {}
        sc = {k: _num(scores.get(k), 50) for k in SCORE_KEYS}
        route_types = {str(r) for r in (body.get("route_types") or [])}
        etype = str(body.get("entity_type") or "")
        # route_match：正式机关对 R5 严格；媒体/人物按内容承载形式(R2/R3/R4)宽松适配
        if etype == "FORMAL_AUTHORITY":
            route_match = 100.0 if ("R5" in route_types or not primary_route) else 20.0
        else:
            if not primary_route or primary_route in route_types:
                route_match = 100.0
            elif primary_route == "R5":
                # 检举优先场景：媒体/人物作为后续跟进渠道，R2/R3/R4 均可
                route_match = 95.0 if route_types.intersection({"R2", "R3", "R4"}) else 50.0
            elif primary_route == "R6":
                # 高敏感核验场景：只有具备 SOURCE_PROTECTION/VERIFIER 角色的实体适配
                roles = set(body.get("roles") or [])
                route_match = 100.0 if roles.intersection(
                    {"SOURCE_PROTECTION", "VERIFIER", "FORMAL_REFERRAL"}) else 30.0
            elif route_types.intersection({"R2", "R3", "R4"}):
                route_match = 85.0
            else:
                route_match = 30.0
        # category_match
        strong_cats = {str(c) for c in (body.get("strong_categories") or [])}
        cats = {c for c in categories if c}
        cat_hit = len(cats & strong_cats)
        cat_match = 100.0 if cat_hit >= 2 else (75.0 if cat_hit == 1 else 15.0)
        # evidence_match
        strong_shapes = {str(s).upper() for s in (body.get("strong_evidence_shapes") or [])}
        shapes_set = {s.upper() for s in shapes}
        ev_hit = len(shapes_set & strong_shapes)
        ev_match = 100.0 if ev_hit >= 2 else (70.0 if ev_hit == 1 else 20.0)
        # source_protection
        sp_level = str(body.get("source_protection") or "medium")
        sp_score = {"very_high": 100, "high": 85, "medium": 60, "low": 30,
                    "none": 0}.get(sp_level, 60)
        # complexity
        cp_level = str(body.get("complexity_capacity") or "medium")
        cp_score = {"very_high": 100, "high": 85, "medium": 60, "low": 30}.get(cp_level, 60)
        # speed
        spd_level = str(body.get("speed") or "medium")
        spd_score = {"very_high": 100, "high": 85, "medium": 60, "low": 30}.get(spd_level, 60)
        # location
        loc_score = 100.0
        local_scope = {str(x) for x in (body.get("location_scope") or [])}
        if local_scope and locations:
            loc_score = 100.0 if any(l in locations for l in local_scope) else 30.0
        # role 基准加成
        roles = set(body.get("roles") or [])
        need_route = primary_route
        role_score = 100.0
        if need_route == "R5" and "FORMAL_REFERRAL" not in roles and \
                body.get("entity_type") == "FORMAL_AUTHORITY":
            role_score = 100.0
        # 规则锚定加成
        rule_bonus = 0.0
        if rule is not None and (body.get("id") in
                                 ((rule.get("media") or []) + (rule.get("disclosure_actors") or []) +
                                  (rule.get("formal_channels") or []) +
                                  (rule.get("platforms") or []) +
                                  (rule.get("local_channels") or []))):
            rule_bonus = 6.0
        raw = (W_ROUTE * route_match + W_CATEGORY * cat_match +
               W_EVIDENCE * ev_match + W_SOURCE_PROT * sp_score +
               W_COMPLEX * cp_score + W_SPEED * spd_score + W_LOCATION * loc_score +
               W_ROLE * role_score + W_CONFIDENCE * sc.get("complexity", 50) +
               W_REACH * sc.get("reach", 50)) / 100.0 + rule_bonus
        # risk penalty（争议/核验风险）：媒体争议罚轻（首发责任由编辑部承担），
        # 政治人物/媒体人/平台罚重（放大者需更高核验门槛）
        controversy = sc.get("controversy_risk", 50)
        if etype == "MEDIA":
            penalty = controversy * 0.06
        elif etype == "SOCIAL_PLATFORM":
            penalty = controversy * 0.10
        else:
            penalty = controversy * 0.18
        raw = max(0.0, raw - penalty)
        return round(min(100.0, raw), 1)

    @staticmethod
    def _group_roles(group: str) -> Set[str]:
        mapping = {
            "media": {"FIRST_RELEASE", "INVESTIGATIVE", "SOURCE_PROTECTION"},
            "disclosure": {"DISCLOSURE", "AMPLIFIER", "DATA_ANALYSIS"},
            "amplifier": {"AMPLIFIER", "DISCLOSURE"},
            "formal": {"FORMAL_REFERRAL", "VERIFIER"},
            "platform": {"FIRST_RELEASE", "AMPLIFIER"},
            "local": {"LOCAL_NETWORK", "DISCLOSURE"},
        }
        return mapping.get(group, set())

    # ------------------------------------------------------------------
    # 分组、排序、截断
    # ------------------------------------------------------------------
    def rank_and_slice(self, cands: dict) -> Dict[str, List[NamedChannelHit]]:
        limits = self.loader.limits
        out: Dict[str, List[NamedChannelHit]] = {}
        groups = [("media", limits["media_top_n"]),
                  ("disclosure", limits["actor_top_n"]),
                  ("amplifier", limits["amplifier_top_n"]),
                  ("formal", limits["formal_top_n"]),
                  ("platform", limits["platform_top_n"]),
                  ("local", 2)]
        for group, n in groups:
            items = list(cands.get(group, {}).values())
            items.sort(key=lambda h: -h.fit_score)
            for i, h in enumerate(items[:n], 1):
                h.rank = i
            out[group] = items[:n]
        return out


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
