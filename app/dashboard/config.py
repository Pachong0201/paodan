"""Dashboard 配置：YAML < ENV；V4.2 MVP 只允许 localhost 绑定。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"

REVIEW_STATUSES = [
    "UNREVIEWED", "VERIFY", "PRIORITY", "VERIFIED",
    "LOW_VALUE", "FALSE_POSITIVE", "ARCHIVED",
]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class DashboardConfigError(RuntimeError):
    pass


def _as_bool(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off", "")


@dataclass
class DashboardConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    page_size: int = 30
    max_page_size: int = 100
    default_days: int = 7
    show_raw_body: bool = False
    show_sender_email: bool = False
    allow_open_local_file: bool = False
    review_statuses: List[str] = field(default_factory=lambda: list(REVIEW_STATUSES))
    source_file: str = ""

    def validate(self) -> None:
        if self.host not in LOCAL_HOSTS:
            raise DashboardConfigError(
                "V4.2 Dashboard 仅支持 localhost 绑定；拒绝 host=%s" % self.host)
        if self.page_size < 1:
            self.page_size = 30
        if self.max_page_size < self.page_size:
            self.max_page_size = self.page_size

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "page_size": self.page_size,
            "max_page_size": self.max_page_size,
            "default_days": self.default_days,
            "review_statuses": list(self.review_statuses),
        }


def load_dashboard_config(config_dir: Path | str | None = None) -> DashboardConfig:
    base = Path(config_dir) if config_dir else CONFIG_DIR
    cfg = DashboardConfig()
    yaml_path = base / "dashboard.yaml"
    data = {}
    if yaml_path.exists():
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    section = data.get("dashboard", data) if isinstance(data, dict) else {}

    cfg.enabled = _as_bool(section.get("enabled"), True)
    if section.get("host"):
        cfg.host = str(section["host"])
    if section.get("port"):
        try:
            cfg.port = int(section["port"])
        except (TypeError, ValueError):
            pass
    if section.get("page_size"):
        try:
            cfg.page_size = int(section["page_size"])
        except (TypeError, ValueError):
            pass
    if section.get("max_page_size"):
        try:
            cfg.max_page_size = int(section["max_page_size"])
        except (TypeError, ValueError):
            pass
    if section.get("default_days") is not None:
        try:
            cfg.default_days = int(section["default_days"])
        except (TypeError, ValueError):
            pass
    cfg.show_raw_body = _as_bool(section.get("show_raw_body"), False)
    cfg.show_sender_email = _as_bool(section.get("show_sender_email"), False)
    cfg.allow_open_local_file = _as_bool(section.get("allow_open_local_file"), False)
    if section.get("review_statuses"):
        cfg.review_statuses = [str(x).strip().upper() for x in section["review_statuses"] if str(x).strip()]
    cfg.source_file = str(yaml_path)

    # ENV 覆盖 YAML
    if os.getenv("DASHBOARD_ENABLED") is not None:
        cfg.enabled = _as_bool(os.getenv("DASHBOARD_ENABLED"), cfg.enabled)
    if os.getenv("DASHBOARD_HOST") is not None:
        cfg.host = os.getenv("DASHBOARD_HOST", cfg.host)
    if os.getenv("DASHBOARD_PORT") is not None:
        try:
            cfg.port = int(os.getenv("DASHBOARD_PORT"))
        except ValueError:
            pass
    if os.getenv("DASHBOARD_PAGE_SIZE") is not None:
        try:
            cfg.page_size = int(os.getenv("DASHBOARD_PAGE_SIZE"))
        except ValueError:
            pass
    if os.getenv("DASHBOARD_DEFAULT_DAYS") is not None:
        try:
            cfg.default_days = int(os.getenv("DASHBOARD_DEFAULT_DAYS"))
        except ValueError:
            pass
    cfg.validate()
    return cfg
