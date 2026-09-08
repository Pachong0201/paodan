# -*- coding: utf-8 -*-
"""GovernanceNumericFeatures 泛化表达归一化测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.governance.numeric_features import extract_numeric_features


def test_population_generalization():
    for text in ("500户", "约五百户", "数百户", "超过400户", "超过四百个家庭"):
        f = extract_numeric_features(text)
        assert f.population_value and f.population_value >= 300, (text, f.to_dict())
        assert f.population_lower_bound and f.population_lower_bound >= 300
        assert f.population_confidence >= 0.7


def test_duration_generalization():
    cases = {"8天": 8, "八天": 8, "一周": 7, "半年": 180, "六个月": 180, "三年": 1095}
    for text, expected in cases.items():
        f = extract_numeric_features(text)
        assert f.duration_days == expected, (text, f.to_dict())
    assert extract_numeric_features("超过一星期").approximate_duration_days
    assert extract_numeric_features("连续数月").approximate_duration_days
    assert extract_numeric_features("多年").approximate_duration_days


def test_frequency_and_money_generalization():
    for text, expected in (("5次", 5), ("五次", 5), ("多次", 3), ("反复", 3),
                           ("打了七次电话", 7)):
        f = extract_numeric_features(text)
        assert f.frequency == expected, (text, f.to_dict())
    f = extract_numeric_features("损失4.8万元")
    assert f.money_loss == 48000
    f2 = extract_numeric_features("等床8天")
    assert f2.waiting_time_days == 8
