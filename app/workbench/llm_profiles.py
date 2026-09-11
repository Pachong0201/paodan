"""LLM Profiles：本地配置定义，API Key 永远只来自 ENV。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

from ..config import LLM_BASE_URL, LLM_MODEL
from ..security.policy import SecurityPolicy, load_security_policy
from ..security.classifier import DestinationClassifier


class LLMProfileError(RuntimeError):
    pass


@dataclass
class LLMProfile:
    id: str
    name: str = ""
    type: str = "off"                  # off / template / api
    model: str = ""
    model_env: str = ""
    base_url: str = ""
    base_url_env: str = ""

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        if self.model_env:
            if self.model_env == "LLM_MODEL":
                return os.getenv(self.model_env) or LLM_MODEL
            return os.getenv(self.model_env, "")
        return ""

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url
        if self.base_url_env:
            if self.base_url_env == "LLM_BASE_URL":
                return os.getenv(self.base_url_env) or LLM_BASE_URL
            return os.getenv(self.base_url_env, "")
        return ""

    def safe_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "type": self.type,
            "model": self.resolved_model(),
            "base_url_host": _hostname(self.resolved_base_url()),
        }


def _hostname(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        return parsed.hostname or ""
    except Exception:
        return ""


def _credentials_in_url(url: str) -> bool:
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        return bool(parsed.username or parsed.password)
    except Exception:
        return False


def load_llm_profiles(config_path: Path | str | None = None) -> Dict[str, LLMProfile]:
    path = Path(config_path) if config_path else Path(__file__).resolve().parents[2] / "config" / "llm_profiles.yaml"
    if not path.exists():
        return {
            "off": LLMProfile("off", "关闭 AI", "off"),
            "template": LLMProfile("template", "模板模式", "template"),
        }
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    section = data.get("profiles", data) if isinstance(data, dict) else {}
    if not isinstance(section, dict):
        raise LLMProfileError("llm_profiles.yaml profiles 必须是 mapping")
    profiles: Dict[str, LLMProfile] = {}
    for pid, body in section.items():
        if not isinstance(body, dict):
            raise LLMProfileError(f"profile {pid} 必须是 mapping")
        if "api_key" in body:
            raise LLMProfileError(f"profile {pid} 不得包含 api_key")
        p = LLMProfile(
            id=str(pid),
            name=str(body.get("name") or pid),
            type=str(body.get("type") or "off").lower(),
            model=str(body.get("model") or ""),
            model_env=str(body.get("model_env") or ""),
            base_url=str(body.get("base_url") or ""),
            base_url_env=str(body.get("base_url_env") or ""),
        )
        if pid in profiles:
            raise LLMProfileError(f"duplicate profile id: {pid}")
        profiles[p.id] = p
    validate_profiles(profiles)
    return profiles


def validate_profiles(profiles: Dict[str, LLMProfile]) -> None:
    for pid, p in profiles.items():
        if p.type not in ("off", "template", "api"):
            raise LLMProfileError(f"{pid}: invalid type {p.type}")
        if p.type == "api":
            if not (p.model or p.model_env):
                raise LLMProfileError(f"{pid}: API profile requires model or model_env")
            url = p.resolved_base_url()
            if not url:
                raise LLMProfileError(f"{pid}: API profile requires base_url or base_url_env")
            if _credentials_in_url(url):
                raise LLMProfileError(f"{pid}: base_url must not contain credentials")
            host = _hostname(url)
            if not host:
                raise LLMProfileError(f"{pid}: invalid base_url")


def get_profile(profile_id: str, config_path: Path | str | None = None) -> LLMProfile:
    profiles = load_llm_profiles(config_path)
    if profile_id not in profiles:
        raise LLMProfileError(f"unknown profile: {profile_id}")
    return profiles[profile_id]


def profile_status(profile_id: str, policy: Optional[SecurityPolicy] = None) -> Dict[str, Any]:
    """返回 UI 安全状态；不返回 API Key 或 URL 凭据。"""
    p = get_profile(profile_id)
    policy = policy or load_security_policy()
    result = {"id": p.id, "name": p.name, "type": p.type, "available": True, "message": ""}
    if p.type != "api":
        return result
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key:
        result.update(available=False, message="模型未配置 API Key")
        return result
    url = p.resolved_base_url()
    try:
        DestinationClassifier(policy).classify(url)
    except Exception as exc:  # noqa: BLE001
        result.update(available=False, message=f"该模型地址未获安全策略授权: {exc}")
    return result
