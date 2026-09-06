# -*- coding: utf-8 -*-
"""V4 治理民生投诉测试：20 高价值 + 15 困难负样本 + 10 一般 + 5 A+G混合 = 50+，覆盖 G01-G12 与 T01-T12。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import NEWS_SIGNAL_DIR
from app.governance.config_loader import GovernanceConfig
from app.governance.engine import GovernanceEngine


def _eng():
    return GovernanceEngine(GovernanceConfig(NEWS_SIGNAL_DIR).load_all())


def _ev(text, has_att=False):
    return _eng().evaluate(text, has_attachment=has_att)


def _cats(res):
    return res.categories


def _pris(res):
    return res.priority


# ---------- 必测 T01-T12 ----------
def test_T01_subsidy_recover():
    text = "租屋补助已核准6个月，每月8000元，政府后来称审核错误资格认定错误，要求缴回追缴4.8万元，还打7次电话每个承办说法都不同。附件有核准函与追缴函。"
    r = _ev(text, has_att=True)
    assert "G03" in _cats(r), r.categories
    assert "G01" in _cats(r) or any(p.pattern_id == "GP06" for p in r.patterns), (r.categories, [p.pattern_id for p in r.patterns])
    assert any(p.pattern_id == "GP03" for p in r.patterns), [p.pattern_id for p in r.patterns]
    assert r.priority in ("S", "A"), (r.score, r.priority)

def test_T02_single_login_fail():
    text = "政府网站登入失败，验证码收不到，持续2天，我一个人无法线上申请。"
    r = _ev(text)
    assert "G02" in _cats(r), r.categories
    assert r.priority in ("B", "C"), (r.score, r.priority)

def test_T03_group_system_fail():
    text = "同系统错误，几百人线上申请截止前大量无法送件，验证码收不到系统报错，申请截止快到了。"
    r = _ev(text)
    assert "G02" in _cats(r)
    assert r.priority == "A" or r.priority == "S", (r.score, r.priority)

def test_T04_single_gongtuo():
    text = "我家公托候补200号，单一个案排不到。"
    r = _ev(text)
    assert "G05" in _cats(r), r.categories
    assert r.priority in ("B", "C"), (r.score, r.priority)

def test_T05_group_gongtuo():
    text = "同一区公托连续3年名额不足，500户家长排不到，候补几百人，双薪家庭撑不住。"
    r = _ev(text)
    assert "G05" in _cats(r)
    assert r.priority == "A" or r.priority == "S", (r.score, r.priority)

def test_T06_mild_emergency():
    text = "急诊等4小时，检伤第5级，病情较轻依检伤顺序，重症优先可以理解。"
    r = _ev(text)
    assert r.priority in ("C", "D"), (r.score, r.priority)

def test_T07_bed_crisis():
    text = "急诊等床8天，走廊待床，医院满床没有床，多名患者共同受影响，转院困难。"
    r = _ev(text)
    assert "G06" in _cats(r)
    assert r.priority in ("S", "A"), (r.score, r.priority)

def test_T08_power_repeat():
    text = "同一区一月停电5次，3000户停电跳电电压不稳电器烧坏，多次陈情1999还是没改善。"
    r = _ev(text)
    assert "G08" in _cats(r)
    assert r.priority in ("S", "A"), (r.score, r.priority)

def test_T09_trash_10min():
    text = "垃圾车晚10分钟，今天稍微晚到。"
    r = _ev(text)
    assert r.priority in ("C", "D"), (r.score, r.priority)

def test_T10_trash_community():
    text = "连续半年垃圾车时间不固定，整个社区垃圾堆积清运太慢，1999投诉6次还是没改善。"
    r = _ev(text)
    assert "G09" in _cats(r)
    assert r.priority in ("A", "B"), (r.score, r.priority)

def test_T11_police_reject():
    text = "警方拒绝受理，不给报案不给三联单，吃案，诈骗款继续转出钱都被转走了，冻结太慢。"
    r = _ev(text)
    assert "G10" in _cats(r)
    assert r.priority in ("S", "A"), (r.score, r.priority)

def test_T12_pure_emotion():
    text = "政府很烂，烂政府垃圾政府，太扯傻眼气死荒谬。"
    r = _ev(text)
    assert r.priority == "D", (r.score, r.priority)

# ---------- 20 高价值（T01/T03/T05/T07/T08/T10/T11 已占7，再补13） ----------
def test_high_G01_delay_group():
    text = "区公所申请案件超过办理期限3个月仍未处理，多次陈情未回复，整区上百户共同受影响，打1999多次未回复。"
    r = _ev(text)
    assert "G01" in _cats(r) and r.priority in ("S", "A")

def test_high_G02_deadline_group():
    text = "政府网站系统当机无法送出上传失败，几百人申请截止前大量无法送件，持续2天。"
    r = _ev(text)
    assert r.priority in ("S", "A")

def test_high_G04_housing():
    text = "社会住宅抽不到候补几百号一屋难求，租金太高租不起，整区几千户都租不起，申请租补就涨房租。"
    r = _ev(text)
    assert "G04" in _cats(r) and r.priority in ("S", "A", "B")

def test_high_G05_3years():
    text = "非营利幼儿园名额不足候补100号，排一年出生就排，连续3年500户家长受影响。"
    r = _ev(text)
    assert r.priority in ("S", "A")

def test_high_G06_huangniu():
    text = "挂不到号挂号秒杀排队黄牛，多名患者等不到病床，走廊待床医院满床。"
    r = _ev(text)
    assert r.priority in ("S", "A")

def test_high_G07_pedestrian():
    text = "行人地狱没有人行道斑马线不让车不让人，死亡路口危险路口，整区家长学生共同受影响，多次陈情未改善。"
    r = _ev(text)
    assert "G07" in _cats(r) and r.priority in ("S", "A", "B")

def test_high_G08_water():
    text = "停水爆管无水可用复水延迟复水时间一直延，整区上百户连续发生，每年都这样，抢修太慢。"
    r = _ev(text)
    assert r.priority in ("S", "A")

def test_high_G09_pothole():
    text = "路面坑坑洞洞坑洞积水水沟不通排水不良，整个社区长期如此，多年没改善，陈情多次未回复。"
    r = _ev(text)
    assert r.priority in ("S", "A", "B")

def test_high_G10_freeze_slow():
    text = "报案后没消息案件没进度两年没结果，警示账户太慢冻结太慢钱都被转走了，上百人受害。"
    r = _ev(text)
    assert r.priority in ("S", "A")

def test_high_G11_flood():
    text = "豪雨淹水地下室淹水排水不及，预警太晚没人通知撤离太晚，整区几千户受影响，跨局处失灵。"
    r = _ev(text)
    assert "G11" in _cats(r) and r.priority in ("S", "A")

def test_high_G12_policy_gap():
    text = "政策看得到吃不到宣传一套实际一套，中央说可以地方说不行，每个县市标准不同，同样资料不同结果，上百户受影响。"
    r = _ev(text)
    assert "G12" in _cats(r) and r.priority in ("S", "A", "B")

def test_high_G03_recover_loss():
    text = "育儿津贴已领取核准后，政府后来认定错误要求返还追缴，被迫缴回额外支出，损失惨重。"
    r = _ev(text)
    assert r.priority in ("S", "A")

def test_high_G01_bujian():
    text = "反复补件来回补件，公文延宕案件长期未处理，等了几个月石沉大海，找议员还是没改善。"
    r = _ev(text)
    assert any(p.pattern_id in ("GP01", "GP02", "GP06") for p in r.patterns)
    assert r.priority in ("S", "A", "B")

# ---------- 15 困难负样本（应 C/D，不进 S/A） ----------
def test_neg_medical_mild():
    text = "急诊等很久约2小时，但检伤分类重症优先，病情较轻依检伤顺序等待可以理解。"
    assert _ev(text).priority in ("C", "D")

def test_neg_resolved():
    text = "之前停水问题已修复已完成处理，现在供水正常，感谢处理。"
    assert _ev(text).priority in ("C", "D")

def test_neg_normal_procedure():
    text = "招标市场调查依法通知，需合法补件，请依程序办理。"
    assert _ev(text).priority in ("C", "D")

def test_neg_pure_tag():
    text = "垃圾政府太扯傻眼，但没有具体事件时间地点损失证据。"
    assert _ev(text).priority == "D"

def test_neg_single_trash_minor():
    text = "垃圾车今天晚了5分钟，整体正常。"
    assert _ev(text).priority in ("C", "D")

def test_neg_single_bus_minor():
    text = "今天公车班距稍长，等了15分钟，整体还好。"
    assert _ev(text).priority in ("C", "D")

def test_neg_refund_done():
    text = "补助已补发已退款，问题已修复，无其他问题。"
    assert _ev(text).priority in ("C", "D")

def test_neg_light_illness_wait():
    text = "门诊等1小时，病人较多但依顺序看诊，无其他问题。"
    assert _ev(text).priority in ("C", "D")

def test_neg_website_once():
    text = "政府网站有一次验证码稍慢，重试后成功，无损失。"
    assert _ev(text).priority in ("C", "D")

def test_neg_pothole_single_fixed():
    text = "门口一个小坑洞，已通报，问题已修复。"
    assert _ev(text).priority in ("C", "D")

def test_neg_policy_question():
    text = "请问政策配套是什么？想了解申请流程，无具体投诉。"
    assert _ev(text).priority in ("C", "D")

def test_neg_old_news():
    text = "去年停电新闻转载分享，无新增内容。"
    assert _ev(text).priority in ("C", "D")

def test_neg_personal_bill():
    text = "我的电费账单已缴，无投诉。"
    assert _ev(text).priority in ("C", "D")

def test_neg_vague_housing():
    text = "房价有点高，希望关注，无具体事件。"
    assert _ev(text).priority in ("C", "D")

def test_neg_single_light_out():
    text = "一盏路灯不亮，已通报等待处理，单一个案。"
    assert _ev(text).priority in ("C", "D")

# ---------- 10 一般个案（C/B，不进 S/A，保留新闻价值梯度） ----------
def test_general_G01_single():
    text = "我的申请超过办理期限2周仍未处理，承办说再等等。"
    r = _ev(text)
    assert r.priority in ("B", "C")

def test_general_G02_single():
    text = "线上申请上传失败一次，验证码收不到，重试后成功。"
    r = _ev(text)
    assert r.priority in ("B", "C", "D")

def test_general_G04_single():
    text = "租金太高涨租，单一个案房东涨租500元。"
    assert _ev(text).priority in ("B", "C", "D")

def test_general_G05_single2():
    text = "公托排不到候补50号，单一个案等待中。"
    assert _ev(text).priority in ("B", "C")

def test_general_G07_single():
    text = "路口标线看不懂，希望改善，单一个案。"
    assert _ev(text).priority in ("B", "C", "D")

def test_general_G08_single():
    text = "昨晚跳电一次，电压不稳约10分钟后恢复。"
    assert _ev(text).priority in ("B", "C", "D")

def test_general_G09_single():
    text = "水沟不通积水，单户门口积水，已通报。"
    assert _ev(text).priority in ("B", "C", "D")

def test_general_G10_single():
    text = "报案后1周案件没进度，单一个案等待中。"
    assert _ev(text).priority in ("B", "C")

def test_general_G11_single():
    text = "昨日豪雨门口积水约脚踝，已退水。"
    assert _ev(text).priority in ("B", "C", "D")

def test_general_G12_single():
    text = "政策没有配套，基层做不到，单一个案咨询。"
    assert _ev(text).priority in ("B", "C", "D")

# ---------- 5 A+G 混合 ----------
def test_mix_delay_vendor():
    text = "长期陈情无效案件长期未处理超过6个月，疑似特定厂商护航，招标前接触特定厂商，金额80万元，有汇款单。"
    r = _ev(text)
    assert "G01" in _cats(r)
    # A 侧由 RuleEngine 验证
    from app.rules.rule_engine import RuleEngine
    from app.config import NEWS_SIGNAL_DIR as _NSD
    from app.rules.config_loader import RuleConfig
    rr = RuleEngine(RuleConfig(_NSD).load_all()).evaluate(text)
    assert rr.matched_categories, rr.matched_categories

def test_mix_subsidy_fraud():
    text = "补助资格被取消要求缴回追缴，同时发现承办与特定厂商勾结，收贿护航，有银行流水。"
    r = _ev(text)
    assert "G03" in _cats(r)

def test_mix_eatcase_delay():
    text = "警方吃案不立案报案后没消息，同时案件长期未处理多次陈情未回复，疑似包庇特定人士。"
    r = _ev(text)
    assert "G10" in _cats(r) or "G01" in _cats(r)

def test_mix_permit_release():
    text = "建照核定迟迟未核定超过办理期限，反复补件，同时业者送顾问费协调护航，有汇款纪录。"
    r = _ev(text)
    assert "G01" in _cats(r)

def test_mix_shelter_procurement():
    text = "社会住宅抽不到一屋难求，同时标案疑似围标绑标，特定厂商得标，有内部公文。"
    r = _ev(text)
    assert "G04" in _cats(r)

# ---------- 覆盖与结构 ----------
def test_coverage_G01_G12():
    seen = set()
    samples = {
        "G01": "超过办理期限逾期未办案件长期未处理",
        "G02": "网站当机无法登入验证码收不到",
        "G03": "租屋补助资格被取消要求缴回追缴",
        "G04": "高房价社宅抽不到候补几百号",
        "G05": "公托公幼抽不到名额不足候补100号",
        "G06": "急诊塞爆等床走廊待床挂不到号",
        "G07": "行人地狱没有人行道死亡路口脱班",
        "G08": "停水爆管停电跳电无预警停电",
        "G09": "垃圾没人收恶臭路灯不亮坑洞",
        "G10": "拒绝报案不给三联单吃案冻结太慢",
        "G11": "淹水豪雨预警太晚没人通知撤离太晚",
        "G12": "政策看得到吃不到中央说可以地方说不行",
    }
    for gid, txt in samples.items():
        r = _ev(txt + "，具体时间2026年5月，地点台北市，损失严重，附件截图。")
        assert gid in r.categories, (gid, r.categories)
        seen.add(gid)
    assert seen == set(samples.keys())

def test_governance_output_shape():
    r = _ev("租屋补助核准后追回4.8万元，打7次电话说法不同，附件核准函。", has_att=True)
    d = r.to_dict()
    assert "governance_categories" in d and "governance_keywords" in d
    assert "G-H" in d["governance_keywords"] or "G-C" in d["governance_keywords"]
    assert "governance_patterns" in d and "governance_score" in d

def test_pipeline_parallel_no_break_A():
    from app.pipeline.screening_pipeline import ScreeningPipeline
    from app.rules.config_loader import RuleConfig
    from app.llm.screener import LLMScreener
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=None, llm_screener=LLMScreener(cfg, mode="template"), allow_llm=False)
    from app.models import EmailDocument
    doc = EmailDocument(email_id="t1", subject="test", sender="a@b.com", body_text="某建商支付80万元顾问费给议员办公室主任，随后帮助协调建照。附件汇款记录和LINE截图。")
    rec = pipe.process_document(doc)
    assert rec.rule is not None and rec.score is not None
    assert rec.rule.rule_score > 0
    # governance 不应破坏 A
    assert "A03" in (rec.llm.categories or rec.rule.matched_categories) or "A03" in rec.rule.matched_categories or rec.rule.matched_categories

def test_pipeline_governance_priority_upgrade():
    from app.pipeline.screening_pipeline import ScreeningPipeline
    from app.rules.config_loader import RuleConfig
    from app.llm.screener import LLMScreener
    cfg = RuleConfig(NEWS_SIGNAL_DIR).load_all()
    pipe = ScreeningPipeline(cfg, db=None, llm_screener=LLMScreener(cfg, mode="template"), allow_llm=False)
    from app.models import EmailDocument
    doc = EmailDocument(email_id="g1", subject="停电", sender="a@b.com",
                        body_text="同一区一月停电5次3000户停电跳电电器烧坏，多次陈情1999还是没改善。")
    rec = pipe.process_document(doc)
    assert rec.governance_categories and rec.governance_score >= 75
    assert rec.score.priority in ("S", "A")
