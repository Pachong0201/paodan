#!/usr/bin/env python3
"""Paodan V5.0 Local Workbench 一键启动器。"""
from __future__ import annotations

import socket
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
    print("Paodan V5.0 Local Workbench")
    print(url)
    threading.Timer(1.2, webbrowser.open, args=[url]).start()
    import uvicorn
    try:
        uvicorn.run(create_app(DB_PATH), host=cfg.host, port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
