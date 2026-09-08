"""SecurityPolicy：config/security.yaml + 环境变量，默认生产安全等级 STRUCTURED_ONLY。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .models import PrivacyLevel, SecurityError

DEFAULT_ALLOWED_HOSTS = ["api.openai.com"]
DEFAULT_TRUSTED_LOCAL_HOSTS = ["localhost", "127.0.0.1", "::1"]
DEFAULT_AUDIT_PATH = "logs/security_audit.jsonl"


@dataclass
class SecurityPolicy:
    external_llm_enabled: bool = True
    privacy_level: str = PrivacyLevel.STRUCTURED_ONLY.value
    allowed_hosts: List[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS))
    trusted_local_hosts: List[str] = field(default_factory=lambda: list(DEFAULT_TRUSTED_LOCAL_HOSTS))
    https_required: bool = True
    allow_safe_snippets: bool = False
    max_payload_chars: int = 12000
    fail_closed: bool = True
    audit_enabled: bool = True
    audit_log_path: str = DEFAULT_AUDIT_PATH
    source_file: str = ""

    def __post_init__(self):
        self.privacy_level = PrivacyLevel.parse(self.privacy_level).value
        self.allowed_hosts = [str(h).strip().lower() for h in (self.allowed_hosts or []) if str(h).strip()]
        self.trusted_local_hosts = [str(h).strip().lower() for h in
                                    (self.trusted_local_hosts or []) if str(h).strip()]
        try:
            self.max_payload_chars = int(self.max_payload_chars)
        except (TypeError, ValueError):
            self.max_payload_chars = 12000
        if self.max_payload_chars < 1:
            self.max_payload_chars = 12000

    @property
    def privacy(self) -> PrivacyLevel:
        return PrivacyLevel.parse(self.privacy_level)

    @property
    def structured_only(self) -> bool:
        return self.privacy == PrivacyLevel.STRUCTURED_ONLY

    def is_allowed_host(self, hostname: str) -> bool:
        h = (hostname or "").strip().lower()
        if not h:
            return False
        if h in self.allowed_hosts:
            return True
        # 支持精确 hostname，不做通配（避免 allowlist 被 *.evil 绕过）
        return False

    def is_trusted_local(self, hostname: str) -> bool:
        h = (hostname or "").strip().lower()
        return h in {x.lower() for x in self.trusted_local_hosts}

    def validate(self) -> None:
        if self.privacy_level not in {p.value for p in PrivacyLevel}:
            raise SecurityError(f"invalid privacy_level: {self.privacy_level}")
        if self.external_llm_enabled and not self.allowed_hosts:
            raise SecurityError("external_llm enabled but allowed_hosts is empty")
        if self.allow_safe_snippets and self.privacy != PrivacyLevel.REDACTED_SNIPPETS:
            # 不抛错：允许配置，但运行时只会在 REDACTED_SNIPPETS 下开启 snippets
            pass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.external_llm_enabled,
            "privacy_level": self.privacy_level,
            "allowed_hosts": list(self.allowed_hosts),
            "trusted_local_hosts": list(self.trusted_local_hosts),
            "https_required": self.https_required,
            "allow_safe_snippets": self.allow_safe_snippets,
            "max_payload_chars": self.max_payload_chars,
            "fail_closed": self.fail_closed,
            "audit_enabled": self.audit_enabled,
            "audit_log_path": self.audit_log_path,
        }


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _read_yaml(path: Optional[Path]) -> Dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise SecurityError(f"security.yaml 无法读取: {exc}") from exc
    if not isinstance(data, dict):
        raise SecurityError("security.yaml 顶层必须是 mapping")
    section = data.get("security", data)
    if not isinstance(section, dict):
        raise SecurityError("security.yaml security 节点必须是 mapping")
    ext = section.get("external_llm", {}) or {}
    audit = section.get("audit", {}) or {}
    if not isinstance(ext, dict) or not isinstance(audit, dict):
        raise SecurityError("security.yaml external_llm/audit 必须是 mapping")
    return {"external_llm": ext, "audit": audit}


def _env_list(name: str) -> Optional[List[str]]:
    raw = os.getenv(name)
    if raw is None:
        return None
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def load_security_policy(config_dir: Optional[Path | str] = None,
                         config_file: Optional[Path | str] = None,
                         cli_overrides: Optional[Dict[str, Any]] = None) -> SecurityPolicy:
    """优先级：代码默认 < YAML < ENV < CLI（不允许 ALLOW_RAW_EXTERNAL_LLM 绕过）。"""
    if config_file is not None:
        candidates = [Path(config_file)]
    else:
        base = Path(config_dir) if config_dir else Path(__file__).resolve().parents[2] / "config"
        candidates = [base / "security.yaml"]
        if base.name == "news_signal":
            candidates.append(base.parent / "security.yaml")
    yaml_path = next((p for p in candidates if p.exists()), candidates[0])
    y = _read_yaml(yaml_path)
    ext = y.get("external_llm", {})
    audit = y.get("audit", {})

    policy = SecurityPolicy(
        external_llm_enabled=_as_bool(ext.get("enabled"), True),
        privacy_level=str(ext.get("privacy_level") or PrivacyLevel.STRUCTURED_ONLY.value),
        allowed_hosts=list(ext.get("allowed_hosts") or DEFAULT_ALLOWED_HOSTS),
        trusted_local_hosts=list(ext.get("trusted_local_hosts") or DEFAULT_TRUSTED_LOCAL_HOSTS),
        https_required=_as_bool(ext.get("https_required"), True),
        allow_safe_snippets=_as_bool(ext.get("allow_safe_snippets"), False),
        max_payload_chars=int(ext.get("max_payload_chars") or 12000),
        fail_closed=_as_bool(ext.get("fail_closed"), True),
        audit_enabled=_as_bool(audit.get("enabled"), True),
        audit_log_path=str(audit.get("log_path") or DEFAULT_AUDIT_PATH),
        source_file=str(yaml_path),
    )

    # ENV > YAML
    if os.getenv("EXTERNAL_LLM_ENABLED") is not None:
        policy.external_llm_enabled = _as_bool(os.getenv("EXTERNAL_LLM_ENABLED"), policy.external_llm_enabled)
    if os.getenv("SECURITY_PRIVACY_LEVEL") is not None:
        policy.privacy_level = PrivacyLevel.parse(os.getenv("SECURITY_PRIVACY_LEVEL")).value
    env_allowed = _env_list("EXTERNAL_LLM_ALLOWED_HOSTS")
    if env_allowed is not None:
        policy.allowed_hosts = env_allowed
    env_local = _env_list("EXTERNAL_LLM_TRUSTED_LOCAL_HOSTS")
    if env_local is not None:
        policy.trusted_local_hosts = env_local
    if os.getenv("EXTERNAL_LLM_HTTPS_REQUIRED") is not None:
        policy.https_required = _as_bool(os.getenv("EXTERNAL_LLM_HTTPS_REQUIRED"), policy.https_required)
    if os.getenv("EXTERNAL_LLM_ALLOW_SAFE_SNIPPETS") is not None:
        policy.allow_safe_snippets = _as_bool(os.getenv("EXTERNAL_LLM_ALLOW_SAFE_SNIPPETS"),
                                              policy.allow_safe_snippets)
    if os.getenv("EXTERNAL_LLM_MAX_PAYLOAD_CHARS") is not None:
        try:
            policy.max_payload_chars = int(os.getenv("EXTERNAL_LLM_MAX_PAYLOAD_CHARS"))
        except ValueError:
            pass
    if os.getenv("EXTERNAL_LLM_FAIL_CLOSED") is not None:
        policy.fail_closed = _as_bool(os.getenv("EXTERNAL_LLM_FAIL_CLOSED"), policy.fail_closed)
    if os.getenv("SECURITY_AUDIT_ENABLED") is not None:
        policy.audit_enabled = _as_bool(os.getenv("SECURITY_AUDIT_ENABLED"), policy.audit_enabled)
    if os.getenv("SECURITY_AUDIT_LOG_PATH") is not None:
        policy.audit_log_path = str(os.getenv("SECURITY_AUDIT_LOG_PATH"))

    # CLI 覆盖层：最后应用，优先级最高。
    if cli_overrides:
        alias = {
            "enabled": "external_llm_enabled", "external_llm_enabled": "external_llm_enabled",
            "privacy_level": "privacy_level", "allowed_hosts": "allowed_hosts",
            "trusted_local_hosts": "trusted_local_hosts", "https_required": "https_required",
            "allow_safe_snippets": "allow_safe_snippets",
            "max_payload_chars": "max_payload_chars", "fail_closed": "fail_closed",
            "audit_enabled": "audit_enabled", "audit_log_path": "audit_log_path",
        }
        for key, value in cli_overrides.items():
            attr = alias.get(str(key))
            if attr and value is not None:
                if attr == "privacy_level":
                    value = PrivacyLevel.parse(value).value
                elif attr in ("allowed_hosts", "trusted_local_hosts") and isinstance(value, str):
                    value = [x.strip().lower() for x in value.split(",") if x.strip()]
                elif attr in ("external_llm_enabled", "https_required", "allow_safe_snippets",
                              "fail_closed", "audit_enabled"):
                    value = _as_bool(value, bool(getattr(policy, attr)))
                elif attr == "max_payload_chars":
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        continue
                setattr(policy, attr, value)

    # H6：本版本禁止任何一键 raw external 绕过。环境变量存在也只记录，绝不启用。
    if os.getenv("ALLOW_RAW_EXTERNAL_LLM"):
        # 不改变策略；由 OutboundGuard/LLMClient 硬拒绝 raw external。
        pass
    policy.validate()
    return policy


def apply_cli_overrides(policy: SecurityPolicy, **overrides) -> SecurityPolicy:
    """CLI 覆盖层：仅覆盖明确传入的字段。"""
    clean = {k: v for k, v in overrides.items() if v is not None}
    return replace(policy, **clean)
