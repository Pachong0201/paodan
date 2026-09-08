# -*- coding: utf-8 -*-
"""Dashboard timezone config gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.dashboard.config import DashboardConfigError, load_dashboard_config


def test_default_timezone(tmp_path, monkeypatch):
    cfg = load_dashboard_config(Path("config"))
    assert cfg.timezone == "Asia/Shanghai"


def test_bad_timezone_rejected(tmp_path):
    p = tmp_path / "dashboard.yaml"
    p.write_text("dashboard:\n  host: '127.0.0.1'\n  timezone: 'Bad/Timezone'\n", encoding="utf-8")
    with pytest.raises(DashboardConfigError):
        load_dashboard_config(tmp_path)
