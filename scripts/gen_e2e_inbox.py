# -*- coding: utf-8 -*-
"""E2E inbox generator: T01-T12 governance fixture emails."""
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path

OUT = Path("data/inbox/e2e_governance_T01_T12")
OUT.mkdir(parents=True, exist_ok=True)

CASES = [
    ("T01_subsidy_recover", "租屋补助已核准6个月，每月8000元，政府后来称审核错误资格认定错误，要求缴回追缴4.8万元，还打7次电话每个承办说法都不同。附件有核准函与追缴函。", True),
    ("T02_single_login_fail", "政府网站登入失败，验证码收不到，持续2天，我一个人无法线上申请。", False),
    ("T03_group_system_fail", "同系统错误，几百人线上申请截止前大量无法送件，验证码收不到系统报错，申请截止快到了。", False),
    ("T04_single_gongtuo", "我家公托候补200号，单一个案排不到。", False),
    ("T05_group_gongtuo", "同一区公托连续3年名额不足，500户家长排不到，候补几百人，双薪家庭撑不住。", False),
    ("T06_mild_emergency", "急诊等4小时，检伤第5级，病情较轻依检伤顺序，重症优先可以理解。", False),
    ("T07_bed_crisis", "急诊等床8天，走廊待床，医院满床没有床，多名患者共同受影响，转院困难。", False),
    ("T08_power_repeat", "同一区一月停电5次，3000户停电跳电电压不稳电器烧坏，多次陈情1999还是没改善。", False),
    ("T09_trash_10min", "垃圾车晚10分钟，今天稍微晚到。", False),
    ("T10_trash_community", "连续半年垃圾车时间不固定，整个社区垃圾堆积清运太慢，1999投诉6次还是没改善。", False),
    ("T11_police_reject", "警方拒绝受理，不给报案不给三联单，吃案，诈骗款继续转出钱都被转走了，冻结太慢。", False),
    ("T12_pure_emotion", "政府很烂，烂政府垃圾政府，太扯傻眼气死荒谬。", False),
]

for name, body, has_att in CASES:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[{name}] 治理民生投诉样本"
    msg["From"] = "citizen@example.com"
    msg["To"] = "1999@city.gov.tw"
    msg["Message-ID"] = f"<e2e-{name}-20260601@citizen.example.com>"
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if has_att:
        att = MIMEText("核准函：租屋补助核准通知书，编号 A-2026-0512。\n"
                       "追缴函：追缴 4.8 万元通知书，编号 B-2026-0530。", "plain", "utf-8")
        att.add_header("Content-Disposition", "attachment",
                       filename=("utf-8", "", "hezhu_zhujiao.txt"))
        msg.attach(att)
    OUT.joinpath(f"{name}.eml").write_bytes(msg.as_bytes())
    print("wrote", name, len(msg.as_bytes()), "bytes")

print("done:", OUT)