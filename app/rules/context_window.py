"""上下文切分：句子、段落、相邻3句窗口，供 Pattern 共现匹配与证据摘录使用."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 繁体中文分句（保留标点作为句子的一部分）
SENT_SPLIT_RE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
PARA_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass
class TextWindow:
    start: int
    end: int
    level: str          # sentence/paragraph/full/context3
    text: str = ""

    def __len__(self) -> int:
        return self.end - self.start


@dataclass
class ContextSplitter:
    text: str
    sentences: list = field(default_factory=list)      # [(start,end,text)]
    paragraphs: list = field(default_factory=list)     # [(start,end,text)]
    windows_ctx3: list = field(default_factory=list)   # 相邻3句窗口

    @staticmethod
    def split_sentences(text: str):
        out = []
        for m in SENT_SPLIT_RE.finditer(text):
            s = m.group(0).strip()
            if s:
                out.append((m.start(), m.end(), s))
        return out

    @staticmethod
    def split_paragraphs(text: str):
        out = []
        pos = 0
        for m in PARA_SPLIT_RE.finditer(text):
            seg = text[pos:m.start()].strip("\n")
            if seg.strip():
                out.append((pos, m.start(), seg.strip()))
            pos = m.end()
        tail = text[pos:].strip("\n")
        if tail.strip():
            out.append((pos, len(text), tail.strip()))
        return out

    def build(self) -> "ContextSplitter":
        self.sentences = self.split_sentences(self.text)
        self.paragraphs = self.split_paragraphs(self.text)
        # 相邻3句
        for i in range(len(self.sentences)):
            start = self.sentences[i][0]
            end = self.sentences[min(i + 2, len(self.sentences) - 1)][1]
            self.windows_ctx3.append((start, end, self.text[start:end].strip()))
        return self

    def find(self, normalized_term: str):
        """在原文中找某词（大小写不敏感）所有出现位置."""
        out = []
        low = self.text.lower()
        t = normalized_term.lower()
        start = 0
        while True:
            idx = low.find(t, start)
            if idx < 0:
                break
            out.append((idx, idx + len(t)))
            start = idx + len(t)
        return out


def term_in_window(term_norm: str, window_text_norm: str) -> bool:
    return term_norm in window_text_norm
