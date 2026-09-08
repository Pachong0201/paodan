"""统一 LLM Client：External 调用强制经过 Privacy Gateway；LOCAL 允许 raw。"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from ..security.audit import SecurityAuditLogger
from ..security.classifier import Destination, DestinationClassifier
from ..security.models import SafeLLMPayload, SecurityBlockedError
from ..security.outbound_guard import OutboundGuard
from ..security.payload_builder import ensure_safe_payload
from ..security.policy import SecurityPolicy, load_security_policy

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    """OpenAI-compatible chat completions 客户端。

    安全契约：
    - ``chat_json`` 在 EXTERNAL 目的地一律拒绝 raw string，绝不发出网络请求；
    - External 只能通过 ``chat_safe(system_prompt, SafeLLMPayload)`` 发送；
    - LOCAL（localhost/127.0.0.1/::1）可保留较完整文本模式。
    """

    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: int = 60, max_tokens: int = 4000,
                 policy: Optional[SecurityPolicy] = None):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = model or "gpt-4o-mini"
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.policy = policy or load_security_policy()
        self.guard = OutboundGuard(self.policy)
        self.audit = SecurityAuditLogger(self.policy)
        self.destination = Destination.EXTERNAL
        self._destination_error = ""
        try:
            self.destination = DestinationClassifier(self.policy).classify(self.base_url)
        except SecurityBlockedError as exc:
            self.destination = Destination.EXTERNAL
            self._destination_error = str(exc)
        # LOCAL（localhost/127.0.0.1/::1）允许无 key 的本地推理服务；EXTERNAL 仍必须 key。
        if not self.api_key and self.is_external:
            raise LLMError("未配置 LLM_API_KEY")

    # ------------------------------------------------------------------
    @property
    def is_external(self) -> bool:
        return self.destination == Destination.EXTERNAL

    @property
    def provider_host(self) -> str:
        try:
            return (urlparse(self._endpoint()).hostname or "").lower()
        except Exception:  # noqa: BLE001
            return ""

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _build_body(self, system_prompt: str, user_content: str,
                    temperature: float) -> dict:
        return {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # ------------------------------------------------------------------
    def chat_json(self, system_prompt: str, user_content: str,
                  temperature: float = 0.1, retries: int = 1) -> dict:
        """LOCAL raw 调用；EXTERNAL raw 一律 BLOCK（network call = 0）。"""
        if self.is_external:
            self.audit.log(
                event="external_raw_blocked", purpose="raw_chat_json",
                provider_host=self.provider_host, model=self.model,
                privacy_level=self.policy.privacy_level,
                payload_size=len(str(user_content or "")),
                result="blocked", reason_codes=["EXTERNAL_RAW_STRING_FORBIDDEN"])
            raise SecurityBlockedError(
                "external LLM raw string blocked; use chat_safe(SafeLLMPayload)")
        body = self._build_body(system_prompt, user_content, temperature)
        return self._post(body, retries=retries)

    def chat_local_raw(self, system_prompt: str, user_content: str,
                       temperature: float = 0.1, retries: int = 1) -> dict:
        """显式 LOCAL 模式；若 destination=EXTERNAL 仍会 BLOCK。"""
        return self.chat_json(system_prompt, user_content, temperature, retries)

    # ------------------------------------------------------------------
    def chat_safe(self, system_prompt: str, safe_payload: SafeLLMPayload | Dict[str, Any],
                  temperature: float = 0.1, retries: int = 1) -> dict:
        """External 唯一允许的调用方式。"""
        payload = ensure_safe_payload(safe_payload, self.policy)
        if self.is_external and self._destination_error:
            self.audit.log(
                event="external_policy_blocked", purpose=payload.purpose,
                email_ref_hash=payload.email_ref, provider_host=self.provider_host,
                model=self.model, privacy_level=payload.privacy_level,
                payload_size=0, result="blocked",
                reason_codes=[self._destination_error])
            raise SecurityBlockedError(self._destination_error)
        body = self._build_body(system_prompt, payload.to_json(), temperature)
        return self._post(body, retries=retries, safe_payload=payload)

    # ------------------------------------------------------------------
    def _post(self, body: dict, retries: int = 1,
              safe_payload: Optional[SafeLLMPayload] = None) -> dict:
        # 最终 HTTP body 扫描：不是只扫输入文本。
        if self.is_external:
            if safe_payload is None:
                self.audit.log(
                    event="external_raw_blocked", purpose="raw_post",
                    provider_host=self.provider_host, model=self.model,
                    privacy_level=self.policy.privacy_level,
                    payload_size=len(json.dumps(body, ensure_ascii=False)),
                    result="blocked", reason_codes=["EXTERNAL_RAW_BODY_FORBIDDEN"])
                raise SecurityBlockedError("external raw body blocked")
            final_json = json.dumps(body, ensure_ascii=False, sort_keys=True)
            result = self.guard.final_scan(final_json)
            if not result.allowed:
                self.audit.log(
                    event="external_payload_blocked", purpose=safe_payload.purpose,
                    email_ref_hash=safe_payload.email_ref,
                    provider_host=self.provider_host, model=self.model,
                    privacy_level=safe_payload.privacy_level,
                    payload_size=result.payload_size,
                    redaction_count=result.redaction_count,
                    result="blocked", reason_codes=result.reason_codes)
                raise SecurityBlockedError("outbound guard blocked: " +
                                           ", ".join(result.reason_codes))
            self.audit.log(
                event="external_request", purpose=safe_payload.purpose,
                email_ref_hash=safe_payload.email_ref,
                provider_host=self.provider_host, model=self.model,
                privacy_level=safe_payload.privacy_level,
                payload_size=result.payload_size,
                redaction_count=result.redaction_count,
                result="allowed")
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = requests.post(self._endpoint(), headers=self._headers(),
                                     json=body, timeout=self.timeout)
                if resp.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    last_err = LLMError(f"HTTP 429 限流: {resp.text[:200]}")
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return json.loads(content) if isinstance(content, str) else content
            except requests.RequestException as e:  # noqa: BLE001
                last_err = LLMError(f"请求失败: {e}")
                time.sleep(1)
            except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:  # noqa: BLE001
                last_err = LLMError(f"响应解析失败: {e}")
        raise last_err if last_err else LLMError("未知 LLM 调用错误")

    def is_configured(self) -> bool:
        return bool(self.api_key)
