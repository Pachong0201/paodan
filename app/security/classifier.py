"""目的地分类：LOCAL / EXTERNAL 只由 URL hostname 决定。"""
from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse

from .models import SecurityBlockedError
from .policy import SecurityPolicy


class Destination(str, Enum):
    LOCAL = "LOCAL"
    EXTERNAL = "EXTERNAL"

    @property
    def is_local(self) -> bool:
        return self == Destination.LOCAL

    @property
    def is_external(self) -> bool:
        return self == Destination.EXTERNAL


def _hostname(url: str) -> str:
    if not url:
        return ""
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate
    try:
        return (urlparse(candidate).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def classify_destination(url: str, policy: SecurityPolicy | None = None) -> Destination:
    """仅根据 hostname 分类；模型名/供应商/API key 不参与。"""
    policy = policy or SecurityPolicy()
    host = _hostname(url)
    if not host:
        return Destination.EXTERNAL
    if policy.is_trusted_local(host):
        return Destination.LOCAL
    return Destination.EXTERNAL


class DestinationClassifier:
    """带 allowlist / https 检查的分类器。"""

    def __init__(self, policy: SecurityPolicy | None = None):
        self.policy = policy or SecurityPolicy()

    def classify(self, url: str) -> Destination:
        parsed = urlparse(url.strip() if "://" in url else "http://" + url.strip())
        host = (parsed.hostname or "").lower()
        scheme = (parsed.scheme or "http").lower()
        if not host:
            raise SecurityBlockedError("URL 无 hostname")
        if self.policy.is_trusted_local(host):
            return Destination.LOCAL
        # 公网：必须 https + allowlist + 全局 enabled
        if not self.policy.external_llm_enabled:
            raise SecurityBlockedError("external_llm disabled by policy")
        if self.policy.https_required and scheme != "https":
            raise SecurityBlockedError(f"external http blocked: {host}")
        if not self.policy.is_allowed_host(host):
            raise SecurityBlockedError(f"external host not in allowlist: {host}")
        return Destination.EXTERNAL

    def is_local(self, url: str) -> bool:
        return classify_destination(url, self.policy) == Destination.LOCAL
