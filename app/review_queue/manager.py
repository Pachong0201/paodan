"""V3.2 重要邮件待审查文件夹 ReviewQueueManager.

原则：复制不移动、重复运行不重复复制、优先级变化自动同步目录、
Review Queue 失败不得导致主 Pipeline 失败。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ILLEGAL_WIN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_SUBJECT_PART = 60
_MAX_BASENAME = 180


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _norm_mid(v: Any) -> str:
    s = str(v or "").strip()
    if s.startswith("<") and s.endswith(">") and len(s) >= 2:
        s = s[1:-1].strip()
    return s.strip()


def _sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:8]


def sanitize_subject(text: Any, max_len: int = _MAX_SUBJECT_PART) -> str:
    """清洗 Windows 非法字符并限长；空主题返回 no_subject。"""
    s = str(text or "").strip()
    s = _ILLEGAL_WIN.sub("_", s)
    # Windows 不允许结尾空格/点
    s = s.strip().rstrip(". ")
    # 压缩空白
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return "no_subject"
    if len(s) > max_len:
        s = s[:max_len].rstrip(". ")
        if not s:
            return "no_subject"
    # 再次去掉结尾点/空格（截断后可能产生）
    return s.rstrip(". ") or "no_subject"


def _parse_dt(date_str: Any) -> datetime:
    s = str(date_str or "").strip()
    if s:
        try:
            dt = parsedate_to_datetime(s)
            if dt is not None:
                return dt.replace(tzinfo=None)
        except Exception:
            pass
        # 尝试 ISO
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now()


class ReviewQueueManager:
    """待审查文件夹管理器。所有单封异常内部消化为 warning，不抛给 Pipeline。"""

    def __init__(
        self,
        path: str | Path | None = None,
        enabled: bool = True,
        priorities: Optional[List[str]] = None,
        min_score: float = 60.0,
        split_by_priority: bool = True,
        copy_original_eml: bool = True,
        move_original: bool = False,
        deduplicate: bool = True,
        sync_priority_changes: bool = True,
        write_sidecar_json: bool = True,
        extract_attachments: bool = False,
    ):
        if path is None:
            try:
                from ..config import DATA_DIR
                path = DATA_DIR / "review_queue"
            except Exception:
                path = Path("data/review_queue")
        self.base = Path(path)
        self.enabled = bool(enabled)
        self.priorities = [str(p).strip().upper() for p in (priorities or ["S", "A", "B"]) if str(p).strip()]
        self.min_score = float(min_score)
        self.split_by_priority = bool(split_by_priority)
        self.copy_original_eml = bool(copy_original_eml)
        self.move_original = bool(move_original)
        self.deduplicate = bool(deduplicate)
        self.sync_priority_changes = bool(sync_priority_changes)
        self.write_sidecar_json = bool(write_sidecar_json)
        self.extract_attachments = bool(extract_attachments)
        if self.extract_attachments:
            logger.warning("ReviewQueue extract_attachments=true 暂不支持附件展开（V3.2 不实现），已忽略该选项")
            self.extract_attachments = False

    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, config_dir: Path | None = None,
                    path_override: str | Path | None = None,
                    enabled_override: Optional[bool] = None) -> "ReviewQueueManager":
        try:
            from ..config import load_review_queue_config
            cfg = load_review_queue_config(config_dir)
        except Exception:
            cfg = {"enabled": True, "path": "data/review_queue", "min_score": 60.0,
                   "priorities": ["S", "A", "B"], "split_by_priority": True,
                   "copy_original_eml": True, "move_original": False,
                   "deduplicate": True, "sync_priority_changes": True,
                   "write_sidecar_json": True, "extract_attachments": False}
        if enabled_override is not None:
            cfg["enabled"] = bool(enabled_override)
        if path_override is not None:
            cfg["path"] = str(path_override)
        return cls(**{k: cfg[k] for k in (
            "path", "enabled", "priorities", "min_score", "split_by_priority",
            "copy_original_eml", "move_original", "deduplicate",
            "sync_priority_changes", "write_sidecar_json", "extract_attachments") if k in cfg})

    # ------------------------------------------------------------------
    def should_queue(self, record: Any) -> bool:
        """命中条件与 Excel 台账一致：priority in priorities OR final_score >= min_score。"""
        try:
            score = _get(record, "score", None)
            if score is None:
                return False
            pri = str(_get(score, "priority", "") or "").strip().upper()
            try:
                final = float(_get(score, "final_score", 0.0) or 0.0)
            except Exception:
                final = 0.0
            if pri in self.priorities:
                return True
            return final >= float(self.min_score)
        except Exception:
            return False

    def _content_hash(self, record: Any) -> str:
        email = _get(record, "email", None)
        if email is None:
            return ""
        for k in ("body_hash", "content_hash"):
            v = str(_get(email, k, "") or "").strip()
            if v:
                return v
        # fallback：仅对正文内容哈希，不得用 subject/sender/date
        for k in ("body_text", "combined_text", "original_text", "normalized_text"):
            try:
                t = str(_get(email, k, "") or "")
            except Exception:
                t = ""
            if t.strip():
                return hashlib.sha256(t.encode("utf-8", errors="ignore")).hexdigest()
        # 附件文本兜底（仍属内容，不属 subject/sender/date）
        try:
            parts = []
            for a in (_get(email, "attachments", []) or []):
                if isinstance(a, dict):
                    t = str(a.get("text") or "")
                    h = str(a.get("content_hash") or "")
                    if h.strip():
                        parts.append(h.strip())
                    elif t.strip():
                        parts.append(t[:2000])
                else:
                    t = str(getattr(a, "text", "") or "")
                    h = str(getattr(a, "content_hash", "") or "")
                    if h.strip():
                        parts.append(h.strip())
                    elif t.strip():
                        parts.append(t[:2000])
            if parts:
                return hashlib.sha256("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()
        except Exception:
            pass
        return ""

    def build_review_key(self, record: Any) -> str:
        """Message-ID 优先，否则 body_hash/content_hash。内部 MID:/HASH: 前缀。"""
        email = _get(record, "email", None)
        mid = _norm_mid(_get(email, "message_id", "") if email is not None else "")
        if mid:
            return f"MID:{mid}"
        h = self._content_hash(record)
        if h:
            return f"HASH:{h}"
        return ""

    def _priority_of(self, record: Any) -> str:
        try:
            return str(_get(_get(record, "score", None), "priority", "") or "").strip().upper() or "B"
        except Exception:
            return "B"

    def _score_of(self, record: Any) -> float:
        try:
            return float(_get(_get(record, "score", None), "final_score", 0.0) or 0.0)
        except Exception:
            return 0.0

    def target_dir(self, priority: str) -> Path:
        if self.split_by_priority:
            return self.base / str(priority or "B").strip().upper()
        return self.base

    def build_filename(self, record: Any) -> str:
        """YYYYMMDD_HHMMSS_<PRIORITY>_<SCORE>_<hash8>_<subject>.eml（已清洗限长）。"""
        email = _get(record, "email", None)
        pri = self._priority_of(record)
        sc = self._score_of(record)
        score_int = int(round(sc))
        try:
            dt = _parse_dt(_get(email, "date", "") if email is not None else "")
            ts = dt.strftime("%Y%m%d_%H%M%S")
        except Exception:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        key = self.build_review_key(record)
        h8 = _sha8(key) if key else "00000000"
        subj = sanitize_subject(_get(email, "subject", "") if email is not None else "")
        base = f"{ts}_{pri}_{score_int}_{h8}_{subj}.eml"
        if len(base) > _MAX_BASENAME:
            # 压缩 subject 部分保证总长
            overflow = len(base) - _MAX_BASENAME
            subj2 = subj[: max(1, len(subj) - overflow)].rstrip(". ")
            base = f"{ts}_{pri}_{score_int}_{h8}_{subj2 or 'no_subject'}.eml"
        return base

    def _sidecar_of(self, eml_path: Path) -> Path:
        # xxx.eml -> xxx.review.json
        if eml_path.suffix.lower() == ".eml":
            return eml_path.with_name(eml_path.stem + ".review.json")
        return eml_path.with_name(eml_path.name + ".review.json")

    def _scan_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        if not self.base.exists():
            return dirs
        if self.split_by_priority:
            for p in (self.priorities + ["C", "D", "S", "A", "B"]):
                d = self.base / p
                if d.is_dir() and d not in dirs:
                    dirs.append(d)
            # 兜底：base 下未知子目录也扫
            try:
                for sub in self.base.iterdir():
                    if sub.is_dir() and sub not in dirs:
                        dirs.append(sub)
            except Exception:
                pass
            # split 模式 base 直属 eml 也兼容
            dirs.append(self.base)
        else:
            dirs.append(self.base)
        return dirs

    def find_existing(self, record: Any) -> List[Path]:
        """按 review_key 在全部 priority 目录中查找已存在的副本。"""
        key = self.build_review_key(record)
        if not key:
            return []
        h8 = _sha8(key)
        found: List[Path] = []
        try:
            for d in self._scan_dirs():
                if not d.is_dir():
                    continue
                try:
                    cands = list(d.glob("*.eml"))
                except Exception:
                    continue
                for c in cands:
                    try:
                        # 优先 sidecar 精确匹配
                        sc = self._sidecar_of(c)
                        if sc.exists():
                            try:
                                data = json.loads(sc.read_text(encoding="utf-8"))
                                if str(data.get("review_key") or "") == key:
                                    if c not in found:
                                        found.append(c)
                                    continue
                            except Exception:
                                pass
                        # fallback：文件名 hash8 匹配
                        if h8 in c.stem:
                            if c not in found:
                                found.append(c)
                    except Exception:
                        continue
        except Exception as e:
            logger.warning("ReviewQueue 查找已存在副本失败: %s", e)
        return found

    # ------------------------------------------------------------------
    def write_sidecar(self, eml_path: Path, record: Any) -> Optional[Path]:
        """写 xxx.review.json；失败只 warning，返回 sidecar 路径或 None。"""
        if not self.write_sidecar_json:
            return None
        try:
            email = _get(record, "email", None)
            rule = _get(record, "rule", None)
            llm = _get(record, "llm", None)
            score = _get(record, "score", None)
            llm_cats = _get(llm, "categories", []) if llm is not None else []
            rule_cats = _get(rule, "matched_categories", []) if rule is not None else []
            cats = list(llm_cats or rule_cats or [])
            llm_persons = _get(llm, "target_persons", []) if llm is not None else []
            rule_persons = _get(rule, "target_persons_found", []) if rule is not None else []
            persons = list(llm_persons or rule_persons or [])
            pats = []
            try:
                for p in (_get(rule, "matched_patterns", []) or []):
                    pid = p.get("pattern_id") if isinstance(p, dict) else getattr(p, "pattern_id", "")
                    if pid:
                        pats.append(str(pid))
            except Exception:
                pass
            sidecar = {
                "review_key": self.build_review_key(record),
                "priority": self._priority_of(record),
                "final_score": self._score_of(record),
                "categories": cats,
                "target_persons": persons,
                "matched_patterns": pats,
                "one_sentence_summary": str(_get(llm, "one_sentence_summary", "") or "") if llm is not None else str(_get(record, "summary_zh", "") or ""),
                "reason_for_attention": str(_get(llm, "reason_for_attention", "") or "") if llm is not None else "",
                "verification_targets": list(_get(record, "verification_targets", []) or (_get(llm, "verification_targets", []) if llm is not None else []) or []),
                "source_path": str(_get(email, "source_path", "") or "") if email is not None else "",
                "queued_at": datetime.now().isoformat(timespec="seconds"),
            }
            sc_path = self._sidecar_of(Path(eml_path))
            sc_path.parent.mkdir(parents=True, exist_ok=True)
            sc_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
            return sc_path
        except (OSError, PermissionError) as e:
            logger.warning("ReviewQueue sidecar 写入失败 %s: %s", eml_path, e)
            return None
        except Exception as e:
            logger.warning("ReviewQueue sidecar 写入异常 %s: %s", eml_path, e)
            return None

    def _remove_copies(self, paths: List[Path]) -> int:
        removed = 0
        for p in paths:
            try:
                if p.exists():
                    p.unlink()
                    removed += 1
            except (PermissionError, OSError) as e:
                logger.warning("ReviewQueue 删除旧副本失败 %s: %s", p, e)
                continue
            except Exception as e:
                logger.warning("ReviewQueue 删除旧副本异常 %s: %s", p, e)
                continue
            try:
                sc = self._sidecar_of(p)
                if sc.exists():
                    sc.unlink()
            except Exception as e:
                logger.warning("ReviewQueue 删除旧 sidecar 失败 %s: %s", sc, e)
        return removed

    def copy_to_queue(self, record: Any) -> Optional[str]:
        """单封复制到待审目录。返回队列 .eml 路径字符串；跳过/失败返回 None。"""
        try:
            if not self.enabled:
                return None
            if not self.should_queue(record):
                # 不应进入：若开启同步则尝试移出旧副本
                if self.sync_priority_changes:
                    try:
                        self.sync_priority(record)
                    except Exception as e:
                        logger.warning("ReviewQueue sync_priority 失败: %s", e)
                return None
            key = self.build_review_key(record)
            if not key:
                logger.warning("ReviewQueue 无法生成 review_key（缺 Message-ID 且无正文哈希），跳过")
                return None
            pri = self._priority_of(record)
            dest_dir = self.target_dir(pri)
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as e:
                logger.warning("ReviewQueue 目录创建失败 %s: %s", dest_dir, e)
                return None
            # 去重：查找已存在
            existing: List[Path] = []
            if self.deduplicate:
                try:
                    existing = self.find_existing(record)
                except Exception as e:
                    logger.warning("ReviewQueue 去重查找失败: %s", e)
                    existing = []
            # 已存在且已在正确目录 -> 去重命中，刷新 sidecar 后返回
            for epath in list(existing):
                try:
                    if epath.parent.resolve() == dest_dir.resolve() and epath.exists():
                        # 同优先级下文件名可能因评分变化而不同：若文件名与目标名不同则重命名为最新
                        want = self.build_filename(record)
                        if epath.name != want:
                            new_path = dest_dir / want
                            if not new_path.exists():
                                try:
                                    epath.rename(new_path)
                                    old_sc = self._sidecar_of(epath)
                                    if old_sc.exists():
                                        try:
                                            old_sc.unlink()
                                        except Exception:
                                            pass
                                    epath = new_path
                                except Exception as re_:
                                    logger.warning("ReviewQueue 重命名失败，保留原文件 %s: %s", epath, re_)
                            else:
                                # 目标名已存在（极端），保留已存在文件
                                pass
                        # 删除同 key 的其他重复副本（保证只保留一份）
                        for other in existing:
                            if other != epath and other.exists():
                                try:
                                    other.unlink()
                                    osc = self._sidecar_of(other)
                                    if osc.exists():
                                        osc.unlink()
                                except Exception:
                                    pass
                        try:
                            self.write_sidecar(epath, record)
                        except Exception:
                            pass
                        return str(epath)
                except Exception:
                    continue
            # 存在但在错误目录 -> 同步迁移：删除旧副本
            if existing and self.sync_priority_changes:
                self._remove_copies(existing)
            elif existing and not self.sync_priority_changes:
                # 不同步但去重：保留第一份，直接返回
                try:
                    if existing[0].exists():
                        return str(existing[0])
                except Exception:
                    pass
            # source 检查
            email = _get(record, "email", None)
            src = str(_get(email, "source_path", "") or "") if email is not None else ""
            if not src:
                logger.warning("ReviewQueue source_path 为空，跳过该邮件")
                return None
            src_path = Path(src)
            if not src_path.exists():
                logger.warning("ReviewQueue 源文件不存在，跳过: %s", src_path)
                return None
            if not self.copy_original_eml:
                logger.warning("ReviewQueue copy_original_eml=false，跳过复制")
                return None
            # 目标文件名（避免同名覆盖：唯一性依赖 hash8；若同名但不同 key 则追加 hash 片段）
            fname = self.build_filename(record)
            dest = dest_dir / fname
            if dest.exists():
                try:
                    sc = self._sidecar_of(dest)
                    if sc.exists():
                        data = json.loads(sc.read_text(encoding="utf-8"))
                        if str(data.get("review_key") or "") == key:
                            try:
                                self.write_sidecar(dest, record)
                            except Exception:
                                pass
                            return str(dest)
                except Exception:
                    pass
                # 同名但不同邮件：追加完整 hash 片段（不用 (1)(2)）
                full_h = hashlib.sha256(key.encode("utf-8", errors="ignore")).hexdigest()
                stem = dest.stem
                dest = dest_dir / f"{stem}_{full_h[8:16]}.eml"
            try:
                if self.move_original:
                    shutil.move(str(src_path), str(dest))
                else:
                    shutil.copy2(str(src_path), str(dest))
            except (PermissionError, OSError) as e:
                logger.warning("ReviewQueue 复制失败 %s -> %s: %s", src_path, dest, e)
                return None
            except Exception as e:
                logger.warning("ReviewQueue 复制异常 %s -> %s: %s", src_path, dest, e)
                return None
            try:
                self.write_sidecar(dest, record)
            except Exception as e:
                logger.warning("ReviewQueue sidecar 失败（保留 .eml）: %s", e)
            return str(dest)
        except Exception as e:
            logger.warning("ReviewQueue 单封处理失败（不中断批次）: %s", e)
            return None

    def sync_priority(self, record: Any) -> Optional[str]:
        """优先级同步：返回当前队列路径；若已移出返回 None。

        - 不应进入且 sync 开启 -> 删除旧副本+sidecar（不删原邮件）。
        - 应进入但目录错误 -> 迁移到正确 priority 目录（删旧建新）。
        - 已正确 -> 返回现有路径。
        """
        try:
            existing = self.find_existing(record)
            if not self.should_queue(record):
                if self.sync_priority_changes and existing:
                    self._remove_copies(existing)
                return None
            if not existing:
                return self.copy_to_queue(record)
            pri = self._priority_of(record)
            want_dir = self.target_dir(pri)
            ok = [p for p in existing if p.exists() and p.parent.resolve() == want_dir.resolve()]
            if ok and len(existing) == len(ok):
                return str(ok[0])
            # 目录错误或有多份 -> 迁移
            if self.sync_priority_changes:
                return self.copy_to_queue(record)
            return str(existing[0]) if existing else None
        except Exception as e:
            logger.warning("ReviewQueue sync_priority 异常: %s", e)
            return None

    # ------------------------------------------------------------------
    def process_batch(self, records: List[Any]) -> Dict[str, Any]:
        """批处理：筛选全部完成后调用一次。任何单封错误不中断。"""
        stats = {"total": len(records or []), "queued": 0, "added": 0, "deduped": 0,
                 "removed": 0, "skipped": 0, "errors": 0, "paths": {}}
        if not self.enabled:
            stats["skipped"] = len(records or [])
            return stats
        # 预建目录结构 data/review_queue/{S,A,B}（split 模式；失败只 warning）
        try:
            if self.split_by_priority:
                for p in self.priorities:
                    (self.base / p).mkdir(parents=True, exist_ok=True)
            else:
                self.base.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as e:
            logger.warning("ReviewQueue 目录创建失败 %s: %s", self.base, e)
            stats["errors"] += 1
            stats["skipped"] = len(records or [])
            return stats
        except Exception as e:
            logger.warning("ReviewQueue 目录创建异常 %s: %s", self.base, e)
        # 记录处理前已存在文件集合（用于区分 added vs deduped）
        for rec in (records or []):
            try:
                if self.should_queue(rec):
                    key = self.build_review_key(rec)
                    pre = self.find_existing(rec) if (key and self.deduplicate) else []
                    pre_set = {str(p) for p in pre if p.exists()}
                    out = self.copy_to_queue(rec)
                    if out:
                        stats["queued"] += 1
                        if key:
                            stats["paths"][key] = out
                        if str(out) in pre_set:
                            stats["deduped"] += 1
                        else:
                            # 同 key 已有但迁移改名也算 deduped？若 pre 非空则算 deduped
                            if pre_set:
                                stats["deduped"] += 1
                            else:
                                stats["added"] += 1
                    else:
                        stats["skipped"] += 1
                else:
                    # 不应进入：同步移出
                    if self.sync_priority_changes:
                        try:
                            pre = self.find_existing(rec)
                            if pre:
                                n = self._remove_copies(pre)
                                if n:
                                    stats["removed"] += 1
                                else:
                                    stats["skipped"] += 1
                            else:
                                stats["skipped"] += 1
                        except Exception as e:
                            logger.warning("ReviewQueue 移出失败: %s", e)
                            stats["errors"] += 1
                    else:
                        stats["skipped"] += 1
            except Exception as e:
                logger.warning("ReviewQueue 批量单封失败（继续）: %s", e)
                stats["errors"] += 1
        return stats

    # 兼容别名
    def process_records(self, records: List[Any]) -> Dict[str, Any]:
        return self.process_batch(records)


__all__ = ["ReviewQueueManager", "sanitize_subject"]
