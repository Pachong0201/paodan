"""端到端筛选流水线：EML -> Secure Parsing -> 附件哈希/Parse Cache -> V1+V4 -> UnifiedSignalSet
-> Local Features -> Privacy Gateway -> LLM(optional) -> Unified Final Score -> Summary/Verification
-> V2/V3 -> SQLite/JSONL/CSV/Excel/Review Queue。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

from ..config import (DATA_DIR, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL, PROCESSED_DIR,
                      RULE_TRIGGER_EXTRA, resolve_llm_trigger_score)
from ..governance.verification import verification_targets_for
from ..llm.screener import LLMScreener
from ..models import (AttachmentDoc, EmailDocument, FinalScore, LLMResult,
                      RuleResult, ScreeningRecord)
from ..parsers import parse_attachment, parse_eml
from ..parsers.attachment_parser import PARSER_VERSION, OCR_VERSION, sha256_file
from ..preprocessing.normalization import Normalizer
from ..rules.config_loader import RuleConfig
from ..rules.rule_engine import RuleEngine
from ..scoring.scorer import FinalScorer
try:
    from ..governance.config_loader import GovernanceConfig
    from ..governance.engine import GovernanceEngine
    _GOV_AVAILABLE = True
except Exception:  # pragma: no cover
    GovernanceConfig = None
    GovernanceEngine = None
    _GOV_AVAILABLE = False
from ..scoring.known_news_matcher import KnownNewsMatcher
from ..security.payload_builder import SafePayloadBuilder
from ..signals.merger import SignalMerger, UnifiedFinalScorer
from ..storage.database import Database
from ..storage.repository import Repository

logger = logging.getLogger(__name__)

TEXT_FILE_TYPES = {"pdf", "docx", "xlsx", "csv", "txt", "md", "jpg", "png"}


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def _sha256_file(path: Path) -> str:
    return sha256_file(path)


def _hash_files(paths: List[Path]) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        try:
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
        except OSError:
            continue
    return h.hexdigest()


def _rule_pack_hash(config_dir: Optional[Path]) -> str:
    if not config_dir:
        return ""
    names = ["taxonomy.yaml", "keywords.yaml", "pattern_rules.yaml",
             "negative_rules.yaml", "scoring_rules.yaml", "unified_signals.yaml"]
    return _hash_files([config_dir / n for n in names if (config_dir / n).exists()])


def _governance_rule_pack_hash(config_dir: Optional[Path]) -> str:
    if not config_dir:
        return ""
    names = ["governance_complaints.yaml", "governance_pattern_rules.yaml",
             "governance_negative_rules.yaml"]
    return _hash_files([config_dir / n for n in names if (config_dir / n).exists()])


def _scoring_rule_hash(config_dir: Optional[Path]) -> str:
    if not config_dir:
        return ""
    p = config_dir / "scoring_rules.yaml"
    return _sha256_text(p.read_text(encoding="utf-8")) if p.exists() else ""


def _mask_sender(sender: str) -> str:
    s = str(sender or "")
    if "@" in s:
        local, _, domain = s.partition("@")
        return (local[:2] + "***@" + domain) if local else "***@" + domain
    return "***" if s else "?"


def _prompt_hash(config_dir: Optional[Path]) -> str:
    if not config_dir:
        return ""
    p = config_dir / "llm_email_screening_prompt.md"
    return _sha256_text(p.read_text(encoding="utf-8")) if p.exists() else ""


def _summary_for(rec: ScreeningRecord, rule: RuleResult, llm: LLMResult,
                 fs: FinalScore, unified=None, governance=None) -> str:
    """生成 80-180 字摘要；Governance/Mixed 必须体现治理语义。"""
    from ..governance.numeric_features import extract_numeric_features
    gov = governance
    track = str(getattr(unified, "primary_track", "") or "")
    gov_cats = list(getattr(gov, "categories", []) or [])
    if track in ("GOVERNANCE", "MIXED") or (gov_cats and track == "GOVERNANCE"):
        nf = extract_numeric_features(rule.normalized or rule.normalized)
        cats = "、".join(gov_cats[:4]) or "治理民生"
        parts = [f"治理民生线索（{cats}）"]
        if nf.duration_days:
            parts.append(f"持续时间约{nf.duration_days:.0f}天")
        elif nf.approximate_duration_days:
            parts.append("持续时间较长")
        if nf.affected_population:
            parts.append(f"影响约{nf.affected_population:.0f}户/人")
        if nf.complaint_count or nf.frequency:
            parts.append(f"陈情/投诉约{max(nf.complaint_count or 0, nf.frequency or 0):.0f}次")
        if nf.money_loss:
            parts.append(f"涉及损失约{nf.money_loss:.0f}元")
        if nf.waiting_time_days:
            parts.append(f"等待约{nf.waiting_time_days:.0f}天")
        ev = "、".join((nf.evidence_terms or [])[:3]) or "待补证据"
        parts.append(f"现有证据形态：{ev}")
        if track == "MIXED":
            pol_cats = "、".join((getattr(unified, "political_categories", []) or [])[:3])
            if pol_cats:
                parts.append(f"同时涉及政治类线索：{pol_cats}")
        parts.append(f"综合评分{fs.final_score:.0f}/{fs.priority}级，尚待核实。")
        base = "，".join(parts)
    else:
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
    base = re.sub(r"\s+", "", base)
    if len(base) > 220:
        base = base[:217] + "…"
    return base


class ScreeningPipeline:
    """处理一个 EML 文件（或直接文本）的完整链路."""

    def __init__(self, config: RuleConfig, db: Optional[Database] = None,
                 llm_screener: Optional[LLMScreener] = None,
                 scorer: Optional[FinalScorer] = None,
                 known_matcher: Optional[KnownNewsMatcher] = None,
                 ocr_enabled: bool = True,
                 llm_trigger_score: Optional[float] = None,
                 allow_llm: bool = True,
                 release_advisor: Optional["ReleaseAdvisor"] = None,
                 named_advisor: Optional["NamedChannelAdvisor"] = None,
                 reprocess: bool = False, rescore: bool = False,
                 reanalyze: bool = False):
        self.config = config
        self.rule_engine = RuleEngine(config)
        self.llm_screener = llm_screener or LLMScreener(config)
        self.known_matcher = known_matcher or KnownNewsMatcher()
        self.scorer = scorer or FinalScorer(config, self.known_matcher)
        self.repo = Repository(db) if db is not None else None
        self.ocr_enabled = ocr_enabled
        self.llm_trigger_score = resolve_llm_trigger_score(llm_trigger_score)
        self.allow_llm = allow_llm and LLM_ENABLED
        self.release_advisor = release_advisor
        self.named_advisor = named_advisor
        self.reprocess = bool(reprocess)
        self.rescore = bool(rescore)
        self.reanalyze = bool(reanalyze)
        self.merger = SignalMerger()
        self.unified_scorer = UnifiedFinalScorer()
        # Rule / prompt hashes for analysis_runs / stale detection
        cfg_dir = getattr(config, "directory", None)
        self.cfg_dir = Path(cfg_dir) if cfg_dir else None
        self.political_rule_pack_hash = _rule_pack_hash(self.cfg_dir)
        self.governance_rule_pack_hash = _governance_rule_pack_hash(self.cfg_dir)
        self.scoring_rule_hash = _scoring_rule_hash(self.cfg_dir)
        self.prompt_hash = _prompt_hash(self.cfg_dir)
        # Governance 配置存在但非法：生产启动 FAIL；只有 GOVERNANCE_ENABLED=0 才显式跳过。
        self.gov_engine = None
        gov_enabled = os.getenv("GOVERNANCE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
        if gov_enabled and _GOV_AVAILABLE:
            base = self.cfg_dir or Path(os.getenv("NEWS_SIGNAL_DIR", "config/news_signal"))
            try:
                self.gov_engine = GovernanceEngine(GovernanceConfig(base).load_all())
            except Exception as exc:
                logger.error("Governance 配置非法，启动 FAIL: %s", exc)
                raise RuntimeError("Governance 配置非法；如确需临时停用请显式设置 GOVERNANCE_ENABLED=0") from exc
        elif not gov_enabled:
            logger.warning("Governance 已通过 GOVERNANCE_ENABLED=0 显式禁用")
        self.release_advisor_min_score = 60.0
        if self.release_advisor is not None and getattr(self.release_advisor, "min_score", None):
            self.release_advisor_min_score = float(self.release_advisor.min_score)

    # ------------------------------------------------------------------
    def process_file(self, eml_path: str | Path, reprocess: bool = False) -> Optional[ScreeningRecord]:
        eml_path = Path(eml_path)
        try:
            doc = parse_eml(eml_path)
        except Exception as e:  # noqa: BLE001
            logger.error("EML 解析失败 %s: %s", eml_path, e)
            return ScreeningRecord(email_id=str(eml_path), error=f"EML解析失败: {e}")
        return self._process_or_skip(doc, reprocess=reprocess or self.reprocess)

    def process_document(self, doc: EmailDocument, reprocess: bool = False) -> Optional[ScreeningRecord]:
        return self._process_or_skip(doc, reprocess=reprocess or self.reprocess)

    def _process_or_skip(self, doc: EmailDocument, reprocess: bool = False) -> Optional[ScreeningRecord]:
        # --reprocess/--rescore/--reanalyze 都允许重新生成 analysis_run；
        # parse cache 仍会被复用，避免重复 OCR。
        force = bool(reprocess or self.reprocess or self.rescore or self.reanalyze)
        if self.repo is not None:
            duplicate = self.repo.is_duplicate(doc)
            if duplicate and not force:
                stale = self.repo.analysis_is_stale(
                    doc.email_id, self.political_rule_pack_hash,
                    self.governance_rule_pack_hash, self.scoring_rule_hash, self.prompt_hash)
                if not stale:
                    logger.info("跳过重复且 analysis up-to-date: %s (%s)", doc.subject, doc.email_id)
                    return None
                logger.info("邮件已导入但 analysis stale，自动重新分析: %s", doc.email_id)
            if duplicate and force:
                logger.info("强制重新分析 (reprocess/rescore/reanalyze): %s", doc.email_id)
        return self._process_document(doc)

    # ------------------------------------------------------------------
    def _process_document(self, doc: EmailDocument) -> ScreeningRecord:
        attachments_text: List[str] = []
        for att in doc.attachments:
            cached = (att.metadata or {}).get("cached_path", "")
            if not cached:
                continue
            if att.file_type not in TEXT_FILE_TYPES:
                att.extraction_status = "skipped"
                att.warnings.append("该类型附件不进文本抽取")
                continue
            source_sha = att.source_sha256 or att.sha256 or _sha256_file(Path(cached))
            att.source_sha256 = source_sha
            # Parse Cache key 必须稳定：parser/ocr 版本由当前配置决定，而不是由本次是否触发 OCR 决定。
            att.parser_version = PARSER_VERSION
            att.ocr_version = OCR_VERSION if self.ocr_enabled else "none"
            cache_hit = None
            if self.repo is not None:
                cache_hit = self.repo.lookup_attachment_parse_cache(
                    source_sha, att.parser_version, att.ocr_version)
            try:
                if cache_hit:
                    # 重复附件复用 parse cache：不重复 OCR、不重复解析。
                    att.text = cache_hit.get("text") or ""
                    att.text_sha256 = cache_hit.get("text_sha256") or ""
                    att.content_hash = att.text_sha256
                    att.metadata = {**(att.metadata or {}), **(cache_hit.get("metadata") or {})}
                    att.tables = cache_hit.get("tables") or []
                    att.extraction_status = cache_hit.get("extraction_status") or att.extraction_status
                    logger.info("附件 parse cache 命中: %s", source_sha[:12])
                else:
                    parsed = parse_attachment(cached, att.filename, ocr_enabled=self.ocr_enabled)
                    att.text = parsed.text
                    att.metadata = {**(att.metadata or {}), **(parsed.metadata or {})}
                    att.tables = parsed.tables
                    att.extraction_status = parsed.extraction_status
                    att.warnings = parsed.warnings
                    att.source_sha256 = parsed.source_sha256 or source_sha
                    att.text_sha256 = parsed.text_sha256
                    att.content_hash = parsed.content_hash
                    att.parser_version = PARSER_VERSION
                    att.ocr_version = OCR_VERSION if self.ocr_enabled else "none"
                    att.ocr_used = parsed.ocr_used
                    if self.repo is not None:
                        self.repo.save_attachment_parse_cache(att)
            except Exception as e:  # noqa: BLE001
                logger.warning("附件解析异常 %s: %s", att.filename, e)
                att.extraction_status = "failed"
                att.warnings.append(f"解析异常: {e}")
            if att.text and att.text.strip():
                attachments_text.append(f"\n===== 附件：{att.filename} =====\n{att.text}")
            if att.tables:
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

        rr = self.rule_engine.evaluate(combined)
        self._apply_transactional_cap(doc, rr)
        no_new = self._judge_no_new_evidence(doc, rr)
        if no_new != rr.no_new_evidence:
            rr = self.rule_engine.evaluate(combined, no_new_evidence=no_new)
            self._apply_transactional_cap(doc, rr)

        known = self.known_matcher.match(
            persons=rr.target_persons_found, companies=rr.target_orgs_found)

        # V4 Governance 在 LLM 之前运行，和 V1 一起进入 UnifiedSignalSet。
        gov = None
        if self.gov_engine is not None:
            try:
                gov = self.gov_engine.evaluate(combined, has_attachment=bool(doc.attachments))
            except Exception as exc:
                logger.error("Governance evaluate failed: %s", exc)
                raise
        gov_cats = list(getattr(gov, "categories", []) or [])
        gov_score = float(getattr(gov, "score", 0.0) or 0.0)
        provisional = self.merger.merge(
            rule=rr, llm=None, score=None, governance=gov, known_news=known,
            political_score=rr.rule_score, governance_score=gov_score,
            attachments=doc.attachments, email_id=doc.email_id)

        llm = self._maybe_llm(doc, rr, provisional, gov, known)
        if llm is None:
            llm = self._fallback_llm(doc, rr)

        # Unified final scoring：V1 final + V4 final，不把 Governance 压回 V1。
        fs = self.scorer.score(rr, llm, known_news=known)
        political_final = float(fs.final_score or 0.0)
        final_score, final_priority, fusion_reason = self.unified_scorer.fuse(
            political_final, gov_score, rr.matched_categories, gov_cats,
            fs.priority, str(getattr(gov, "priority", "") or ""))
        fs.final_score = final_score
        fs.priority = final_priority
        fs.governance_score = gov_score
        fs.primary_track = provisional.primary_track
        fs.unified_reason = fusion_reason
        unified = self.merger.merge(
            rule=rr, llm=llm, score=fs, governance=gov, known_news=known,
            political_score=political_final, governance_score=gov_score,
            attachments=doc.attachments, email_id=doc.email_id)
        unified.final_score = fs.final_score
        unified.priority = fs.priority

        rec = ScreeningRecord(email_id=doc.email_id, email=doc, rule=rr, llm=llm,
                              score=fs, known_news=known, unified_signals=unified)
        if gov is not None:
            rec.governance_categories = gov_cats
            rec.governance_keywords = dict(gov.keywords)
            rec.governance_patterns = list(gov.patterns)
            rec.governance_score = gov_score
            rec.governance_priority = str(getattr(gov, "priority", "") or "")
            rec.governance_dims = dict(gov.dims)
            rec.governance_negatives = list(getattr(gov, "negatives", []) or [])
            rec.governance_enhance = dict(getattr(gov, "enhance", {}) or {})
            rec.governance_details = list(getattr(gov, "details", []) or [])

        rec.summary_zh = _summary_for(rec, rr, llm, fs, unified, gov)
        vt = list(llm.verification_targets or [])
        if gov_cats:
            for t in verification_targets_for(gov_cats):
                if t not in vt:
                    vt.append(t)
        if fs.priority in ("S", "A", "B") and not vt:
            vt = llm.verification_targets or self.llm_screener._make_verification_targets(rr, [], [])
        rec.verification_targets = vt
        # Governance 语义同步到 LLM 结果字段（不把 Gxx 混入 political llm.categories，保持双轨独立）。
        if gov_cats:
            if not llm.reason_for_attention:
                llm.reason_for_attention = rec.summary_zh
            if not llm.one_sentence_summary:
                llm.one_sentence_summary = rec.summary_zh
            if not llm.verification_targets:
                llm.verification_targets = list(vt)

        # V2/V3 只在 Governance 成为主轨/Mixed 时携带 governance 语义，避免污染纯政治线索既有路由。
        include_gov = bool(gov_cats) and unified.primary_track in ("GOVERNANCE", "MIXED")
        if self.release_advisor is not None and fs.priority in ("S", "A", "B") \
                and fs.final_score >= self.release_advisor_min_score:
            try:
                rel = self.release_advisor.advise(
                    email_id=doc.email_id, rule=rr, llm=llm, score=fs, doc=doc,
                    governance_categories=gov_cats if include_gov else [],
                    unified=unified)
                rec.release_recommendation = rel.to_dict()
            except Exception as e:  # noqa: BLE001
                logger.error("首发渠道推荐失败 %s: %s", doc.email_id, e)
                rec.release_recommendation = None
        if self.named_advisor is not None and rec.release_recommendation is not None \
                and fs.priority in ("S", "A", "B") \
                and fs.final_score >= self.release_advisor_min_score:
            try:
                named = self.named_advisor.advise(
                    email_id=doc.email_id, rule=rr, llm=llm, score=fs,
                    release_recommendation=rec.release_recommendation, doc=doc,
                    governance_categories=gov_cats if include_gov else [],
                    unified=unified)
                rec.named_channel_recommendation = named.to_dict()
            except Exception as e:  # noqa: BLE001
                logger.error("具名渠道推荐失败 %s: %s", doc.email_id, e)
                rec.named_channel_recommendation = None

        # 持久化：整封邮件一个业务事务，任一 save 抛异常则 rollback。
        if self.repo is not None:
            try:
                with self.repo.db.transaction():
                    run_id = self.repo.start_analysis_run(
                        doc.email_id,
                        pipeline_version="4.1",
                        political_rule_pack_hash=self.political_rule_pack_hash,
                        governance_rule_pack_hash=self.governance_rule_pack_hash,
                        scoring_rule_hash=self.scoring_rule_hash,
                        prompt_hash=self.prompt_hash,
                        llm_mode=self.llm_screener.mode,
                        llm_provider=("template" if self.llm_screener.mode != "api"
                                      else (urlparse(LLM_BASE_URL or "").hostname or "")),
                        llm_model=LLM_MODEL)
                    self.repo.save_email(doc, force=True)
                    if gov is not None:
                        self.repo.save_governance(doc.email_id, gov)
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
                    self.repo.finish_analysis_run(run_id, "ok")
            except Exception as e:  # noqa: BLE001
                logger.error("落库失败并已 rollback %s: %s", doc.email_id, e)
                raise
        return rec

    # ------------------------------------------------------------------
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
            logger.info("个人事务性邮件降权: %s (%s)", doc.subject, _mask_sender(sender))

    def _judge_no_new_evidence(self, doc: EmailDocument, rr: RuleResult) -> bool:
        text = doc.normalized_text
        old_signals = ["去年", "先前", "当年", "当时", "旧闻", "过去", "转发", "转贴", "转载",
                       "曾报道", "曾報導", "旧案", "当年新闻", "旧新闻"]
        news_signals = ["报道", "報導", "新闻", "新聞", "记者", "記者", "媒体", "媒體"]
        has_old = any(s in text for s in old_signals)
        has_news = any(s in text for s in news_signals)
        has_new = bool(rr.money)
        has_new = has_new or bool(rr.matched_keywords.get("E"))
        if rr.no_new_evidence:
            return True
        if has_old and has_news and not has_new:
            return True
        return False

    # ------------------------------------------------------------------
    def _maybe_llm(self, doc: EmailDocument, rr: RuleResult, unified=None,
                   governance=None, known_news=None) -> Optional[LLMResult]:
        """绝对阈值：rule_score < LLM_TRIGGER_SCORE 不得触发 LLM。

        Extra 触发条件只在已达到绝对阈值后作为补充说明，避免 50 分邮件因 pattern
        被送外部模型。
        """
        if rr.rule_score < self.llm_trigger_score:
            return None
        reason = f"rule_score={rr.rule_score}>={self.llm_trigger_score}"
        if RULE_TRIGGER_EXTRA:
            hp = [p for p in rr.matched_patterns if p.pattern_id not in ("P20",) and p.pattern_score >= 70]
            if hp:
                reason += f";高价值Pattern {hp[0].pattern_id}"
            elif rr.matched_keywords.get("E") and (rr.target_persons_found or rr.matched_keywords.get("C")) and rr.money:
                reason += ";E类证据+目标+具体金额"
        logger.info("触发 LLM (%s): %s", reason, doc.subject)
        if not self.allow_llm:
            return None
        if self.llm_screener.mode == "api" and self.llm_screener.client is not None \
                and getattr(self.llm_screener.client, "is_external", False):
            from ..security.payload_builder import SafePayloadBuilder
            policy = getattr(self.llm_screener.client, "policy", None)
            safe = SafePayloadBuilder(policy=policy).build_v1(
                rule=rr, llm=None, score=None, governance=governance,
                unified=unified, known_news=known_news, email_id=doc.email_id)
            return self.llm_screener.screen_safe(safe, rr)
        return self.llm_screener.screen(doc.combined_text, doc.subject, doc.sender, rr)

    def _fallback_llm(self, doc: EmailDocument, rr: RuleResult) -> LLMResult:
        if rr.rule_score < self.llm_trigger_score:
            return LLMResult(llm_status="degraded", relevant=False)
        raw = self.llm_screener._screen_template(doc.combined_text, doc.subject, doc.sender, rr)
        ok, cleaned, errors = __import__("app.llm.schemas", fromlist=["validate_result"]).validate_result(raw)
        llm = LLMResult(llm_status="degraded", relevant=False)
        if ok:
            llm = LLMResult(llm_status="degraded", **cleaned)
        return llm

    # ------------------------------------------------------------------
    def process_directory(self, inbox: str | Path, reprocess: bool = False) -> List[ScreeningRecord]:
        inbox = Path(inbox)
        records = []
        for eml in sorted(inbox.glob("*.eml")):
            rec = self.process_file(eml, reprocess=reprocess)
            if rec is not None:
                records.append(rec)
        return records
