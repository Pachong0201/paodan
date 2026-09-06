"""全局配置：路径、阈值、日志、DB、LLM（自 .env 读取）."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

CONFIG_DIR = ROOT / "config"
NEWS_SIGNAL_DIR = Path(os.getenv("NEWS_SIGNAL_DIR", CONFIG_DIR / "news_signal"))
DATA_DIR = ROOT / "data"
INBOX_DIR = DATA_DIR / "inbox"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = DATA_DIR / "reports"
DB_PATH = Path(os.getenv("SCREENING_DB", DATA_DIR / "news_screening.db"))
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "app.log"
KNOWN_CASES_FILE = Path(os.getenv("KNOWN_CASES_FILE", DATA_DIR / "known_cases.jsonl"))
PERSON_ALIASES_FILE = CONFIG_DIR / "news_signal" / "person_aliases.yaml"

for _d in (DATA_DIR, INBOX_DIR, PROCESSED_DIR, REPORTS_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------- 运行开关 ----------
LLM_ENABLED = os.getenv("LLM_ENABLED", "1").strip().lower() not in ("0", "false", "no")
LLM_MODE = os.getenv("LLM_MODE", "template")          # api / template
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))
LLM_TEMPLATE_DIR = Path(os.getenv("LLM_TEMPLATE_DIR", DATA_DIR / "llm_templates"))
LLM_TEMPLATE_FILE = LLM_TEMPLATE_DIR / "template_result.json"

OCR_ENABLED = os.getenv("OCR_ENABLED", "1").strip().lower() not in ("0", "false", "no")
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")

# ---------- 流水线阈值（可配置） ----------
LLM_TRIGGER_SCORE = float(os.getenv("LLM_TRIGGER_SCORE", "35"))
MIN_PRIORITY = os.getenv("MIN_PRIORITY", "D")        # 报告最低输出优先级

# ---------- 重要爆料邮件 Excel 台账 V3.1（可配置） ----------
# 默认与 config/excel_register.yaml 保持一致；.env / CLI 可覆盖。
EXCEL_REGISTER_ENABLED = os.getenv("EXCEL_REGISTER_ENABLED", "1").strip().lower() not in ("0", "false", "no")
EXCEL_REGISTER_PATH = Path(os.getenv("EXCEL_REGISTER_PATH", str(REPORTS_DIR / "important_email_register.xlsx")))
EXCEL_REGISTER_MIN_SCORE = float(os.getenv("EXCEL_REGISTER_MIN_SCORE", "60"))
_EXCEL_PRIORITIES_RAW = os.getenv("EXCEL_REGISTER_PRIORITIES", "S,A,B")
EXCEL_REGISTER_PRIORITIES = [p.strip().upper() for p in _EXCEL_PRIORITIES_RAW.split(",") if p.strip()]
EXCEL_REGISTER_BACKUP_BEFORE_BATCH = os.getenv("EXCEL_REGISTER_BACKUP", "1").strip().lower() not in ("0", "false", "no")
EXCEL_REGISTER_SHEET = "重要爆料邮件"


# ---------- 重要邮件待审查文件夹 V3.2（可配置） ----------
# 默认与 config/review_queue.yaml 保持一致；.env / CLI 可覆盖。
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


REVIEW_QUEUE_ENABLED = _env_bool("REVIEW_QUEUE_ENABLED", True)
REVIEW_QUEUE_PATH = Path(os.getenv("REVIEW_QUEUE_PATH", str(DATA_DIR / "review_queue")))
REVIEW_QUEUE_MIN_SCORE = float(os.getenv("REVIEW_QUEUE_MIN_SCORE", "60"))
_REVIEW_PRIORITIES_RAW = os.getenv("REVIEW_QUEUE_PRIORITIES", "S,A,B")
REVIEW_QUEUE_PRIORITIES = [p.strip().upper() for p in _REVIEW_PRIORITIES_RAW.split(",") if p.strip()]
REVIEW_QUEUE_SPLIT_BY_PRIORITY = _env_bool("REVIEW_QUEUE_SPLIT_BY_PRIORITY", True)
REVIEW_QUEUE_COPY_ORIGINAL = _env_bool("REVIEW_QUEUE_COPY_ORIGINAL", True)
REVIEW_QUEUE_MOVE_ORIGINAL = _env_bool("REVIEW_QUEUE_MOVE_ORIGINAL", False)
REVIEW_QUEUE_DEDUPLICATE = _env_bool("REVIEW_QUEUE_DEDUPLICATE", True)
REVIEW_QUEUE_SYNC_PRIORITY = _env_bool("REVIEW_QUEUE_SYNC_PRIORITY", True)
REVIEW_QUEUE_WRITE_SIDECAR = _env_bool("REVIEW_QUEUE_WRITE_SIDECAR", True)
REVIEW_QUEUE_EXTRACT_ATTACHMENTS = _env_bool("REVIEW_QUEUE_EXTRACT_ATTACHMENTS", False)


def load_review_queue_config(config_dir: Path | None = None) -> dict:
    """合并 config/review_queue.yaml + .env 默认，返回 review_queue 配置 dict。"""
    cfg = {
        "enabled": REVIEW_QUEUE_ENABLED,
        "path": str(REVIEW_QUEUE_PATH),
        "min_score": REVIEW_QUEUE_MIN_SCORE,
        "priorities": list(REVIEW_QUEUE_PRIORITIES),
        "split_by_priority": REVIEW_QUEUE_SPLIT_BY_PRIORITY,
        "copy_original_eml": REVIEW_QUEUE_COPY_ORIGINAL,
        "move_original": REVIEW_QUEUE_MOVE_ORIGINAL,
        "deduplicate": REVIEW_QUEUE_DEDUPLICATE,
        "sync_priority_changes": REVIEW_QUEUE_SYNC_PRIORITY,
        "write_sidecar_json": REVIEW_QUEUE_WRITE_SIDECAR,
        "extract_attachments": REVIEW_QUEUE_EXTRACT_ATTACHMENTS,
    }
    try:
        import yaml as _yaml
        base = Path(config_dir) if config_dir else CONFIG_DIR
        for cand in (base / "review_queue.yaml", base / "news_signal" / "review_queue.yaml"):
            if cand.exists():
                data = _yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                section = data.get("review_queue", data) if isinstance(data, dict) else {}
                if not isinstance(section, dict):
                    break
                for k in ("enabled", "split_by_priority", "copy_original_eml",
                          "move_original", "deduplicate", "sync_priority_changes",
                          "write_sidecar_json", "extract_attachments"):
                    if k in section:
                        cfg[k] = bool(section[k])
                if "path" in section and section["path"]:
                    cfg["path"] = str(section["path"])
                if "min_score" in section:
                    try:
                        cfg["min_score"] = float(section["min_score"])
                    except (TypeError, ValueError):
                        pass
                if "priorities" in section and section["priorities"]:
                    cfg["priorities"] = [str(p).strip().upper() for p in section["priorities"] if str(p).strip()]
                break
    except Exception:
        pass
    # .env 显式设置优先于 yaml：若环境变量存在则覆盖 yaml 值
    try:
        import os as _os
        if _os.getenv("REVIEW_QUEUE_ENABLED") is not None:
            cfg["enabled"] = REVIEW_QUEUE_ENABLED
        if _os.getenv("REVIEW_QUEUE_PATH") is not None:
            cfg["path"] = str(REVIEW_QUEUE_PATH)
        if _os.getenv("REVIEW_QUEUE_MIN_SCORE") is not None:
            cfg["min_score"] = REVIEW_QUEUE_MIN_SCORE
        if _os.getenv("REVIEW_QUEUE_PRIORITIES") is not None:
            cfg["priorities"] = list(REVIEW_QUEUE_PRIORITIES)
        if _os.getenv("REVIEW_QUEUE_SPLIT_BY_PRIORITY") is not None:
            cfg["split_by_priority"] = REVIEW_QUEUE_SPLIT_BY_PRIORITY
        if _os.getenv("REVIEW_QUEUE_COPY_ORIGINAL") is not None:
            cfg["copy_original_eml"] = REVIEW_QUEUE_COPY_ORIGINAL
        if _os.getenv("REVIEW_QUEUE_MOVE_ORIGINAL") is not None:
            cfg["move_original"] = REVIEW_QUEUE_MOVE_ORIGINAL
        if _os.getenv("REVIEW_QUEUE_DEDUPLICATE") is not None:
            cfg["deduplicate"] = REVIEW_QUEUE_DEDUPLICATE
        if _os.getenv("REVIEW_QUEUE_SYNC_PRIORITY") is not None:
            cfg["sync_priority_changes"] = REVIEW_QUEUE_SYNC_PRIORITY
        if _os.getenv("REVIEW_QUEUE_WRITE_SIDECAR") is not None:
            cfg["write_sidecar_json"] = REVIEW_QUEUE_WRITE_SIDECAR
        if _os.getenv("REVIEW_QUEUE_EXTRACT_ATTACHMENTS") is not None:
            cfg["extract_attachments"] = REVIEW_QUEUE_EXTRACT_ATTACHMENTS
    except Exception:
        pass
    return cfg


def load_excel_register_config(config_dir: Path | None = None) -> dict:
    """合并 config/excel_register.yaml + .env 默认，返回 excel_register 配置 dict。"""
    cfg = {
        "enabled": EXCEL_REGISTER_ENABLED,
        "path": str(EXCEL_REGISTER_PATH),
        "min_score": EXCEL_REGISTER_MIN_SCORE,
        "priorities": list(EXCEL_REGISTER_PRIORITIES),
        "backup_before_batch": EXCEL_REGISTER_BACKUP_BEFORE_BATCH,
    }
    try:
        import yaml as _yaml
        base = Path(config_dir) if config_dir else CONFIG_DIR
        for cand in (base / "excel_register.yaml", base / "news_signal" / "excel_register.yaml"):
            if cand.exists():
                data = _yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
                section = data.get("excel_register", data) if isinstance(data, dict) else {}
                if not isinstance(section, dict):
                    break
                if "enabled" in section:
                    cfg["enabled"] = bool(section["enabled"])
                if "path" in section and section["path"]:
                    cfg["path"] = str(section["path"])
                if "min_score" in section:
                    try:
                        cfg["min_score"] = float(section["min_score"])
                    except (TypeError, ValueError):
                        pass
                if "priorities" in section and section["priorities"]:
                    cfg["priorities"] = [str(p).strip().upper() for p in section["priorities"] if str(p).strip()]
                if "backup_before_batch" in section:
                    cfg["backup_before_batch"] = bool(section["backup_before_batch"])
                break
    except Exception:
        pass
    # .env 显式设置优先于 yaml（若环境变量被显式设置则已体现在 EXCEL_* 默认中）
    # 此处不再二次覆盖，保持 cfg 为 yaml+env 合并结果。
    return cfg

RULE_TRIGGER_EXTRA = bool(os.getenv("RULE_TRIGGER_EXTRA", "1").strip() not in ("0", "false", "no"))
# 低于阈值仍送 LLM 的条件：
#  E 类原始证据词 >= 1 且 (实体人名或目标角色词命中) 且 具体金额存在


def priority_min_value(p: str) -> float:
    return {"S": 90, "A": 75, "B": 60, "C": 40, "D": 0}.get(p.upper(), 0)
