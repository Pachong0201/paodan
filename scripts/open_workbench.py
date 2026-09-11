#!/usr/bin/env python3
"""Paodan V5.0.2 Local Workbench 一键启动器。"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _free_port(start: int) -> int:
    port = start
    while port < start + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("no free local port")


def _open_browser(url: str) -> None:
    """在 WSL 里调用 Windows 默认浏览器；其他平台使用 webbrowser。"""
    try:
        is_wsl = bool(os.environ.get("WSL_DISTRO_NAME"))
        if not is_wsl:
            try:
                is_wsl = "microsoft" in Path("/proc/version").read_text(
                    encoding="utf-8", errors="ignore").lower()
            except OSError:
                is_wsl = False
        if is_wsl:
            cmd_exe = shutil.which("cmd.exe")
            if cmd_exe:
                subprocess.Popen(
                    [cmd_exe, "/c", "start", "", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
    except Exception:
        pass
    threading.Timer(1.2, webbrowser.open, args=[url]).start()


def main() -> int:
    from app.dashboard.config import load_dashboard_config
    from app.dashboard.server import create_app
    from app.storage.database import Database
    from app.config import DB_PATH

    cfg = load_dashboard_config()
    if not Path(DB_PATH).exists():
        print("启动失败：未找到 paodan 数据库")
        return 1
    Database(DB_PATH).close()
    port = _free_port(cfg.port)
    url = f"http://127.0.0.1:{port}"
    print("Paodan V5.0.2 Local Workbench")
    print(url)
    _open_browser(url)
    import uvicorn
    try:
        uvicorn.run(create_app(DB_PATH), host=cfg.host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
