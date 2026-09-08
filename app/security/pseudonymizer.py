"""请求级假名化：不同邮件/不同请求不得复用长期稳定映射。"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional


class Pseudonymizer:
    """一次请求内的本地映射；映射只存在于内存，绝不进入 external payload。"""

    def __init__(self, namespace: str = ""):
        self.namespace = namespace
        self._counters: Dict[str, int] = defaultdict(int)
        self._reverse: Dict[str, str] = {}
        self._forward: Dict[str, str] = {}

    @staticmethod
    def _kind_prefix(kind: str) -> str:
        k = str(kind or "ENTITY").upper().replace("-", "_")
        mapping = {
            "PERSON": "PERSON", "TARGET": "TARGET", "TARGET_PERSON": "TARGET",
            "SOURCE": "SOURCE", "WHISTLEBLOWER": "SOURCE", "INFORMANT": "SOURCE",
            "ORG": "ORG", "ORGANIZATION": "ORG", "COMPANY": "COMPANY",
            "GOVERNMENT_AGENCY": "ORG", "PROJECT": "PROJECT",
        }
        return mapping.get(k, re.sub(r"[^A-Z0-9_]", "_", k) or "ENTITY")

    def pseudonymize(self, value: str, kind: str = "ENTITY") -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        key = f"{kind}:{value}"
        if key in self._forward:
            return self._forward[key]
        prefix = self._kind_prefix(kind)
        self._counters[prefix] += 1
        token = f"{prefix}_{self._counters[prefix]:02d}"
        self._forward[key] = token
        self._reverse[token] = value
        return token

    def anonymize(self, value: str, kind: str = "ENTITY") -> str:
        return self.pseudonymize(value, kind)

    def map_entities(self, values: Iterable[dict], kind_key: str = "type",
                     text_key: str = "text") -> List[dict]:
        out: List[dict] = []
        for item in values or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get(kind_key) or "ENTITY")
            text = str(item.get(text_key) or item.get("name") or "")
            token = self.pseudonymize(text, kind)
            if token:
                out.append({"pseudonym": token, "kind": kind.upper()})
        return out

    def local_mapping(self) -> Dict[str, str]:
        """仅本地审计/调试使用；调用方不得写入 external payload。"""
        return dict(self._reverse)

    @property
    def mapping(self) -> Dict[str, str]:
        return self.local_mapping()

    def reset(self) -> None:
        self._counters.clear()
        self._forward.clear()
        self._reverse.clear()
