"""统一 LLM Client：OpenAI-compatible API（requests 直连，无 openai 依赖）。"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient:
    """OpenAI-compatible chat completions 客户端.

    环境变量：LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_TIMEOUT
    无 API Key 时 raise LLMError，由调用方决定是否回退模板模式。
    """

    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: int = 60, max_tokens: int = 4000):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.model = model or "gpt-4o-mini"
        self.timeout = timeout
        self.max_tokens = max_tokens
        if not self.api_key:
            raise LLMError("未配置 LLM_API_KEY")

    def _endpoint(self) -> str:
        # 兼容 base_url 已含 /chat/completions 的写法
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def chat_json(self, system_prompt: str, user_content: str,
                  temperature: float = 0.1, retries: int = 1) -> dict:
        """调用一次并返回解析后的 JSON dict；失败抛 LLMError."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }
        last_err = None
        for attempt in range(retries + 1):
            try:
                resp = requests.post(self._endpoint(), headers=headers, json=body,
                                     timeout=self.timeout)
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
