"""端到端筛选流水线：EML -> 解析 -> 实体 -> 词典 -> Pattern -> Negative -> LLM -> 评分 -> 队列."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from ..config import (DATA_DIR, LLM_ENABLED, LLM_TRIGGER_SCORE, RULE_TRIGGER_EXTRA,
                      PROCESSED_DIR)
from ..llm.screener import LLMScreener
from ..models import (AttachmentDoc, EmailDocument, FinalScore, LLMResult,
                      RuleResult, ScreeningRecord)
from ..parsers import parse_attachment, parse_eml
from ..preprocessing.normalization import Normalizer
from ..rules.config_loader import RuleConfig
from ..rules.rule_engine import RuleEngine
from ..scoring.scorer import FinalScorer
try:
    from ..governance.config_loader import GovernanceConfig
    from ..governance.engine import GovernanceEngine
    _GOV_AVAILABLE = True
except Exception:
    GovernanceConfig = None
    GovernanceEngine = None
    _GOV_AVAILABLE = False
from ..scoring.known_news_matcher import KnownNewsMatcher
from ..storage.database import Database
from ..storage.repository import Repository

logger = logging.getLogger(__name__)

# 需要回填附件内容的解析器
TEXT_FILE_TYPES = {"pdf", "docx", "xlsx", "csv", "txt", "md", "jpg", "png"}


def _summary_for(rec: ScreeningRecord, rule: RuleResult, llm: LLMResult,
                 fs: FinalScore) -> str:
    """生成 80-180 字「为什么值得记者看」摘要（谨慎表述）。"""
    persons = "、".join((llm.target_persons or rule.target_persons_found)[:3]) or "相關人士"
    cats = "、".join((llm.categories or rule.matched_categories)[:3])
    pats = "/".join(p.pattern_id for p in rule.matched_patterns[:3])
    money_desc = "、".join(f"{m.get('raw')}({m.get('amount')} {m.get('currency')})" for m in rule.money[:2])
    evid = "、".join(h.term for h in rule.matched_keywords.get("E", [])[:3])
    if llm.evidence_items:
        evid = evid or "、".join(llm.evidence_items[:3])
    neg = ""
    if rule.ex_present:
        neg = "；注意該事項已有反向結果記錄，需查證本次是否提供新證據"
    base = (f"郵件指稱{persons}涉{cats}相關事項，規則層命中Pattern {pats or '無'}，"
            f"含原始證據線索（{evid or '未見'}）。")
    if money_desc:
        base += f"郵件提及資金（{money_desc}）。"
    if llm.relationship_chain:
        base += "構成潛在關係鏈：" + "、".join(llm.relationship_chain[:3]) + "。"
    base += f"郵件分數{fs.final_score:.0f}/{fs.priority}級，尚待核實。{neg}"
    # 控制长度
    base = re.sub(r"\s+", "", base)
    if len(base) > 180:
        base = base[:177] + "…"
    return base


class ScreeningPipeline:
    """处理一个 EML 文件（或直接文本）的完整链路."""

    def __init__(self, config: RuleConfig, db: Optional[Database] = None,
                 llm_screener: Optional[LLMScreener] = None,
                 scorer: Optional[FinalScorer] = None,
                 known_matcher: Optional[KnownNewsMatcher] = None,
                 ocr_enabled: bool = True,
                 llm_trigger_score: float = LLM_TRIGGER_SCORE,
                 allow_llm: bool = True,
                 release_advisor: Optional["ReleaseAdvisor"] = None,
                 named_advisor: Optional["NamedChannelAdvisor"] = None):
        self.config = config
        self.rule_engine = RuleEngine(config)
        self.llm_screener = llm_screener or LLMScreener(config)
        self.known_matcher = known_matcher or KnownNewsMatcher()
        self.scorer = scorer or FinalScorer(config, self.known_matcher)
        self.repo = Repository(db) if db is not None else None
        self.ocr_enabled = ocr_enabled
        self.llm_trigger_score = llm_trigger_score
        self.allow_llm = allow_llm and LLM_ENABLED
        self.release_advisor = release_advisor
        self.named_advisor = named_advisor
        # V4 governance engine (parallel, never break V1-V3)
        self.gov_engine = None
        try:
            if _GOV_AVAILABLE:
                import pathlib as _pl
                gov_base = None
                try:
                    _cfg_dir = getattr(config, "directory", None)
                    if _cfg_dir:
                        gov_base = _pl.Path(_cfg_dir)
                except Exception:
                    gov_base = None
                from app.config import NEWS_SIGNAL_DIR as _NSD
                self.gov_engine = GovernanceEngine(gov_base or _NSD)
        except Exception as _e:
            logger.warning("Governance engine init failed (V1-V3 unaffected): %s", _e)
            self.gov_engine = None
        # S/A/B 进入首发渠道推荐的分数门槛（默认 60，可由 advisor.min_score 覆盖）
        self.release_advisor_min_score = 60.0
        if self.release_advisor is not None and getattr(self.release_advisor, "min_score", None):
            self.release_advisor_min_score = float(self.release_advisor.min_score)

    # ------------------------------------------------------------------
    def process_file(self, eml_path: str | Path) -> Optional[ScreeningRecord]:
        eml_path = Path(eml_path)
        try:
            doc = parse_eml(eml_path)
        except Exception as e:  # noqa: BLE001
            logger.error("EML 解析失败 %s: %s", eml_path, e)
            return ScreeningRecord(email_id=str(eml_path), error=f"EML解析失败: {e}")
        if self.repo is not None and self.repo.is_duplicate(doc):
            logger.info("跳过重复邮件: %s (%s)", doc.subject, eml_path.name)
            return None
        return self._process_document(doc)

    def process_document(self, doc: EmailDocument) -> Optional[ScreeningRecord]:
        if self.repo is not None and self.repo.is_duplicate(doc):
            logger.info("跳过重复邮件: %s", doc.subject)
            return None
        return self._process_document(doc)

    # ------------------------------------------------------------------
    def _process_document(self, doc: EmailDocument) -> ScreeningRecord:
        # 附件解析：跳过已知哈希（OCR/LLM 不重复调用）
        attachments_text = []
        for att in doc.attachments:
            cached = (att.metadata or {}).get("cached_path", "")
            if not cached:
                continue
            if att.file_type not in TEXT_FILE_TYPES:
                att.extraction_status = "skipped"
                att.warnings.append("该类型附件不进文本抽取")
                continue
            if self.repo is not None and self.repo.attachment_known("") and att.content_hash:
                continue  # 占位：真正判定在 parse 后
            try:
                parsed = parse_attachment(cached, att.filename, ocr_enabled=self.ocr_enabled)
                att.text = parsed.text
                att.metadata = {**att.metadata, **(parsed.metadata or {})}
                att.tables = parsed.tables
                att.extraction_status = parsed.extraction_status
                att.warnings = parsed.warnings
                att.content_hash = parsed.content_hash
                att.ocr_used = parsed.ocr_used
            except Exception as e:  # noqa: BLE001
                logger.warning("附件解析异常 %s: %s", att.filename, e)
                att.extraction_status = "failed"
                att.warnings.append(f"解析异常: {e}")
            if att.text and att.text.strip():
                attachments_text.append(f"\n===== 附件：{att.filename} =====\n{att.text}")
            if att.tables:
                # 表格内容（保留行列）附加
                tbl_lines = []
                for t in att.tables[:300]:
                    tbl_lines.append(f"[{t.get('sheet')} r{t.get('row')}c{t.get('col')}] {t.get('value')}")
                if tbl_lines:
                    attachments_text.append(f"\n===== 附件表格：{att.filename} =====\n" + "\n".join(tbl_lines))

        combined = doc.body_text
        if attachments_text:
            combined += "\n\n" + "\n\n".join(attachments_text)
        doc.combined_text = combined
        doc.original_text = combined
        doc.normalized_text = Normalizer.normalize(combined)

        # 全文规则引擎
        rr = self.rule_engine.evaluate(combined)
        # 个人事务性邮件识别（本人账单/银行官方通知等——与爆料场景无关，压至 D）
        self._apply_transactional_cap(doc, rr)
        # 旧闻判定（无新增证据）——给 negative/LLM 语义组
        no_new = self._judge_no_new_evidence(doc, rr)
        if no_new != rr.no_new_evidence:
            # 重新评估（需要 no_new_evidence 参与 P20/X_result 判定）
            rr = self.rule_engine.evaluate(combined, no_new_evidence=no_new)
            self._apply_transactional_cap(doc, rr)

        # 已知新闻匹配
        known = self.known_matcher.match(
            persons=rr.target_persons_found, companies=rr.target_orgs_found)

        # LLM 触发
        llm = self._maybe_llm(doc, rr)
        if llm is None:
            llm = self._fallback_llm(doc, rr)  # 低分也保留 template 判断，保持结构

        # 最终评分
        fs = self.scorer.score(rr, llm, known_news=known)

        rec = ScreeningRecord(email_id=doc.email_id, email=doc, rule=rr, llm=llm,
                              score=fs, known_news=known)
        # V4 governance parallel (never affect rule_score; only upgrade final when G stronger)
        try:
            if self.gov_engine is not None:
                has_att = bool(doc.attachments)
                gov = self.gov_engine.evaluate(combined, has_attachment=has_att)
                rec.governance_categories = list(gov.categories)
                rec.governance_keywords = dict(gov.keywords)
                rec.governance_patterns = list(gov.patterns)
                rec.governance_score = float(gov.score)
                rec.governance_priority = str(gov.priority)
                rec.governance_dims = dict(gov.dims)
                # 融合：保留两套分类，仅升级 final（不降级，不改 rule_score）
                _order = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}
                if gov.categories and _order.get(gov.priority, 0) > _order.get(fs.priority, 0):
                    fs.final_score = float(max(float(fs.final_score), float(gov.score)))
                    fs.priority = str(gov.priority)
        except Exception as _e:
            logger.warning("Governance evaluate failed (V1-V3 unaffected): %s", _e)
        rec.summary_zh = _summary_for(rec, rr, llm, fs)
        rec.verification_targets = llm.verification_targets or []
        if fs.priority in ("S", "A", "B") and not rec.verification_targets:
            rec.verification_targets = llm.verification_targets or \
                self.llm_screener._make_verification_targets(rr, [], [])
        # 首发渠道推荐：仅 S/A/B 且 final_score >= 阈值（默认 60）
        if self.release_advisor is not None and fs.priority in ("S", "A", "B") \
                and fs.final_score >= self.release_advisor_min_score:
            try:
                rel = self.release_advisor.advise(
                    email_id=doc.email_id, rule=rr, llm=llm, score=fs, doc=doc)
                rec.release_recommendation = rel.to_dict()
            except Exception as e:  # noqa: BLE001
                logger.error("首发渠道推荐失败 %s: %s", doc.email_id, e)
                rec.release_recommendation = None
        # V3 具名渠道推荐：V2 存在且 S/A/B 才运行
        if self.named_advisor is not None and rec.release_recommendation is not None \
                and fs.priority in ("S", "A", "B") \
                and fs.final_score >= self.release_advisor_min_score:
            try:
                named = self.named_advisor.advise(
                    email_id=doc.email_id, rule=rr, llm=llm, score=fs,
                    release_recommendation=rec.release_recommendation, doc=doc)
                rec.named_channel_recommendation = named.to_dict()
            except Exception as e:  # noqa: BLE001
                logger.error("具名渠道推荐失败 %s: %s", doc.email_id, e)
                rec.named_channel_recommendation = None
        # 落库
        if self.repo is not None:
            try:
                self.repo.save_email(doc)
                self.repo.save_record(rec, store_full=False)
                if rec.release_recommendation is not None:
                    from ..release_advisor.models import ReleaseRecommendation
                    rel_dict = dict(rec.release_recommendation)
                    rel_dict.pop("email_id", None)
                    self.repo.save_release_recommendation(
                        ReleaseRecommendation(email_id=doc.email_id, **{
                            k: v for k, v in rel_dict.items()
                            if k in ReleaseRecommendation.__dataclass_fields__}))
                if rec.named_channel_recommendation is not None:
                    self.repo.save_named_channel_recommendations(
                        doc.email_id, rec.named_channel_recommendation)
            except Exception as e:  # noqa: BLE001
                logger.error("落库失败 %s: %s", doc.email_id, e)
        return rec

    # ------------------------------------------------------------------
    # 个人事务性邮件识别：银行账单/电子发票/行程单/积分里程/验证码等官方系统发给
    # 本人的日常凭证与通知，与爆料场景无关，即便含金额/姓名/公司也不得进入 S/A/B。
    # （区别于爆料场景：爆料人转发的他人流水/发票——发件人不是平台官方域名）
    _PERSONAL_SENDERS = ("citiccard.com", "cmbchina.com", "ccb.com", "icbc.com.cn", "abchina.com",
                         "boc.cn", "bankcomm.com", "cib.com.cn", "spdb.com.cn", "cmbc.com.cn",
                         "cebbank.com", "pingan.com", "cgbchina.com.cn", "hxb.com.cn", "paypal.com",
                         "alipay.com", "tenpay.com", "apple.com", "microsoft.com", "github.com",
                         "51fapiao.cloud", "fapiao", "ceair.com", "csair.com", "mu.com", "chinaair.com",
                         "damai.cn", "maoyan.com", "meituan.com", "dianping.com", "ele.me",
                         "didiglobal.com", "xiaojuche.com", "helloinc.com", "qingtengzhuche.com",
                         "amap.com", "taobao.com", "tmall.com", "jd.com", "reddit.com", "feedspot.com")
    _PERSONAL_SIGNALS = ("电子账单", "電子帳單", "信用卡账单", "信用卡帳單", "账单已产生", "帳單已產生",
                         "应还款总额", "應還款總額", "月结单", "月結單", "本期账单", "本期帳單",
                         "个人消费卡", "個人消費卡", "对账单", "對帳單", "电子发票", "電子發票",
                         "数电发票", "數電發票", "电子行程单", "電子行程單", "行程单", "行程單",
                         "报销凭证", "報銷憑證", "发票开具成功", "發票開具成功", "价税合计", "價稅合計",
                         "发票号码", "發票號碼", "购方名称", "購方名稱", "积分变动", "積分變動",
                         "里程到期", "里程變動", "验证码", "驗證碼", "确认邮箱", "確認郵箱",
                         "体检报告", "體檢報告", "会员积分", "會員積分", "升级提醒")

    def _apply_transactional_cap(self, doc: EmailDocument, rr: RuleResult):
        sender = (doc.sender or "").lower()
        subject = doc.subject or ""
        text = doc.normalized_text
        is_personal_sender = any(d in sender for d in self._PERSONAL_SENDERS)
        hits = [s for s in self._PERSONAL_SIGNALS if s in subject or s in text]
        # 官方平台发件人 + 凭证/通知特征；或强凭证特征(>=2)且含平台特征
        if (is_personal_sender and hits) or (len(hits) >= 2 and any(
                k in text for k in ("开具", "開具", "电子", "電子", "发票", "發票", "账单", "帳單"))):
            rr.rule_score = min(rr.rule_score, 15.0)
            rr.matched_patterns = []
            rr.matched_categories = []
            rr.money = []
            for k in list(rr.matched_keywords.keys()):
                rr.matched_keywords[k] = []
            rr.negative_matches.append({
                "rule_id": "N01", "condition": "个人消费凭证/官方通知类事务性邮件",
                "score_delta": -30.0, "max_score": 30.0, "matched_terms": hits[:4], "snippets": []})
            logger.info("个人事务性邮件降权: %s (%s)", doc.subject, sender or "?")

    def _judge_no_new_evidence(self, doc: EmailDocument, rr: RuleResult) -> bool:
        """判断是否「旧案无新增证据」：旧闻信号 + 无新证据要素."""
        text = doc.normalized_text
        old_signals = ["去年", "先前", "当年", "当时", "旧闻", "过去", "转发", "转贴", "转载",
                       "曾报道", "曾報導", "旧案", "当年新闻", "旧新闻"]
        news_signals = ["报道", "報導", "新闻", "新聞", "记者", "記者", "媒体", "媒體"]
        has_old = any(s in text for s in old_signals)
        has_news = any(s in text for s in news_signals)
        # 新证据要素：新金额/新文件/新账户/新公司/新时间
        has_new = bool(rr.money)
        has_new = has_new or bool(rr.matched_keywords.get("E"))
        # 附件有文本也算潜在新证据
        if rr.no_new_evidence:
            return True
        if has_old and has_news and not has_new:
            return True
        return False

    def _maybe_llm(self, doc: EmailDocument, rr: RuleResult) -> Optional[LLMResult]:
        """规则触发 LLM：rule_score>=35 或 高价值 pattern 或 E+人/公司+金额."""
        trigger = False
        reason = ""
        if rr.rule_score >= self.llm_trigger_score:
            trigger, reason = True, f"rule_score={rr.rule_score}>={self.llm_trigger_score}"
        if RULE_TRIGGER_EXTRA and not trigger:
            hp = [p for p in rr.matched_patterns if p.pattern_id not in ("P20",) and p.pattern_score >= 70]
            if hp:
                trigger, reason = True, f"高价值Pattern {hp[0].pattern_id}"
            else:
                e_terms = rr.matched_keywords.get("E", [])
                has_person = bool(rr.target_persons_found or rr.matched_keywords.get("C"))
                if e_terms and has_person and rr.money:
                    trigger, reason = True, "E类证据+目标+具体金额"
        if trigger:
            logger.info("触发 LLM (%s): %s", reason, doc.subject)
            if not self.allow_llm:
                return None
            return self.llm_screener.screen(doc.combined_text, doc.subject, doc.sender, rr)
        return None

    def _fallback_llm(self, doc: EmailDocument, rr: RuleResult) -> LLMResult:
        """未达 LLM 阈值(rule_score<35)：不造内容，返回空 LLM 结果。
        避免模板后端把低信号营销/通知信抬高；最终分由规则分与负向规则决定。"""
        if rr.rule_score < self.llm_trigger_score:
            return LLMResult(llm_status="degraded", relevant=False)
        raw = self.llm_screener._screen_template(doc.combined_text, doc.subject, doc.sender, rr)
        ok, cleaned, errors = __import__("app.llm.schemas", fromlist=["validate_result"]).validate_result(raw)
        llm = LLMResult(llm_status="degraded", relevant=False)
        if ok:
            llm = LLMResult(llm_status="degraded", **cleaned)
        return llm

    # ------------------------------------------------------------------
    def process_directory(self, inbox: str | Path) -> List[ScreeningRecord]:
        inbox = Path(inbox)
        records = []
        for eml in sorted(inbox.glob("*.eml")):
            rec = self.process_file(eml)
            if rec is not None:
                records.append(rec)
        return records
