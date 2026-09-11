"""Workbench 运行设置：只把 last_llm_profile 写入 DB，不写 .env。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .llm_profiles import get_profile, load_llm_profiles


def get_setting(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM workbench_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"] if row is not None else default)


def set_setting(conn, key: str, value: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO workbench_settings(key, value, updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, value, now))
    conn.commit()


CUSTOM_LLM_BASE_URL_KEY = "custom_llm_base_url"
CUSTOM_LLM_MODEL_KEY = "custom_llm_model"
CUSTOM_LLM_API_KEY_KEY = "custom_llm_api_key"


def get_custom_llm_settings(conn) -> dict:
    """读取设置页自定义 LLM 配置；api_key 仅在本地 SQLite 中使用。"""
    return {
        "base_url": get_setting(conn, CUSTOM_LLM_BASE_URL_KEY, ""),
        "model": get_setting(conn, CUSTOM_LLM_MODEL_KEY, ""),
        "api_key": get_setting(conn, CUSTOM_LLM_API_KEY_KEY, ""),
    }


def set_custom_llm_settings(conn, base_url: str, model: str,
                            api_key: str = "", clear_api_key: bool = False) -> None:
    set_setting(conn, CUSTOM_LLM_BASE_URL_KEY, base_url.strip())
    set_setting(conn, CUSTOM_LLM_MODEL_KEY, model.strip())
    if clear_api_key:
        set_setting(conn, CUSTOM_LLM_API_KEY_KEY, "")
    elif api_key and api_key.strip():
        set_setting(conn, CUSTOM_LLM_API_KEY_KEY, api_key.strip())


def get_selected_profile_id(conn) -> str:
    value = get_setting(conn, "last_llm_profile", "template")
    try:
        get_profile(value)
        return value
    except Exception:
        return "template"


def set_selected_profile_id(conn, profile_id: str) -> None:
    get_profile(profile_id)
    set_setting(conn, "last_llm_profile", profile_id)
