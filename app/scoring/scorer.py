"""FinalScorer：按 scoring_rules.yaml 维度做 0-100 综合评分（LLM 分数不作为自报分）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..models import FinalScore, LLMResult, RuleResult
from .known_news_matcher import KnownNewsMatcher

logger = logging.getLogger(__name__)

PRIORITY_BANDS = [  # (low, high_exclusive, level)
    (90, 101, "S"), (75, 90, "A"), (60, 75, "B"), (40, 60, "C"), (0, 40, "D"),
]

STAGE_ADJUST_DEFAULT = {"E0": -20, "E1": 0, "E2": 5, "E3": 8, "E4": 10, "E5": 12, "EX": -25}


def priority_of(score: float) -> str:
    for low, high, level in PRIORITY_BANDS:
        if low <= score <= high:
            return level
    return "D"


class FinalScorer:
    """评分维度来自 scoring_rules.yaml；LLM 只提供语义要素，分数由本类规则计算."""

    def __init__(self, config, known_matcher: Optional[KnownNewsMatcher] = None):
        self.config = config
        self.known_matcher = known_matcher or KnownNewsMatcher()
        sd = config.scoring_data()
        dims = sd.get("dimensions", {})
        self.max_target = _num(dims.get("target_relevance", {}).get("max"), 15)
        self.max_severity = _num(dims.get("behavior_severity", {}).get("max"), 20)
        self.max_evidence = _num(dims.get("evidence_quality", {}).get("max"), 20)
        self.max_chain = _num(dims.get("relationship_chain", {}).get("max"), 15)
        self.max_specific = _num(dims.get("specificity", {}).get("max"), 10)
        self.max_novelty = _num(dims.get("novelty", {}).get("max"), 10)
        self.max_interest = _num(dims.get("public_interest", {}).get("max"), 10)
        self.stage_adj = {**STAGE_ADJUST_DEFAULT,
                          **{k: _num(v, 0) for k, v in sd.get("stage_adjustment", {}).items()}}
        self.total_max = (self.max_target + self.max_severity + self.max_evidence +
                          self.max_chain + self.max_specific + self.max_novelty + self.max_interest)

    # ------------------------------------------------------------------
    def score(self, rule: RuleResult, llm: LLMResult,
              known_news: Optional[dict] = None) -> FinalScore:
        from ..preprocessing.normalization import to_simplified as _t2s2
        details: List[dict] = []
        dims: Dict[str, float] = {}
        text_for_novelty = llm.one_sentence_summary or ""
        evidence_items = llm.evidence_items or [h.term for h in rule.matched_keywords.get("E", [])]

        # A 目标相关度 0-15
        persons = llm.target_persons or rule.target_persons_found
        orgs = llm.target_organizations or rule.target_orgs_found
        from ..preprocessing.normalization import to_simplified as _t2s
        role_terms = list(dict.fromkeys(
            _t2s(h.term) for h in rule.matched_keywords.get("C", [])
            if any(k in _t2s(h.term) for k in
                   ("议员", "立委", "市长", "县长", "局长", "处长", "主委", "董事长", "主任",
                    "发言", "顾问", "官员", "政务官", "首长", "助理", "委员", "党"))))
        target = 0.0
        if persons or orgs or role_terms:
            target += 10.0
        # 高层/重要民代/首长
        senior = any(k in " ".join(role_terms) for k in
                     ("市长", "县长", "局长", "处长", "主委", "立委", "中执委", "中常委", "委员", "董事长"))
        if persons or senior:
            target = min(self.max_target, target + 5.0)
        target = min(target, self.max_target)
        dims["target_relevance"] = target
        details.append({"dim": "目标相关度", "score": target, "max": self.max_target,
                        "basis": f"人物{len(persons)}/机构{len(orgs)}/角色词{len(role_terms)}；高层另+5"})

        # B 行为严重度 0-20
        severity = self._severity(rule, llm)
        dims["behavior_severity"] = severity
        details.append({"dim": "行为严重度", "score": severity, "max": self.max_severity,
                        "basis": self._severity_basis(rule, llm)})

        # C 证据质量 0-20
        evidence = self._evidence(rule, evidence_items, persons, rule.money)
        # 正式程序阶段(E2+)的样本，其可信度下限提高
        if (llm.evidence_stage or "E1") in ("E2", "E3", "E4", "E5"):
            evidence = max(evidence, 12.0)
        dims["evidence_quality"] = evidence
        details.append({"dim": "证据质量", "score": evidence, "max": self.max_evidence,
                        "basis": f"原始证据词{len(evidence_items)}项、金额{len(rule.money)}笔"})

        # D 关系链完整度 0-15
        chain = self._chain(rule, llm)
        dims["relationship_chain"] = chain
        details.append({"dim": "关系链完整度", "score": chain, "max": self.max_chain,
                        "basis": self._chain_basis(rule, llm)})

        # E 具体性 0-10
        specific = self._specificity(rule, llm)
        dims["specificity"] = specific
        details.append({"dim": "具体性", "score": specific, "max": self.max_specific,
                        "basis": "时间/地点/金额/项目/公司计数"})

        # F 新颖度 0-10
        novelty = self._novelty(rule, llm, known_news)
        dims["novelty"] = novelty
        details.append({"dim": "新颖度", "score": novelty, "max": self.max_novelty,
                        "basis": "旧闻/新细节/新人物公司金额判定"})

        # G 公共利益 0-10
        interest = self._public_interest(rule, llm)
        dims["public_interest"] = interest
        details.append({"dim": "公共利益", "score": interest, "max": self.max_interest,
                        "basis": "类别公共利益阈值"})

        total = sum(dims.values())
        # ---- 规则包信号融合：高价值组合给有限加成（不超过维度语义，且负样本受 cap 保护） ----
        rule_bonus = 0.0
        strong_pat = [p for p in rule.matched_patterns
                      if p.pattern_id not in ("P19", "P20") and float(p.pattern_score or 0) >= 78]
        if len(strong_pat) >= 1:
            rule_bonus += 3.0
        if len(strong_pat) >= 2:
            rule_bonus += 2.0
        # 证据链：E 类(聊天/录音/截图等) + 强原件证据 + 金额 -> 加
        ev_terms = [_t2s2(h.term) for h in rule.matched_keywords.get("E", [])]
        strong_ev = [t for t in ev_terms
                     if t in ("银行流水", "汇款单", "存折", "合同", "契约", "内部公文", "签呈",
                              "标书", "帐册", "账册", "需求书", "采购文件", "名册") or "汇款" in t]
        mid_ev = [t for t in ev_terms if t in ("line", "微信", "录音", "录影", "照片", "截图",
                                               "对话", "聊天", "简讯")]
        if strong_ev and rule.money:
            rule_bonus += 4.0
        elif (strong_ev or mid_ev) and (rule.money or rule.target_persons_found):
            rule_bonus += 2.0
        if len(strong_pat) and (strong_ev or mid_ev):
            rule_bonus += 1.0
        # P19（原始证据+金额+人物）单独加成——E 类原始证据型线索门禁
        p19_hit = any(p.pattern_id == "P19" for p in rule.matched_patterns)
        if p19_hit and rule.money:
            rule_bonus += 5.0
        # 隐性表达集群确认
        m_n = len(rule.matched_keywords.get("M", []))
        if m_n >= 3 and not rule.matched_patterns:
            rule_bonus += 2.0
        # 吃案/压制司法：A12 与个人违法/性骚等并存时新闻价值叠加
        cats_all = set(rule.matched_categories)
        if "A12" in cats_all and any(c in cats_all for c in ("A13", "A11", "A08", "A03")):
            rule_bonus += 3.0
        # 正式调查中的学术/私德案件新闻价值补强
        if "A01" in cats_all and any(h.stage in ("S2", "S3")
                                     for h in rule.matched_keywords.get("S", [])):
            rule_bonus += 1.0
        # 规则分融合：词典/模式累积强度(rule_score)在维度分之外的补充；
        # 无 negative cap 时温和计入，cap 仍会截断最终分(负样本不受益)
        rule_norm_bonus = min(8.0, float(rule.rule_score) / 35.0)
        if rule.negative_matches or rule.ex_present or any(
                p.pattern_id == "P20" for p in rule.matched_patterns):
            rule_norm_bonus = 0.0
        total = total + rule_bonus + rule_norm_bonus

        # 证据阶段调整；EX 有新增证据(金额/银行流水/新模式)时不重罚(规则包 hard_rules:
        # 原始证据+关系链优先级高于单纯司法结果词；反向结果须记录但不掩盖新证据)
        stage = llm.evidence_stage or "E1"
        has_new_evidence = bool(rule.money) or any(
            p.pattern_id in ("P19",) or float(p.pattern_score or 0) >= 86
            for p in rule.matched_patterns)
        stage_delta = self.stage_adj.get(stage, 0.0)
        if stage == "EX" and has_new_evidence:
            stage_delta = -8.0
        # Negative rule 惩罚与上限（N01-N08 语义来自 negative_rules.yaml）
        neg_delta = 0.0
        neg_caps = []
        for n in rule.negative_matches:
            if isinstance(n, dict):
                neg_delta += float(n.get("score_delta") or 0)
                if n.get("max_score") is not None:
                    neg_caps.append(float(n["max_score"]))
            else:
                neg_delta += float(n.score_delta or 0)
                if n.max_score is not None:
                    neg_caps.append(float(n.max_score))
        # P20 cap
        cap = 100.0
        for p in rule.matched_patterns:
            if p.pattern_id == "P20":
                cap = min(cap, float(p.pattern_score) or 35.0)
        # 规则信号门控：词典/pattern 几乎无信号(rule_score 低)时，
        # 维度分不足以把营销/通知/订阅信抬入 S/A/B
        rs = float(rule.rule_score or 0)
        if rs < 45:
            cap = min(cap, 38.0)          # 纯噪声信 -> 最高 D
        elif rs < 70:
            cap = min(cap, 65.0)          # 弱信号但真实题材 -> 最多 B 下限，不进 A
        elif rs < 90:
            cap = min(cap, 80.0)          # 中等信号 -> 最多 A 下限
        if neg_caps:
            cap = min(cap, min(neg_caps))
        # EX 且无新增证据
        if stage == "EX" and rule.no_new_evidence:
            cap = min(cap, 40.0)
        if (llm.negative_or_exculpatory_evidence or rule.ex_present) and rule.no_new_evidence and not rule.money:
            cap = min(cap, 40.0)

        final = total + stage_delta + neg_delta
        final = min(final, cap)
        final = max(0.0, min(100.0, round(final)))
        return FinalScore(
            final_score=float(final), priority=priority_of(float(final)),
            dimension_scores={k: round(v, 1) for k, v in dims.items()},
            dimension_details=details, stage_adjustment=round(stage_delta, 1),
            negative_adjustment=round(neg_delta, 1), evidence_stage=stage,
        )

    # ---------------- 维度分项 ----------------
    def _severity(self, rule: RuleResult, llm: LLMResult) -> float:
        from ..preprocessing.normalization import to_simplified as _t2s
        high_terms = [_t2s(h.term) for h in rule.matched_keywords.get("H", [])]
        heavy = {"收贿", "受贿", "索贿", "贿选", "围标", "绑标", "泄密", "刺探", "贩毒", "洗钱",
                 "组织犯罪", "性侵", "人头助理", "诈领", "低薪高报", "非法", "买票", "吃案"}
        medium = {"图利", "利益冲突", "裙带", "未回避", "虚报", "浮报", "挪用", "假发票",
                  "不实核销", "性骚扰", "权势性骚", "家暴", "酒驾"}
        hl = sum(1 for t in high_terms if t in heavy)
        ml = sum(1 for t in high_terms if t in medium)
        pat_cats = set()
        for p in rule.matched_patterns:
            pat_cats.update(str(p.category).split("|"))
        sev = 4.0
        if "A03" in pat_cats or "A10" in pat_cats or "A17" in pat_cats or hl >= 1:
            sev = max(sev, 15.0)
        elif ml >= 1 or any(c in pat_cats for c in ("A02", "A06", "A08", "A09", "A12")):
            sev = max(sev, 10.0)
        elif high_terms:
            sev = max(sev, 8.0)
        # Pattern 自身权重映射严重度（规则包 base_score 是模式强度的权威信号）
        for p in rule.matched_patterns:
            pscore = float(p.pattern_score or 0)
            if pscore >= 90:
                sev = max(sev, 18.0)
            elif pscore >= 86:
                sev = max(sev, 16.0)
            elif pscore >= 80:
                sev = max(sev, 14.0)
            elif pscore >= 74:
                sev = max(sev, 12.0)
            else:
                sev = max(sev, 8.0)
        # 隐性表达集群（无 H 词、无 pattern，但 M>=2 + C 角色 + E 证据）
        m_terms = [_t2s(h.term) for h in rule.matched_keywords.get("M", [])]
        c_role_n = sum(1 for h in rule.matched_keywords.get("C", [])
                       if any(k in _t2s(h.term) for k in ("议员", "主任", "助理", "顾问", "官员", "党", "董")))
        e_n = len(rule.matched_keywords.get("E", []))
        if len(m_terms) >= 2 and c_role_n >= 1 and e_n >= 1 and not rule.matched_patterns:
            sev = max(sev, 12.0)
        if hl >= 2:
            sev = min(sev + 3, 20.0)
        if "A02" in pat_cats and not hl and not ml:
            sev = max(sev, 10.0)
        # 高价值组合 (证据+金额+pattern) 轻微上浮
        if sev >= 15 and rule.money and any(p.pattern_score >= 80 for p in rule.matched_patterns):
            sev = min(sev + 2, 20.0)
        # 类别特定严重度修正
        h_simp = [_t2s(h.term) for h in rule.matched_keywords.get("H", [])]
        m_simp = [_t2s(h.term) for h in rule.matched_keywords.get("M", [])]
        c_simp = [_t2s(h.term) for h in rule.matched_keywords.get("C", [])]
        hmc = " ".join(h_simp + m_simp + c_simp)
        if "来源不明" in hmc or "人头账户" in hmc or ("A08" in pat_cats and "现金" in hmc):
            sev = max(sev, 14.0)      # 来源不明现金/人头账户
        if "A13" in pat_cats and "A12" in pat_cats:
            sev = max(sev, 16.0)      # 个人违法+吃案压案 -> 权贵压制司法
        if "A14" in pat_cats and any(k in hmc for k in ("权势", "职权", "升迁", "胁迫", "安排")):
            sev = max(sev, 12.0)      # 权势关系私德(涉职权与利益)
        if "A01" in pat_cats and any(h.stage in ("S2", "S3")
                                     for h in rule.matched_keywords.get("S", [])):
            sev = max(sev, 13.0)      # 学伦正式调查中
        return min(sev, self.max_severity)

    def _severity_basis(self, rule: RuleResult, llm: LLMResult) -> str:
        hh = [h.term for h in rule.matched_keywords.get("H", [])][:5]
        pats = [p.pattern_id for p in rule.matched_patterns][:5]
        return f"H词{hh}；Pattern {pats}" if (hh or pats) else "未见高强度行为词"

    def _evidence(self, rule: RuleResult, evidence_items, persons, money) -> float:
        from ..preprocessing.normalization import to_simplified as _t2s
        ev = [_t2s(h.term) for h in rule.matched_keywords.get("E", [])]
        # 强原件类：银行/汇款/流水/合同/内部公文/标案文件/名册/薪资账户文件
        strong_hits = sum(1 for t in ev if any(k in t for k in
                          ("流水", "汇款", "汇", "存折", "提款", "合同", "合约", "契约", "公文",
                           "签呈", "标书", "标单", "招标", "需求书", "采购", "底价", "账册", "帐册",
                           "申报", "收据", "发票", "名册", "名单", "证", "报告", "比对",
                           "纪录单", "记录单", "银行", "银行", "薪资", "薪資", "账户", "帳戶",
                           "明细", "明細", "转账", "轉帳", "汇款纪录", "收款", "存折", "簿",
                           "表", "单", "册")) or t in
                          ("标书", "标单", "底价表", "汇款单", "银行流水", "存折"))
        # 原始聊天/媒体类
        mid_hits = sum(1 for t in ev if any(k in t.lower() for k in
                       ("line", "微信", "录音", "录影", "录像", "照片", "截图", "简讯", "对话",
                        "聊天", "邮件", "email", "通联", "简讯", "行事历", "行程")))
        doc_hits = max(strong_hits, mid_hits)
        if money and doc_hits >= 1:
            if mid_hits >= 2 or strong_hits >= 1:
                return self.max_evidence   # 多源或含强原件
            return min(18.0, 15.0 + mid_hits * 1.5)
        if strong_hits >= 2:
            return 20.0
        if strong_hits >= 1:
            return 15.0
        if mid_hits >= 1:
            if money:
                return min(12.0, 10.0 + min(2, len(money)))
            return 10.0
        if ev:
            return 6.0
        if persons and money:
            return 6.0
        if rule.matched_keywords.get("M") or rule.matched_keywords.get("H"):
            return 6.0 if persons else 2.0
        return 2.0

    def _chain(self, rule: RuleResult, llm: LLMResult) -> float:
        from ..preprocessing.normalization import to_simplified as _t2s
        persons = llm.target_persons or rule.target_persons_found
        orgs = llm.target_organizations or rule.target_orgs_found
        related = llm.related_entities or {}
        companies = related.get("companies") or orgs
        # 关系人：亲属/助理/中间人
        rels = (related.get("relatives") or []) + (related.get("assistants") or []) + \
               (related.get("intermediaries") or [])
        rels = [r for r in rels if r]
        money = llm.money_or_benefits or [str(m["amount"]) for m in rule.money]
        # 利益节点：金额或现金/酬金类词(未量化资金也算利益要素)
        hm_simp = [_t2s(h.term) for h in
                   rule.matched_keywords.get("H", []) + rule.matched_keywords.get("M", [])]
        if not money and any(any(k in t for k in ("现金", "酬金", "佣金", "回扣", "顾问费", "干股",
                                                  "股份", "汇款", "利益", "好处"))
                             for t in hm_simp):
            money = ["(现金/酬金类)"]
        pat_cats = set()
        for p in rule.matched_patterns:
            pat_cats.update(str(p.category).split("|"))
        # 权力动作（简体域比对）
        powers = llm.power_actions or [_t2s(h.term) for h in rule.matched_keywords.get("H", []) if
                                       any(k in _t2s(h.term) for k in
                                           ("关说", "施压", "协调", "护航", "放水", "删改", "要求", "协助", "安排"))]
        # A12 压制动作(吃案/压案/撤案/封口/包庇/删消息/调职)即公权力压制行为
        if not powers:
            suppress = [h.term for h in rule.matched_keywords.get("H", [])
                        if any(k in _t2s(h.term) for k in ("吃案", "压案", "撤案", "封口", "包庇", "灭证",
                                                           "删除消息", "删消息", "调职", "解雇"))]
            if suppress:
                powers = [suppress[0]]
        # 人物链：真人名缺失时以 C 角色词(议员/主任/助理等)作为目标/关系节点
        role_hits = list(dict.fromkeys(
            _t2s(h.term) for h in rule.matched_keywords.get("C", [])
            if any(k in _t2s(h.term) for k in ("议员", "立委", "市长", "县长", "主任", "助理",
                                               "董事长", "顾问", "官员", "党", "委员", "局", "处",
                                               "桩脚", "服务处", "候选人", "总干事", "干事",
                                               "承办", "老板", "秘书", "长"))))
        node_person = persons or (role_hits[:1] or [])
        node_rel = role_hits[1:3] if not rels else rels
        # 吃案/压制语境：A12 命中但无角色词时，仍承认当事人-权力压制链
        if not node_person and "A12" in pat_cats:
            node_person = ["(被压制方)"]
        if not node_person:
            # 完全没有目标节点时 chain 为 0；但 pattern 命中已隐含人物节点时以 pattern 为准
            if not rule.matched_patterns:
                return 0.0
        score = 2.0
        if companies:
            score = max(score, 5.0)
        if node_rel:
            score = max(score, 5.0)
        if money:
            score = max(score, 9.0)
        if powers:
            score = max(score, 15.0)
        # pattern 命中：规则包 pattern 本身是「人物-关系人-利益-权力动作」结构化证据
        if rule.matched_patterns:
            high_pat = [p for p in rule.matched_patterns
                        if p.pattern_id not in ("P19", "P20") and float(p.pattern_score or 0) >= 78]
            if high_pat and money:
                score = max(score, 15.0)
            elif high_pat:
                score = max(score, 9.0)
        # 隐性表达集群：M>=3 说明存在关系人之间的利益许诺语境
        m_n = len(rule.matched_keywords.get("M", []))
        if m_n >= 3 and node_person and (node_rel or companies):
            score = max(score, 9.0)
        # 阶梯式（与规则包描述一致：人物+关系人+利益+权力动作=15）
        chain_len = (1 if node_person else 0) + (1 if (companies or node_rel) else 0) + \
                    (1 if money else 0) + (1 if powers else 0)
        if chain_len >= 4:
            score = 15.0
        elif chain_len == 3:
            score = max(score, 9.0)
        return min(score, self.max_chain)

    def _chain_basis(self, rule: RuleResult, llm: LLMResult) -> str:
        persons = len(llm.target_persons or rule.target_persons_found)
        money = len(llm.money_or_benefits or rule.money)
        return f"人物{persons}个、资金{ money}笔、权力动作{len(llm.power_actions or [])}项"

    def _specificity(self, rule: RuleResult, llm: LLMResult) -> float:
        score = 0.0
        # 时间/地点
        def _etext(e):
            return e.get("text") if isinstance(e, dict) else getattr(e, "text", "")

        def _etype(e):
            return e.get("type") if isinstance(e, dict) else getattr(e, "type", "")
        dates = list(dict.fromkeys(_etext(e) for e in rule.entities if _etype(e) == "DATE"))
        if dates:
            score += 2.0
        if rule.money:
            score += 2.5
        companies = list(dict.fromkeys(_etext(e) for e in rule.entities
                                       if _etype(e) in ("COMPANY", "ORGANIZATION")))
        if not companies:
            companies = llm.target_organizations or rule.target_orgs_found
        projects = list(dict.fromkeys(_etext(e) for e in rule.entities if _etype(e) == "PROJECT"))
        if not projects:
            projects = llm.projects_or_cases or []
        if projects:
            score += 2.0
        if companies:
            score += 2.0
        # 人名等可核验；角色词(议员/主任等具体职务)同样可核验
        from ..preprocessing.normalization import to_simplified as _t2s_spec
        role_hits_spec = [_t2s_spec(h.term) for h in rule.matched_keywords.get("C", [])
                          if any(k in _t2s_spec(h.term) for k in ("议员", "立委", "市长", "县长", "主任", "助理",
                                                                  "董事长", "局", "处", "长"))]
        if llm.target_persons or rule.target_persons_found or role_hits_spec:
            score += 1.5
        # 证据文件类型算具体项（附件/文件/对话记录等）
        e_n = len(rule.matched_keywords.get("E", []))
        if e_n >= 1:
            score += 1.0
        if e_n >= 3:
            score += 1.5
        if score >= 9:
            return 10.0
        return round(min(score, 10.0), 1)

    def _novelty(self, rule: RuleResult, llm: LLMResult, known_news: Optional[dict]) -> float:
        # 已知旧闻匹配
        known = known_news or {}
        if known.get("known"):
            if rule.money or llm.new_information or llm.evidence_items:
                return 5.0   # 旧案新细节
            if rule.no_new_evidence or not rule.money:
                return 0.0
            return 5.0
        # 未知：看新增信息字段
        if llm.new_information:
            return 10.0
        if rule.no_new_evidence:
            return 0.0
        # 旧闻转发（强信号：转发/转载/旧闻 等字样 + 无金额新线索）降为新细节档
        # 不用 "当时/曾/先前" 等弱叙述词——正常爆料叙述也会出现
        oldish = any(k in rule.normalized for k in
                     ("转发", "轉發", "转寄", "轉寄", "转贴", "轉貼", "转载", "轉載",
                      "旧闻", "舊聞", "当年新闻", "當年新聞", "去年报道", "去年報導",
                      "旧新闻", "舊新聞", "旧案", "舊案"))
        oldish = oldish or (("报道" in rule.normalized or "報導" in rule.normalized or
                             "新闻" in rule.normalized or "新聞" in rule.normalized)
                            and ("去年" in rule.normalized or "先前" in rule.normalized or
                                 "当年" in rule.normalized or "當年" in rule.normalized or
                                 "此前已" in rule.normalized))
        if oldish and not rule.money and not rule.matched_keywords.get("E"):
            return 0.0
        if oldish and not rule.money:
            return 5.0
        # 新爆料线索默认给满新颖度
        return 10.0

    def _public_interest(self, rule: RuleResult, llm: LLMResult) -> float:
        cats = llm.categories or rule.matched_categories
        high = {"A02", "A03", "A04", "A05", "A06", "A07", "A08", "A10", "A12", "A15", "A16", "A17", "A18"}
        mid = {"A09", "A11", "A01"}
        if any(c in high for c in cats):
            return 10.0
        if any(c in mid for c in cats):
            return 7.0
        if cats:
            return 5.0
        return 1.0


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
