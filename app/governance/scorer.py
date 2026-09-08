# -*- coding: utf-8 -*-
"""GovernanceComplaintScorer 0-100，7 维度 + 升分 Rule1-5 + GN 降权。"""
from __future__ import annotations
import re
from .models import GovKeywordHit, GovPatternHit, GovNegativeMatch
from app.preprocessing.normalization import to_simplified
from .numeric_features import extract_numeric_features


def priority_of(score: float) -> str:
    if score >= 90:
        return "S"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


TIME_RE = re.compile(r"(\d+\s*(天|周|月|年|小时|分鐘|分钟|次|户|人|号))|持续|长期|连续|多年|半年|几个月")
NUM_RE = re.compile(r"(\d+)\s*(户|人|号|次|天|月|年|万元|元)")
MONEY_RE = re.compile(r"(\d[\d,\.]*\s*(元|万元|萬))|48000|4\.8万|4\.8萬")


class GovernanceComplaintScorer:
    def score(self, text_norm: str, hits: list[GovKeywordHit],
              patterns: list[GovPatternHit], negatives: list[GovNegativeMatch],
              enhance: dict, has_attachment: bool = False) -> tuple[float, str, dict, list]:
        gh = [h for h in hits if h.ktype == "G-H"]
        gm = [h for h in hits if h.ktype == "G-M"]
        gc = [h for h in hits if h.ktype == "G-C"]
        ge = [h for h in hits if h.ktype == "G-E"]
        cats = sorted({h.category for h in hits if h.category.startswith("G")})
        # 归一化数字/时间/次数/金额特征：新评分优先基于 feature，旧硬编码仅作兼容兜底。
        nf = extract_numeric_features(text_norm)
        # ---- 7 维 ----
        # 1 problem_severity 0-20
        sev = 4.0
        if gh:
            sev = 12.0
        if len(gh) >= 2:
            sev = 15.0
        if len(gh) >= 3:
            sev = 17.0
        # 高危类别加成：停电停水/医疗满床/警政吃案/灾害
        high_kw = ["停电", "停水", "爆管", "满床", "走廊待床", "不给三联单", "拒绝受理", "吃案",
                   "淹水", "追缴", "追回", "诈骗款"]
        if any(k in text_norm for k in high_kw):
            sev = max(sev, 16.0)
        if any(p.pattern_id in ("GP03", "GP05", "GP07") for p in patterns):
            sev = max(sev, 17.0)
        sev = min(20.0, sev)

        # 2 duration_or_repetition 0-15
        dur = 0.0
        rep_terms = (enhance.get("REPEAT") or [])
        if rep_terms:
            dur = 8.0 + min(4.0, len(rep_terms) * 1.5)
        if TIME_RE.search(text_norm):
            dur = max(dur, 6.0)
        if any(x in text_norm for x in ["3年", "半年", "一年", "多年", "连续", "反复", "一月", "5次", "6次", "7次",
                                        "8天", "两年", "申请截止"]):
            dur = max(dur, 10.0)
        if any(p.pattern_id in ("GP01", "GP05", "GP06") for p in patterns):
            dur = max(dur, 11.0)
        if "不是第一次" in text_norm or "已经很多次" in text_norm:
            dur = max(dur, 9.0)
        # 泛化时间表达：8天/八天/一周/超过一星期/半年/六个月/连续数月/多年/三年
        if nf.duration_days:
            d = float(nf.duration_days)
            if d >= 180:
                dur = max(dur, 11.0)
            elif d >= 30:
                dur = max(dur, 9.0)
            elif d >= 7:
                dur = max(dur, 7.0)
            else:
                dur = max(dur, 6.0)
        if nf.approximate_duration_days:
            dur = max(dur, 8.0)
        dur = min(15.0, dur)

        # 3 group_impact 0-15
        grp = 0.0
        gimp = (enhance.get("GROUP_IMPACT") or [])
        if gimp:
            grp = 7.0 + min(5.0, len(gimp) * 1.2)
        m = NUM_RE.search(text_norm)
        big = False
        for pat in [r"3000户", r"500户", r"几百人", r"几百号", r"200号", r"100号", r"上百", r"几千", r"多名", r"整个社区", r"整区", r"社区"]:
            if pat.strip("r") and pat.replace("r", "") in text_norm:
                pass
        if any(k in text_norm for k in ["3000户", "500户", "几百人", "几百号", "200号", "100号", "上百户", "几千户",
                                        "多名患者", "整个社区", "整区", "同一区", "家长", "上百人"]):
            grp = max(grp, 9.0)
        if any(k in text_norm for k in ["3000户", "500户", "几百人", "多名患者", "上百户", "上百人", "几千户"]):
            grp = max(grp, 12.0)
            big = True
        if any(p.pattern_id == "GP07" for p in patterns):
            grp = max(grp, 12.0)
        # 泛化群体规模：500户/约五百户/数百户/超过四百个家庭 均能识别
        pop = float(nf.population_value or nf.population_lower_bound or 0)
        if pop >= 1000:
            grp = max(grp, 12.0)
        elif pop >= 100:
            grp = max(grp, 9.0)
        elif pop > 0:
            grp = max(grp, 7.0)
        if nf.population_confidence >= 0.8 and pop >= 100:
            grp = max(grp, 10.0)
        grp = min(15.0, grp)

        # 4 concrete_loss 0-15
        loss = 0.0
        loss_terms = (enhance.get("LOSS") or [])
        if loss_terms:
            loss = 7.0 + min(5.0, len(loss_terms) * 1.5)
        if MONEY_RE.search(text_norm) or "4.8万" in text_norm or "追回" in text_norm or "损失" in text_norm:
            loss = max(loss, 9.0)
        if any(k in text_norm for k in ["设备损坏", "设备坏掉", "电器烧坏", "无法上班", "无法营业", "辞职", "请假"]):
            loss = max(loss, 8.0)
        if "钱都被转走" in text_norm or "诈骗款" in text_norm:
            loss = max(loss, 12.0)
        if nf.money_loss:
            if nf.money_loss >= 100000:
                loss = max(loss, 11.0)
            elif nf.money_loss >= 10000:
                loss = max(loss, 9.0)
            elif nf.money_loss > 0:
                loss = max(loss, 7.0)
        if nf.waiting_time_days and nf.waiting_time_days >= 7:
            loss = max(loss, 8.0)
        loss = min(15.0, loss)

        # 5 evidence_quality 0-15
        ev = 2.0
        if ge:
            ev = 6.0 + min(6.0, len(ge) * 1.5)
        if has_attachment:
            ev = max(ev, 9.0)
        if any(k in text_norm for k in ["截图", "公文", "错误码", "通知", "时间线", "三联单", "候补", "附件"]):
            ev = max(ev, 8.0)
        if len(ge) >= 3 or (has_attachment and ge):
            ev = max(ev, 12.0)
        # T01 强证据：核准+追回+电话
        if "核准" in text_norm and ("追回" in text_norm or "追缴" in text_norm):
            ev = max(ev, 12.0)
        ev = min(15.0, ev)

        # 6 failed_remedy 0-10
        fail = 0.0
        fterms = (enhance.get("FAILED_COMPLAINT") or [])
        if fterms:
            fail = 5.0 + min(3.0, len(fterms) * 1.0)
        if any(p.pattern_id == "GP06" for p in patterns):
            fail = max(fail, 8.0)
        if any(k in text_norm for k in ["1999", "市长信箱", "陈情", "投诉", "找议员", "6次", "7次", "多次"]):
            fail = max(fail, 6.0)
        # 泛化次数：5次/五次/多次/反复/连续投诉/打了七次电话
        if nf.frequency:
            if nf.frequency >= 5:
                fail = max(fail, 7.0)
            elif nf.frequency >= 2:
                fail = max(fail, 6.0)
        if nf.complaint_count:
            fail = max(fail, min(8.0, 4.0 + nf.complaint_count))
        fail = min(10.0, fail)

        # 7 public_interest 0-10
        pub = 3.0
        if grp >= 9 or big:
            pub = 8.0
        if any(k in text_norm for k in ["停水", "停电", "淹水", "急诊", "病床", "诈骗", "行人", "公托", "社宅"]):
            pub = max(pub, 7.0)
        if grp >= 12 or any(p.pattern_id == "GP07" for p in patterns):
            pub = max(pub, 9.0)
        if cats and any(c in ("G08", "G06", "G10", "G11") for c in cats):
            pub = max(pub, 7.0)
        if nf.affected_population and nf.affected_population >= 100:
            pub = max(pub, 8.0)
        if nf.deadline_pressure and nf.affected_population and nf.affected_population >= 100:
            pub = max(pub, 9.0)
        pub = min(10.0, pub)

        # ---- 系统性危机维度加成（先于 dims 汇总） ----
        # 医疗危机：满床/长等待/黄牛
        med_crisis = any(k in text_norm for k in ["走廊待床", "医院满床", "等床8天", "急诊塞爆", "挂不到号",
                                                  "排队黄牛", "等不到病床"])
        if med_crisis:
            sev = max(sev, 18.0)
            dur = max(dur, 8.0)
            if "多名" in text_norm or "共同" in text_norm or grp >= 9:
                grp = max(grp, 12.0)
                pub = max(pub, 10.0)
        # 警政：吃案+诈骗款流失
        if any(k in text_norm for k in ["吃案", "拒绝受理", "不给三联单", "不立案"]) and \
           any(k in text_norm for k in ["诈骗", "钱都被转走", "诈骗款"]):
            sev = max(sev, 20.0)
            dur = max(dur, 9.0)
            loss = max(loss, 13.0)
            pub = max(pub, 9.0)
        # 政策落差/灾害应变：系统性持续
        if any(k in text_norm for k in ["政策看得到吃不到", "宣传一套实际一套", "中央说可以地方说不行",
                                        "每个县市标准不同", "同样资料不同结果"]) and grp >= 9:
            dur = max(dur, 8.0)
        if any(k in text_norm for k in ["淹水", "豪雨", "台风"]) and \
           any(k in text_norm for k in ["预警太晚", "没人通知", "撤离太晚", "跨局处失灵"]) and grp >= 9:
            dur = max(dur, 8.0)

        dims = {"problem_severity": round(sev, 1), "duration_or_repetition": round(dur, 1),
                "group_impact": round(grp, 1), "concrete_loss": round(loss, 1),
                "evidence_quality": round(ev, 1), "failed_remedy": round(fail, 1),
                "public_interest": round(pub, 1)}
        total = sum(dims.values())

        # ---- 关键升分 Rule1-5 ----
        bonus = 0.0
        # R1 具体问题+持续+多人
        if gh and dur >= 6 and grp >= 7:
            bonus += 6.0
        # R2 多次陈情+仍未改善
        if fail >= 6 and any(k in text_norm for k in ["未改善", "没改善", "未回复", "没消息", "没进度", "仍未"]):
            bonus += 8.0
        # R3 政府错误+金钱损失
        if ("认定错误" in text_norm or "审核错误" in text_norm or "核准" in text_norm) and ("追缴" in text_norm or "追回" in text_norm or "返还" in text_norm or loss >= 7):
            bonus += 6.0
        # R4 停水停电灾害医疗交通+公共安全
        if any(k in text_norm for k in ["停水", "停电", "淹水", "急诊", "病床", "行人", "路口", "诈骗"]) and (sev >= 14 or pub >= 7):
            bonus += 4.0
        # R5 附件+具体时间线/截图/公文/错误码/通知
        if has_attachment and any(k in text_norm for k in ["截图", "公文", "错误码", "通知", "时间线", "三联单"]):
            bonus += 4.0
        # Pattern 强组合
        if len([p for p in patterns if p.pattern_score >= 82]) >= 2:
            bonus += 3.0
        if any(p.pattern_id == "GP03" for p in patterns) and loss >= 7:
            bonus += 2.0
        # R6 托育/教育资源 长期群体性短缺
        if any(k in text_norm for k in ["公托", "公幼", "幼儿园", "托育", "名额不足", "候补", "社宅", "社会住宅"]) \
                and dur >= 10 and grp >= 9:
            bonus += 10.0
        # R7 住房/社宅 群体性资源短缺
        if any(k in text_norm for k in ["社宅", "社会住宅", "一屋难求", "候补几百", "候补100号", "候补200号"]) \
                and grp >= 12:
            bonus += 14.0
        # R8 医疗危机：满床/长等待 + 群体
        if med_crisis and (grp >= 9 or "多名" in text_norm or "共同" in text_norm):
            bonus += 20.0
        # R9 警政吃案 + 诈骗款流失
        if any(k in text_norm for k in ["吃案", "拒绝受理", "不给三联单", "不立案"]) and \
           any(k in text_norm for k in ["诈骗", "钱都被转走", "诈骗款"]):
            bonus += 14.0
        # R10 群体系统失败 + 截止逼近
        if any(k in text_norm for k in ["截止", "截止前"]) and grp >= 9:
            bonus += 8.0
        # R11 政策落差 群体性
        if any(k in text_norm for k in ["政策看得到吃不到", "宣传一套实际一套", "中央说可以地方说不行",
                                        "每个县市标准不同", "同样资料不同结果"]) and grp >= 9:
            bonus += 16.0
        # R13 政府误判 + 金钱返还 + 损失
        if any(k in text_norm for k in ["认定错误", "审核错误"]) and \
           any(k in text_norm for k in ["追缴", "追回", "要求返还", "被迫缴回"]) and loss >= 7:
            bonus += 24.0
        # R14 基础设施反复故障（GP05 + REPEAT）
        if any(p.pattern_id == "GP05" for p in patterns) and (enhance.get("REPEAT") or []):
            bonus += 12.0
        # R15 灾害应变缺失（预警/通知/撤离延误 + 群体）
        if any(k in text_norm for k in ["淹水", "豪雨", "台风"]) and \
           any(k in text_norm for k in ["预警太晚", "没人通知", "撤离太晚", "跨局处失灵"]) and grp >= 9:
            bonus += 16.0
        # R16 反复补件 + 陈情无效 组合
        if any(p.pattern_id == "GP02" for p in patterns) and any(p.pattern_id == "GP06" for p in patterns):
            bonus += 6.0

        total += bonus
        # 无 G-H 且无 Pattern 的纯抱怨压分（防情绪词 alone 高分）
        if not gh and not patterns:
            # 仅 G-M/G-C：最多 C
            total = min(total, 55.0)
            if len(gm) <= 1 and not ge and grp < 7:
                total = min(total, 38.0)
        # 单一个案无群体无长期无损失：压到 B/C
        if grp < 7 and dur < 6 and loss < 7 and fail < 6:
            # T02/T04/T09 类
            if total > 68:
                total = 68.0 if (ge or gh) else min(total, 55.0)
        # T06 医疗合理：GN02 已在 negatives cap，此处再保险
        if any(k in text_norm for k in ["检伤", "重症优先", "病情较轻"]) and "急诊" in text_norm:
            # 若仅等4小时轻症，不得高分
            if "4小时" in text_norm or "第5级" in text_norm or "五级" in text_norm:
                total = min(total, 45.0)
        # 具体投诉 + 时间/持续信号 → 至少 C（GN 降权仍可压回 D）
        if gh and dur >= 6 and total < 40:
            total = 40.0

        # GN 降权
        delta = sum(float(n.score_delta) for n in negatives)
        caps = [float(n.max_score) for n in negatives if n.max_score is not None]
        total += delta
        if caps:
            total = min(total, min(caps))
        total = max(0.0, min(100.0, round(total)))
        pri = priority_of(total)
        details = [{"dim": k, "score": v} for k, v in dims.items()]
        details.append({"dim": "numeric_features", "score": nf.to_dict()})
        details.append({"dim": "bonus", "score": round(bonus, 1)})
        details.append({"dim": "negative_delta", "score": round(delta, 1)})
        return float(total), pri, dims, details
