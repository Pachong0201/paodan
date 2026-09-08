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
RULE_TRIGGER_EXTRA = bool(os.getenv("RULE_TRIGGER_EXTRA", "1").strip().lower() not in ("0", "false", "no"))
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


def _fresh_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _fresh_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    return [x.strip().upper() for x in raw.split(",") if x.strip()]


def _read_yaml_section(path: Path, section_name: str) -> dict:
    try:
        import yaml as _yaml
        if not path.exists():
            return {}
        data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return {}
        section = data.get(section_name, data)
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def merge_config_precedence(defaults: dict, yaml_values: dict, env_values: dict,
                            cli_values: dict | None = None) -> dict:
    """统一优先级：代码默认 < YAML < ENV < CLI。"""
    out = dict(defaults)
    out.update({k: v for k, v in (yaml_values or {}).items() if v is not None})
    out.update({k: v for k, v in (env_values or {}).items() if v is not None})
    out.update({k: v for k, v in (cli_values or {}).items() if v is not None})
    return out


def load_review_queue_config(config_dir: Path | None = None, cli_overrides: dict | None = None) -> dict:
    """合并 review_queue.yaml + ENV，优先级：默认 < YAML < ENV。"""
    defaults = {
        "enabled": True,
        "path": str(DATA_DIR / "review_queue"),
        "min_score": 60.0,
        "priorities": ["S", "A", "B"],
        "split_by_priority": True,
        "copy_original_eml": True,
        "move_original": False,
        "deduplicate": True,
        "sync_priority_changes": True,
        "write_sidecar_json": True,
        "extract_attachments": False,
    }
    base = Path(config_dir) if config_dir else CONFIG_DIR
    yaml_values = _read_yaml_section(base / "review_queue.yaml", "review_queue")
    yaml_clean = {}
    bool_keys = ("enabled", "split_by_priority", "copy_original_eml", "move_original",
                 "deduplicate", "sync_priority_changes", "write_sidecar_json",
                 "extract_attachments")
    for k in bool_keys:
        if k in yaml_values:
            yaml_clean[k] = bool(yaml_values[k])
    if yaml_values.get("path"):
        yaml_clean["path"] = str(yaml_values["path"])
    if "min_score" in yaml_values:
        try:
            yaml_clean["min_score"] = float(yaml_values["min_score"])
        except (TypeError, ValueError):
            pass
    if yaml_values.get("priorities"):
        yaml_clean["priorities"] = [str(p).strip().upper() for p in yaml_values["priorities"] if str(p).strip()]
    env_values = {}
    env_map = {
        "REVIEW_QUEUE_ENABLED": "enabled", "REVIEW_QUEUE_PATH": "path",
        "REVIEW_QUEUE_MIN_SCORE": "min_score", "REVIEW_QUEUE_PRIORITIES": "priorities",
        "REVIEW_QUEUE_SPLIT_BY_PRIORITY": "split_by_priority",
        "REVIEW_QUEUE_COPY_ORIGINAL": "copy_original_eml",
        "REVIEW_QUEUE_MOVE_ORIGINAL": "move_original",
        "REVIEW_QUEUE_DEDUPLICATE": "deduplicate",
        "REVIEW_QUEUE_SYNC_PRIORITY": "sync_priority_changes",
        "REVIEW_QUEUE_WRITE_SIDECAR": "write_sidecar_json",
        "REVIEW_QUEUE_EXTRACT_ATTACHMENTS": "extract_attachments",
    }
    for env_name, key in env_map.items():
        if os.getenv(env_name) is None:
            continue
        if key == "path":
            env_values[key] = os.getenv(env_name)
        elif key == "min_score":
            try:
                env_values[key] = float(os.getenv(env_name))
            except ValueError:
                pass
        elif key == "priorities":
            env_values[key] = _fresh_list(env_name, defaults["priorities"])
        else:
            env_values[key] = _fresh_bool(env_name, defaults[key])
    return merge_config_precedence(defaults, yaml_clean, env_values, cli_overrides)


def load_excel_register_config(config_dir: Path | None = None, cli_overrides: dict | None = None) -> dict:
    """合并 excel_register.yaml + ENV，优先级：默认 < YAML < ENV。"""
    defaults = {
        "enabled": True,
        "path": str(REPORTS_DIR / "important_email_register.xlsx"),
        "min_score": 60.0,
        "priorities": ["S", "A", "B"],
        "backup_before_batch": True,
    }
    base = Path(config_dir) if config_dir else CONFIG_DIR
    yaml_values = _read_yaml_section(base / "excel_register.yaml", "excel_register")
    yaml_clean = {}
    if "enabled" in yaml_values:
        yaml_clean["enabled"] = bool(yaml_values["enabled"])
    if yaml_values.get("path"):
        yaml_clean["path"] = str(yaml_values["path"])
    if "min_score" in yaml_values:
        try:
            yaml_clean["min_score"] = float(yaml_values["min_score"])
        except (TypeError, ValueError):
            pass
    if yaml_values.get("priorities"):
        yaml_clean["priorities"] = [str(p).strip().upper() for p in yaml_values["priorities"] if str(p).strip()]
    if "backup_before_batch" in yaml_values:
        yaml_clean["backup_before_batch"] = bool(yaml_values["backup_before_batch"])
    env_values = {}
    env_map = {
        "EXCEL_REGISTER_ENABLED": ("enabled", "bool"),
        "EXCEL_REGISTER_PATH": ("path", "str"),
        "EXCEL_REGISTER_MIN_SCORE": ("min_score", "float"),
        "EXCEL_REGISTER_PRIORITIES": ("priorities", "list"),
        "EXCEL_REGISTER_BACKUP": ("backup_before_batch", "bool"),
    }
    for env_name, (key, kind) in env_map.items():
        if os.getenv(env_name) is None:
            continue
        raw = os.getenv(env_name)
        if kind == "bool":
            env_values[key] = _fresh_bool(env_name, defaults[key])
        elif kind == "float":
            try:
                env_values[key] = float(raw)
            except ValueError:
                pass
        elif kind == "list":
            env_values[key] = _fresh_list(env_name, defaults["priorities"])
        else:
            env_values[key] = raw
    return merge_config_precedence(defaults, yaml_clean, env_values, cli_overrides)


def resolve_llm_trigger_score(cli_value: float | None = None) -> float:
    """优先级：代码默认 < ENV < CLI。"""
    default = float(globals().get("LLM_TRIGGER_SCORE", 35.0) or 35.0)
    env = os.getenv("LLM_TRIGGER_SCORE")
    value = default
    if env is not None:
        try:
            value = float(env)
        except ValueError:
            value = default
    if cli_value is not None:
        try:
            value = float(cli_value)
        except (TypeError, ValueError):
            pass
    return value


def priority_min_value(p: str) -> float:
    return {"S": 90, "A": 75, "B": 60, "C": 40, "D": 0}.get(p.upper(), 0)
